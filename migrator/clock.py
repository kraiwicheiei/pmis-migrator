"""
시간에 의존하는 부분(대기, 현재 시각)을 한곳에 모아 테스트에서 교체할 수 있게 함
[TH] รวมส่วนที่ขึ้นกับเวลา (การรอ, เวลาปัจจุบัน) ไว้ที่เดียวเพื่อให้เทสต์แทนที่ได้
[EN] Keeps everything that depends on time (sleeping, the current time) in one place so tests can replace it
"""
from __future__ import annotations

import datetime
import time
from typing import Callable


# 테스트에서 교체할 수 있도록 모듈 변수로 둠
# [TH] ให้เทสต์แทนที่ได้
# [EN] Kept as a module variable so tests can replace it
SLEEP: Callable[[float], None] = time.sleep

NOW = lambda: datetime.datetime.now(datetime.timezone.utc)  # noqa: E731  (tests replace it, like SLEEP)
