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


def _execute(query: str) -> pd.DataFrame:
    global _conn
    with _lock:
        if _conn is None:
            _conn = _connect()
        conn = _conn
    with conn.cursor() as cur:
        cur.execute(query)
        try:
            return cur.fetchall_arrow().to_pandas()
        except Exception:
            cols = [d[0] for d in cur.description]
            return pd.DataFrame([tuple(r) for r in cur.fetchall()], columns=cols)


def run_df(query: str) -> pd.DataFrame:
    """쿼리 실행. 세션이 끊겼으면 한 번 재연결 후 재시도."""
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
