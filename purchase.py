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
    {"brand": "무신사 스탠다드", "file_id": "15mZKH9Vh4SLsl2Jb3m3TTrENEckGt9maeE5chJr2sus", "tab": "2. 매장별 SKU산정"},
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
    df["offline_yn"] = df["offline_sale_enabled"].map(lambda v: "Y" if str(v).lower() in ("true", "1") else ("N" if str(v).lower() in ("false", "0") else ""))
    df["product_name"] = df["product_name"].fillna(df["product_name_in"])
    keep = ["brand_in", "store_name", "storage_no", "storage_id", "shop_no", "store_in", "lgort", "store_match",
            "uid", "product_name", "category", "size", "option_name", "sku_id", "fixed_qty", "purchase_type",
            "sales_status", "offline_yn", "storage_status", "uid_sizes", "severity", "flags_text", "src_row"]
    return df[[c for c in keep if c in df]].reset_index(drop=True)


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
    out = build(pd.concat(frames, ignore_index=True)) if frames else pd.DataFrame()
    o = out.copy()
    for c in o.columns:
        if o[c].dtype == object:
            o[c] = o[c].map(lambda v: "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
    o.to_parquet(os.path.join(cache_dir, "purchase_rows.parquet"), index=False)
    summ = {"rows": len(out), "uids": int(out["uid"].nunique()) if len(out) else 0,
            "skus": int(out["sku_id"].nunique()) if len(out) else 0,
            "stores": int(out["store_name"].nunique()) if len(out) else 0,
            "by_severity": out["severity"].value_counts().to_dict() if len(out) else {}}
    return {"files": files, "summary": summ}
