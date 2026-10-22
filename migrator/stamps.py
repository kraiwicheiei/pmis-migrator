"""
큐 시트에 적는 시각 표시: --extract 의 새로고침 시각(I열)과 마이그레이션 실행 시각(K열)
[TH] เวลาที่เขียนลงชีตคิว: เวลา refresh ของ --extract (คอลัมน์ I) และเวลาที่รัน migration (คอลัมน์ K)
[EN] The times written to the queue sheet: the --extract refresh time (column I) and the migration run time (column K)
"""
from __future__ import annotations

import datetime
from typing import Any, Optional

from . import clock
from .sheets import sheets_call, values_url


# 큐 시트의 시각 칸: 1행 = 라벨, 2행 = 시각. A~G열(큐 데이터) 밖이라 처리/중복 확인에 영향이 없음
#   I열 = --extract 가 큐를 쓴 마지막 시각, K열 = 마이그레이션(Gemini 실행)이 끝난 마지막 시각, H/J열은 비워 둠
# [TH] ช่องเวลาในชีตคิว: แถว 1 = ป้ายชื่อ, แถว 2 = เวลา อยู่นอกข้อมูลคิว (คอลัมน์ A~G) จึงไม่กระทบการประมวลผล/ตรวจซ้ำ
#      คอลัมน์ I = เวลาล่าสุดที่ --extract เขียนคิว, คอลัมน์ K = เวลาล่าสุดที่ migration (รัน Gemini) จบ, เว้นคอลัมน์ H/J ไว้ว่าง
# [EN] The time cells of the queue sheet: row 1 = label, row 2 = time. They are outside the queue data (columns A-G),
#      so processing and the duplicate check are not affected.
#      Column I = when --extract last wrote the queue, column K = when the migration (the Gemini run) last finished; H and J stay empty
REFRESH_COL = "I"
QUEUE_REFRESH_LABEL = "Last extract (refresh time)"
MIGRATION_COL = "K"
MIGRATION_LABEL = "Last migration run (finish time)"
STAMP_TZ = datetime.timezone(datetime.timedelta(hours=7))


def time_stamp(now: Optional[datetime.datetime] = None) -> str:
    """
    큐 시트에 적는 시각 문자열 (태국 시간 UTC+7)
    [TH] ข้อความเวลาที่เขียนลงชีตคิว (เวลาไทย UTC+7)
    [EN] The time text written to the queue sheet (Thai time, UTC+7)
    """
    return (now or clock.NOW()).astimezone(STAMP_TZ).strftime("%Y-%m-%d %H:%M:%S") + " (UTC+7)"


def write_time_cells(sess: Any, sid: str, tab: str, col: str, label: str, stamp: str) -> None:
    """
    col1 = 라벨, col2 = 시각 을 RAW 로 기록
    [TH] เขียน col1 = ป้ายชื่อ, col2 = เวลา แบบ RAW
    [EN] Writes col1 = label and col2 = time, as RAW
    """
    quoted = tab.replace("'", "''")
    sheets_call(sess, "PUT", values_url(sid, f"'{quoted}'!{col}1:{col}2"), params={"valueInputOption": "RAW"},
                json={"majorDimension": "ROWS", "values": [[label], [stamp]]})


def write_refresh_time(sess: Any, sid: str, tab: str, stamp: str) -> None:
    """
    I1 = 라벨, I2 = 시각. write_queue 가 시트를 비운 뒤에 호출
    [TH] I1 = ป้ายชื่อ, I2 = เวลา เรียกหลัง write_queue ล้างชีตแล้ว
    [EN] I1 = label, I2 = time. Called after write_queue has cleared the sheet
    """
    write_time_cells(sess, sid, tab, REFRESH_COL, QUEUE_REFRESH_LABEL, stamp)


def write_migration_time(sess: Any, sid: str, tab: str, stamp: str) -> None:
    """
    K1 = 라벨, K2 = 마이그레이션이 끝난 시각
    [TH] K1 = ป้ายชื่อ, K2 = เวลาที่ migration จบ
    [EN] K1 = label, K2 = when the migration finished
    """
    write_time_cells(sess, sid, tab, MIGRATION_COL, MIGRATION_LABEL, stamp)
