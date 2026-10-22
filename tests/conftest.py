"""
모든 테스트 공통 설정: 대기 없음 + 고정된 시각 (UTC 07:05:33 = 태국 14:05:33)
[TH] การตั้งค่าที่ใช้กับทุกเทสต์: ไม่รอจริง + เวลาคงที่ (UTC 07:05:33 = ไทย 14:05:33)
[EN] Settings shared by every test: no real waiting + a fixed clock (UTC 07:05:33 = Thai 14:05:33)
"""
import pytest

from migrator import clock
from tests.legacy_fakes import FIXED_NOW


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    # 실제 backoff 대기 없음 / [TH] ไม่รอ backoff จริง / [EN] No real backoff waiting
    monkeypatch.setattr(clock, "SLEEP", lambda seconds: None)
    monkeypatch.setattr(clock, "NOW", lambda: FIXED_NOW)
