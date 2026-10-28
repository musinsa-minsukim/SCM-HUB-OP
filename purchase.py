"""매입(무신사 스탠다드 등) 운영 시트 → 매장 × SKU ID 전개 (1단계, 2026-10-29 사용자 요청).

시트 양식 ('2. 매장별 SKU산정' 탭, 예: 무탠다드_온오프판매및MFS재고):
  14행  매장 열(AA~)마다 ERP plant1000 저장위치 코드(lgort, 예 3055=킥스 홍대)
  15행  매장명
  16행  헤더 — 상품번호(UID)·상품명·대카테 … 사이즈 열(FREE, 230~290 등) … 매장 열 '운영 체크'
  17행~ 상품(UID) 행. 사이즈 열 = 사이즈별 매장 적정 수량(모든 운영 매장 공통), 매장 열 'Y' = 그 매장 운영
→ 'Y' 매장 × 수량>0 사이즈마다 한 줄. UID + 사이즈 → SCM-HUB SKU ID
  (product[MUSINSA].product_no = UID, product_option.option_name 의 마지막 '^' 뒤 = 사이즈, sku_product_option ACTIVE).

위탁(브랜드 공유 시트)과 섞지 않는다 — 결과는 CACHE_DIR/purchase_rows.parquet, 화면은 '매입 운영' 탭.
재고·보충(2단계)은 매입 재고가 ERP 에 있어(SCM-HUB 일별 재고에 없음) 별도로 붙인다.
"""
from __future__ import annotations

import json
import os
import re

import pandas as pd

import dbx
import progress
import queries as Q

# 매입 시트 목록 — (브랜드 표시명, 파일 id, 탭 이름). 늘어나면 여기(또는 env PURCHASE_SHEETS JSON)에 추가.
PURCHASE_SHEETS = json.loads(os.environ.get("PURCHASE_SHEETS", "null") or "null") or [
    {"brand": "무신사 스탠다드", "brand_id": "musinsastandard",
     "file_id": "15mZKH9Vh4SLsl2Jb3m3TTrENEckGt9maeE5chJr2sus", "tab": "2. 매장별 SKU산정"},
]
SIZE_RE = re.compile(r"^(FREE|F|ONE ?SIZE|OS|XXS|XS|S|M|L|XL|XXL|XXXL|2XL|3XL|\d{2,3}(\.\d)?)$", re.I)

SEVERITY = {"매장 확인 불가": "error", "SKU 없음(사이즈 옵션 없음)": "error", "상품 없음(UID)": "error",
            "UID 형식 오류": "error", "매입 SKU 아님": "warn", "중복 행(같은 매장·SKU)": "warn",
            "SCM 매장 등록 없음": "info"}


def _norm(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def read_values(sheet: dict) -> list[list]:
    """시트 값(2차원). 로컬 테스트는 env PURCHASE_XLSX(내려받은 xlsx)로 대신한다."""
    x = os.environ.get("PURCHASE_XLSX")
    if x:
        import openpyxl
        wb = openpyxl.load_workbook(x, read_only=True, data_only=True)
        ws = wb[sheet["tab"]]
        return [list(r) for r in ws.iter_rows(min_row=1, max_col=80, values_only=True)]
    import sheets
    sess = sheets._session()
    r = sheets._get(sess, f"https://sheets.googleapis.com/v4/spreadsheets/{sheet['file_id']}/values/'{sheet['tab']}'!A1:CB8000",
                    params={"valueRenderOption": "UNFORMATTED_VALUE"})
    return r.json().get("values", [])


def parse(values: list[list], brand: str) -> tuple[pd.DataFrame, dict]:
    """헤더('상품번호' 가 있는 행)를 찾아 사이즈 열·매장 열을 이름으로 잡는다(열 위치가 바뀌어도 되게)."""
    hdr_i = next((i for i, r in enumerate(values[:40]) if any(_norm(v) == "상품번호" for v in r)), None)
    if hdr_i is None or hdr_i < 2:
        raise ValueError("'상품번호' 헤더 행을 찾지 못함")
    hdr, store_row, code_row = values[hdr_i], values[hdr_i - 1], values[hdr_i - 2]
    w = max(len(hdr), len(store_row), len(code_row))
    cell = lambda row, j: _norm(row[j]) if j < len(row) else ""
    col = {cell(hdr, j): j for j in range(w) if cell(hdr, j)}
    uid_c, name_c, cat_c = col["상품번호"], col.get("상품명"), col.get("대카테")
    store_cols = [j for j in range(w) if cell(hdr, j) == "운영 체크" and cell(store_row, j)]
    first_store = min(store_cols) if store_cols else w
    size_cols = [j for j in range(uid_c + 1, first_store) if SIZE_RE.match(cell(hdr, j))]
    stores = [{"col": j, "store_in": cell(store_row, j), "lgort": cell(code_row, j)} for j in store_cols]
    recs = []
    for i in range(hdr_i + 1, len(values)):
        r = values[i]
        uid = cell(r, uid_c)
        if not uid:
            continue
        ys = [s for s in stores if cell(r, s["col"]).upper() == "Y"]
        if not ys:
            continue
        for j in size_cols:
            try:
                q = float(cell(r, j) or 0)
            except ValueError:
                continue
            if q <= 0:
                continue
            for s in ys:
                recs.append({"brand_in": brand, "uid_in": uid, "product_name_in": cell(r, name_c) if name_c is not None else "",
                             "category": cell(r, cat_c) if cat_c is not None else "", "size": cell(hdr, j).upper(),
                             "fixed_qty": int(q) if q == int(q) else q, "store_in": s["store_in"], "lgort": s["lgort"],
                             "src_row": i + 1})
    info = {"stores": [{k: s[k] for k in ("store_in", "lgort")} for s in stores],
            "sizes": [cell(hdr, j) for j in size_cols], "header_row": hdr_i + 1}
    return pd.DataFrame(recs), info


def _store_map(df: pd.DataFrame) -> pd.DataFrame:
    """ERP lgort → BIZEST shop_no → SCM-HUB storage_id. lgort 가 없거나 안 맞으면 매장명으로(StoreMatcher)."""
    from storematch import StoreMatcher
    stores = dbx.run_df(Q.STORES)
    loc = dbx.run_df("SELECT DISTINCT CAST(shop_no AS INT) shop_no, erp_location_code lgort FROM ocmp.moss.shop_location "
                     "WHERE erp_plant_code = '1000'")
    l2s = dict(zip(loc["lgort"].astype(str), loc["shop_no"]))
    s2 = stores.drop_duplicates("shop_no").set_index("shop_no")
    sm = StoreMatcher(stores.to_dict("records"))
    out = []
    for lg, name in df[["lgort", "store_in"]].drop_duplicates().itertuples(index=False):
        shop = l2s.get(str(lg))
        sid, how = None, ""
        if shop is not None and shop in s2.index and pd.notna(s2.at[shop, "storage_id"]):
            sid, how = int(s2.at[shop, "storage_id"]), "ERP 코드"
        else:
            m = sm.match(name)
            if m[0] is not None and m[1] != "후보 여럿":
                sid, how = int(m[0]), f"매장명({m[1]})"
        out.append({"lgort": lg, "store_in": name, "storage_id": sid, "store_match": how})
    mp = pd.DataFrame(out)
    st = stores.drop_duplicates("storage_id").set_index("storage_id")
    mp["store_name"] = mp["storage_id"].map(st["scm_name"])
    mp["storage_no"] = mp["storage_id"].map(st["storage_no"])
    mp["shop_no"] = mp["storage_id"].map(st["shop_no"])
    return mp


def sku_by_uid_size(uids) -> str:
    return f"""
SELECT CAST(p.product_no AS BIGINT) uid, p.product_name, p.sales_status, p.offline_sale_enabled,
       po.option_name, upper(trim(element_at(split(po.option_name, '\\\\^'), -1))) size,
       s._id fk_sku_id, s.sku_id, s.purchase_type, spo.updated_at
FROM ocmp.scm_hub.product p
JOIN ocmp.scm_hub.product_option po ON po.fk_product_id = p._id
LEFT JOIN ocmp.scm_hub.sku_product_option spo ON spo.fk_product_option_id = po._id AND spo.mapping_type = 'ACTIVE'
LEFT JOIN ocmp.scm_hub.sku s ON s._id = spo.fk_sku_id
WHERE p.platform = 'MUSINSA' AND p.product_no IN ({Q.str_list(uids)})"""


# ── 매입 재고 = ERP (2026-10-29 사용자 결정: "1번은 ERP 재고로") ─────────────────────────────
# 매장: ocmp.moss.sap_inventory (plant 1000, storage_location = 시트 14행 lgort). A1 전체적재 매일 ~10시 + M1 증분 수시.
#   키(plant, sloc, material_no, batch_no) 중복(옵션 매핑 바뀐 옛 행) → 최신 1행만, delete_indicator 'X' 제외.
#   available_stock = 가용(판매가능), current_stock = 현재고, transfer_stock = 이동중(ERP).
# 허브: team.partner.raw_erp_stock 최신 dt(매일 10:01 KST 적재 — 하루 1번) — 1000 (2000,2010,2020,2060) + 1700 (2000).
#   ⚠️ 허브 1000 은 LIKE '20%' 금지(신규 창고 과대계상).
# 키 = UID(goods_no) + 사이즈(option_name / goods_opt, 대문자·공백 제거).
def erp_store_stock(uids, lgorts) -> str:
    return f"""
WITH s AS (
  SELECT plant_code, storage_location, goods_no, option_name, available_stock, current_stock, transfer_stock, reserved_stock,
         modified_at,
         ROW_NUMBER() OVER (PARTITION BY plant_code, storage_location, material_no, batch_no
                            ORDER BY modified_at DESC, version DESC, id DESC) rn
  FROM ocmp.moss.sap_inventory
  WHERE plant_code = '1000' AND storage_location IN ({Q.str_list(lgorts)}) AND goods_no IN ({Q.str_list(uids)})
    AND COALESCE(delete_indicator, '') <> 'X')
SELECT storage_location lgort, CAST(goods_no AS BIGINT) uid, upper(trim(option_name)) size,
       CAST(SUM(available_stock) AS BIGINT) erp_avail_qty, CAST(SUM(current_stock) AS BIGINT) erp_stock_qty,
       CAST(SUM(transfer_stock) AS BIGINT) erp_transfer_qty, CAST(SUM(reserved_stock) AS BIGINT) erp_reserved_qty,
       date_format(MAX(modified_at), 'yyyy-MM-dd HH:mm') erp_store_at
FROM s WHERE rn = 1 GROUP BY 1, 2, 3"""


def erp_hub_stock(uids) -> str:
    return f"""
WITH le AS (SELECT MAX(dt) d FROM team.partner.raw_erp_stock)
SELECT CAST(r.goods_no AS BIGINT) uid, upper(trim(r.goods_opt)) size,
       CAST(SUM(CASE WHEN r.store_cd = '1000' THEN r.wqty ELSE 0 END) AS BIGINT) hub1000_qty,
       CAST(SUM(CASE WHEN r.store_cd = '1700' THEN r.wqty ELSE 0 END) AS BIGINT) hub1700_qty,
       MAX(r.dt) hub_dt
FROM team.partner.raw_erp_stock r JOIN le ON r.dt = le.d
WHERE r.goods_no IN ({Q.str_list(uids)})
  AND ((r.store_cd = '1000' AND r.lgort IN ('2000', '2010', '2020', '2060')) OR (r.store_cd = '1700' AND r.lgort = '2000'))
GROUP BY 1, 2"""


# ── 허브 → 매장 ERP 재고이동(STO) 진행분 (2026-10-29, erp-inventory-move-flow 스킬 기준) ──────────
#   편집샵 보충 요청      = 이동지시(iif_sap_sto MSTAT 5) − 그 지시의 반출(oif_sap_str 313), MVNO·MVITEM 별, 최근 30일 지시만
#                          (오래된 미출고 지시가 쌓여 부풀지 않게)
#   보충 확정 후 이동중    = 반출 313 누적 − 매장 입고 315(shkzg S) 누적, 음수는 0 (315 는 MVNO 가 없어 문서별 매칭 불가 →
#                          매장×UID×사이즈 누적 차이. 다른 경로 입고 때문에 음수가 나올 수 있다)
#   매장 코드 = (WERKS 1000, lgort) 쌍. 315(erp_inout_bound)는 플랜트가 없어 lgort 로만.
def erp_moves(uids, lgorts) -> str:
    pairs = [f"1000-{x}" for x in lgorts]
    return f"""
WITH o AS (
  SELECT GR_LGORT lgort, MVNO, MVITEM, S_MATNR uid, upper(trim(OPTION)) size, SUM(CAST(MENGE AS DOUBLE)) q, MIN(INS_DATE) d
  FROM pbo.moms.iif_sap_sto
  WHERE CONCAT(WERKS, '-', GR_LGORT) IN ({Q.str_list(pairs)}) AND MSTAT = '5' AND (CANC IS NULL OR CANC = '')
    AND S_MATNR IN ({Q.str_list(uids)})
  GROUP BY 1, 2, 3, 4, 5),
g AS (
  SELECT GR_LGORT lgort, MVNO, MVITEM, S_MATNR uid, upper(trim(OPTION)) size, SUM(CAST(MENGE AS DOUBLE)) q
  FROM pbo.moms.oif_sap_str
  WHERE CONCAT(WERKS, '-', GR_LGORT) IN ({Q.str_list(pairs)}) AND BWART = '313' AND (CANC IS NULL OR CANC = '')
    AND S_MATNR IN ({Q.str_list(uids)})
  GROUP BY 1, 2, 3, 4, 5),
r AS (
  SELECT lgort, zz_smatnr uid, upper(trim(zz_option)) size, SUM(CAST(menge AS DOUBLE)) q
  FROM musinsa.stock.erp_inout_bound
  WHERE lgort IN ({Q.str_list(lgorts)}) AND bwart = '315' AND shkzg = 'S' AND zz_smatnr IN ({Q.str_list(uids)})
  GROUP BY 1, 2, 3),
pend AS (
  SELECT o.lgort, o.uid, o.size, SUM(GREATEST(o.q - COALESCE(g.q, 0), 0)) req_q
  FROM o LEFT JOIN g ON g.lgort = o.lgort AND g.MVNO = o.MVNO AND g.MVITEM = o.MVITEM
  WHERE substr(o.d, 1, 8) >= date_format(date_sub(current_date(), 30), 'yyyyMMdd')
  GROUP BY 1, 2, 3),
gs AS (SELECT lgort, uid, size, SUM(q) q FROM g GROUP BY 1, 2, 3),
k AS (SELECT lgort, uid, size FROM pend UNION SELECT lgort, uid, size FROM gs UNION SELECT lgort, uid, size FROM r)
SELECT k.lgort, CAST(k.uid AS BIGINT) uid, k.size,
       CAST(COALESCE(pend.req_q, 0) AS BIGINT) req_in_qty,
       CAST(GREATEST(COALESCE(gs.q, 0) - COALESCE(r.q, 0), 0) AS BIGINT) moving_in_qty
FROM k LEFT JOIN pend ON pend.lgort = k.lgort AND pend.uid = k.uid AND pend.size = k.size
LEFT JOIN gs ON gs.lgort = k.lgort AND gs.uid = k.uid AND gs.size = k.size
LEFT JOIN r ON r.lgort = k.lgort AND r.uid = k.uid AND r.size = k.size"""


# 매입 매장 판매 — moss 주문 goods_no + 옵션명(= 사이즈), 순판매(완료 − 환불). 주차 정의는 위탁(queries.offline_sales)과 같다.
def purchase_sales(uids) -> str:
    wk = lambda a, b: f"l.d BETWEEN date_sub({Q.KST_TODAY}, {a}) AND date_sub({Q.KST_TODAY}, {b})"
    return f"""
WITH oo AS (SELECT order_id, goods_no, upper(trim(option_name)) size, quantity FROM ocmp.moss.order_option
            WHERE goods_no IN ({Q.str_list(uids)})),
ord AS (SELECT CAST(COALESCE(om.transaction_at, om.created_at) AS DATE) d, om.shop_no, oo.goods_no, oo.size, 1 sgn, oo.quantity q
        FROM oo JOIN ocmp.moss.order_master om ON om.order_id = oo.order_id WHERE om.dummy_order = 0 AND om.order_status = 50),
ref AS (SELECT rc.d, om.shop_no, oo.goods_no, oo.size, -1 sgn, oo.quantity q
        FROM (SELECT order_id, CAST(created_at AS DATE) d FROM ocmp.moss.claim WHERE claim_type = 'REFUND' GROUP BY 1, 2) rc
        JOIN ocmp.moss.order_master om ON om.order_id = rc.order_id JOIN oo ON oo.order_id = rc.order_id WHERE om.dummy_order = 0),
l AS (SELECT * FROM ord UNION ALL SELECT * FROM ref)
SELECT CAST(l.goods_no AS BIGINT) uid, l.size, CAST(l.shop_no AS INT) shop_no,
  CAST(SUM(CASE WHEN {wk(7, 1)} THEN l.sgn*l.q ELSE 0 END) AS BIGINT) off_w1,
  CAST(SUM(CASE WHEN {wk(28, 1)} THEN l.sgn*l.q ELSE 0 END) AS BIGINT) off_4w,
  CAST(SUM(l.sgn*l.q) AS BIGINT) off_cum
FROM l GROUP BY 1, 2, 3"""


def enabled_purchase(storage_ids, brand_ids) -> str:
    """SCM-HUB 에서 운영중(OPERATION_ENABLED)인 매입 SKU × 시트 매장 — 시트에 없으면 '미운영' 대상."""
    return f"""
SELECT ss.fk_storage_id storage_id, s.sku_id, CAST(p.product_no AS BIGINT) uid, p.product_name, po.option_name
FROM ocmp.scm_hub.storage_sku ss
JOIN ocmp.scm_hub.sku s ON s._id = ss.fk_sku_id AND s.purchase_type = 'PURCHASE'
JOIN ocmp.scm_hub.sku_product_option spo ON spo.fk_sku_id = s._id AND spo.mapping_type = 'ACTIVE'
JOIN ocmp.scm_hub.product_option po ON po._id = spo.fk_product_option_id
JOIN ocmp.scm_hub.product p ON p._id = po.fk_product_id AND p.platform = 'MUSINSA'
WHERE ss.status = 'OPERATION_ENABLED' AND ss.fk_storage_id IN ({Q.int_list(storage_ids)})
  AND p.product_no IN (SELECT CAST(id AS STRING) FROM musinsa.stock.product WHERE brand_id IN ({Q.str_list(brand_ids)}))"""


def build(rows: pd.DataFrame) -> pd.DataFrame:
    df = rows.copy()
    df["flags"] = [[] for _ in range(len(df))]

    def flag(mask, name):
        for i in df.index[mask.fillna(False)]:
            df.at[i, "flags"].append(name)

    df["uid"] = pd.to_numeric(df["uid_in"], errors="coerce").astype("Int64")
    flag(df["uid"].isna(), "UID 형식 오류")
    # 매장
    progress.step("매입: 매장 매핑")
    mp = _store_map(df)
    df = df.merge(mp, on=["lgort", "store_in"], how="left")
    flag(df["storage_id"].isna(), "매장 확인 불가")
    # SKU
    progress.step("매입: UID·사이즈 → SKU ID")
    uids = df["uid"].dropna().astype("int64").unique()
    sk = dbx.run_df(sku_by_uid_size(uids)) if len(uids) else pd.DataFrame(columns=["uid", "size"])
    sk = sk.assign(size=sk["size"].astype(str).str.upper())
    has_uid = set(sk["uid"].astype("int64")) if len(sk) else set()
    # 같은 UID·사이즈에 SKU 가 여럿이면 매입·최근 연결 우선
    sk = sk.sort_values(["purchase_type", "updated_at"], ascending=[True, False], key=lambda c: c.ne("PURCHASE") if c.name == "purchase_type" else c)
    skd = sk.dropna(subset=["sku_id"]).drop_duplicates(["uid", "size"])
    df["uid_k"] = df["uid"].astype("float").astype("Int64")
    df = df.merge(skd.rename(columns={"uid": "uid_k"}), on=["uid_k", "size"], how="left")
    flag(df["uid"].notna() & ~df["uid"].isin(has_uid), "상품 없음(UID)")
    flag(df["uid"].isin(has_uid) & df["sku_id"].isna(), "SKU 없음(사이즈 옵션 없음)")
    flag(df["purchase_type"].notna() & (df["purchase_type"] != "PURCHASE"), "매입 SKU 아님")
    # 상품별 실제 옵션(사이즈) 목록 — 옵션 없음일 때 참고
    opts = sk.groupby("uid")["size"].agg(lambda s: ", ".join(sorted(set(s), key=lambda x: (len(x), x)))) if len(sk) else pd.Series(dtype=str)
    df["uid_sizes"] = df["uid_k"].map(lambda u: opts.get(int(u), "") if pd.notna(u) else "")
    dup = df.duplicated(["storage_id", "sku_id"], keep="first") & df["storage_id"].notna() & df["sku_id"].notna()
    flag(dup, "중복 행(같은 매장·SKU)")
    # SCM-HUB 매장 등록 상태(참고)
    fk = df["fk_sku_id"].dropna().astype("int64").unique()
    sids = df["storage_id"].dropna().astype("int64").unique()
    if len(fk) and len(sids):
        progress.step("매입: SCM-HUB 매장 등록 상태")
        ss = dbx.run_df(Q.storage_sku(fk, sids))[["fk_sku_id", "storage_id", "storage_status"]]
        df = df.merge(ss.astype({"fk_sku_id": "float64", "storage_id": "float64"}),
                      left_on=[df["fk_sku_id"].astype("float64"), df["storage_id"].astype("float64")],
                      right_on=["fk_sku_id", "storage_id"], how="left", suffixes=("", "_ss"))
        df = df.drop(columns=[c for c in df.columns if c.startswith("key_") or c.endswith("_ss")], errors="ignore")
    if "storage_status" not in df:
        df["storage_status"] = None
    flag(df["sku_id"].notna() & df["storage_id"].notna() & df["storage_status"].isna(), "SCM 매장 등록 없음")
    df["severity"] = df["flags"].map(lambda fs: "error" if any(SEVERITY[f] == "error" for f in fs)
                                     else "warn" if any(SEVERITY[f] == "warn" for f in fs) else "info" if fs else "ok")
    df["flags_text"] = df["flags"].map(" / ".join)
    # ERP 재고 (매장·허브)
    uids_s = [str(int(u)) for u in uids]
    lg = sorted({str(x) for x in df["lgort"].dropna() if str(x)})
    if uids_s and lg:
        progress.step("매입: ERP 매장 재고")
        es = dbx.run_df(erp_store_stock(uids_s, lg))
        if len(es):
            es = es.assign(lgort=es["lgort"].astype(str), uid=es["uid"].astype("int64"), size=es["size"].astype(str))
            df = df.merge(es, left_on=[df["lgort"].astype(str), df["uid_k"].astype("float").astype("Int64").astype("float"), df["size"]],
                          right_on=[es["lgort"], es["uid"].astype("float"), es["size"]], how="left", suffixes=("", "_es"))
            df = df.drop(columns=[c for c in df.columns if c.startswith("key_") or c.endswith("_es")], errors="ignore")
    if uids_s:
        progress.step("매입: ERP 허브 재고")
        hs = dbx.run_df(erp_hub_stock(uids_s))
        if len(hs):
            hs = hs.assign(size=hs["size"].astype(str), uid_k=hs["uid"].astype("int64").astype("float"))
            df = df.assign(_u=df["uid_k"].astype("float")).merge(hs.drop(columns=["uid"]).rename(columns={"uid_k": "_u"}),
                                                                 on=["_u", "size"], how="left").drop(columns=["_u"])
    if uids_s and lg:
        progress.step("매입: ERP 허브→매장 이동")
        mv = dbx.run_df(erp_moves(uids_s, lg))
        if len(mv):
            mv = mv.assign(_k=mv["lgort"].astype(str) + "|" + mv["uid"].astype("int64").astype(str) + "|" + mv["size"].astype(str))
            df["_k"] = df["lgort"].astype(str) + "|" + df["uid_k"].astype("float").astype("Int64").astype(str) + "|" + df["size"]
            df = df.merge(mv[["_k", "req_in_qty", "moving_in_qty"]], on="_k", how="left").drop(columns=["_k"])
        progress.step("매입: 매장 판매")
        sl = dbx.run_df(purchase_sales(uids_s))
        if len(sl):
            sl = sl.assign(_k=sl["shop_no"].astype(str) + "|" + sl["uid"].astype("int64").astype(str) + "|" + sl["size"].astype(str))
            df["_k"] = pd.to_numeric(df["shop_no"], errors="coerce").astype("Int64").astype(str) + "|" \
                + df["uid_k"].astype("float").astype("Int64").astype(str) + "|" + df["size"]
            df = df.merge(sl[["_k", "off_w1", "off_4w", "off_cum"]].drop_duplicates("_k"), on="_k", how="left").drop(columns=["_k"])
    for c in ("erp_avail_qty", "erp_stock_qty", "erp_transfer_qty", "erp_reserved_qty", "hub1000_qty", "hub1700_qty",
              "req_in_qty", "moving_in_qty", "off_w1", "off_4w", "off_cum"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64") if c in df else 0
    df["hub_qty"] = df["hub1000_qty"] + df["hub1700_qty"]
    # 위탁과 같은 정의(2026-10-21): 이동 입고 예정 = 편집샵 보충 요청 + 보충 확정 후 이동중,
    # 예상 재고 = 매장 현재고 + 이동 입고 예정 − 매장 반납/반출 예정(매입은 아직 0 — ERP 매장→허브 반품 진행분 미연결)
    df["incoming_qty"] = df["req_in_qty"] + df["moving_in_qty"]
    df["outgoing_qty"] = 0
    df["expected_qty"] = df["erp_stock_qty"] + df["incoming_qty"] - df["outgoing_qty"]
    fq = pd.to_numeric(df["fixed_qty"], errors="coerce").fillna(0)
    ok = df["sku_id"].notna() & df["storage_id"].notna() & (df["severity"].ne("error") if "severity" in df else True)
    have = df["expected_qty"].clip(lower=0)
    df["need_qty"] = (fq - have).clip(lower=0).where(ok, 0).astype("int64")
    df["over_qty"] = (have - fq).clip(lower=0).where(ok, 0).astype("int64")
    df["ret_qty"] = pd.concat([df["erp_avail_qty"].clip(lower=0), df["over_qty"]], axis=1).min(axis=1).astype("int64")
    df["offline_yn"] = df["offline_sale_enabled"].map(lambda v: "Y" if str(v).lower() in ("true", "1") else ("N" if str(v).lower() in ("false", "0") else ""))
    df["product_name"] = df["product_name"].fillna(df["product_name_in"])
    keep = ["brand_in", "store_name", "storage_no", "storage_id", "shop_no", "store_in", "lgort", "store_match",
            "uid", "product_name", "category", "size", "option_name", "sku_id", "fixed_qty", "purchase_type",
            "sales_status", "offline_yn", "storage_status",
            "erp_stock_qty", "erp_avail_qty", "erp_transfer_qty", "erp_reserved_qty", "req_in_qty", "moving_in_qty",
            "incoming_qty", "outgoing_qty", "expected_qty", "hub1000_qty", "hub1700_qty", "hub_qty",
            "off_w1", "off_4w", "off_cum", "need_qty", "over_qty", "ret_qty", "alloc_qty", "fk_sku_id", "erp_store_at", "hub_dt",
            "uid_sizes", "severity", "flags_text", "src_row"]
    return df[[c for c in keep if c in df]].reset_index(drop=True)


def allocate(df: pd.DataFrame) -> pd.DataFrame:
    """허브(1000+1700) 재고를 보충 필요에 배분 — 위탁 MFS 배분과 같은 순서(매장 판매 7일 → 4주 → 누적 → 보충 필요).
    매입은 오프라인 판매 여부와 무관하게(ERP 출고) 배분하고, 오류·중복 행은 제외."""
    df = df.copy()
    df["alloc_qty"] = 0
    if df.empty:
        return df
    left = df.drop_duplicates("sku_id").set_index("sku_id")["hub_qty"].to_dict()
    cand = df[(df["need_qty"] > 0) & (df["severity"] != "error") & ~df["flags_text"].str.contains("중복", regex=False)]
    for i, r in cand.sort_values(["off_w1", "off_4w", "off_cum", "need_qty"], ascending=False).iterrows():
        a = int(min(r["need_qty"], max(left.get(r["sku_id"], 0), 0)))
        df.at[i, "alloc_qty"] = a
        left[r["sku_id"]] = left.get(r["sku_id"], 0) - a
    return df


def uploads(df: pd.DataFrame, brand_ids: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """SCM-HUB 업로드 파일(위탁과 같은 양식) — ① 운영상태 ② 재고 보충.
    ① 시트 'Y' 인데 SCM 운영중 아님 → 운영중 / SCM 운영중인데 시트에 없음(같은 브랜드 매입 SKU, 시트 매장) → 미운영
    ② 이동수량 = 허브 배분(alloc), 과재고 반출 수량 = min(가용, 예상 재고 − 적정). 바코드 = SCM-HUB 대표 바코드."""
    import engine
    ok = df[(df["severity"] != "error") & ~df["flags_text"].str.contains("중복", regex=False)]
    on = ok[ok["storage_status"].fillna("") != "OPERATION_ENABLED"].assign(
        target_status="운영중", reason=lambda x: x["storage_status"].fillna("").replace("", "등록 없음").map(lambda v: f"시트 Y · SCM {v}"))
    sids = df["storage_id"].dropna().astype("int64").unique()
    off = pd.DataFrame()
    if len(sids) and brand_ids:
        progress.step("매입: SCM 운영중 매장×SKU")
        en = dbx.run_df(enabled_purchase(sids, brand_ids))
        keys = set(zip(ok["storage_id"].astype("int64"), ok["sku_id"]))
        off = en[[(int(a), b) not in keys for a, b in zip(en["storage_id"], en["sku_id"])]].assign(
            target_status="미운영", reason="시트에 없음 · SCM 운영중", storage_status="OPERATION_ENABLED") if len(en) else pd.DataFrame()
        st = df.dropna(subset=["storage_id"]).assign(storage_id=lambda x: x["storage_id"].astype("int64"))             .drop_duplicates("storage_id").set_index("storage_id")
        if off.empty or "storage_id" not in off:
            off = pd.DataFrame()
        else:
            off = off.assign(store_name=off["storage_id"].astype("int64").map(st["store_name"]),
                             storage_no=off["storage_id"].astype("int64").map(st["storage_no"]), size=off["option_name"])
    cols = ["storage_no", "store_name", "sku_id", "uid", "product_name", "size", "target_status", "reason", "storage_status", "erp_stock_qty"]
    ops = pd.concat([on.reindex(columns=cols), off.reindex(columns=cols)], ignore_index=True)
    # ② 재고 보충
    bc = {}
    fks = df["fk_sku_id"].dropna().astype("int64").unique()
    if len(fks):
        sm = dbx.run_df(Q.sku_master(df["sku_id"].dropna().unique()))
        bc = dict(zip(sm["sku_id"], sm["rep_barcode"]))
    mv = df[(df["alloc_qty"] > 0) | (df["ret_qty"] > 0)].copy()
    mv["barcode"] = mv["sku_id"].map(bc)
    mv["move_qty"] = mv["alloc_qty"]
    mv["_kind"] = (mv["move_qty"] == 0).astype(int)
    mv = mv.sort_values(["sku_id", "_kind", "off_w1", "off_4w", "off_cum"], ascending=[True, True, False, False, False]).drop(columns="_kind")
    return ops, mv


def run(cache_dir: str) -> dict:
    """모든 매입 시트를 읽어 전개 → purchase_rows.parquet. 시트별 상태를 돌려준다(meta.purchase)."""
    frames, files = [], []
    for sh in PURCHASE_SHEETS:
        st = {"brand": sh["brand"], "file_id": sh["file_id"], "tab": sh["tab"], "status": "ok", "detail": "", "rows": 0}
        try:
            progress.step(f"매입 시트 읽기: {sh['brand']}")
            rows, info = parse(read_values(sh), sh["brand"])
            st.update(rows=len(rows), stores=info["stores"], sizes=info["sizes"])
            if len(rows):
                frames.append(rows)
        except Exception as e:  # 매입 시트 실패가 위탁 새로고침을 막지 않게
            st.update(status="읽기 실패", detail=str(e)[:300])
        files.append(st)
    raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    raw.to_pickle(os.path.join(cache_dir, "purchase_sheet_rows.pkl"))
    return _save(cache_dir, build(raw) if len(raw) else pd.DataFrame(), files)


def rebuild(cache_dir: str, prev: dict) -> dict:
    """시트는 다시 읽지 않고(저장해 둔 원본 행) ERP 재고·SKU·매장 상태만 다시 붙인다 — 어떤 새로고침 버튼이든 재고는 매번 새로."""
    p = os.path.join(cache_dir, "purchase_sheet_rows.pkl")
    if not os.path.exists(p):
        return prev or {}
    raw = pd.read_pickle(p)
    return _save(cache_dir, build(raw) if len(raw) else pd.DataFrame(), (prev or {}).get("files", []))


def _save(cache_dir: str, out: pd.DataFrame, files: list) -> dict:
    if len(out):
        out = allocate(out)
        brand_ids = sorted({sh.get("brand_id") for sh in PURCHASE_SHEETS if sh.get("brand_id")})
        ops, mv = uploads(out, brand_ids)
    else:
        ops, mv = pd.DataFrame(), pd.DataFrame()
    for name, x in (("purchase_ops.parquet", ops), ("purchase_moves.parquet", mv)):
        x = x.copy()
        for c in x.columns:
            if x[c].dtype == object:
                x[c] = x[c].map(lambda v: "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
        x.to_parquet(os.path.join(cache_dir, name), index=False)
    o = out.copy()
    for c in o.columns:
        if o[c].dtype == object:
            o[c] = o[c].map(lambda v: "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
    o.to_parquet(os.path.join(cache_dir, "purchase_rows.parquet"), index=False)
    summ = {"rows": len(out), "uids": int(out["uid"].nunique()) if len(out) else 0,
            "skus": int(out["sku_id"].nunique()) if len(out) else 0,
            "stores": int(out["store_name"].nunique()) if len(out) else 0,
            "by_severity": out["severity"].value_counts().to_dict() if len(out) else {},
            "need_qty": int(out["need_qty"].sum()) if len(out) else 0, "alloc_qty": int(out["alloc_qty"].sum()) if len(out) else 0,
            "ret_qty": int(out["ret_qty"].sum()) if len(out) else 0, "incoming_qty": int(out["incoming_qty"].sum()) if len(out) else 0,
            "ops_on": int((ops["target_status"] == "운영중").sum()) if len(ops) else 0,
            "ops_off": int((ops["target_status"] == "미운영").sum()) if len(ops) else 0,
            "erp_store_at": str(out["erp_store_at"].dropna().max()) if len(out) and "erp_store_at" in out and out["erp_store_at"].notna().any() else "",
            "hub_dt": str(out["hub_dt"].dropna().max()) if len(out) and "hub_dt" in out and out["hub_dt"].notna().any() else ""}
    return {"files": files, "summary": summ}
