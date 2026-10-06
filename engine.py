"""운영 상품(브랜드 취합) → 검증 → 원천 결합 → 보충 제안.

입력 행 = 브랜드 시트 한 줄 (매장 × SKU ID × 매장 고정 수량). 모든 결합 키는 SKU ID.

1단계 보충 규칙 (사용자 결정 2026-09-30, 매일 보충 가능 기준):
    필요 = max(매장 고정 수량 − (판매가능 재고 + 입고 예정), 0)
    MFS 가용(mfs_qty) 안에서 매장별로 배분 — 최근 4주 매장 판매가 많은 매장 먼저, 같으면 부족분 큰 순.
    과잉 = (판매가능 + 입고 예정) − 고정 수량 > 0 이고 4주 판매 0 → 반출 후보(자동 지시 아님, 표시만).
2단계에서 '고정 수량' 자리를 사이즈런·판매 기반 적정 capa 로 바꾼다 → target_qty() 만 교체하면 된다.
"""
from __future__ import annotations

import os
import re
import argparse

import numpy as np
import pandas as pd

import dbx
import progress
import queries as Q
from storematch import StoreMatcher

# 대상 브랜드 = 이 오프라인 MD 들이 담당하는 브랜드 (partnerportal.company_brand.offline_md_id).
# MD 가 늘어나면 코드 수정 없이 환경변수 TARGET_MDS="a,b,c" 로 바꾼다 (Cloud Run: --update-env-vars).
MD_IDS = tuple(x.strip() for x in os.environ.get("TARGET_MDS", "minsu.kim,jieun.kim12").split(",") if x.strip())
# MD 기준 밖이지만 사용자가 포함하기로 한 브랜드 (com_id, brand) — 2026-09-30 결정.
#   앤더슨벨: 오프라인 MD sunsik.park 이지만 직접 운영. 에버에이유: 오프라인 MD 미지정(partnerportal 등록 필요),
#   SCM-HUB SKU 아직 없음. 중고(musinsa_used) 업체코드는 넣지 않는다.
EXTRA_BRANDS = (("anderssonbell", "anderssonbell"), ("everau", "everau"))
# 운영 매장 범위 = 시트 '매장코드' 탭의 SCM storage 17개. 운영 상태 파일은 이 매장들만 만든다.
STORE_SCOPE = (51, 52, 53, 105, 130, 131, 135, 137, 138, 145, 146, 147, 148, 153, 154, 157, 197)
SKU_RE = re.compile(r"^S\d{13}$")
# 브랜드 시트 템플릿에 남아 있는 예시 행 — 모든 브랜드 파일에 1줄씩 있고 옛 3-2 가 실제 상품처럼 가져왔다.
TEMPLATE_SKUS = {"S2601011236471"}
TEMPLATE_BRANDS = {"오드타입"}

# 매장명 별칭·매칭 규칙은 storematch.py (ALIASES) 에 있다.

SEVERITY = {  # error = 보충 계산에서 제외, warn = 계산은 하되 표시, info = 참고
    "SKU ID 형식 오류": "error", "SKU ID 원천에 없음": "error", "템플릿 예시 행": "error",
    "매장명 확인 불가": "error", "매장명 후보 여럿": "error", "매장명 자동 매칭": "info", "중복 행(같은 매장·SKU)": "warn", "고정 수량 비어 있음/숫자 아님": "error",
    "위탁 SKU 아님": "error", "브랜드 불일치": "warn", "담당 MD 브랜드 아님": "warn",
    "SCM 운영중 전환 필요": "warn", "비제스트 오프라인 판매 N": "warn",
    "SCM 오프라인 판매 미반영": "warn", "오프라인 판매 비제스트 N·SCM Y": "info",
    "글로벌 SKU(GLOBAL_3P)": "warn", "바코드 없음": "warn", "바코드 2개 이상": "info",
    "SKU에 UID 여러 개 연결": "info",
}


def target_qty(df: pd.DataFrame) -> pd.Series:
    """적정 재고 목표. 1단계 = 브랜드가 적은 매장 고정 수량."""
    return df["fixed_qty"]


def _chunks(seq, n=10000):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _run_chunked(builder, keys, *extra) -> pd.DataFrame:
    progress.step(f"원천 조회: {builder.__name__}")
    frames = [dbx.run_df(builder(part, *extra)) for part in _chunks(keys)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


LINK_COLS = ["fk_sku_id", "goods_no", "product_name", "option_name", "option_code", "offline_sale_enabled",
             "product_platform", "n_goods", "all_goods"]


def pick_links(fk_sku_ids) -> pd.DataFrame:
    """SKU 별 대표 상품 옵션(UID) 1건 + 함께 연결된 UID 수·목록. 우선순위는 queries.sku_links 위 주석 참고."""
    fk_sku_ids = [int(x) for x in fk_sku_ids]
    if not fk_sku_ids:
        return pd.DataFrame(columns=LINK_COLS)
    ln = _run_chunked(Q.sku_links, fk_sku_ids)
    if ln.empty:
        return pd.DataFrame(columns=LINK_COLS)
    op = _run_chunked(Q.option_products, ln["fk_product_option_id"].dropna().astype("int64").unique())
    m = ln.merge(op, on="fk_product_option_id", how="left")
    m["goods_no"] = pd.to_numeric(m["goods_no"], errors="coerce").astype("Int64")
    act = m["mapping_type"] == "ACTIVE"
    m["_k1"] = (~act).astype(int)
    m["_k2"] = (m["sales_status"] != "ACTIVE").astype(int)
    m["_k3"] = (~m["offline_sale_enabled"].astype(str).str.lower().isin(["true", "1"])).astype(int)
    m["_ut"] = pd.to_datetime(m["updated_at"], errors="coerce", utc=True)
    best = (m.sort_values(["fk_sku_id", "_k1", "_k2", "_k3", "_ut"], ascending=[True, True, True, True, False])
             .drop_duplicates("fk_sku_id"))
    allg = (m[act & m["goods_no"].notna()].groupby("fk_sku_id")["goods_no"]
            .agg(lambda s: sorted({str(int(x)) for x in s})))
    best["all_goods"] = best["fk_sku_id"].map(lambda k: ",".join(allg.get(k, [])))
    best["n_goods"] = best["fk_sku_id"].map(lambda k: len(allg.get(k, [])))
    return best[LINK_COLS].reset_index(drop=True)


def fetch_sku(ids) -> pd.DataFrame:
    """SKU 마스터 + 대표 UID(pick_links) + 상품 브랜드·카테고리(goods_attr)."""
    sm = _run_chunked(Q.sku_master, ids)
    if len(sm):
        sm = sm.merge(pick_links(sm["fk_sku_id"].dropna().astype("int64").unique()), on="fk_sku_id", how="left")
    gn = sm["goods_no"].dropna().astype(str).unique() if len(sm) and "goods_no" in sm else []
    if len(gn):
        ga = _run_chunked(Q.goods_attr, gn)
        if len(ga):
            sm = sm.merge(ga.assign(goods_no=ga["goods_no"].astype(str)),
                          left_on=sm["goods_no"].astype(str), right_on="goods_no", how="left",
                          suffixes=("", "_g")).drop(columns=["key_0", "goods_no_g"], errors="ignore")
    for c in ["sku_id", "fk_sku_id", "rep_barcode", "other_barcodes", "n_barcode", "purchase_type", "consignment_type",
              *LINK_COLS[1:], "brand_nm", "com_id", "brand", "small_nm", "normal_price", "img"]:
        if c not in sm:
            sm[c] = None
    return sm


def _all_sids(src) -> list[int]:
    st = src.whole("stores", lambda: dbx.run_df(Q.STORES))
    return sorted(st["storage_id"].dropna().astype("int64").unique())


def _sales(src, sku_fk: pd.DataFrame) -> pd.DataFrame:
    """오프라인 판매(sku_id × shop_no). 조회는 fk_sku_id 로 한다."""
    fkmap = dict(zip(sku_fk["sku_id"].astype(str), sku_fk["fk_sku_id"]))
    ids = [i for i in fkmap if pd.notna(fkmap[i])]
    return src.get("sales", ids, "sku_id",
                   lambda ks: _run_chunked(Q.offline_sales, [int(fkmap[k]) for k in ks if k in fkmap]))


def build(rows: pd.DataFrame, src) -> tuple[pd.DataFrame, dict]:
    """rows: brand_in, store_in, sku_id_in, fixed_qty_in, src_file, src_row
    src: source.Source — 원천 조회 결과 저장소(영역별 새로고침)."""
    df = rows.copy()
    df["sku_id"] = df["sku_id_in"].astype(str).str.strip().str.upper()
    # CSV 로 읽으면 "1,000" 같은 천단위 쉼표가 붙어 온다
    df["fixed_qty"] = pd.to_numeric(df["fixed_qty_in"].astype(str).str.replace(",", "").str.strip(), errors="coerce")
    df["flags"] = [[] for _ in range(len(df))]

    def flag(mask, name):
        for i in df.index[mask.fillna(False)]:
            df.at[i, "flags"].append(name)

    # ── 입력 자체 검증 ──
    flag(df["sku_id"].isin(TEMPLATE_SKUS) | df["brand_in"].isin(TEMPLATE_BRANDS), "템플릿 예시 행")
    flag(~df["sku_id"].str.match(SKU_RE), "SKU ID 형식 오류")
    flag(df["fixed_qty"].isna(), "고정 수량 비어 있음/숫자 아님")

    # ── 매장 매핑 ──
    # 매장명은 브랜드마다 조금씩 다르게 적는다 → storematch 가 정확·끝'점'·키워드 순으로 찾고,
    # 여러 매장이 걸리면(예: '홍대' = 스토어/킥스/뷰티) 자동으로 정하지 않는다. test_storematch.py 참고.
    stores = src.whole("stores", lambda: dbx.run_df(Q.STORES))
    sm = StoreMatcher(stores.to_dict("records"))
    m = df["store_in"].map(sm.match)
    df["storage_id"] = m.map(lambda x: x[0])
    df["store_match"] = m.map(lambda x: x[1])
    df["store_match_note"] = m.map(lambda x: x[2])
    flag(df["store_match"] == "못 찾음", "매장명 확인 불가")
    flag(df["store_match"] == "후보 여럿", "매장명 후보 여럿")
    flag(df["store_match"] == "키워드", "매장명 자동 매칭")
    sid2 = stores.set_index("storage_id")
    df["store_name"] = df["storage_id"].map(sid2["scm_name"])
    df["storage_no"] = df["storage_id"].map(sid2["storage_no"])
    df["shop_no"] = df["storage_id"].map(sid2["shop_no"])

    dup = df.duplicated(["storage_id", "sku_id"], keep="first") & df["storage_id"].notna()
    flag(dup, "중복 행(같은 매장·SKU)")

    # ── SKU 마스터 ──
    ok_ids = df.loc[df["sku_id"].str.match(SKU_RE), "sku_id"].unique()
    sm = src.get("sku", ok_ids, "sku_id", fetch_sku)
    for c in fetch_sku([]).columns if sm.empty else []:
        sm[c] = None
    df = df.merge(sm.drop_duplicates("sku_id"), on="sku_id", how="left")
    flag(df["sku_id"].str.match(SKU_RE) & df["fk_sku_id"].isna(), "SKU ID 원천에 없음")
    flag(df["purchase_type"].notna() & (df["purchase_type"] != "CONSIGNMENT"), "위탁 SKU 아님")
    flag(df["consignment_type"] == "GLOBAL_3P", "글로벌 SKU(GLOBAL_3P)")
    flag(df["brand_nm"].notna() & (df["brand_nm"] != df["brand_in"]), "브랜드 불일치")
    flag(df["fk_sku_id"].notna() & (df["n_barcode"] == 0), "바코드 없음")
    flag(df["n_barcode"] > 1, "바코드 2개 이상")
    # 한 SKU 에 UID 가 여러 개 ACTIVE 로 연결 → 판매중·오프라인 판매 가능한 UID 를 골랐고 나머지는 other_goods 로 표시
    gstr = pd.to_numeric(df["goods_no"], errors="coerce").astype("Int64").astype(str)
    df["other_goods"] = [",".join(u for u in (a if isinstance(a, str) else "").split(",") if u and u != g)
                         for a, g in zip(df["all_goods"], gstr)]
    flag(pd.to_numeric(df["n_goods"], errors="coerce").fillna(0) > 1, "SKU에 UID 여러 개 연결")

    # 보충 발주 조건 ①: 상품(UID) 오프라인 판매 여부 = Y
    #   발주를 실제로 막는 값 = SKU 가 연결된 SCM-HUB product 행의 offline_sale_enabled (True → Y) → offline_yn
    #   비제스트 원장 값(stock.product.for_offline_sale) → bz_offline_yn. 둘이 다르면 플래그로 구분.
    en = df["offline_sale_enabled"].map(offline_yn_from)
    df["offline_yn"] = en.where(df["fk_sku_id"].notna(), "")
    df = with_bizest_offline(df, src)
    for f, m in offline_flags(df).items():
        flag(m, f)

    tb = src.whole("tb", lambda: dbx.run_df(Q.target_brands(MD_IDS, EXTRA_BRANDS)))
    tb_keys = set(zip(tb["com_id"], tb["brand"]))
    df["offline_md"] = [dict(zip(zip(tb["com_id"], tb["brand"]), tb["offline_md_id"])).get((c, b))
                        for c, b in zip(df["com_id"], df["brand"])]
    flag(df["com_id"].notna() & ~pd.Series([(c, b) in tb_keys for c, b in zip(df["com_id"], df["brand"])],
                                           index=df.index), "담당 MD 브랜드 아님")

    # ── 원천 수치 ──
    fk = pd.to_numeric(df["fk_sku_id"], errors="coerce").dropna().astype("int64").unique()
    sids = _all_sids(src)
    st = src.get("stock", fk, "fk_sku_id", lambda ks: _run_chunked(Q.store_stock, ks, sids), int)
    ss = src.get("ss", fk, "fk_sku_id", lambda ks: _run_chunked(Q.storage_sku, ks, sids), int)
    sales = _sales(src, df[["sku_id", "fk_sku_id"]].dropna().drop_duplicates("sku_id"))
    mfs = src.get("mfs", ok_ids, "sku_id", lambda ks: _run_chunked(Q.mfs_stock, ks))
    inb = src.get("inb", ok_ids, "sku_id", lambda ks: _run_chunked(Q.mfs_inbound, ks))
    onl = src.get("onl", df["option_code"].dropna().unique(), "option_code", lambda ks: _run_chunked(Q.online_sales, ks))
    if "storage_id" in st:
        st = st.drop_duplicates(["fk_sku_id", "storage_id"])
    if "storage_id" in ss:
        ss = ss.drop_duplicates(["fk_sku_id", "storage_id"])

    df["fk_sku_id"] = pd.to_numeric(df["fk_sku_id"], errors="coerce").astype("Int64")
    df["storage_id"] = pd.to_numeric(df["storage_id"], errors="coerce").astype("Int64")
    if len(st):
        st = st.astype({"fk_sku_id": "Int64", "storage_id": "Int64"})
        df = df.merge(st, on=["fk_sku_id", "storage_id"], how="left")
    if len(ss):
        ss = ss.astype({"fk_sku_id": "Int64", "storage_id": "Int64"})
        df = df.merge(ss, on=["fk_sku_id", "storage_id"], how="left")
    else:
        df["storage_status"] = None
    reg = df["fk_sku_id"].notna() & df["storage_id"].notna()
    # 시트에 있는데 SCM-HUB 가 운영중이 아님(등록 없음 포함) → 운영상태 업로드로 '운영중' 전환 (등록 없는 조합도 업로드로 반영됨, 사용자 확인)
    flag(reg & (df["storage_status"] != "OPERATION_ENABLED"), "SCM 운영중 전환 필요")

    if len(sales):
        sales = sales.astype({"shop_no": "Int64"})
        df["shop_no"] = df["shop_no"].astype("Int64")
        df = df.merge(sales, on=["sku_id", "shop_no"], how="left")
        tot = sales.groupby("sku_id")[["off_w1", "off_w2", "off_w3", "off_w4"]].sum()
        df["off_all_w1"] = df["sku_id"].map(tot["off_w1"])
        df["off_all_4w"] = df["sku_id"].map(tot.sum(axis=1))
        df["off_all_cum"] = df["sku_id"].map(sales.groupby("sku_id")["off_cum"].sum())
    if len(mfs):
        df = df.merge(mfs.drop_duplicates("sku_id"), on="sku_id", how="left")
    if len(inb):
        df = df.merge(inb, on="sku_id", how="left")
    if len(onl):
        onl = onl.assign(option_code=onl["option_code"].astype(str)).drop_duplicates("option_code")
        df = df.merge(onl, left_on=df["option_code"].astype(str), right_on="option_code", how="left",
                      suffixes=("", "_o")).drop(columns=["key_0", "option_code_o"], errors="ignore")

    num = ["stock_qty", "avail_qty", "incoming_qty", "req_in_qty", "moving_in_qty", "direct_in_qty", "outgoing_qty", "defect_qty", "off_w1", "off_w2",
           "off_w3", "off_w4", "off_today", "off_cum", "off_all_w1", "off_all_4w", "off_all_cum", "mfs_qty", "mfs_inbound_qty", "mfs_in_qty", "mfs_in_late_qty",
           "onl_w1", "onl_4w"]
    for c in num:
        if c not in df:
            df[c] = 0
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")
    df["off_4w"] = df[["off_w1", "off_w2", "off_w3", "off_w4"]].sum(axis=1)

    return replenish(df)


OFFLINE_FLAG = "비제스트 오프라인 판매 N"          # 비제스트 N(→ SCM 도 N): 비제스트에서 Y 로 바꿔야 함
SYNC_FLAG = "SCM 오프라인 판매 미반영"              # 비제스트 Y 인데 SCM-HUB 는 아직 N: 넘어오길 기다리거나 SCM-HUB 문의
REV_FLAG = "오프라인 판매 비제스트 N·SCM Y"        # 반대로 어긋남(참고)
SCM_FLAG = "SCM 운영중 전환 필요"
OFFLINE_FLAGS = (OFFLINE_FLAG, SYNC_FLAG, REV_FLAG)


def with_bizest_offline(df: pd.DataFrame, src) -> pd.DataFrame:
    """비제스트 원장 오프라인 판매 여부(bz_offline_yn)·변경 시각(bz_offline_ut, KST)을 UID 기준으로 붙인다."""
    df = df.drop(columns=["bz_offline_yn", "bz_offline_ut"], errors="ignore")
    g = pd.to_numeric(df["goods_no"], errors="coerce")
    gnos = g.dropna().astype("int64").unique()
    bz = src.get("bizest", gnos, "goods_no", lambda ks: _run_chunked(Q.bizest_offline, ks), int) if len(gnos) else pd.DataFrame()
    if bz.empty:
        bz = pd.DataFrame({"goods_no": pd.Series(dtype="int64"), "bz_offline": [], "bz_offline_ut": []})
    ynmap = dict(zip(bz["goods_no"].astype("int64"), bz["bz_offline"].map(offline_yn_from)))
    utmap = dict(zip(bz["goods_no"].astype("int64"),
                     pd.to_datetime(bz["bz_offline_ut"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M")))
    df["bz_offline_yn"] = [ynmap.get(int(a), "") if pd.notna(a) else "" for a in g]
    df["bz_offline_ut"] = [utmap.get(int(a), "") if pd.notna(a) else "" for a in g]
    return df


def offline_flags(df: pd.DataFrame) -> dict:
    scm, bz = df["offline_yn"], df["bz_offline_yn"]
    return {OFFLINE_FLAG: (scm == "N") & (bz != "Y"),
            SYNC_FLAG: (scm == "N") & (bz == "Y"),
            REV_FLAG: (scm == "Y") & (bz == "N")}


def offline_yn_from(v) -> str:
    return "Y" if str(v).lower() in ("true", "1") else ("N" if str(v).lower() in ("false", "0") else "")


def replenish(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """검증 플래그(df['flags'] 리스트) → 심각도 → 보충 필요·MFS 배분·반출. build 와 SCM 빠른 새로고침이 같이 쓴다."""
    # ── 보충 ──
    df["severity"] = df["flags"].map(lambda fs: "error" if any(SEVERITY[f] == "error" for f in fs)
                                     else "warn" if any(SEVERITY[f] == "warn" for f in fs)
                                     else "info" if fs else "ok")
    # 중복 행은 표시만 하고 계산은 첫 행만 (두 번 세면 보충이 2배가 된다)
    df["is_dup"] = df.duplicated(["storage_id", "sku_id"], keep="first") & df["storage_id"].notna()
    usable = (df["severity"] != "error") & ~df["is_dup"]
    df["target_qty"] = target_qty(df).where(usable)
    have = df["avail_qty"] + df["incoming_qty"]
    df["need_qty"] = (df["target_qty"] - have).clip(lower=0).fillna(0).astype("int64")
    df["over_qty"] = (have - df["target_qty"]).clip(lower=0).fillna(0).astype("int64")
    df["return_candidate"] = (df["over_qty"] > 0) & (df["off_4w"] <= 0)
    # 과재고 반출 가능 수량 = 판매가능 − 고정 수량 (입고 예정분은 아직 매장에 없어 반출 불가)
    df["ret_qty"] = (df["avail_qty"] - df["target_qty"]).clip(lower=0).fillna(0).astype("int64")
    # 시트에 있는 매장×SKU 는 '운영' 이어야 하므로(운영상태 업로드로 전환) SCM 상태와 무관하게 배분한다.
    # 단, 이동 등록 전에 운영상태 파일을 먼저 올려야 한다 → need_ops 로 표시.
    # 보충 발주 가능 = ① 비제스트 오프라인 판매 Y + ② SCM 매장 운영중(시트에 있으면 운영상태 업로드로 전환).
    # ① 이 N 이면 발주가 막히므로 MFS 재고를 배분하지 않는다(발주 가능한 매장·상품에 먼저 돌아가게).
    #    필요하면 OFFLINE_GATE=0 으로 끌 수 있다.
    gate = os.environ.get("OFFLINE_GATE", "1") == "1"
    movable = usable & ((df["offline_yn"] == "Y") if gate else True)
    df["need_ops"] = usable & (df["storage_status"] != "OPERATION_ENABLED")
    df["order_ok"] = usable & (df["offline_yn"] == "Y") & ~df["need_ops"]

    df["alloc_qty"] = 0
    # 배분 우선순위: 7일 매장 판매 → 4주 → 누적 → 보충 필요 (판매 좋은 매장 먼저, 2026-09-30 사용자 요청: 7일 먼저)
    order = df[movable & (df["need_qty"] > 0)].sort_values(["off_w1", "off_4w", "off_cum", "need_qty"], ascending=False)
    left = df.drop_duplicates("sku_id").set_index("sku_id")["mfs_qty"].to_dict()
    for i, r in order.iterrows():
        a = int(min(r["need_qty"], max(left.get(r["sku_id"], 0), 0)))
        df.at[i, "alloc_qty"] = a
        left[r["sku_id"]] = left.get(r["sku_id"], 0) - a
    df["short_qty"] = np.where(movable, df["need_qty"] - df["alloc_qty"], df["need_qty"])
    df["flags_text"] = df["flags"].map(" / ".join)

    summary = {
        "rows": len(df),
        "by_severity": df["severity"].value_counts().to_dict(),
        "flags": pd.Series([f for fs in df["flags"] for f in fs]).value_counts().to_dict(),
        "need_qty": int(df["need_qty"].sum()), "alloc_qty": int(df["alloc_qty"].sum()),
        "short_qty": int(df["short_qty"].sum()), "return_candidates": int(df["return_candidate"].sum()),
        "offline_n_need_qty": int(df.loc[usable & (df["offline_yn"] != "Y"), "need_qty"].sum()),
    }
    return df, summary


def ops_changes(df: pd.DataFrame, ok_brands: set[str], brand_pairs, src) -> pd.DataFrame:
    """SCM-HUB 스토어 운영상태 업로드 대상.

    규칙(사용자 정의 2026-09-30): 브랜드 시트에 있는 매장×SKU = 운영중, 없는 조합 = 미운영. 업로드 값은 '운영중'/'미운영' 둘뿐.
      - '운영중' 으로: 시트에 있는데(오류·중복 아님) SCM 상태가 OPERATION_ENABLED 가 아닌 것
      - '미운영' 으로: SCM 에서 OPERATION_ENABLED 인데 시트에 없는 것
    ⚠️ 시트를 정상으로 읽은 브랜드(ok_brands)만 대상 — 읽기 실패한 브랜드를 통째로 미운영 처리하면 사고다.
    """
    scope = set(STORE_SCOPE)
    sheet = df[(df["severity"] != "error") & ~df["is_dup"] & df["storage_id"].isin(scope)
               & df["brand_nm"].isin(ok_brands)]
    on = sheet[sheet["storage_status"] != "OPERATION_ENABLED"].assign(
        target_status="운영중", reason=lambda x: x["storage_status"].fillna("등록 없음").map(lambda v: f"시트에 있음 · SCM {v}"))
    en = src.whole("enabled", lambda: dbx.run_df(Q.enabled_store_skus(scope, brand_pairs)))
    en = en[en["brand_nm"].isin(ok_brands)]
    keys = set(zip(sheet["storage_id"].astype("int64"), sheet["sku_id"]))
    off = en[[(int(a), b) not in keys for a, b in zip(en["storage_id"], en["sku_id"])]].assign(
        target_status="미운영", reason="시트에 없음 · SCM 운영중", storage_status="OPERATION_ENABLED")
    stores = src.whole("stores", lambda: dbx.run_df(Q.STORES)).set_index("storage_id")
    cols = ["storage_id", "fk_sku_id", "sku_id", "target_status", "reason", "brand_nm", "goods_no", "product_name",
            "option_name", "storage_status", "stock_qty"]
    out = pd.concat([on[cols], off[cols]], ignore_index=True)
    out["storage_id"] = out["storage_id"].astype("int64")
    out["storage_no"] = out["storage_id"].map(stores["storage_no"])
    out["store_name"] = out["storage_id"].map(stores["scm_name"])
    return out.sort_values(["target_status", "brand_nm", "store_name", "sku_id"]).reset_index(drop=True)


def case2_rows(ops: pd.DataFrame, df: pd.DataFrame, src) -> pd.DataFrame:
    """취합 검증 CASE2 = 브랜드 운영리스트에 없는데 SCM-HUB 에서 운영중인 매장×SKU.
    CASE1(시트 행)과 같은 재고·판매 지표를 붙여서 한 표에 섞어 보여준다."""
    c2 = ops[ops["target_status"] == "미운영"].copy()
    if c2.empty:
        return pd.DataFrame()
    stores = src.whole("stores", lambda: dbx.run_df(Q.STORES)).set_index("storage_id")
    sids = _all_sids(src)
    c2["storage_id"] = c2["storage_id"].astype("int64")
    c2["fk_sku_id"] = c2["fk_sku_id"].astype("int64")
    c2["shop_no"] = c2["storage_id"].map(stores["shop_no"]).astype("Int64")
    fk = c2["fk_sku_id"].unique()
    st = src.get("stock", fk, "fk_sku_id", lambda ks: _run_chunked(Q.store_stock, ks, sids), int)
    if len(st) and "storage_id" in st:
        st = st.drop(columns=["stock_qty"], errors="ignore").drop_duplicates(["fk_sku_id", "storage_id"])
        c2 = c2.merge(st.astype({"fk_sku_id": "int64", "storage_id": "int64"}), on=["fk_sku_id", "storage_id"], how="left")
    sales = _sales(src, c2[["sku_id", "fk_sku_id"]].drop_duplicates("sku_id"))
    if len(sales):
        c2 = c2.merge(sales.astype({"shop_no": "Int64"}), on=["sku_id", "shop_no"], how="left")
    mfs = src.get("mfs", c2["sku_id"].unique(), "sku_id", lambda ks: _run_chunked(Q.mfs_stock, ks))
    if len(mfs):
        c2 = c2.merge(mfs.drop_duplicates("sku_id"), on="sku_id", how="left")
    inb = src.get("inb", c2["sku_id"].unique(), "sku_id", lambda ks: _run_chunked(Q.mfs_inbound, ks))
    if len(inb):
        c2 = c2.merge(inb.drop_duplicates("sku_id"), on="sku_id", how="left")
    sm = src.get("sku", c2["sku_id"].unique(), "sku_id", fetch_sku)
    if len(sm):
        c2 = c2.merge(sm.drop_duplicates("sku_id")[["sku_id", "rep_barcode", "other_barcodes", "n_barcode"]], on="sku_id", how="left")
    for c in ("stock_qty", "avail_qty", "incoming_qty", "outgoing_qty", "off_w1", "off_cum", "mfs_qty", "mfs_inbound_qty", "mfs_in_qty", "mfs_in_late_qty"):
        if c not in c2:          # 해당 원천 결과가 0건이면 열 자체가 없다 (예: 입고 예정 없음)
            c2[c] = 0
        c2[c] = pd.to_numeric(c2[c], errors="coerce").fillna(0).astype("int64")
    # 브랜드 표기는 시트 파일 기준(src_file)으로 맞춘다
    b2f = df.dropna(subset=["brand_nm"]).groupby("brand_nm")["src_file"].agg(lambda s: s.mode().iat[0])
    c2["src_file"] = c2["brand_nm"].map(b2f).fillna(c2["brand_nm"])
    c2["brand_in"] = c2["src_file"]
    c2["storage_no"] = c2["storage_id"].map(stores["storage_no"])
    c2["store_name"] = c2["storage_id"].map(stores["scm_name"])
    c2["severity"] = "warn"
    c2["flags_text"] = "시트에 없음 · SCM 운영중"
    c2["src_row"] = ""
    return c2.drop(columns=["target_status", "reason"])


def stock_barcodes(df: pd.DataFrame, src) -> pd.DataFrame:
    """반출 후보 행의 매장 바코드별 판매가능 재고 (반출 줄을 바코드별로 나누는 데 쓴다)."""
    c = df[(df["ret_qty"] > 0) & df["fk_sku_id"].notna() & df["storage_id"].notna()]
    if c.empty:
        return pd.DataFrame(columns=["fk_sku_id", "storage_id", "barcode", "avail_qty"])
    sids = _all_sids(src)
    return src.get("stock_bc", c["fk_sku_id"].astype("int64").unique(), "fk_sku_id",
                   lambda ks: _run_chunked(Q.store_stock_barcode, ks, sids), int)



# ── RT(점간이동) 추천 (2026-10-06 사용자 요청) ─────────────────────────────────
# 받는 쪽 = 브랜드 운영리스트에 있는(운영중이어야 할) 매장×SKU 중 **MFS 배분 뒤에도 남는 부족분**(short_qty).
#   사용자 결정: MFS 먼저 → ② 재고 보충(MFS 이동) 수량은 그대로 두고, RT 는 남는 부족분만.
#   오프라인 판매 N 이면 발주가 막히므로 제외(MFS 배분과 같은 조건).
#   받는 매장 순서 = 매장 판매 7일 → 4주 → 누적 → 부족분 (MFS 배분과 같은 기준)
# 보내는 쪽 1순위 = 미운영 매장 재고: 그 SKU 가 운영리스트에 없는 대상 매장(STORE_SCOPE)의 판매가능 재고
#                  (재고가 많은 매장부터)
# 보내는 쪽 2순위 = 과재고: 운영리스트 행의 과재고 반출 가능 수량(ret_qty, 판매가능 − 고정)
#                  (4주 무판매 먼저 → 과잉 많은 순). RT 로 쓴 만큼 ② 파일의 과재고 반출 수량에서 뺀다.
# 바코드 = 보내는 매장에 그 바코드 재고가 있는 것부터(반출과 같은 방식)으로 줄을 나눈다.
RT_COLS = ["priority", "from_storage_id", "to_storage_id", "sku_id", "fk_sku_id", "barcode", "rt_qty"]


def rt_plan(df: pd.DataFrame, src) -> tuple[pd.DataFrame, pd.DataFrame]:
    """반환: (RT 추천 줄, df[rt_in_qty·rt_out_qty 반영, ret_qty 차감])."""
    df = df.copy()
    df["rt_in_qty"] = 0
    df["rt_out_qty"] = 0
    usable = (df["severity"] != "error") & ~df["is_dup"] & df["storage_id"].notna() & df["fk_sku_id"].notna()
    scope = set(STORE_SCOPE)
    sheet = df[usable & df["storage_id"].isin(scope)]
    if sheet.empty:
        return pd.DataFrame(columns=RT_COLS), df
    sheet_keys = set(zip(sheet["fk_sku_id"].astype("int64"), sheet["storage_id"].astype("int64")))
    fks = sheet["fk_sku_id"].astype("int64").unique()

    # 받는 쪽
    dest = sheet[(sheet["offline_yn"] == "Y") & (sheet["short_qty"] > 0)]
    # 보내는 쪽 1: 미운영 매장 재고
    sids = _all_sids(src)
    st = src.get("stock", fks, "fk_sku_id", lambda ks: _run_chunked(Q.store_stock, ks, sids), int)
    s1 = pd.DataFrame(columns=["fk_sku_id", "storage_id", "avail_qty"])
    if len(st) and "storage_id" in st:
        st = st.drop_duplicates(["fk_sku_id", "storage_id"])
        st = st[st["storage_id"].astype("int64").isin(scope) & (pd.to_numeric(st["avail_qty"], errors="coerce").fillna(0) > 0)]
        s1 = st[[(int(a), int(b)) not in sheet_keys for a, b in zip(st["fk_sku_id"], st["storage_id"])]]
    # 보내는 쪽 2: 과재고
    s2 = sheet[sheet["ret_qty"] > 0]

    if dest.empty or (s1.empty and s2.empty):
        return pd.DataFrame(columns=RT_COLS), df

    sup: dict[int, list] = {}   # fk → [[priority, storage_id, 남은 수량, df index(2순위만), 정렬키]]
    for r in s1.itertuples():
        sup.setdefault(int(r.fk_sku_id), []).append([1, int(r.storage_id), int(r.avail_qty), None, -int(r.avail_qty)])
    for i, r in s2.iterrows():
        nos = 0 if str(r["return_candidate"]) == "True" else 1
        sup.setdefault(int(r["fk_sku_id"]), []).append([2, int(r["storage_id"]), int(r["ret_qty"]), i, nos * 10**9 - int(r["ret_qty"])])
    for v in sup.values():
        v.sort(key=lambda x: (x[0], x[4]))

    moves = []   # (priority, from_sid, to_sid, fk, qty, src_index)
    order = dest.sort_values(["off_w1", "off_4w", "off_cum", "short_qty"], ascending=False)
    for i, r in order.iterrows():
        fk, to, want = int(r["fk_sku_id"]), int(r["storage_id"]), int(r["short_qty"])
        for sp in sup.get(fk, []):
            if want <= 0:
                break
            if sp[2] <= 0 or sp[1] == to:
                continue
            q = min(want, sp[2])
            sp[2] -= q
            want -= q
            moves.append((sp[0], sp[1], to, fk, q, sp[3]))
            df.at[i, "rt_in_qty"] += q
            if sp[3] is not None:
                df.at[sp[3], "rt_out_qty"] += q
    if not moves:
        return pd.DataFrame(columns=RT_COLS), df
    df["ret_qty"] = (df["ret_qty"] - df["rt_out_qty"]).clip(lower=0)
    df["short_qty"] = (df["short_qty"] - df["rt_in_qty"]).clip(lower=0)

    # 바코드별로 나누기 (보내는 매장에 재고가 있는 바코드부터)
    bc = src.get("stock_bc", sorted({m[3] for m in moves}), "fk_sku_id",
                 lambda ks: _run_chunked(Q.store_stock_barcode, ks, sids), int)
    by: dict = {}
    for b in bc.itertuples():
        by.setdefault((int(b.fk_sku_id), int(b.storage_id)), []).append([b.barcode, int(b.avail_qty)])
    for v in by.values():
        v.sort(key=lambda x: -x[1])
    rep = dict(zip(df["fk_sku_id"].dropna().astype("int64"), df.loc[df["fk_sku_id"].notna(), "rep_barcode"]))
    sku = dict(zip(df["fk_sku_id"].dropna().astype("int64"), df.loc[df["fk_sku_id"].notna(), "sku_id"]))
    lines = []
    for pri, fr, to, fk, q, _ in moves:
        left = q
        for cand in by.get((fk, fr), []):
            if left <= 0:
                break
            take = min(left, cand[1])
            if take <= 0:
                continue
            cand[1] -= take
            left -= take
            lines.append((pri, fr, to, sku.get(fk), fk, cand[0], take))
        if left > 0:   # 바코드별 재고를 못 찾으면 대표 바코드로
            lines.append((pri, fr, to, sku.get(fk), fk, rep.get(fk), left))
    return pd.DataFrame(lines, columns=RT_COLS), df


def rt_enrich(rt: pd.DataFrame, df: pd.DataFrame, src) -> pd.DataFrame:
    """RT 줄에 매장명·상품·양쪽 매장 재고/판매·MFS 재고를 붙인다(화면·CSV 용)."""
    if rt.empty:
        return rt
    stores = src.whole("stores", lambda: dbx.run_df(Q.STORES)).drop_duplicates("storage_id").set_index("storage_id")
    rt = rt.copy()
    for side in ("from", "to"):
        sid = rt[f"{side}_storage_id"].astype("int64")
        rt[f"{side}_storage_no"] = sid.map(stores["storage_no"])
        rt[f"{side}_store"] = sid.map(stores["scm_name"])
        rt[f"{side}_shop_no"] = sid.map(stores["shop_no"])
    prod = df.dropna(subset=["sku_id"]).drop_duplicates("sku_id").set_index("sku_id")
    for c in ("goods_no", "product_name", "option_name", "src_file", "mfs_qty", "mfs_in_qty"):
        rt[c] = rt["sku_id"].map(prod[c]) if c in prod else None
    rt["priority_name"] = rt["priority"].map({1: "1순위 미운영 매장 재고", 2: "2순위 과재고"})
    # 받는 매장(운영리스트 행) 지표
    key = df[df["storage_id"].notna() & df["fk_sku_id"].notna()].drop_duplicates(["fk_sku_id", "storage_id"])
    key = key.set_index([key["fk_sku_id"].astype("int64"), key["storage_id"].astype("int64")])
    k_to = list(zip(rt["fk_sku_id"].astype("int64"), rt["to_storage_id"].astype("int64")))
    for c, n in (("fixed_qty", "to_fixed"), ("stock_qty", "to_stock"), ("avail_qty", "to_avail"), ("incoming_qty", "to_incoming"),
                 ("off_w1", "to_off_w1"), ("off_4w", "to_off_4w"), ("off_cum", "to_off_cum"), ("need_qty", "to_need"),
                 ("alloc_qty", "to_mfs_alloc")):
        rt[n] = [key[c].get(k, 0) if c in key else 0 for k in k_to]
    rt["to_need_ops"] = [str(key["need_ops"].get(k, False)) == "True" if "need_ops" in key else False for k in k_to]
    # 보내는 매장 재고: 매장 재고 원천(미운영 매장 포함)
    sids = _all_sids(src)
    st = src.get("stock", rt["fk_sku_id"].astype("int64").unique(), "fk_sku_id",
                 lambda ks: _run_chunked(Q.store_stock, ks, sids), int)
    stk = {}
    if len(st) and "storage_id" in st:
        st = st.drop_duplicates(["fk_sku_id", "storage_id"])
        stk = {(int(a), int(b)): (int(c or 0), int(d or 0)) for a, b, c, d in
               zip(st["fk_sku_id"], st["storage_id"], st["stock_qty"], st["avail_qty"])}
    k_fr = list(zip(rt["fk_sku_id"].astype("int64"), rt["from_storage_id"].astype("int64")))
    rt["from_stock"] = [stk.get(k, (0, 0))[0] for k in k_fr]
    rt["from_avail"] = [stk.get(k, (0, 0))[1] for k in k_fr]
    rt["from_fixed"] = [key["fixed_qty"].get(k, 0) if "fixed_qty" in key else 0 for k in k_fr]
    # 보내는 매장 판매(7일·누적): 판매 원천(sku × shop)
    sales = _sales(src, rt[["sku_id", "fk_sku_id"]].dropna().drop_duplicates("sku_id"))
    sl = {}
    if len(sales) and "shop_no" in sales:
        sl = {(a, int(b)): (int(c or 0), int(d or 0)) for a, b, c, d in
              zip(sales["sku_id"], pd.to_numeric(sales["shop_no"], errors="coerce").fillna(-1), sales["off_w1"], sales["off_cum"])}
    k_sl = [(a, int(b) if pd.notna(b) else -1) for a, b in zip(rt["sku_id"], rt["from_shop_no"])]
    rt["from_off_w1"] = [sl.get(k, (0, 0))[0] for k in k_sl]
    rt["from_off_cum"] = [sl.get(k, (0, 0))[1] for k in k_sl]
    rt["note"] = ["받는 매장 운영상태(운영중) 먼저 업로드" if x else "" for x in rt["to_need_ops"]]
    return rt.sort_values(["src_file", "sku_id", "priority", "to_off_w1", "to_off_4w", "to_off_cum"],
                          ascending=[True, True, True, False, False, False]).reset_index(drop=True)

OUT_COLS = ["src_file", "src_row", "brand_in", "store_name", "sku_id", "goods_no", "product_name", "option_name",
            "rep_barcode", "other_barcodes", "n_barcode", "consignment_type", "storage_status",
            "fixed_qty", "stock_qty", "avail_qty", "incoming_qty", "outgoing_qty",
            "off_w1", "off_4w", "off_all_4w", "onl_w1", "mfs_qty", "mfs_inbound_qty",
            "need_qty", "alloc_qty", "short_qty", "over_qty", "return_candidate", "severity", "flags_text"]

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="brand_in,store_in,sku_id_in,fixed_qty_in[,src_file,src_row]")
    ap.add_argument("--out", default="out.csv")
    a = ap.parse_args()
    rows = pd.read_csv(a.csv, dtype=str)
    for c in ("src_file", "src_row"):
        if c not in rows:
            rows[c] = ""
    import source
    out, summ = build(rows, source.Source(os.environ.get("CACHE_DIR", "cache"), source.SCM_TABLES | source.BIZEST_TABLES))
    out[[c for c in OUT_COLS if c in out]].to_csv(a.out, index=False, encoding="utf-8-sig")
    print(summ)

