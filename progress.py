"""새로고침 진행 단계 — 화면 상단에 '어디까지 했는지' 보여 주고, 단계별 소요 시간을 meta.json 에 남긴다.

새로고침은 백그라운드 스레드로 돌고 화면은 /api/status 로 이 값을 5초마다 읽는다.
(같은 프로세스 안의 전역 상태라 Cloud Run 인스턴스가 1개일 때 정확하다 — 새로고침 중에는 화면 폴링이 같은 인스턴스를 유지시킨다)
"""
from __future__ import annotations

import time

STATE: dict = {"step": "", "since": 0.0, "timings": []}


def reset() -> None:
    STATE.update(step="", since=time.time(), timings=[])


def step(text: str) -> None:
    now = time.time()
    if STATE["step"]:
        STATE["timings"].append([STATE["step"], round(now - STATE["since"], 1)])
    STATE.update(step=text, since=now)


def done() -> list:
    step("")
    return list(STATE["timings"])
