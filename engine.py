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
    "글로벌 SKU(GLOBAL_3P)": "warn", "바코드 없음": "warn", "바코드 2개 이상": "info",
}


def target_qty(df: pd.DataFrame) -> pd.Series:
    """적정 재고 목표. 1단계 = 브랜드가 적은 매장 고정 수량."""
    return df["fixed_qty"]


def _chunks(seq, n=10000):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _run_chunked(builder, keys, *extra) -> pd.DataFrame:
    frames = [dbx.run_df(builder(part, *extra)) for part in _chunks(keys)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def build(rows: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """rows: brand_in, store_in, sku_id_in, fixed_qty_in, src_file, src_row"""
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
    stores = dbx.run_df(Q.STORES)
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
    sm = _run_chunked(Q.sku_master, ok_ids)
    df = df.merge(sm, on="sku_id", how="left")
    flag(df["sku_id"].str.match(SKU_RE) & df["fk_sku_id"].isna(), "SKU ID 원천에 없음")
    flag(df["purchase_type"].notna() & (df["purchase_type"] != "CONSIGNMENT"), "위탁 SKU 아님")
    flag(df["consignment_type"] == "GLOBAL_3P", "글로벌 SKU(GLOBAL_3P)")
    flag(df["brand_nm"].notna() & (df["brand_nm"] != df["brand_in"]), "브랜드 불일치")
    flag(df["fk_sku_id"].notna() & (df["n_barcode"] == 0), "바코드 없음")
    flag(df["n_barcode"] > 1, "바코드 2개 이상")

    # 보충 발주 조건 ①: 비제스트 상품(UID) 오프라인 판매 여부 = Y
    #   = SKU 가 연결된 SCM-HUB product 행의 offline_sale_enabled (True → Y)
    en = df["offline_sale_enabled"].map(offline_yn_from)
    df["offline_yn"] = en.where(df["fk_sku_id"].notna(), "")
    flag((df["offline_yn"] == "N"), "비제스트 오프라인 판매 N")

    tb = dbx.run_df(Q.target_brands(MD_IDS, EXTRA_BRANDS))
    tb_keys = set(zip(tb["com_id"], tb["brand"]))
    df["offline_md"] = [dict(zip(zip(tb["com_id"], tb["brand"]), tb["offline_md_id"])).get((c, b))
                        for c, b in zip(df["com_id"], df["brand"])]
    flag(df["com_id"].notna() & ~pd.Series([(c, b) in tb_keys for c, b in zip(df["com_id"], df["brand"])],
                                           index=df.index), "담당 MD 브랜드 아님")

    # ── 원천 수치 ──
    fk = df["fk_sku_id"].dropna().astype("int64").unique()
    sids = df["storage_id"].dropna().astype("int64").unique()
    if len(fk) and len(sids):
        st = _run_chunked(Q.store_stock, fk, sids)
        ss = _run_chunked(Q.storage_sku, fk, sids)
        sales = _run_chunked(Q.offline_sales, fk)
    else:
        st = ss = sales = pd.DataFrame()
    mfs = _run_chunked(Q.mfs_stock, ok_ids)
    inb = _run_chunked(Q.mfs_inbound, ok_ids)
    onl = _run_chunked(Q.online_sales, df["option_code"].dropna().unique())

    df["fk_sku_id"] = df["fk_sku_id"].astype("Int64")
    df["storage_id"] = df["storage_id"].astype("Int64")
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
    df = df.merge(mfs, on="sku_id", how="left")
    if len(inb):
        df = df.merge(inb, on="sku_id", how="left")
    if len(onl):
        df = df.merge(onl, on="option_code", how="left")

    num = ["stock_qty", "avail_qty", "incoming_qty", "outgoing_qty", "defect_qty", "off_w1", "off_w2",
           "off_w3", "off_w4", "off_today", "off_cum", "off_all_w1", "off_all_4w", "off_all_cum", "mfs_qty", "mfs_inbound_qty", "mfs_in_qty", "mfs_in_late_qty",
           "onl_w1", "onl_4w"]
    for c in num:
        if c not in df:
            df[c] = 0
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")
    df["off_4w"] = df[["off_w1", "off_w2", "off_w3", "off_w4"]].sum(axis=1)

    return replenish(df)


OFFLINE_FLAG = "비제스트 오프라인 판매 N"
SCM_FLAG = "SCM 운영중 전환 필요"


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


def ops_changes(df: pd.DataFrame, ok_brands: set[str], brand_pairs) -> pd.DataFrame:
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
    en = dbx.run_df(Q.enabled_store_skus(scope, brand_pairs))
    en = en[en["brand_nm"].isin(ok_brands)]
    keys = set(zip(sheet["storage_id"].astype("int64"), sheet["sku_id"]))
    off = en[[(int(a), b) not in keys for a, b in zip(en["storage_id"], en["sku_id"])]].assign(
        target_status="미운영", reason="시트에 없음 · SCM 운영중", storage_status="OPERATION_ENABLED")
    stores = dbx.run_df(Q.STORES).set_index("storage_id")
    cols = ["storage_id", "fk_sku_id", "sku_id", "target_status", "reason", "brand_nm", "goods_no", "product_name",
            "option_name", "storage_status", "stock_qty"]
    out = pd.concat([on[cols], off[cols]], ignore_index=True)
    out["storage_id"] = out["storage_id"].astype("int64")
    out["storage_no"] = out["storage_id"].map(stores["storage_no"])
    out["store_name"] = out["storage_id"].map(stores["scm_name"])
    return out.sort_values(["target_status", "brand_nm", "store_name", "sku_id"]).reset_index(drop=True)


def case2_rows(ops: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """취합 검증 CASE2 = 브랜드 운영리스트에 없는데 SCM-HUB 에서 운영중인 매장×SKU.
    CASE1(시트 행)과 같은 재고·판매 지표를 붙여서 한 표에 섞어 보여준다."""
    c2 = ops[ops["target_status"] == "미운영"].copy()
    if c2.empty:
        return pd.DataFrame()
    stores = dbx.run_df(Q.STORES).set_index("storage_id")
    c2["storage_id"] = c2["storage_id"].astype("int64")
    c2["fk_sku_id"] = c2["fk_sku_id"].astype("int64")
    c2["shop_no"] = c2["storage_id"].map(stores["shop_no"]).astype("Int64")
    fk, sids = c2["fk_sku_id"].unique(), c2["storage_id"].unique()
    st = _run_chunked(Q.store_stock, fk, sids).drop(columns=["stock_qty"], errors="ignore")
    c2 = c2.merge(st.astype({"fk_sku_id": "int64", "storage_id": "int64"}), on=["fk_sku_id", "storage_id"], how="left")
    sales = _run_chunked(Q.offline_sales, fk)
    if len(sales):
        c2 = c2.merge(sales.astype({"shop_no": "Int64"}), on=["sku_id", "shop_no"], how="left")
    c2 = c2.merge(_run_chunked(Q.mfs_stock, c2["sku_id"].unique()), on="sku_id", how="left")
    inb = _run_chunked(Q.mfs_inbound, c2["sku_id"].unique())
    if len(inb):
        c2 = c2.merge(inb, on="sku_id", how="left")
    sm = _run_chunked(Q.sku_master, c2["sku_id"].unique())[["sku_id", "rep_barcode", "other_barcodes", "n_barcode"]]
    c2 = c2.merge(sm, on="sku_id", how="left")
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


def stock_barcodes(df: pd.DataFrame) -> pd.DataFrame:
    """반출 후보 행의 매장 바코드별 판매가능 재고 (반출 줄을 바코드별로 나누는 데 쓴다)."""
    c = df[(df["ret_qty"] > 0) & df["fk_sku_id"].notna() & df["storage_id"].notna()]
    if c.empty:
        return pd.DataFrame(columns=["fk_sku_id", "storage_id", "barcode", "avail_qty"])
    return _run_chunked(Q.store_stock_barcode, c["fk_sku_id"].astype("int64").unique(),
                        c["storage_id"].astype("int64").unique())


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
    out, summ = build(rows)
    out[[c for c in OUT_COLS if c in out]].to_csv(a.out, index=False, encoding="utf-8-sig")
    print(summ)


def scm_quick(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """SCM 상태만 빠르게 다시 반영 — 매장×SKU 운영상태(storage_sku) + 상품 오프라인 판매 여부.
    재고·판매·MFS 는 직전 전체 새로고침 값을 그대로 쓰고, 두 상태와 그에 따른 플래그·배분만 다시 계산한다.
    (원천은 Databricks 사본이라 SCM-HUB 대비 30분~1시간 지연은 그대로)"""
    df = df.copy()
    df["flags"] = df["flags_text"].fillna("").map(lambda t: [f for f in t.split(" / ") if f and f not in (SCM_FLAG, OFFLINE_FLAG)])
    fk = pd.to_numeric(df["fk_sku_id"], errors="coerce")
    sid = pd.to_numeric(df["storage_id"], errors="coerce")
    fks, sids = fk.dropna().astype("int64").unique(), sid.dropna().astype("int64").unique()
    if len(fks) and len(sids):
        ss = _run_chunked(Q.storage_sku, fks, sids)[["fk_sku_id", "storage_id", "storage_status"]]
        key = dict(zip(zip(ss["fk_sku_id"].astype("int64"), ss["storage_id"].astype("int64")), ss["storage_status"]))
        df["storage_status"] = [key.get((int(a), int(b))) if pd.notna(a) and pd.notna(b) else None for a, b in zip(fk, sid)]
        off = _run_chunked(Q.sku_offline, fks)
        offmap = dict(zip(off["fk_sku_id"].astype("int64"), off["offline_sale_enabled"].map(offline_yn_from)))
        df["offline_yn"] = [offmap.get(int(a), "") if pd.notna(a) else "" for a in fk]
    reg = fk.notna() & sid.notna()
    for i in df.index[reg & (df["storage_status"] != "OPERATION_ENABLED")]:
        df.at[i, "flags"].append(SCM_FLAG)
    for i in df.index[df["offline_yn"] == "N"]:
        df.at[i, "flags"].append(OFFLINE_FLAG)
    return replenish(df)
