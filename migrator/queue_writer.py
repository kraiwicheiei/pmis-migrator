"""
큐 시트 쓰기 (--extract 의 쓰기 쪽): 새로 만들기, 합치기(merge), 새로고침 시각
[TH] เขียนชีตคิว (ฝั่งเขียนของ --extract): สร้างใหม่, รวม (merge), เวลา refresh
[EN] Writing the queue sheet (the writing half of --extract): rebuild, merge, the refresh time
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Optional

from . import clock
from .config import SHEETS_BASE
from .legacy_sheet import LegacyItem
from .sheets import append_rows, sheets_call, values_url


# 큐 시트의 헤더 (Apps Script 와 동일, 영어)
# [TH] หัวตารางของชีตคิว (เหมือน Apps Script เป็นภาษาอังกฤษ)
# [EN] Header of the queue sheet (same as Apps Script, in English)
QUEUE_HEADER = ["Doc No", "Date", "Discipline", "PDF URL", "Folder URL", "Status(C/P)", "AI Processed"]

EXTRACT_ROWS_PER_WRITE = 2000

EXTRACT_UPDATES_PER_WRITE = 500

# --extract 가 끝난 시각을 적는 칸: 큐 시트의 I1(라벨) / I2(시각). A~G열(큐 데이터) 밖이고 H열은 비워 둠
# [TH] ช่องที่ --extract เขียนเวลาที่เสร็จ: I1 (ป้ายชื่อ) / I2 (เวลา) ของชีตคิว อยู่นอกคอลัมน์ A~G (ข้อมูลคิว) และเว้นคอลัมน์ H ไว้ว่าง
# [EN] Where --extract writes when it finished: I1 (label) / I2 (time) of the queue sheet, outside columns A-G (the queue data) with column H left empty
REFRESH_COL = "I"

QUEUE_REFRESH_LABEL = "Last extract (refresh time)"

REFRESH_TZ = datetime.timezone(datetime.timedelta(hours=7))


def queue_progress(values: list[list[str]]) -> dict[str, int]:
    """
    큐에서 이미 처리된 행의 수 (Waiting / 빈 상태는 제외). 큐를 다시 만들면 이 상태들이 사라짐
    [TH] จำนวนแถวในคิวที่ถูกประมวลผลไปแล้ว (ไม่นับ Waiting / ว่าง) ถ้าสร้างคิวใหม่สถานะเหล่านี้จะหายไป
    [EN] Number of queue rows that were already processed (Waiting / blank are not counted). Rebuilding the queue wipes these statuses
    """
    counts = {"done": 0, "error": 0, "other": 0}
    for row in values[1:]:
        status = str(row[6]).strip() if len(row) > 6 else ""
        if status in ("", "Waiting"):
            continue
        if status == "Done":
            counts["done"] += 1
        elif status.startswith("Error"):
            counts["error"] += 1
        else:
            counts["other"] += 1
    return counts


def write_queue(sess: Any, sid: str, tab: str, rows: list[list]) -> None:
    """
    큐 시트를 비우고 헤더 + 행을 기록 (RAW: 날짜/숫자처럼 보이는 문자열이 변환되지 않음). 행은 append 로 넣어 시트가 자동 확장됨
    [TH] ล้างชีตคิวแล้วเขียนหัวตาราง + แถว (RAW: ข้อความที่หน้าตาเหมือนวันที่/ตัวเลขจะไม่ถูกแปลง) แถวเพิ่มด้วย append ชีตจึงขยายให้เอง
    [EN] Clears the queue sheet and writes the header + rows (RAW: text that looks like a date/number is not converted).
         Rows go in with append, so the sheet grows by itself
    """
    quoted = tab.replace("'", "''")
    sheets_call(sess, "POST", values_url(sid, f"'{quoted}'") + ":clear", json={})
    sheets_call(sess, "PUT", values_url(sid, f"'{quoted}'!A1:G1"), params={"valueInputOption": "RAW"},
                 json={"majorDimension": "ROWS", "values": [QUEUE_HEADER]})
    for i in range(0, len(rows), EXTRACT_ROWS_PER_WRITE):
        append_rows(sess, sid, tab, rows[i:i + EXTRACT_ROWS_PER_WRITE], value_input="RAW")


def refresh_stamp(now: Optional[datetime.datetime] = None) -> str:
    """
    큐 시트에 적는 '마지막 extract 시각' 문자열 (태국 시간 UTC+7)
    [TH] ข้อความเวลา "extract ล่าสุด" ที่เขียนลงชีตคิว (เวลาไทย UTC+7)
    [EN] The "last extract" time text written to the queue sheet (Thai time, UTC+7)
    """
    return (now or clock.NOW()).astimezone(REFRESH_TZ).strftime("%Y-%m-%d %H:%M:%S") + " (UTC+7)"


@dataclass
class MergePlan:
    new_items: list[LegacyItem] = field(default_factory=list)   # 큐에 없는 항목 / [TH] รายการที่ยังไม่มีในคิว / [EN] items not in the queue yet
    updates: dict[int, list] = field(default_factory=dict)      # 행 번호 -> 새 B~F 값 / [TH] เลขแถว -> ค่า B~F ใหม่ / [EN] row number -> new B..F values
    unchanged: int = 0
    kept_done: int = 0         # Done 이라 손대지 않은 행 / [TH] แถว Done ที่ไม่แตะ / [EN] Done rows left alone
    done_link_differs: int = 0  # 그중 옛 시트의 링크가 다른 행 / [TH] ในนั้นที่ลิงก์ในชีตเก่าต่างออกไป / [EN] of those, rows whose legacy link differs
    missing: int = 0           # 큐에는 있지만 옛 시트에는 없는 행 / [TH] อยู่ในคิวแต่ไม่อยู่ในชีตเก่าแล้ว / [EN] rows in the queue that are not in the legacy sheet any more


def plan_merge(existing: list[list], items: list[LegacyItem]) -> MergePlan:
    """
    기존 큐(헤더 포함 값)와 옛 시트에서 만든 항목을 (Doc No, Date) 로 맞춰 봄:
      * 큐에 없으면 새 행으로 추가 (Waiting)
      * Done 이면 손대지 않음 (옛 시트의 링크가 달라도 그대로 둠)
      * 그 외(Waiting / Error ...)는 상태(G열)는 그대로 두고 B~F 열(Date, Discipline, PDF URL, Folder URL, C/P)만 옛 시트 값으로 갱신
      * 큐에만 있는 행은 건드리지 않음
    [TH] จับคู่คิวเดิม (ค่าพร้อมหัวตาราง) กับรายการจากชีตเก่าด้วย (Doc No, Date):
         * ไม่มีในคิว -> เพิ่มเป็นแถวใหม่ (Waiting)
         * เป็น Done -> ไม่แตะ (ถึงลิงก์ในชีตเก่าจะต่างก็คงไว้)
         * อื่นๆ (Waiting / Error ...) -> คงสถานะ (คอลัมน์ G) แต่อัปเดตคอลัมน์ B~F (Date, Discipline, PDF URL, Folder URL, C/P) ตามชีตเก่า
         * แถวที่มีแต่ในคิว -> ไม่แตะ
    [EN] Matches the existing queue (values incl. the header) against the items built from the legacy sheet by (Doc No, Date):
         * not in the queue -> added as a new row (Waiting)
         * Done -> left alone (even if the legacy link differs)
         * anything else (Waiting / Error ...) -> the status (column G) is kept, but columns B..F (Date, Discipline, PDF URL, Folder URL, C/P)
           are refreshed from the legacy sheet
         * rows that exist only in the queue are not touched
    """
    index: dict[tuple[str, str], tuple[int, list[str]]] = {}
    for number, row in enumerate(existing[1:], start=2):
        cells = [str(c).strip() for c in row][:len(QUEUE_HEADER)]
        cells += [""] * (len(QUEUE_HEADER) - len(cells))
        if cells[0]:
            index.setdefault((cells[0], cells[1]), (number, cells))
    plan = MergePlan()
    seen: set[tuple[str, str]] = set()
    for it in items:
        key = (it.doc_no, it.date)
        hit = index.get(key)
        if hit is None:
            plan.new_items.append(it)
            continue
        seen.add(key)
        number, cells = hit
        wanted = it.to_row()[1:6]
        if cells[6] == "Done":
            plan.kept_done += 1
            if it.pdf_url and cells[3] != it.pdf_url:
                plan.done_link_differs += 1
        elif cells[1:6] != wanted:
            plan.updates[number] = wanted
        else:
            plan.unchanged += 1
    plan.missing = len(index) - len(seen)
    return plan


def apply_merge(sess: Any, sid: str, tab: str, plan: MergePlan) -> None:
    """
    갱신은 B~F 열만 values:batchUpdate 로 (G열 상태는 건드리지 않음), 새 행은 append 로 (시트가 자동 확장됨)
    [TH] อัปเดตเฉพาะคอลัมน์ B~F ด้วย values:batchUpdate (ไม่แตะสถานะคอลัมน์ G) แถวใหม่เพิ่มด้วย append (ชีตขยายให้เอง)
    [EN] Updates go to columns B..F only, through values:batchUpdate (the status in column G is never touched);
         new rows go in with append (the sheet grows by itself)
    """
    quoted = tab.replace("'", "''")
    updates = sorted(plan.updates.items())
    for i in range(0, len(updates), EXTRACT_UPDATES_PER_WRITE):
        body = {"valueInputOption": "RAW",
                "data": [{"range": f"'{quoted}'!B{row}:F{row}", "values": [values]} for row, values in updates[i:i + EXTRACT_UPDATES_PER_WRITE]]}
        sheets_call(sess, "POST", f"{SHEETS_BASE}/{sid}/values:batchUpdate", json=body)
    rows = [it.to_row() for it in plan.new_items]
    for i in range(0, len(rows), EXTRACT_ROWS_PER_WRITE):
        append_rows(sess, sid, tab, rows[i:i + EXTRACT_ROWS_PER_WRITE], value_input="RAW")


def write_refresh_time(sess: Any, sid: str, tab: str, stamp: str) -> None:
    """
    I1 = 라벨, I2 = 시각. 큐 데이터(A~G열) 밖이라 처리/중복 확인에 영향이 없음. write_queue 가 시트를 비운 뒤에 호출
    [TH] I1 = ป้ายชื่อ, I2 = เวลา อยู่นอกข้อมูลคิว (คอลัมน์ A~G) จึงไม่กระทบการประมวลผล/ตรวจซ้ำ เรียกหลัง write_queue ล้างชีตแล้ว
    [EN] I1 = label, I2 = time. Outside the queue data (columns A-G), so processing and the duplicate check are not affected.
         Called after write_queue has cleared the sheet
    """
    quoted = tab.replace("'", "''")
    sheets_call(sess, "PUT", values_url(sid, f"'{quoted}'!{REFRESH_COL}1:{REFRESH_COL}2"), params={"valueInputOption": "RAW"},
                 json={"majorDimension": "ROWS", "values": [[QUEUE_REFRESH_LABEL], [stamp]]})
