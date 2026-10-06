"""새로고침: 브랜드 시트 읽기 → 검증·원천 결합(engine) → 스냅샷 저장.

스냅샷은 CACHE_DIR(배포는 GCS 볼륨 /mnt/cache, 로컬은 ./cache)에 저장하고 API 는 스냅샷만 읽는다.
  rows.parquet  — 결과 행 전체 (engine.build 출력)
  meta.json     — 갱신 시각, 요약, 브랜드 파일별 읽기 상태
실행:  python refresh.py [full|scm|bizest|sheets]   (매일 스케줄 / 화면의 새로고침 버튼 4개)
"""
from __future__ import annotations

import os
import json
import time
import datetime as dt

import pandas as pd

import dbx
import engine
import progress
import queries as Q
import sheets
import source

CACHE_DIR = os.environ.get("CACHE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache"))
KST = dt.timezone(dt.timedelta(hours=9))


def _now() -> str:
    return dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


KINDS = {  # 버튼 → 다시 읽는 영역 (source.py 참고)
    "full":   {"sheets": True,  "tables": source.SCM_TABLES | source.BIZEST_TABLES},
    "scm":    {"sheets": False, "tables": source.SCM_TABLES},
    "bizest": {"sheets": False, "tables": source.BIZEST_TABLES},
    "sheets": {"sheets": True,  "tables": set()},
}
STAMP = {"sheets": "sheets_at", "scm": "scm_at", "bizest": "bizest_at"}


def _prev_meta() -> dict:
    try:
        with open(os.path.join(CACHE_DIR, "meta.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def run(kind: str = "full") -> dict:
    """kind = full / scm / bizest / sheets. 다시 읽지 않는 영역은 저장값을 쓰고 새 키만 추가 조회한다."""
    if kind not in KINDS:
        raise ValueError(f"알 수 없는 새로고침 종류: {kind}")
    t0 = time.time()
    progress.reset()
    os.makedirs(CACHE_DIR, exist_ok=True)
    prev = _prev_meta()
    src = source.Source(CACHE_DIR, KINDS[kind]["tables"])
    tb = src.whole("tb", lambda: dbx.run_df(Q.target_brands(engine.MD_IDS, engine.EXTRA_BRANDS)))
    rows_p = os.path.join(CACHE_DIR, "sheet_rows.pkl")
    read_sheets = KINDS[kind]["sheets"] or not os.path.exists(rows_p)
    if read_sheets:
        target_names = set(tb["brand_nm"].dropna())
        rows, files = sheets.read_all(target_names)
        if rows.empty:
            raise RuntimeError("읽은 운영 상품 행이 없습니다 — 브랜드 폴더 공유/권한을 확인하세요.")
        for c in ("brand_in", "store_in", "sku_id_in", "fixed_qty_in", "src_file", "src_row"):
            if c not in rows:
                rows[c] = ""
        # 대상 브랜드 중 파일이 없는 브랜드도 현황에 보이도록
        have = {f["brand"] for f in files}
        test_mode = bool(os.environ.get("BRAND_ROWS_CSV"))
        for r in tb.drop_duplicates("brand_nm").itertuples():
            if r.brand_nm and r.brand_nm not in have:
                files.append({"brand": r.brand_nm, "file_id": "",
                              "status": "시트 미연결" if test_mode else "파일 없음",
                              "detail": "테스트 모드 — 브랜드 시트를 읽지 않음(서비스 계정 공유 필요)" if test_mode
                              else "브랜드 공유 시트가 폴더에 없음", "rows": 0})
        rows.to_pickle(rows_p)
    else:
        rows = pd.read_pickle(rows_p)
        files = prev.get("files", [])
    md_by_brand = tb.dropna(subset=["brand_nm"]).groupby("brand_nm").agg(
        offline_md=("offline_md_id", lambda s: ", ".join(sorted({x for x in s if isinstance(x, str) and x})) or "미지정"),
        target_reason=("target_reason", "first")).to_dict("index")
    for f in files:
        f.update(md_by_brand.get(f["brand"], {}))

    out, summary = engine.build(rows, src)
    stamps = {v: prev.get(v, prev.get("refreshed_at", "")) for v in STAMP.values()}
    now = _now()
    for k, v in STAMP.items():
        if kind == "full" or kind == k or (k == "sheets" and read_sheets):
            stamps[v] = now
    meta = _finish(out, summary, files, tb, t0, src, stamps, kind)
    src.save()
    return meta


def _finish(out: pd.DataFrame, summary: dict, files: list, tb: pd.DataFrame, t0: float, src, stamps: dict,
            kind: str) -> dict:
    """업로드 파일(ops/case2/stock_bc) 재계산 + 스냅샷·meta 저장. 전체 새로고침과 SCM 빠른 새로고침이 같이 쓴다."""
    # 업로드 파일용: 운영상태 변경 대상 + 반출 바코드별 재고 (시트를 정상으로 읽은 브랜드만)
    progress.step("업로드 파일·스냅샷 저장")
    ok_files = {f["brand"] for f in files if f.get("status") == "ok"}
    ok_brands = set(out.loc[out["src_file"].isin(ok_files), "brand_nm"].dropna())
    pairs = list(zip(tb["com_id"], tb["brand"]))
    ops = engine.ops_changes(out, ok_brands, pairs, src)
    bc = engine.stock_barcodes(out, src)
    c2 = engine.case2_rows(ops, out, src)
    for c in c2.columns:
        if c2[c].dtype == object:
            c2[c] = c2[c].map(lambda v: "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
    c2.to_parquet(os.path.join(CACHE_DIR, "case2.parquet"), index=False)
    ops.to_parquet(os.path.join(CACHE_DIR, "ops.parquet"), index=False)
    bc.to_parquet(os.path.join(CACHE_DIR, "stock_bc.parquet"), index=False)
    summary["ops_to_on"] = int((ops["target_status"] == "운영중").sum())
    summary["ops_to_off"] = int((ops["target_status"] == "미운영").sum())

    out = out.drop(columns=["flags"]).copy()
    for c in out.columns:  # parquet 저장용: object 열은 문자열로 통일
        if out[c].dtype == object:
            out[c] = out[c].map(lambda v: "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
    tmp = os.path.join(CACHE_DIR, "rows.parquet.tmp")
    out.to_parquet(tmp, index=False)
    os.replace(tmp, os.path.join(CACHE_DIR, "rows.parquet"))

    stock_date = out.get("stock_date")
    now = _now()
    timings = progress.done()
    meta = {
        "refreshed_at": now, "kind": kind, **stamps,
        "scm_refreshed_at": stamps.get("scm_at", now), "timings": timings,
        "elapsed_sec": round(time.time() - t0, 1),
        "stock_date": str(stock_date[stock_date != ""].max()) if stock_date is not None and (stock_date != "").any() else "",
        "summary": summary,
        "files": files,
    }
    with open(os.path.join(CACHE_DIR, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1, default=str)
    return meta


def run_scm() -> dict:
    """(옛 이름) SCM-HUB 새로고침."""
    return run("scm")


if __name__ == "__main__":
    import sys
    m = run(sys.argv[1] if len(sys.argv) > 1 else "full")
    print({k: m[k] for k in ("refreshed_at", "elapsed_sec", "stock_date", "summary")})
