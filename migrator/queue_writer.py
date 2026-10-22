"""
큐 시트 쓰기 (--extract 의 쓰기 쪽): 새로 만들기, 합치기(merge), 새로고침 시각
[TH] เขียนชีตคิว (ฝั่งเขียนของ --extract): สร้างใหม่, รวม (merge), เวลา refresh
[EN] Writing the queue sheet (the writing half of --extract): rebuild, merge, the refresh time
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import SHEETS_BASE
from .legacy_sheet import LegacyItem
from .sheets import append_rows, sheets_call, values_url


# 큐 시트의 헤더 (Apps Script 와 동일, 영어)
# [TH] หัวตารางของชีตคิว (เหมือน Apps Script เป็นภาษาอังกฤษ)
# [EN] Header of the queue sheet (same as Apps Script, in English)
QUEUE_HEADER = ["Doc No", "Date", "Discipline", "PDF URL", "Folder URL", "Status(C/P)", "AI Processed"]

EXTRACT_ROWS_PER_WRITE = 2000

EXTRACT_UPDATES_PER_WRITE = 500


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


@dataclass
class MergePlan:
    new_items: list[LegacyItem] = field(default_factory=list)   # 큐에 없는 항목 / [TH] รายการที่ยังไม่มีในคิว / [EN] items not in the queue yet
    updates: dict[int, list] = field(default_factory=dict)      # 행 번호 -> 새 B~F 값 / [TH] เลขแถว -> ค่า B~F ใหม่ / [EN] row number -> new B..F values
    renamed: dict[int, str] = field(default_factory=dict)       # 행 번호 -> 새 Doc No (A열) / [TH] เลขแถว -> Doc No ใหม่ (คอลัมน์ A) / [EN] row number -> new Doc No (column A)
    old_names: dict[int, str] = field(default_factory=dict)     # 행 번호 -> 바뀌기 전 Doc No (로그용) / [TH] เลขแถว -> Doc No เดิม (สำหรับ log) / [EN] row number -> the Doc No before the change (for the log)
    unchanged: int = 0
    kept_done: int = 0         # Done 이라 손대지 않은 행 / [TH] แถว Done ที่ไม่แตะ / [EN] Done rows left alone
    done_link_differs: int = 0  # 그중 옛 시트의 링크가 다른 행 / [TH] ในนั้นที่ลิงก์ในชีตเก่าต่างออกไป / [EN] of those, rows whose legacy link differs
    missing: int = 0           # 큐에는 있지만 옛 시트에는 없는 행 / [TH] อยู่ในคิวแต่ไม่อยู่ในชีตเก่าแล้ว / [EN] rows in the queue that are not in the legacy sheet any more


def plan_merge(existing: list[list], items: list[LegacyItem]) -> MergePlan:
    """
    기존 큐(헤더 포함 값)와 옛 시트에서 만든 항목을 맞춰 봄:
      * 먼저 (Doc No, Date) 가 같은 행끼리 맞춤
      * 못 맞춘 항목은 (Date, Discipline, 칸의 값) 으로 한 번 더 맞춤: 옛 시트의 A열(No.)만 바뀌어 Doc No 가 달라진 같은 항목.
        이 행은 Doc No(A열)를 새 값으로 바꿈 (Done 이어도 바꿈. 상태와 나머지 열은 아래 규칙대로)
      * 어느 쪽으로도 못 맞추면 새 행으로 추가 (Waiting)
      * Done 이면 Doc No 외에는 손대지 않음 (옛 시트의 링크가 달라도 그대로 둠)
      * 그 외(Waiting / Error ...)는 상태(G열)는 그대로 두고 B~F 열(Date, Discipline, PDF URL, Folder URL, C/P)만 옛 시트 값으로 갱신
      * 큐에만 있는 행은 건드리지 않음
    [TH] จับคู่คิวเดิม (ค่าพร้อมหัวตาราง) กับรายการจากชีตเก่า:
         * ก่อนอื่นจับคู่แถวที่ (Doc No, Date) ตรงกัน
         * รายการที่ยังจับคู่ไม่ได้ ลองอีกครั้งด้วย (Date, Discipline, ค่าในช่อง): คือรายการเดิมที่แค่คอลัมน์ A (No.) ของชีตเก่าเปลี่ยนจน Doc No ต่างไป
           แถวนี้จะถูกเปลี่ยน Doc No (คอลัมน์ A) เป็นค่าใหม่ (แม้เป็น Done ก็เปลี่ยน ส่วนสถานะและคอลัมน์อื่นเป็นไปตามกฎด้านล่าง)
         * ถ้ายังจับคู่ไม่ได้ -> เพิ่มเป็นแถวใหม่ (Waiting)
         * เป็น Done -> ไม่แตะอะไรนอกจาก Doc No (ถึงลิงก์ในชีตเก่าจะต่างก็คงไว้)
         * อื่นๆ (Waiting / Error ...) -> คงสถานะ (คอลัมน์ G) แต่อัปเดตคอลัมน์ B~F (Date, Discipline, PDF URL, Folder URL, C/P) ตามชีตเก่า
         * แถวที่มีแต่ในคิว -> ไม่แตะ
    [EN] Matches the existing queue (values incl. the header) against the items built from the legacy sheet:
         * first the rows with the same (Doc No, Date)
         * an item that is still unmatched is tried again by (Date, Discipline, cell value): the same item whose Doc No only differs because
           column A (No.) of the legacy sheet changed. That row gets the new Doc No (column A), even when it is Done
           (its status and the other columns follow the rules below)
         * still no match -> added as a new row (Waiting)
         * Done -> nothing is touched except the Doc No (even if the legacy link differs)
         * anything else (Waiting / Error ...) -> the status (column G) is kept, but columns B..F (Date, Discipline, PDF URL, Folder URL, C/P)
           are refreshed from the legacy sheet
         * rows that exist only in the queue are not touched
    """
    index: dict[tuple[str, str], tuple[int, list[str]]] = {}
    by_identity: dict[tuple[str, str], list[tuple[int, list[str]]]] = {}        # (Date, Discipline) -> rows, in sheet order
    for number, row in enumerate(existing[1:], start=2):
        cells = [str(c).strip() for c in row][:len(QUEUE_HEADER)]
        cells += [""] * (len(QUEUE_HEADER) - len(cells))
        if cells[0] and (cells[0], cells[1]) not in index:
            index[(cells[0], cells[1])] = (number, cells)
            by_identity.setdefault((cells[1], cells[2]), []).append((number, cells))

    plan = MergePlan()
    claimed: set[int] = set()

    def settle(it: LegacyItem, number: int, cells: list[str], renamed: bool) -> None:
        wanted = it.to_row()[1:6]
        if cells[6] == "Done":
            plan.kept_done += 1
            if it.pdf_url and cells[3] != it.pdf_url:
                plan.done_link_differs += 1
        elif cells[1:6] != wanted:
            plan.updates[number] = wanted
        elif not renamed:
            plan.unchanged += 1

    pending: list[LegacyItem] = []
    for it in items:
        hit = index.get((it.doc_no, it.date))
        if hit is None:
            pending.append(it)
            continue
        claimed.add(hit[0])
        settle(it, hit[0], hit[1], False)

    for it in pending:
        suffix = f"-{it.discipline}-{it.key_text}"
        found = None
        if it.key_text:
            found = next(((n, c) for n, c in by_identity.get((it.date, it.discipline), []) if n not in claimed and c[0].endswith(suffix)), None)
        if found is None:
            plan.new_items.append(it)
            continue
        number, cells = found
        claimed.add(number)
        plan.renamed[number] = it.doc_no
        plan.old_names[number] = cells[0]
        settle(it, number, cells, True)

    plan.missing = len(index) - len(claimed)
    return plan


def apply_merge(sess: Any, sid: str, tab: str, plan: MergePlan) -> None:
    """
    갱신은 A열(바뀐 Doc No)과 B~F 열만 values:batchUpdate 로 (G열 상태는 건드리지 않음), 새 행은 append 로 (시트가 자동 확장됨)
    [TH] อัปเดตเฉพาะคอลัมน์ A (Doc No ที่เปลี่ยน) และ B~F ด้วย values:batchUpdate (ไม่แตะสถานะคอลัมน์ G) แถวใหม่เพิ่มด้วย append (ชีตขยายให้เอง)
    [EN] Updates go to column A (a changed Doc No) and columns B..F only, through values:batchUpdate (the status in column G is never touched);
         new rows go in with append (the sheet grows by itself)
    """
    quoted = tab.replace("'", "''")
    writes: list[dict] = []
    for row in sorted(set(plan.updates) | set(plan.renamed)):
        if row in plan.renamed:
            writes.append({"range": f"'{quoted}'!A{row}", "values": [[plan.renamed[row]]]})
        if row in plan.updates:
            writes.append({"range": f"'{quoted}'!B{row}:F{row}", "values": [plan.updates[row]]})
    for i in range(0, len(writes), EXTRACT_UPDATES_PER_WRITE):
        sheets_call(sess, "POST", f"{SHEETS_BASE}/{sid}/values:batchUpdate",
                    json={"valueInputOption": "RAW", "data": writes[i:i + EXTRACT_UPDATES_PER_WRITE]})
    rows = [it.to_row() for it in plan.new_items]
    for i in range(0, len(rows), EXTRACT_ROWS_PER_WRITE):
        append_rows(sess, sid, tab, rows[i:i + EXTRACT_ROWS_PER_WRITE], value_input="RAW")
