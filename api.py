"""위탁 운영 — FastAPI. 스냅샷(cache/rows.parquet + meta.json)만 읽는다. 원천 조회는 refresh.py 몫.

인증: Cloud Run 앞단 IAP(사내 Google 계정)가 막고, IAP 가 넣어 주는
X-Goog-Authenticated-User-Email 헤더로 사용자를 표시한다. ALLOWED_DOMAIN 이 설정돼 있으면
그 도메인이 아닌 요청을 거부한다(IAP 우회 방지용 2차 방어). 로컬 개발은 설정하지 않으면 열려 있다.

실행(로컬):  uvicorn api:app --port 8010
"""
from __future__ import annotations

import io
import os
import json
import threading
import traceback

import progress

import pandas as pd
from fastapi import FastAPI, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles

import refresh

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(APP_DIR, "web", "dist")
ALLOWED_DOMAIN = os.environ.get("ALLOWED_DOMAIN", "")      # 예: musinsa.com
REFRESH_TOKEN = os.environ.get("REFRESH_TOKEN", "")          # Cloud Scheduler 용

app = FastAPI(title="위탁 운영 API")


# ── 인증 ────────────────────────────────────────────────────────────────
def user(x_goog_authenticated_user_email: str | None = Header(default=None)) -> str:
    email = (x_goog_authenticated_user_email or "").split(":")[-1]
    if ALLOWED_DOMAIN and not email.endswith("@" + ALLOWED_DOMAIN):
        raise HTTPException(401, "사내 계정으로 접속해 주세요")
    return email or "local"


# ── 스냅샷 캐시 (파일 mtime 이 바뀌면 다시 읽음) ────────────────────────
_cache = {"mtime": None, "df": None, "meta": None}
_lock = threading.Lock()
_lock = threading.Lock()
_job = {"running": False, "error": "", "started_at": "", "run_id": 0}
# Cloud Run 요청 제한 시간(--timeout 900초)을 넘기면 클라이언트 요청은 끊기지만 서버 스레드는 CPU 없이 남아
# running=True 가 계속 유지된다 → 버튼이 영영 비활성화(2026-10-06). 시작 후 이 시간이 지나면 멈춘 것으로 본다.
# 백그라운드 실행으로 바꾼 뒤(2026-10-06)엔 요청 제한이 없으니 넉넉히 30분.
STALE_SEC = int(os.environ.get("REFRESH_STALE_SEC", "3600"))


def _running() -> bool:
    if not _job["running"]:
        return False
    try:
        import datetime as _dt
        started = _dt.datetime.strptime(_job["started_at"], "%Y-%m-%d %H:%M:%S")
        now = _dt.datetime.strptime(refresh._now(), "%Y-%m-%d %H:%M:%S")
        if (now - started).total_seconds() > STALE_SEC:
            _job.update(running=False, error=f"이전 새로고침({_job['started_at']} 시작)이 제한 시간을 넘겨 멈춤 — 다시 실행하세요")
            return False
    except Exception:
        pass
    return True


def snap() -> tuple[pd.DataFrame, dict]:
    p = os.path.join(refresh.CACHE_DIR, "rows.parquet")
    m = os.path.join(refresh.CACHE_DIR, "meta.json")
    if not os.path.exists(p):
        raise HTTPException(503, "데이터가 아직 없습니다 — 새로고침을 실행해 주세요")
    mt = os.stat(p).st_mtime
    with _lock:
        if _cache["mtime"] != mt:
            _cache["df"] = pd.read_parquet(p)
            with open(m, encoding="utf-8") as f:
                _cache["meta"] = json.load(f)
            _cache["mtime"] = mt
        return _cache["df"], _cache["meta"]


def records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records", force_ascii=False))


def _md_files(md: str) -> set[str]:
    """선택한 오프라인 MD 가 담당하는 브랜드 파일(src_file). MD 는 브랜드 현황과 같은 브랜드 단위 값."""
    _, meta = snap()
    want = set(md.split("|"))
    return {f["brand"] for f in meta.get("files", [])
            if want & {x.strip() for x in str(f.get("offline_md") or "미지정").split(",")}}


def _filter(df, brand=None, store=None, severity=None, flag=None, q=None, md=None):
    if md:
        df = df[df["src_file"].isin(_md_files(md))]
    if brand:
        df = df[df["src_file"].isin(brand.split("|"))]
    if store:
        df = df[df["store_name"].isin(store.split("|"))]
    if severity:
        df = df[df["severity"].isin(severity.split("|"))]
    if flag:
        df = df[df["flags_text"].str.contains(flag, regex=False)]
    if q:
        s = q.strip().lower()
        hay = (df["sku_id"].astype(str) + " " + df["goods_no"].astype(str) + " " + df["product_name"].astype(str)
               + " " + df["rep_barcode"].astype(str) + " " + df["other_barcodes"].astype(str)).str.lower()
        df = df[hay.str.contains(s, regex=False)]
    return df


# ── API ─────────────────────────────────────────────────────────────────
@app.get("/api/me")
def me(u: str = Depends(user)):
    return {"email": u}


@app.get("/api/status")
def status(u: str = Depends(user)):
    try:
        _, meta = snap()
    except HTTPException:
        meta = {}
    return {"refreshed_at": meta.get("refreshed_at"), "scm_refreshed_at": meta.get("scm_refreshed_at"),
            "stock_date": meta.get("stock_date"),
            "summary": meta.get("summary"), "timings": meta.get("timings"),
            "job": {**_job, "running": _running(), "step": progress.STATE["step"] if _job["running"] else ""}}


def _run_refresh(scm_only: bool = False):
    rid = _job["run_id"] + 1
    _job.update(running=True, error="", started_at=refresh._now(), kind="scm" if scm_only else "full", run_id=rid)
    try:
        refresh.run_scm() if scm_only else refresh.run()
    except Exception as e:
        if _job["run_id"] == rid:
            _job["error"] = f"{e}"
        traceback.print_exc()
    finally:
        if _job["run_id"] == rid:   # 멈춘 것으로 처리된 옛 실행이 나중에 끝나도 새 실행 상태를 덮지 않게
            _job["running"] = False


def _start_bg(scm_only: bool) -> dict:
    """백그라운드 스레드로 시작하고 바로 응답. 화면은 /api/status 의 job.running·step 을 5초마다 본다.
    (2026-10-06: 요청 안에서 끝까지 돌리면 오래 걸릴 때 요청이 끊겨 ERROR 가 남 → 배포에 --no-cpu-throttling 을
    줘서 응답 뒤에도 CPU 가 있으니 백그라운드로 돌린다)"""
    with _lock:
        if _running():
            return {"started": False, "job": _job}
        _job.update(running=True, error="", started_at=refresh._now(), kind="scm" if scm_only else "full")   # 스레드 시작 전 표시(중복 시작 방지)
    threading.Thread(target=_run_refresh, args=(scm_only,), daemon=True).start()
    return {"started": True}


@app.post("/api/refresh")
def start_refresh(u: str = Depends(user)):
    """전체 새로고침 시작(백그라운드)."""
    return _start_bg(False)


@app.post("/api/refresh/scm")
def start_refresh_scm(u: str = Depends(user)):
    """SCM 상태만 빠른 새로고침(동기) — 매장 운영상태·오프라인 판매 여부만 다시 읽고 보충·업로드 파일 재계산.
    재고·판매·브랜드 시트는 직전 전체 새로고침 값. SCM-HUB 사본 자체의 30분~1시간 지연은 그대로."""
    return _start_bg(True)


@app.post("/api/cron/refresh")
def cron_refresh(token: str = Query("")):
    """Cloud Scheduler 전용 — 동기 실행(끝날 때까지 응답 대기)."""
    if not REFRESH_TOKEN or token != REFRESH_TOKEN:
        raise HTTPException(403, "forbidden")
    m = refresh.run()
    return {"ok": True, "refreshed_at": m["refreshed_at"], "summary": m["summary"]}


@app.get("/api/options")
def options(u: str = Depends(user)):
    df, meta = snap()
    mds = sorted({x.strip() for f in meta.get("files", []) if f.get("status") == "ok"
                  for x in str(f.get("offline_md") or "미지정").split(",") if x.strip()})
    return {"brands": sorted(x for x in df["src_file"].dropna().unique() if x),
            "stores": sorted(x for x in df["store_name"].dropna().unique() if x), "mds": mds}


@app.get("/api/brands")
def brands(u: str = Depends(user)):
    df, meta = snap()
    base = df[df["is_dup"].astype(str) != "True"]          # 재고·판매 합계는 중복 행 제외
    g, gall = base.groupby("src_file"), df.groupby("src_file")
    agg = pd.DataFrame({
        "rows": gall.size(),
        "stores": g["store_name"].apply(lambda x: x[x != ""].nunique()),
        "skus": g["sku_id"].nunique(),
        "errors": gall["severity"].apply(lambda s: int((s == "error").sum())),
        "warns": gall["severity"].apply(lambda s: int((s == "warn").sum())),
        "fixed_pcs": g["fixed_qty"].sum(min_count=1),
        "stock_qty": g["stock_qty"].sum(),
        "incoming_qty": g["incoming_qty"].sum(),
        "outgoing_qty": g["outgoing_qty"].sum(),
        "mfs_qty": base.drop_duplicates(["src_file", "sku_id"]).groupby("src_file")["mfs_qty"].sum(),
        "mfs_in_qty": base.drop_duplicates(["src_file", "sku_id"]).groupby("src_file")["mfs_in_qty"].sum(),
        "mfs_in_late_qty": base.drop_duplicates(["src_file", "sku_id"]).groupby("src_file")["mfs_in_late_qty"].sum(),
        "off_cum": g["off_cum"].sum(),
        "off_w1": g["off_w1"].sum(),
        "off_4w": g["off_4w"].sum(),
        "need_qty": g["need_qty"].sum(),
        "alloc_qty": g["alloc_qty"].sum(),
        "short_qty": g["short_qty"].sum(),
        "return_cand": g["return_candidate"].apply(lambda s: int((s.astype(str) == "True").sum())),
        "unregistered": g["flags_text"].apply(lambda s: int(s.str.contains("SCM 운영중 전환 필요").sum())),
        "multi_barcode": g["flags_text"].apply(lambda s: int(s.str.contains("바코드 2개").sum())),
        "offline_n": gall["flags_text"].apply(lambda s: int(s.str.contains("비제스트 오프라인 판매 N|SCM 오프라인 판매 미반영").sum())),
    }).reset_index().rename(columns={"src_file": "brand"})
    files = pd.DataFrame(meta.get("files", []))
    if len(files):
        files = files[files["status"] != "대상 아님"].rename(columns={"rows": "file_rows"})
        files = files.drop(columns=[c for c in ("columns",) if c in files])
        agg = files.merge(agg, on="brand", how="outer")
    agg = agg.fillna({"rows": 0}).sort_values(["rows", "brand"], ascending=[False, True])
    return {"brands": records(agg), "refreshed_at": meta.get("refreshed_at")}


@app.get("/api/issues")
def issues(brand: str | None = None, store: str | None = None, flag: str | None = None,
           severity: str | None = "error|warn", case: str | None = None, mismatch: int = 0,
           md: str | None = None, u: str = Depends(user)):
    """취합 검증 대상 매장×SKU
      CASE1 = 브랜드 운영리스트(시트)에 있는 조합 → 정상 상태 '운영중'
      CASE2 = 시트에 없는데 SCM-HUB 에서 운영중인 조합 → 정상 상태 '미운영'
    상태(실제) = SCM-HUB storage_sku.status 가 OPERATION_ENABLED 면 운영중, 아니면(등록 없음 포함) 미운영."""
    df, _ = snap()
    c1 = df.assign(case="CASE1", expected_status="운영중")
    c2 = _extra("case2.parquet")
    if len(c2):
        c2 = c2.assign(case="CASE2", expected_status="미운영", is_dup=False)
    allr = pd.concat([c1, c2], ignore_index=True) if len(c2) else c1
    allr["storage_status"] = allr["storage_status"].fillna("").astype(str)
    allr["actual_status"] = allr["storage_status"].map(lambda s: "운영중" if s == "OPERATION_ENABLED" else "미운영")
    allr["scm_code"] = allr["storage_status"].replace("", "등록 없음")
    allr["mismatch"] = allr["actual_status"] != allr["expected_status"]
    # 요약 건수도 필터를 따른다: 브랜드·매장 → CASE 건수 / + CASE → 확인 사항 카드·불일치 건수
    scope = _filter(allr, brand, store, md=md)
    case_counts = scope.groupby("case").size().to_dict()
    if case:
        scope = scope[scope["case"].isin(case.split("|"))]
    flags = pd.Series([f for s in scope["flags_text"].fillna("") for f in s.split(" / ") if f]).value_counts()
    mismatch_count = int(scope["mismatch"].sum())
    sub = _filter(scope, severity=None if severity in (None, "", "all") else severity, flag=flag)
    if mismatch:
        sub = sub[sub["mismatch"]]
    return {"flag_counts": flags.to_dict(), "case_counts": case_counts,
            "mismatch_count": mismatch_count, "rows": records(sub.head(20000)), "total": len(sub)}


@app.get("/api/issues.csv")
def issues_csv(brand: str | None = None, store: str | None = None, flag: str | None = None,
               severity: str | None = "error|warn", case: str | None = None, mismatch: int = 0,
               md: str | None = None, u: str = Depends(user)):
    rows_ = issues(brand, store, flag, severity, case, mismatch, md, u)["rows"]
    df = pd.DataFrame(rows_)
    cols = {"case": "구분", "actual_status": "상태(실제)", "expected_status": "정상 상태", "scm_code": "SCM 상태 코드",
            **CSV_COLS}
    df = df[[c for c in cols if c in df]].rename(columns=cols)
    return _csv(df, "consign_ips_issues.csv")


@app.get("/api/matrix")
def matrix(metric: str = "skus", u: str = Depends(user)):
    df, _ = snap()
    ok = df[(df["severity"] != "error") & (df["is_dup"].astype(str) != "True")]
    val = {"skus": ("sku_id", "nunique"), "fixed": ("fixed_qty", "sum"),
           "stock": ("stock_qty", "sum"), "need": ("need_qty", "sum")}[metric]
    pv = ok.pivot_table(index="src_file", columns="store_name", values=val[0], aggfunc=val[1], fill_value=0)
    stores = list(pv.columns)
    rows = [{"brand": b, **{s: float(pv.at[b, s]) for s in stores}, "_total": float(pv.loc[b].sum())}
            for b in pv.index]
    return {"stores": stores, "rows": rows}


@app.get("/api/rows")
def rows(brand: str | None = None, store: str | None = None, severity: str | None = None,
         flag: str | None = None, q: str | None = None, only: str | None = None, md: str | None = None,
         u: str = Depends(user)):
    df, _ = snap()
    sub = _filter(df, brand, store, severity, flag, q, md)
    if only == "need":
        sub = sub[sub["need_qty"] > 0].sort_values(["alloc_qty", "need_qty"], ascending=False)
    elif only == "return":
        sub = sub[sub["return_candidate"].astype(str) == "True"].sort_values("over_qty", ascending=False)
    return {"rows": records(sub.head(20000)), "total": len(sub)}


CSV_COLS = {
    "brand_in": "브랜드", "store_name": "매장", "sku_id": "SKU ID", "goods_no": "UID",
    "product_name": "상품명", "option_name": "옵션명", "other_goods": "다른 연결 UID", "rep_barcode": "대표 바코드", "other_barcodes": "추가 바코드",
    "fixed_qty": "고정 운영 수량", "stock_qty": "매장 현재고", "incoming_qty": "이동중 재고", "outgoing_qty": "매장 반납 예정", "mfs_qty": "MFS 재고", "mfs_in_qty": "MFS 입고 예정",
    "mfs_in_date": "MFS 입고 예정일", "mfs_in_late_qty": "MFS 입고 지연(7일+)",
    "off_cum": "누적 판매", "off_w1": "7일 판매", "avail_qty": "판매가능", "off_4w": "매장 판매 4주", "mfs_qty": "MFS 재고", "need_qty": "보충 필요",
    "alloc_qty": "MFS 배분", "short_qty": "부족", "over_qty": "과잉", "storage_status": "SCM 매장 운영",
    "bz_offline_yn": "오프라인 판매(비제스트)", "offline_yn": "오프라인 판매(SCM-HUB)", "severity": "검증", "flags_text": "확인 사항", "src_file": "원본 파일", "src_row": "원본 행",
}


@app.get("/api/rows.csv")
def rows_csv(brand: str | None = None, store: str | None = None, severity: str | None = None,
             flag: str | None = None, q: str | None = None, only: str | None = None, md: str | None = None,
             u: str = Depends(user)):
    data = rows(brand, store, severity, flag, q, only, md, u)["rows"]
    df = pd.DataFrame(data)
    df = df[[c for c in CSV_COLS if c in df]].rename(columns=CSV_COLS)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=consign_ips.csv"})


# ── 업로드 파일 ─────────────────────────────────────────────────────────
def _extra(name: str) -> pd.DataFrame:
    snap()  # 스냅샷 존재 확인
    p = os.path.join(refresh.CACHE_DIR, name)
    return pd.read_parquet(p) if os.path.exists(p) else pd.DataFrame()


def _csv(df: pd.DataFrame, filename: str) -> Response:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


def _ops(status: str | None, brand: str | None, store: str | None, md: str | None = None) -> pd.DataFrame:
    ops = _extra("ops.parquet")
    if ops.empty:
        return ops
    # 브랜드 표기를 시트 파일 기준(src_file)으로 맞춰야 브랜드·MD 필터가 다른 화면과 같게 동작한다
    df, _ = snap()
    b2f = df[df["brand_nm"] != ""].groupby("brand_nm")["src_file"].agg(lambda s: s.mode().iat[0])
    ops = ops.assign(src_file=ops["brand_nm"].map(b2f).fillna(ops["brand_nm"]))
    if status:
        ops = ops[ops["target_status"].isin(status.split("|"))]
    if md:
        ops = ops[ops["src_file"].isin(_md_files(md))]
    if brand:
        ops = ops[ops["src_file"].isin(brand.split("|"))]
    if store:
        ops = ops[ops["store_name"].isin(store.split("|"))]
    return ops


@app.get("/api/ops")
def ops_list(status: str | None = None, brand: str | None = None, store: str | None = None,
             md: str | None = None, u: str = Depends(user)):
    scoped = _ops(None, brand, store, md)      # 건수 카드는 MD·브랜드·매장 필터를 따른다
    counts = scoped["target_status"].value_counts().to_dict() if len(scoped) else {}
    sub = _ops(status, brand, store, md)
    return {"counts": counts, "rows": records(sub.head(20000)), "total": len(sub)}


@app.get("/api/ops.csv")
def ops_csv(status: str | None = None, brand: str | None = None, store: str | None = None,
            detail: int = 0, md: str | None = None, u: str = Depends(user)):
    """SCM-HUB 스토어 운영상태 업로드 양식: 스토어코드 / SKU번호 / 스토어 운영상태"""
    sub = _ops(status, brand, store, md)
    out = pd.DataFrame({"스토어코드": sub.get("storage_no"), "SKU번호": sub.get("sku_id"),
                        "스토어 운영상태": sub.get("target_status")})
    if detail:
        out = out.assign(UID=sub.get("goods_no"), 브랜드=sub.get("brand_nm"), 매장=sub.get("store_name"),
                         상품명=sub.get("product_name"), 옵션명=sub.get("option_name"), 현재상태=sub.get("storage_status"), 사유=sub.get("reason"),
                         매장재고=sub.get("stock_qty"))
    return _csv(out, "store_operation_status.csv")


def _moves(ret: str, brand: str | None, store: str | None, md: str | None = None) -> pd.DataFrame:
    """재고 보충 등록 양식 행: 이동(MFS→매장)은 대표 바코드 1줄, 과재고 반출은 매장에 재고가 있는 바코드별로 나눈다."""
    df, _ = snap()
    df = _filter(df, brand, store, md=md)
    ok = (df["severity"] != "error") & (df["is_dup"].astype(str) != "True")
    lines = []

    def stock_of(r) -> dict:
        # 매장×SKU 단위 재고 (화면 확인용, CSV 양식에는 안 들어감). 이동중 = 출고 요청(출고 전) + 출고 후 이동중 + 직납 예정
        g = lambda c: int(getattr(r, c, 0) or 0)
        return {"fixed_qty": g("fixed_qty"), "stock_qty": g("stock_qty"), "avail_qty": g("avail_qty"),
                "incoming_qty": g("incoming_qty"), "req_in_qty": g("req_in_qty"),
                "moving_in_qty": g("moving_in_qty"), "direct_in_qty": g("direct_in_qty")}

    mv = df[ok & (df["alloc_qty"] > 0)]
    for r in mv.itertuples():
        lines.append({"storage_no": r.storage_no, "sku_id": r.sku_id, "goods_no": r.goods_no, "barcode": r.rep_barcode,
                      "move_qty": int(r.alloc_qty), "ret_qty": 0, "brand": r.src_file, "store_name": r.store_name,
                      "product_name": r.product_name, "option_name": r.option_name,
                      "off_4w": int(r.off_4w), "off_cum": int(r.off_cum), "off_w1": int(r.off_w1),
                      "note": "운영상태(운영중) 먼저 업로드" if str(r.need_ops) == "True" else "", **stock_of(r)})
    rt = df[ok & (df["ret_qty"] > 0)]
    if ret == "nosale":
        rt = rt[rt["return_candidate"].astype(str) == "True"]
    bc = _extra("stock_bc.parquet")
    by = {}
    for b in bc.itertuples():
        by.setdefault((int(b.fk_sku_id), int(b.storage_id)), []).append((b.barcode, int(b.avail_qty)))
    for r in rt.itertuples():
        left = int(r.ret_qty)
        cands = sorted(by.get((int(r.fk_sku_id), int(r.storage_id)), []), key=lambda x: -x[1]) or [(r.rep_barcode, left)]
        st = stock_of(r)          # 바코드별로 나뉘어도 매장×SKU 재고는 첫 줄에만 (합계 중복 방지)
        for code, q in cands:
            if left <= 0:
                break
            take = min(left, q)
            lines.append({"storage_no": r.storage_no, "sku_id": r.sku_id, "goods_no": r.goods_no, "barcode": code, "move_qty": 0,
                          "ret_qty": take, "brand": r.src_file, "store_name": r.store_name,
                          "product_name": r.product_name, "option_name": r.option_name,
                          "off_4w": int(r.off_4w), "off_cum": int(r.off_cum), "off_w1": int(r.off_w1),
                          "note": "4주 판매 없음" if str(r.return_candidate) == "True" else "", **st})
            st = {k: None for k in st}
            left -= take
    m = pd.DataFrame(lines)
    if m.empty:
        return m
    # 같은 SKU 를 여러 매장으로 보낼 때 판매 좋은 매장이 위로: SKU 끼리 묶고 → 이동 먼저, 반출 나중 →
    # 매장 판매(7일 → 4주 → 누적) 높은 순. (MFS 배분 우선순위와 같은 기준)
    m["_kind"] = (m["move_qty"] == 0).astype(int)
    m = m.sort_values(["brand", "sku_id", "_kind", "off_w1", "off_4w", "off_cum", "store_name"],
                      ascending=[True, True, True, False, False, False, True])
    return m.drop(columns="_kind").reset_index(drop=True)


@app.get("/api/moves")
def moves(ret: str = "all", brand: str | None = None, store: str | None = None, md: str | None = None,
          u: str = Depends(user)):
    m = _moves(ret, brand, store, md)
    tot = {"move_qty": int(m["move_qty"].sum()) if len(m) else 0, "ret_qty": int(m["ret_qty"].sum()) if len(m) else 0}
    return {"rows": records(m), "total": len(m), **tot}


@app.get("/api/moves.csv")
def moves_csv(ret: str = "all", brand: str | None = None, store: str | None = None, md: str | None = None,
              u: str = Depends(user)):
    """재고 보충 등록 양식: 스토어코드 / SKU 번호 / 바코드 / 이동수량 / 과재고 반출 수량"""
    m = _moves(ret, brand, store, md)
    out = pd.DataFrame({"스토어코드": m.get("storage_no"), "SKU 번호": m.get("sku_id"), "바코드": m.get("barcode"),
                        "이동수량": m.get("move_qty"), "과재고 반출 수량": m.get("ret_qty")})
    return _csv(out, "store_replenishment.csv")


def _offline_goods(brand: str | None, md: str | None) -> pd.DataFrame:
    """보충 발주 조건 ① 미충족 — SCM-HUB 오프라인 판매 여부가 Y 가 아닌 상품(UID). 시트 운영리스트에 있는 상품만.
    비제스트가 이미 Y 면 'SCM 반영 대기', 아니면 '비제스트 Y 전환'."""
    df, _ = snap()
    df = _filter(df, brand, md=md)
    x = df[(df["severity"] != "error") & (df["is_dup"].astype(str) != "True")
           & (df["offline_yn"] != "Y") & (df["offline_yn"] != "")]
    if x.empty:
        return pd.DataFrame()
    x = x.assign(goods_no=pd.to_numeric(x["goods_no"], errors="coerce").astype("Int64").astype(str))
    g = x.groupby(["src_file", "goods_no"]).agg(
        product_name=("product_name", "first"), offline_yn=("offline_yn", "first"),
        bz_offline_yn=("bz_offline_yn", "first"), bz_offline_ut=("bz_offline_ut", "first"),
        stores=("store_name", "nunique"), skus=("sku_id", "nunique"),
        fixed_qty=("fixed_qty", "sum"), stock_qty=("stock_qty", "sum"),
        need_qty=("need_qty", "sum"), off_cum=("off_cum", "sum"), off_w1=("off_w1", "sum")).reset_index()
    g["todo"] = g["bz_offline_yn"].eq("Y").map({True: "SCM 반영 대기", False: "비제스트 Y 전환"})
    return g.sort_values(["need_qty", "off_w1"], ascending=False)


@app.get("/api/offline_goods")
def offline_goods(brand: str | None = None, md: str | None = None, u: str = Depends(user)):
    g = _offline_goods(brand, md)
    return {"rows": records(g), "total": len(g)}


@app.get("/api/offline_goods.csv")
def offline_goods_csv(brand: str | None = None, md: str | None = None, u: str = Depends(user)):
    g = _offline_goods(brand, md)
    cols = {"goods_no": "UID", "src_file": "브랜드", "product_name": "상품명", "todo": "할 일",
            "bz_offline_yn": "비제스트 오프라인 판매", "bz_offline_ut": "비제스트 변경 시각", "offline_yn": "SCM-HUB 오프라인 판매",
            "stores": "운영 매장 수", "skus": "SKU 수", "fixed_qty": "고정 운영 수량", "stock_qty": "매장 현재고",
            "need_qty": "보충 필요", "off_cum": "누적 판매", "off_w1": "7일 판매"}
    return _csv(g[[c for c in cols if c in g]].rename(columns=cols), "offline_goods_yn.csv")


# ── SPA ─────────────────────────────────────────────────────────────────
if os.path.isdir(os.path.join(DIST, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST, "assets")), name="assets")


@app.get("/{path:path}")
def spa(path: str):
    f = os.path.join(DIST, path)
    if path and os.path.isfile(f):
        return FileResponse(f)
    idx = os.path.join(DIST, "index.html")
    if os.path.exists(idx):  # index.html 은 캐시 금지 — 재배포 후 옛 번들을 계속 쓰는 문제 방지
        return FileResponse(idx, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"detail": "web/dist 없음 — npm run build 필요"}, 404)
