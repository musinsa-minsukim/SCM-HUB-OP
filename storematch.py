"""브랜드 시트의 매장명 → SCM storage_id 매칭.

브랜드마다 매장명을 조금씩 다르게 적는다('무신사킥스 홍대점', '킥스 홍대', '메가스토어 용산', 'AK 수원' …).
단계별로 찾고, 정확히 일치하지 않은 경우엔 '어떻게 찾았는지'를 남겨 화면에서 확인할 수 있게 한다.

  1) 정확    : 공백 무시 후 매장 마스터 이름(shop_name / SCM 이름) 또는 스토어코드(SS03…)와 같음, 별칭 포함
  2) 끝 '점' : '…점' 을 떼면 같음
  3) 키워드  : 매장 이름을 키워드로 나눠(무신사·스토어·점 같은 공통어 제외) 비교
               a. 그 매장의 키워드가 입력에 전부 들어 있음 → 가장 구체적인(키워드 많은) 매장
               b. 입력 키워드가 전부 그 매장 키워드에 들어 있음 → 후보가 하나일 때만
  4) 후보가 여럿이면 자동으로 고르지 않고 '매장명 확인 필요(후보: …)' 로 남긴다. 홍대처럼
     '스토어 홍대'·'킥스 홍대' 가 함께 있는 지역은 키워드 하나로는 정할 수 없기 때문이다.
"""
from __future__ import annotations

import re

# 브랜드 시트 표기 ↔ 마스터 이름이 다른 것 (공백 제거 후 비교)
ALIASES = {
    "무신사스토어송도트리플스트리트점": "무신사스토어트리플스트리트송도점",
    "무신사스토어트리플스트리송도점": "무신사스토어트리플스트리트송도점",   # 브랜드 시트·매장코드 탭 오타
    "무신사스토어성수@대림창고": "무신사스토어성수",
    "무신사메가스토어아이파크몰용산점": "무신사메가스토어용산",
    "무신사아울렛&유즈드롯데몰은평": "무신사아울렛&유즈드롯데몰은평점",
}
# 키워드에서 뺄 공통어. '스토어' 는 빼지 않는다 — '스토어 홍대'·'킥스 홍대'·'뷰티 홍대' 를 가르는 매장 유형이라서.
# (단 '메가스토어' 안의 '스토어' 와는 구분해야 한다 → _has)
STOP = {"무신사", "점", "매장"}


def _key(s: str) -> str:
    k = re.sub(r"\s+", "", str(s or ""))
    return ALIASES.get(k, k)


def _tokens(s: str) -> set[str]:
    s = str(s or "").replace("@", " ").replace("&", " ").replace("(", " ").replace(")", " ")
    s = re.sub(r"무신사(?=\S)", "무신사 ", s)            # '무신사킥스' → '무신사 킥스'
    s = re.sub(r"(?<!메가)스토어(?=\S)", "스토어 ", s)     # '스토어홍대' → '스토어 홍대'
    out = set()
    for t in s.split():
        t = re.sub(r"점$", "", t) if len(t) > 2 else t   # '수원점' → '수원' (단, '점' 하나는 STOP)
        if t and t not in STOP:
            out.add(t.upper())
    return out


def _has(flat: str, tok: str) -> bool:
    """입력(공백 제거)에 키워드가 들어 있나. '스토어' 는 '메가스토어' 의 일부가 아닐 때만 인정."""
    if tok == "스토어":
        return re.search(r"(?<!메가)스토어", flat) is not None
    return tok in flat


def _same(x: str, y: str) -> bool:
    """키워드 비교 — '스토어' 는 정확히 같아야, 나머지는 부분 일치 허용('AK' ↔ 'AK플라자')."""
    if x == "스토어" or y == "스토어":
        return x == y
    return x in y or y in x


class StoreMatcher:
    def __init__(self, stores):
        """stores: rows with storage_id, shop_name, scm_name, storage_no"""
        self.exact: dict[str, int] = {}
        self.toks: dict[int, set[str]] = {}
        self.name: dict[int, str] = {}
        for r in stores:
            sid = int(r["storage_id"])
            self.name[sid] = r.get("scm_name") or r.get("shop_name")
            t = set()
            for nm in (r.get("shop_name"), r.get("scm_name")):
                if nm:
                    self.exact[_key(nm)] = sid
                    t |= _tokens(nm)
            if r.get("storage_no"):
                self.exact[str(r["storage_no"]).upper()] = sid
            self.toks[sid] = t
        self._cache: dict[str, tuple] = {}

    def match(self, raw) -> tuple[int | None, str, str]:
        """→ (storage_id | None, 방법, 메모). 방법: 정확 / 끝'점' / 키워드 / 후보 여럿 / 못 찾음"""
        s = str(raw or "").strip()
        if s in self._cache:
            return self._cache[s]
        res = self._match(s)
        self._cache[s] = res
        return res

    def _match(self, s: str):
        if not s:
            return None, "못 찾음", ""
        k = _key(s)
        if k in self.exact:
            return self.exact[k], "정확", ""
        if k.upper() in self.exact:
            return self.exact[k.upper()], "정확", ""
        if k.endswith("점") and k[:-1] in self.exact:
            return self.exact[k[:-1]], "끝'점'", ""
        flat = re.sub(r"\s+", "", s).upper()
        inp = _tokens(s)
        # a. 매장 키워드가 입력에 전부 들어 있음 → 가장 구체적인 매장
        full = [(len(t), sid) for sid, t in self.toks.items() if t and all(_has(flat, x) for x in t)]
        if full:
            best = max(n for n, _ in full)
            top = [sid for n, sid in full if n == best]
            if len(top) == 1:
                return top[0], "키워드", f"'{s}' → {self.name[top[0]]}"
        # b. 입력 키워드가 전부 그 매장 키워드(부분 일치 포함)에 들어 있음 → 후보 하나일 때만
        if inp:
            cand = [sid for sid, t in self.toks.items()
                    if all(any(_same(x, y) for y in t) for x in inp)]
            if len(cand) == 1:
                return cand[0], "키워드", f"'{s}' → {self.name[cand[0]]}"
            if len(cand) > 1:
                return None, "후보 여럿", " / ".join(sorted(self.name[c] for c in cand))
        if full:
            return None, "후보 여럿", " / ".join(sorted(self.name[sid] for _, sid in full))
        return None, "못 찾음", ""
