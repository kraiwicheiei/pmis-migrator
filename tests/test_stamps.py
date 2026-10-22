"""
큐 시트의 시각 칸 테스트: 형식(태국 시간)과 위치(A~G열 밖)
[TH] เทสต์ช่องเวลาในชีตคิว: รูปแบบ (เวลาไทย) และตำแหน่ง (นอกคอลัมน์ A~G)
[EN] Tests for the time cells of the queue sheet: the format (Thai time) and the place (outside columns A-G)
"""
import datetime

from migrator.queue_writer import QUEUE_HEADER
from migrator.stamps import MIGRATION_COL, MIGRATION_LABEL, QUEUE_REFRESH_LABEL, REFRESH_COL, time_stamp

from tests.legacy_fakes import STAMP


def test_time_stamp_is_thai_time():
    assert time_stamp() == STAMP
    assert time_stamp(datetime.datetime(2026, 1, 1, 18, 0, 0, tzinfo=datetime.timezone.utc)) == "2026-01-02 01:00:00 (UTC+7)"   # crosses midnight


def test_the_time_cells_do_not_overlap_each_other_or_the_queue_data():
    assert len(QUEUE_HEADER) == 7                                                   # A-G = the queue data
    assert (REFRESH_COL, MIGRATION_COL) == ("I", "K")                               # H and J stay empty
    assert QUEUE_REFRESH_LABEL != MIGRATION_LABEL
