"""원천 조회 결과 저장소 — 새로고침을 영역별로 나누기 위한 것 (2026-10-06 사용자 요청).

버튼 4개:
  전체        = 브랜드 시트 + SCM-HUB + 비제스트 를 모두 다시 읽음
  SCM-HUB     = SKU·UID 연결(sku_master), 매장 운영상태, 매장 재고·이동, MFS 재고·입고 예정, 판매, 대상 브랜드
  비제스트    = 상품(UID) 오프라인 판매 여부(비제스트 원장)
  브랜드 시트 = 운영 상품 리스트만 다시 읽음

각 원천 표는 '어떤 키(SKU·UID 등)로 조회했는지'와 함께 CACHE_DIR/src 에 저장한다.
  - 다시 읽는 영역의 표: 이번에 필요한 키 전체를 새로 조회해서 교체
  - 다시 읽지 않는 영역의 표: 저장값을 쓰고, 저장값에 없는 키(예: 시트에 새로 추가된 SKU)만 추가 조회
그래서 '브랜드 시트'만 눌러도 새 SKU 의 재고·판매가 비지 않는다.
"""
from __future__ import annotations

import json
import os
import pickle

import pandas as pd

import progress

SCM_TABLES = {"stores", "tb", "sku", "stock", "ss", "stock_bc", "sales", "mfs", "inb", "onl", "enabled"}
BIZEST_TABLES = {"bizest"}
LABEL = {"stores": "매장 목록", "tb": "대상 브랜드", "sku": "SKU 마스터(UID 연결·바코드)", "stock": "매장 재고·이동",
         "ss": "매장 운영상태", "stock_bc": "매장 바코드별 재고", "sales": "오프라인 판매", "mfs": "MFS 재고",
         "inb": "MFS 입고 예정", "onl": "온라인 판매", "enabled": "SCM 운영중 매장×SKU", "bizest": "비제스트 오프라인 판매 여부"}
ALL = "__all__"


def norm(v) -> str:
    """키 비교용 문자열 (3417694 / 3417694.0 / '3417694' 를 같게)."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    s = str(v)
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


class Source:
    def __init__(self, cache_dir: str, refresh: set[str]):
        self.dir = os.path.join(cache_dir, "src")
        os.makedirs(self.dir, exist_ok=True)
        self.refresh = set(refresh)
        self.fresh: set[str] = set()      # 이번 실행에서 새로 읽기 시작한 표
        self.dirty: set[str] = set()
        self.frames: dict[str, pd.DataFrame | None] = {}
        try:
            with open(os.path.join(self.dir, "keys.json"), encoding="utf-8") as f:
                self.keys = {k: set(v) for k, v in json.load(f).items()}
        except Exception:
            self.keys = {}

    def _frame(self, name: str):
        if name not in self.frames:
            p = os.path.join(self.dir, f"{name}.pkl")
            try:
                self.frames[name] = pd.read_pickle(p) if os.path.exists(p) else None
            except Exception:
                self.frames[name] = None
                self.keys.pop(name, None)
        return self.frames[name]

    def _start(self, name: str) -> None:
        if name in self.refresh and name not in self.fresh:
            self.frames[name], self.keys[name] = None, set()
            self.fresh.add(name)

    def get(self, name: str, keys, col: str, fetch, conv=str) -> pd.DataFrame:
        """keys(col 값) 에 해당하는 행. 없는 키만 fetch(list) 로 추가 조회한다."""
        self._start(name)
        want = {norm(k) for k in keys if norm(k) != ""}
        df = self._frame(name)
        known = self.keys.get(name, set()) if df is not None else set()
        missing = want - known
        if missing:
            progress.step(f"원천 조회: {LABEL.get(name, name)}")
            new = fetch([conv(k) for k in sorted(missing)])
            df = new if df is None else pd.concat([df, new], ignore_index=True)
            if col in df:
                df = df.drop_duplicates()
            self.frames[name] = df
            self.keys[name] = known | missing
            self.dirty.add(name)
        if df is None:
            return pd.DataFrame(columns=[col])
        if col not in df:
            return df.iloc[0:0]
        return df[df[col].map(norm).isin(want)].reset_index(drop=True)

    def whole(self, name: str, fetch) -> pd.DataFrame:
        """키 없이 통째로 읽는 작은 표(매장 목록·대상 브랜드 등)."""
        self._start(name)
        df = self._frame(name)
        if df is None or ALL not in self.keys.get(name, set()):
            progress.step(f"원천 조회: {LABEL.get(name, name)}")
            df = fetch()
            self.frames[name], self.keys[name] = df, {ALL}
            self.dirty.add(name)
        return df.copy()

    def save(self) -> None:
        for name in self.dirty:
            df = self.frames.get(name)
            if df is None:
                continue
            tmp = os.path.join(self.dir, f"{name}.pkl.tmp")
            with open(tmp, "wb") as f:
                pickle.dump(df, f)
            os.replace(tmp, os.path.join(self.dir, f"{name}.pkl"))
        with open(os.path.join(self.dir, "keys.json"), "w", encoding="utf-8") as f:
            json.dump({k: sorted(v) for k, v in self.keys.items()}, f, ensure_ascii=False)
        self.dirty.clear()
