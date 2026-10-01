"""브랜드 공유 시트 읽기 (Drive + Sheets REST, 서비스 계정).

브랜드 파일 규칙 (2026-09-30 실측):
- 폴더 BRAND_FOLDER_ID 안의 `{브랜드명}_온오프판매및MFS재고` 스프레드시트
- 탭 `운영 상품 리스트(브랜드사 작성 및 주기적 업데이트)` — 이름이 '운영 상품 리스트' 로 시작하는 탭
- 템플릿 버전이 브랜드마다 달라 **열 위치가 제각각**이다. 수량 열 이름도
  '매장 고정 수량'(휠라) / '매장 초도 수량'(야세·기호, 위에 '▼ 매장 고정 운영 수량' 배너) 로 다르다.
  → 옛 3-2 처럼 22행·24행·B~F 고정으로 읽지 않고, 상단 40행에서 'SKU ID' 와 '매장' 이 함께 있는 행을
    헤더로 찾아 **열 이름으로** 매핑한다.

인증: google.auth.default() — Cloud Run 은 서비스에 붙인 서비스 계정, 로컬은
GOOGLE_APPLICATION_CREDENTIALS(키 파일) 또는 `gcloud auth application-default login`.
서비스 계정 이메일을 브랜드 폴더에 편집자로 공유해야 한다(읽기만이면 뷰어로 충분, 배포하려면 편집자).
HTTP 는 requests(AuthorizedSession) 로 한다 — httplib2 는 사내 프록시 인증서를 못 읽는다.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict

import pandas as pd

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:  # pragma: no cover
    pass

BRAND_FOLDER_ID = os.environ.get("BRAND_FOLDER_ID", "1DF5dsU2LgA04Gm9CNLC2bBKQhFCitL6Y")
FILE_SUFFIX = "_온오프판매및MFS재고"
TAB_PREFIX = "운영 상품 리스트"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly",
          "https://www.googleapis.com/auth/spreadsheets"]

# 열 이름 후보 — 앞에 있을수록 우선. 공백은 무시하고 비교한다.
HEADERS = {
    # 브랜드 시트에서 가져오는 건 이 3개뿐 (2026-09-30 사용자 결정). 나머지 열(브랜드·상품번호·옵션·
    # 주력 여부·MFS 입고 수량 등)은 무시한다 — 상품 정보·재고·입고 예정은 모두 SKU ID 로 원천에서 붙인다.
    "store_in":     ["매장"],
    "sku_id_in":    ["SKU ID", "SKUID"],
    "fixed_qty_in": ["매장 고정 수량", "매장 고정 운영 수량", "매장 초도 수량"],
}
REQUIRED = ("store_in", "sku_id_in", "fixed_qty_in")


def _k(s) -> str:
    return re.sub(r"\s+", "", str(s or ""))


@dataclass
class FileStatus:
    brand: str
    file_id: str
    modified: str = ""
    owner: str = ""
    status: str = "ok"          # ok / 대상 아님 / 탭 없음 / 헤더 없음 / 필수 열 없음 / 읽기 실패
    detail: str = ""
    qty_header: str = ""
    rows: int = 0
    url: str = ""
    columns: dict = field(default_factory=dict)


def _session():
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    creds, _ = google.auth.default(scopes=SCOPES)
    return AuthorizedSession(creds)


def list_brand_files(sess) -> list[dict]:
    out, token = [], None
    q = (f"'{BRAND_FOLDER_ID}' in parents and trashed = false "
         "and mimeType = 'application/vnd.google-apps.spreadsheet'")
    while True:
        r = sess.get("https://www.googleapis.com/drive/v3/files", params={
            "q": q, "pageSize": 200, "pageToken": token or "",
            "fields": "nextPageToken, files(id,name,modifiedTime,owners(emailAddress),webViewLink)",
            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
        r.raise_for_status()
        j = r.json()
        out += j.get("files", [])
        token = j.get("nextPageToken")
        if not token:
            return out


def parse_values(values: list[list], st: FileStatus) -> pd.DataFrame:
    """시트 값(2차원) → 입력 행. 헤더 행을 이름으로 찾는다."""
    hdr_i = None
    for i, row in enumerate(values[:40]):
        ks = {_k(c) for c in row}
        if "SKUID" in ks and "매장" in ks:
            hdr_i = i
            break
    if hdr_i is None:
        st.status, st.detail = "헤더 없음", "상단 40행에서 'SKU ID'·'매장' 헤더 행을 찾지 못함"
        return pd.DataFrame()
    hdr = [_k(c) for c in values[hdr_i]]
    cols = {}
    for key, names in HEADERS.items():
        for nm in names:
            if _k(nm) in hdr:
                cols[key] = hdr.index(_k(nm))
                if key == "fixed_qty_in":
                    st.qty_header = nm
                break
    st.columns = {k: v + 1 for k, v in cols.items()}
    missing = [k for k in REQUIRED if k not in cols]
    if missing:
        st.status, st.detail = "필수 열 없음", ", ".join(missing)
        return pd.DataFrame()
    recs = []
    for i in range(hdr_i + 1, len(values)):
        row = values[i]
        get = lambda k: (row[cols[k]] if k in cols and cols[k] < len(row) else "")
        store, sku = str(get("store_in")).strip(), str(get("sku_id_in")).strip()
        if not store and not sku:
            continue
        rec = {k: get(k) for k in cols}
        rec.update(src_row=i + 1)
        recs.append(rec)
    df = pd.DataFrame(recs)
    st.rows = len(df)
    return df


def read_all(target_brand_names: set[str] | None = None) -> tuple[pd.DataFrame, list[dict]]:
    """폴더의 모든 브랜드 파일을 읽는다. 대상이 아닌 브랜드는 읽지 않고 상태만 남긴다."""
    csv_dir = os.environ.get("BRAND_CSV_DIR")  # 로컬: Drive 에서 CSV 로 내려받은 브랜드 시트 폴더(<브랜드>.csv)
    if csv_dir:
        import csv as _csv
        frames, statuses = [], []
        for fn in sorted(os.listdir(csv_dir)):
            if not fn.lower().endswith(".csv"):
                continue
            brand = fn[:-4]
            st = FileStatus(brand=brand, file_id="csv-dir",
                            modified=pd.Timestamp(os.path.getmtime(os.path.join(csv_dir, fn)), unit="s").isoformat())
            if target_brand_names is not None and brand not in target_brand_names:
                st.status, st.detail = "대상 아님", "담당 MD 브랜드 아님"
            else:
                with open(os.path.join(csv_dir, fn), encoding="utf-8-sig", newline="") as f:
                    df = parse_values(list(_csv.reader(f)), st)
                if len(df):
                    df["src_file"], df["file_id"] = brand, "csv-dir"
                    df["brand_in"] = brand   # 브랜드 = 시트 파일 브랜드
                    frames.append(df)
            statuses.append(asdict(st))
        return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), statuses

    csv = os.environ.get("BRAND_ROWS_CSV")  # 로컬 개발용: 서비스 계정 없이 CSV 로 대체
    if csv:
        df = pd.read_csv(csv, dtype=str)
        return df, [asdict(FileStatus(brand=b, file_id="csv", rows=int(n)))
                    for b, n in df.groupby("src_file").size().items()]

    sess = _session()
    frames, statuses = [], []
    for f in sorted(list_brand_files(sess), key=lambda x: x["name"]):
        name = f["name"]
        brand = name[:-len(FILE_SUFFIX)] if name.endswith(FILE_SUFFIX) else name
        st = FileStatus(brand=brand, file_id=f["id"], modified=f.get("modifiedTime", ""),
                        owner=(f.get("owners") or [{}])[0].get("emailAddress", ""),
                        url=f.get("webViewLink", ""))
        if not name.endswith(FILE_SUFFIX):
            st.status, st.detail = "대상 아님", "파일명 규칙 아님"
        elif target_brand_names is not None and brand not in target_brand_names:
            st.status, st.detail = "대상 아님", "담당 MD 브랜드 아님"
        else:
            try:
                meta = sess.get(f"https://sheets.googleapis.com/v4/spreadsheets/{f['id']}",
                                params={"fields": "sheets.properties.title"})
                meta.raise_for_status()
                tabs = [s["properties"]["title"] for s in meta.json().get("sheets", [])]
                tab = next((t for t in tabs if t.startswith(TAB_PREFIX)), None)
                if not tab:
                    st.status, st.detail = "탭 없음", f"'{TAB_PREFIX}…' 탭 없음"
                else:
                    r = sess.get(f"https://sheets.googleapis.com/v4/spreadsheets/{f['id']}/values/"
                                 f"'{tab}'!A1:BZ5000",
                                 params={"valueRenderOption": "UNFORMATTED_VALUE"})
                    r.raise_for_status()
                    df = parse_values(r.json().get("values", []), st)
                    if len(df):
                        df["src_file"], df["file_id"] = brand, f["id"]
                        df["brand_in"] = brand   # 브랜드 = 시트 파일 브랜드
                        frames.append(df)
            except Exception as e:  # 한 브랜드 실패가 전체를 막지 않게
                st.status, st.detail = "읽기 실패", str(e)[:300]
        statuses.append(asdict(st))
    rows = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return rows, statuses
