"""Databricks 접속 — 대시보드 db.py 와 같은 방식(env 우선, 로컬은 secrets.toml).

- 배포(Cloud Run): DATABRICKS_HOST / DATABRICKS_HTTP_PATH / DATABRICKS_TOKEN (Secret Manager 주입)
- 로컬: LOCAL_DBX_SECRETS(기본 = 대시보드의 .streamlit/secrets.toml) 의 [databricks] 섹션
- use_cloud_fetch 기본 False — 사내 SASE 프록시 TLS 와 CloudFetch 가 충돌한다(대시보드와 동일 이유).
"""
from __future__ import annotations

import os
import threading
import tomllib

import pandas as pd
from databricks import sql

try:  # 사내 프록시 인증서 — 로컬 PC 에서만 필요, 없으면 무시
    import truststore
    truststore.inject_into_ssl()
except Exception:  # pragma: no cover
    pass

_DEFAULT_LOCAL = r"C:\Users\MUSINSA\musinsa-offline-dashboard\.streamlit\secrets.toml"
_lock = threading.Lock()
_conn = None


def _creds() -> tuple[str, str, str]:
    host = os.environ.get("DATABRICKS_HOST")
    path = os.environ.get("DATABRICKS_HTTP_PATH")
    token = os.environ.get("DATABRICKS_TOKEN")
    if host and path and token:
        return host, path, token
    with open(os.environ.get("LOCAL_DBX_SECRETS", _DEFAULT_LOCAL), "rb") as f:
        c = tomllib.load(f)["databricks"]
    return c["host"], c["http_path"], c["token"]


def _connect():
    host, path, token = _creds()
    return sql.connect(server_hostname=host, http_path=path, access_token=token,
                       use_cloud_fetch=os.environ.get("DBX_CLOUD_FETCH", "0") == "1")


# 2026-10-06: 조회가 간헐적으로 응답 없이 멈춤(같은 쿼리가 1초 / 90초+ 들쭉날쭉) → 새로고침 전체가 끝없이 대기.
# 조회마다 제한 시간(DBX_QUERY_TIMEOUT)을 두고, 넘기면 취소·연결 폐기 후 새 연결로 재시도.
# ⚠️ 같은 날 확인: 공용 웨어하우스(Shared SQL Warehouse, 2X-Small, 최대 5클러스터)가 5/5 로 꽉 차서 조회가 대기열에서
#    오래 기다린다. 너무 빨리 끊으면 대기열 맨 뒤로 다시 서게 되니 넉넉히 10분 × 2번.
QUERY_TIMEOUT = float(os.environ.get("DBX_QUERY_TIMEOUT", "600"))
TRIES = int(os.environ.get("DBX_TRIES", "2"))


def _drop_conn(conn) -> None:
    global _conn
    with _lock:
        if _conn is conn:
            _conn = None
    try:
        conn.close()
    except Exception:
        pass


def _execute_once(query: str, box: dict) -> None:
    global _conn
    try:
        with _lock:
            if _conn is None:
                _conn = _connect()
            conn = _conn
        box["conn"] = conn
        with conn.cursor() as cur:
            box["cur"] = cur
            cur.execute(query)
            try:
                box["df"] = cur.fetchall_arrow().to_pandas()
            except Exception:
                cols = [d[0] for d in cur.description]
                box["df"] = pd.DataFrame([tuple(r) for r in cur.fetchall()], columns=cols)
    except Exception as e:  # noqa: BLE001
        box["err"] = e


def _execute(query: str) -> pd.DataFrame:
    last = None
    for i in range(TRIES):
        box: dict = {}
        t = threading.Thread(target=_execute_once, args=(query, box), daemon=True)
        t.start()
        t.join(QUERY_TIMEOUT)
        if not t.is_alive():
            if "err" in box:
                raise box["err"]
            return box["df"]
        # 시간 초과 — 취소 시도하고 연결을 버린 뒤 새 연결로 다시
        last = TimeoutError(f"Databricks 조회가 {int(QUERY_TIMEOUT)}초 안에 끝나지 않음 ({i + 1}/{TRIES}회)")
        try:
            if box.get("cur") is not None:
                box["cur"].cancel()
        except Exception:
            pass
        if box.get("conn") is not None:
            threading.Thread(target=_drop_conn, args=(box["conn"],), daemon=True).start()
        else:
            with _lock:
                globals()["_conn"] = None
    raise last


def run_df(query: str) -> pd.DataFrame:
    """쿼리 실행. 세션이 끊겼으면 한 번 재연결 후 재시도. 응답 없으면 _execute 가 시간 제한·재시도."""
    global _conn
    try:
        return _execute(query)
    except Exception as e:
        m = str(e).lower()
        if not ("session" in m or "expired" in m or "closed" in m):
            raise
        with _lock:
            try:
                if _conn is not None:
                    _conn.close()
            except Exception:
                pass
            _conn = None
        return _execute(query)
