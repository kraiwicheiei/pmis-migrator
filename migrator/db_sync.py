"""
PMIS 의 DB(DB_DWR / DB_DRQ)를 큐(= 옛 시트와 같은 내용)에 맞추기 (`sync db` 의 AI 를 쓰지 않는 부분): C/P 와 Folder Link 갱신, "Legacy sync" 표시(Synced / Outdated),
옛 시트와 맞춘 적이 있는데 큐에서 사라진 PDF 의 행 삭제. 바뀐 PDF 의 행을 제자리에서 교체하는 쓰기도 여기 있음 (AI 부분이 사용)
[TH] ทำให้ DB ของ PMIS (DB_DWR / DB_DRQ) ตรงกับคิว (= เนื้อหาเดียวกับชีตเก่า) (ส่วนของ `sync db` ที่ไม่ใช้ AI): อัปเดต C/P และ Folder Link, ติดป้าย "Legacy sync" (Synced / Outdated),
     ลบแถวที่เคย sync กับชีตเก่าแต่ PDF หายไปจากคิว และมีการเขียนแทนที่แถวของ PDF ที่เปลี่ยนในที่เดิมไว้ที่นี่ด้วย (ส่วน AI เรียกใช้)
[EN] Making the PMIS DB (DB_DWR / DB_DRQ) match the queue (= the same content as the legacy sheet) (the part of `sync db` that does not use the AI): updating C/P and Folder Link,
     the "Legacy sync" tag (Synced / Outdated), and removing the rows that were synced with the legacy sheet but whose PDF left the queue.
     The write that replaces the row of a changed PDF in place lives here too (the AI part uses it)
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .config import SHEETS_BASE
from .db_columns import apply_column_updates, ensure_cp_header, ensure_sync_header
from .models import Clients
from .sheets import SheetsError, append_rows, list_tabs, read_values, sheets_call, values_url
from .specs import (DB_CP_HEADER, DB_OUTDATED_VALUE, DB_STATUS_LEGACY_VALUE, DB_STATUS_VALUE, DB_SYNC_HEADER, DB_SYNC_VALUE, QueueSpec, SPECS)
from .stamps import time_stamp
from .utils import col_letter, count_lines, extract_file_id, removal_limit

# 이 도구가 쓴 행의 Status 값. 이 값이 아닌 행(사람이 넣은 행 등)은 절대 삭제하지 않음
# [TH] ค่า Status ของแถวที่เครื่องมือนี้เขียน แถวที่ Status เป็นค่าอื่น (เช่นแถวที่คนใส่เอง) จะไม่ถูกลบเด็ดขาด
# [EN] The Status value of the rows this tool wrote. A row with any other Status (e.g. one a person added) is never removed
MIGRATED_STATUS = DB_STATUS_VALUE
# 예전 이름("AI Migrated")의 행도 이 도구가 쓴 행으로 인정 (`sync db` 가 새 이름으로 바꿔 줌)
# [TH] แถวที่ใช้ชื่อเก่า ("AI Migrated") ก็ถือว่าเป็นแถวที่เครื่องมือนี้เขียน (`sync db` จะเปลี่ยนเป็นชื่อใหม่ให้)
# [EN] Rows with the old name ("AI Migrated") also count as written by this tool (`sync db` renames them to the new one)
MIGRATED_STATUSES = (DB_STATUS_VALUE, DB_STATUS_LEGACY_VALUE)
DB_CELLS_PER_WRITE = 500
DELETES_PER_REQUEST = 200
ARCHIVE_ROWS_PER_WRITE = 2000


def db_status_col(spec: QueueSpec) -> int:
    """DB 에서 Status(AI Synced) 열의 인덱스 / [TH] index ของคอลัมน์ Status (AI Synced) ใน DB / [EN] The index of the Status (AI Synced) column of the DB"""
    for i, col in enumerate(spec.columns):
        if col == ("const", MIGRATED_STATUS):
            return i
    return -1


def archive_tab_name(spec: QueueSpec) -> str:
    """삭제한 행을 보관하는 탭 이름 (DB 마다 하나) / [TH] ชื่อแท็บที่เก็บแถวที่ลบ (หนึ่งแท็บต่อ DB) / [EN] The tab that keeps the removed rows (one per DB)"""
    return f"_REMOVED_{spec.db_tab}"


# ---------------------------------------------------------------------------
# 바뀐 PDF 의 DB 행을 제자리에서 교체 (AI 부분이 사용)
# [TH] แทนที่แถวใน DB ของ PDF ที่เปลี่ยนในที่เดิม (ส่วน AI ใช้)
# [EN] Replacing the DB row of a changed PDF in place (used by the AI part)
# ---------------------------------------------------------------------------
def replacement_ranges(spec: QueueSpec, row_number: int, row: list) -> list[dict]:
    """
    DB 행을 덮어쓸 범위들. A열(CSC Ref. No)과 값이 늘 비어 있는 상수 열(Ext1 / Ext2 등)은 건드리지 않음
    [TH] ช่วงที่ใช้เขียนทับแถวใน DB ไม่แตะคอลัมน์ A (CSC Ref. No) และคอลัมน์คงที่ที่ว่างเสมอ (เช่น Ext1 / Ext2)
    [EN] The ranges that overwrite a DB row. Column A (CSC Ref. No) and the always-empty constant columns (Ext1 / Ext2, ...) are not touched
    """
    quoted = spec.db_tab.replace("'", "''")
    ranges: list[dict] = []
    start: Optional[int] = None
    for i in range(1, len(spec.columns) + 1):
        skip = i >= len(spec.columns) or spec.columns[i] == ("const", "")
        if skip:
            if start is not None:
                ranges.append({"range": f"'{quoted}'!{col_letter(start + 1)}{row_number}:{col_letter(i)}{row_number}", "values": [row[start:i]]})
                start = None
        elif start is None:
            start = i
    return ranges


def replace_db_rows(sess: Any, sid: str, spec: QueueSpec, items: list[tuple[int, list]]) -> None:
    """
    (DB 행 번호, 새 행 값) 들을 제자리에 씀. 추가(append)와 같은 USER_ENTERED 로 써서 날짜 등이 같은 모양이 되게 함
    [TH] เขียน (เลขแถว DB, ค่าของแถวใหม่) ในที่เดิม ใช้ USER_ENTERED เหมือนการต่อท้าย (append) เพื่อให้วันที่ ฯลฯ ออกมารูปแบบเดียวกัน
    [EN] Writes (DB row number, new row values) in place, with the same USER_ENTERED as an append so that dates etc. come out the same way
    """
    writes: list[dict] = []
    for number, row in items:
        writes.extend(replacement_ranges(spec, number, row))
    for i in range(0, len(writes), DB_CELLS_PER_WRITE):
        sheets_call(sess, "POST", f"{SHEETS_BASE}/{sid}/values:batchUpdate",
                    json={"valueInputOption": "USER_ENTERED", "data": writes[i:i + DB_CELLS_PER_WRITE]})


# ---------------------------------------------------------------------------
# DB 를 큐에 맞추기
# [TH] ทำให้ DB ตรงกับคิว
# [EN] Making the DB match the queue
# ---------------------------------------------------------------------------
@dataclass
class DbSyncPlan:
    rows: int = 0                                                    # 살펴본 DB 데이터 행 / [TH] แถวข้อมูล DB ที่ตรวจ / [EN] DB data rows looked at
    cp_updates: dict[int, str] = field(default_factory=dict)         # DB 행 번호 -> 큐의 C/P / [TH] เลขแถว DB -> C/P ของคิว / [EN] DB row number -> the queue's C/P
    folder_updates: dict[int, str] = field(default_factory=dict)     # DB 행 번호 -> 큐의 Folder URL / [TH] เลขแถว DB -> Folder URL ของคิว / [EN] DB row number -> the queue's Folder URL
    # DB 행 번호 -> 새 "Legacy sync" 값 ("Synced" / "Outdated" / 표시를 지우는 ""). 지금 값과 다른 행만
    # [TH] เลขแถว DB -> ค่าใหม่ของ "Legacy sync" ("Synced" / "Outdated" / "" คือล้างป้าย) เฉพาะแถวที่ต่างจากค่าปัจจุบัน
    # [EN] DB row number -> the new "Legacy sync" value ("Synced" / "Outdated" / "" to clear); only the rows whose value differs from the current one
    tag_updates: dict[int, str] = field(default_factory=dict)
    # DB 행 번호 -> 새 Status 값. 예전 이름("AI Migrated")의 행을 새 이름("AI Synced")으로 바꿈
    # [TH] เลขแถว DB -> ค่า Status ใหม่ เปลี่ยนแถวที่ใช้ชื่อเก่า ("AI Migrated") เป็นชื่อใหม่ ("AI Synced")
    # [EN] DB row number -> the new Status value. Renames the rows that still carry the old name ("AI Migrated") to the new one ("AI Synced")
    status_updates: dict[int, str] = field(default_factory=dict)
    removals: dict[int, str] = field(default_factory=dict)           # DB 행 번호 -> 삭제 이유 / [TH] เลขแถว DB -> เหตุผลที่ลบ / [EN] DB row number -> why it is removed
    synced: int = 0            # 이번 실행 뒤 "Synced" 인 행 (이미 붙어 있는 행 포함) / [TH] แถวที่เป็น "Synced" หลังรอบนี้ (รวมแถวที่ติดอยู่แล้ว) / [EN] rows that are "Synced" after this run (including the ones that already are)
    outdated: int = 0          # 이번 실행 뒤 "Outdated" 인 행 / [TH] แถวที่เป็น "Outdated" หลังรอบนี้ / [EN] rows that are "Outdated" after this run
    pending: int = 0           # 그중 바뀐 PDF 의 교체를 기다리는 행 (PDF 가 큐의 H열에 있음) / [TH] ในนั้นแถวที่รอการแทนที่จาก PDF ที่เปลี่ยน (PDF อยู่ในคอลัมน์ H ของคิว) / [EN] of those, rows that wait to be replaced by a changed PDF (their PDF is in column H of the queue)
    protected: int = 0         # 큐에 없지만 이 도구가 쓴 행이 아니라 삭제하지 않는 행 / [TH] แถวที่ไม่อยู่ในคิวแต่ไม่ใช่แถวที่เครื่องมือนี้เขียน จึงไม่ลบ / [EN] rows not in the queue that this tool did not write, so they are not removed
    unsynced: int = 0          # 큐에 없지만 옛 시트와 맞춘 적이 없어(표시 없음) 남겨 두는 행 / [TH] แถวที่ไม่อยู่ในคิวแต่ไม่เคย sync กับชีตเก่า (ไม่มีป้าย) จึงเก็บไว้ / [EN] rows not in the queue that were never synced with the legacy sheet (no tag), so they are kept
    ambiguous: int = 0         # 큐에서 같은 PDF 의 C/P 또는 Folder URL 이 서로 다른 행 (정할 수 없어 "Outdated") / [TH] แถวที่ PDF เดียวกันในคิวมี C/P หรือ Folder URL ไม่ตรงกัน (ตัดสินไม่ได้ จึงเป็น "Outdated") / [EN] rows whose PDF has different C/P or Folder URL values in the queue (cannot be decided, so "Outdated")
    no_pdf: int = 0            # PDF 링크가 없는 행 (판단할 수 없어 건드리지 않음) / [TH] แถวที่ไม่มีลิงก์ PDF (ตัดสินไม่ได้ จึงไม่แตะ) / [EN] rows without a PDF link (cannot be judged, so they are left alone)
    # 쓰거나 지울 DB 행 번호 -> 그 행의 PDF file ID (PDF 가 없는 행은 ""). 쓰기 직전에 DB 가 그대로인지 확인하는 데 사용 (그사이 행이 끼어들어 번호가 밀렸으면 중단)
    # [TH] เลขแถว DB ที่จะเขียนหรือลบ -> file ID ของ PDF ในแถวนั้น ใช้เช็กก่อนเขียนว่า DB ยังเหมือนเดิม (ถ้ามีแถวแทรกเข้ามาจนเลขเลื่อนจะหยุด)
    # [EN] DB row number to be written or removed -> the PDF file ID of that row. Used right before writing to check that the DB is unchanged (stop if a row was inserted and the numbers shifted)
    touched: dict[int, str] = field(default_factory=dict)


def same_link(a: str, b: str) -> bool:
    """
    두 링크가 같은지: 글자가 같거나 같은 Drive ID 를 가리킴 (링크 형식만 다른 경우는 같은 것으로 봄)
    [TH] ลิงก์สองอันเหมือนกันหรือไม่: ข้อความเหมือนกัน หรือชี้ Drive ID เดียวกัน (ต่างกันแค่รูปแบบลิงก์ถือว่าเหมือนกัน)
    [EN] Whether two links are the same: the same text, or the same Drive ID (links that differ only in their format count as the same)
    """
    if a == b:
        return True
    ident = extract_file_id(a)
    return bool(ident) and ident == extract_file_id(b)


def plan_db_sync(queue_rows: list[list], db_values: list[list], spec: QueueSpec) -> DbSyncPlan:
    """
    큐의 PDF(Drive file ID)를 기준으로 DB 행을 판단:
      * PDF 가 큐에 있으면 남기고, 큐의 C/P 나 Folder URL 이 하나뿐이고 DB 와 다르면 그 칸을 갱신 (큐의 값이 비었으면 DB 값을 지우지 않음).
        "Synced" 는 PDF, C/P, Folder Link 가 큐와 맞는 것을 확인한 행에만 붙임. 큐에서 같은 PDF 의 C/P 나 Folder URL 이 여러 값이면(같은 파일을 여러 항목이 씀) DB 값이 그중 하나면 맞는 것이고,
        어느 것도 아니면 정할 수 없으므로 "Outdated" (값을 추측해 쓰지 않음)
      * 큐 H열(바뀌기 전 PDF)에 있는 PDF 는 AI 가 교체하기를 기다리므로 남기고 "Outdated"
      * 그 밖에 PDF 가 큐에 없으면 삭제 대상. 단 Status 가 "AI Synced"(또는 예전 이름 "AI Migrated") 이고 "Legacy sync" 가 "Synced" 또는 "Outdated" 인 행만 (= 옛 시트에서 온 행인데 옛 시트에서 사라진 행).
        옛 시트에서 온 적이 없는 행(표시 없음, 예: 옛 시트와 무관하게 다른 시스템이 넣은 문서)과 사람이 넣은 행은 남김
      * 표시가 없어야 하는 행: 큐에 없는 행(고아), 지우지 않는 행, PDF 링크가 없는 행 (지금과 다른 행만 계획에 넣음. 지워질 행은 표시를 건드리지 않음)
      * PDF 링크가 없는 행은 그 밖에는 건드리지 않음
    [TH] ตัดสินแถวใน DB ด้วย PDF (Drive file ID) ของคิว:
         * ถ้า PDF อยู่ในคิวก็เก็บไว้ และถ้า C/P หรือ Folder URL ของคิวมีค่าเดียวและต่างจาก DB จะอัปเดตช่องนั้น (ถ้าค่าในคิวว่างจะไม่ล้างค่าใน DB)
           "Synced" ติดให้เฉพาะแถวที่ตรวจแล้วว่า PDF, C/P, Folder Link ตรงกับคิว ถ้า PDF เดียวกันมี C/P หรือ Folder URL หลายค่าในคิว (ไฟล์เดียวกันถูกใช้ในหลายรายการ)
           ค่าใน DB เป็นหนึ่งในนั้นก็ถือว่าตรง ถ้าไม่ตรงกับค่าไหนเลยจะตัดสินไม่ได้ จึงเป็น "Outdated" (ไม่เดาแล้วเขียนทับ)
         * PDF ที่อยู่ในคอลัมน์ H ของคิว (PDF ก่อนเปลี่ยน) รอ AI แทนที่ จึงเก็บไว้และเป็น "Outdated"
         * นอกจากนั้นถ้า PDF ไม่อยู่ในคิวจะเป็นแถวที่จะลบ แต่เฉพาะแถวที่ Status เป็น "AI Synced" (หรือชื่อเก่า "AI Migrated") และ "Legacy sync" เป็น "Synced" หรือ "Outdated" (= เป็นแถวจากชีตเก่าที่หายไปจากชีตเก่า)
           แถวที่ไม่เคยมาจากชีตเก่า (ไม่มีป้าย เช่นเอกสารที่ระบบอื่นเพิ่มโดยไม่เกี่ยวกับชีตเก่า) และแถวที่คนใส่เองจะถูกเก็บไว้
         * แถวที่ต้องไม่มีป้าย: แถวที่ไม่อยู่ในคิว (แถวกำพร้า), แถวที่ไม่ลบ และแถวที่ไม่มีลิงก์ PDF (ใส่ในแผนเฉพาะแถวที่ต่างจากตอนนี้ แถวที่จะถูกลบไม่แตะป้าย)
         * แถวที่ไม่มีลิงก์ PDF นอกนั้นไม่แตะ
    [EN] Judges the DB rows by the PDF (Drive file ID) of the queue:
         * a PDF that is in the queue is kept, and when the queue has exactly one C/P (or Folder URL) for it and it differs from the DB, that cell is updated
           (an empty value in the queue never clears the DB value). "Synced" goes only on a row whose PDF, C/P and Folder Link were checked against the queue;
           when the queue has several C/P or Folder URL values for the same PDF (several items use the same file) the DB value is fine if it is one of them,
           and when it is none of them it cannot be decided, so the row is "Outdated" (the value is not guessed and overwritten)
         * a PDF listed in column H of the queue (the PDF before a change) waits for the AI to replace it, so it is kept and "Outdated"
         * any other PDF that is not in the queue is to be removed, but only a row whose Status is "AI Synced" (or the old name "AI Migrated") and whose "Legacy sync" is "Synced" or "Outdated"
           (= it came from the legacy sheet and then left it). A row that never came from the legacy sheet (no tag, e.g. a document another system added that
           has nothing to do with the legacy sheet) and a row a person added are kept
         * the rows that must have no tag: a row that is not in the queue (an orphan), a row that is not removed and a row without a PDF link
           (only the rows that differ from now go into the plan; a row that is about to be removed is not tagged)
         * a row without a PDF link is not touched otherwise
    """
    cps: dict[str, set[str]] = {}
    folders: dict[str, set[str]] = {}
    in_queue: set[str] = set()
    waiting: set[str] = set()
    for row in queue_rows:
        cells = [str(c).strip() for c in row]
        cells += [""] * (8 - len(cells))
        fid = extract_file_id(cells[3])
        if fid:
            in_queue.add(fid)
            if cells[5]:
                cps.setdefault(fid, set()).add(cells[5])
            if cells[4]:
                folders.setdefault(fid, set()).add(cells[4])
        old = extract_file_id(cells[7]) if cells[7] else None
        if old:
            waiting.add(old)

    status_col = db_status_col(spec)
    width = len(spec.columns)
    plan = DbSyncPlan()
    for number, row in enumerate(db_values[1:], start=2):
        cells = [str(c).strip() for c in row]
        cells += [""] * (width - len(cells))
        if not any(cells):
            continue
        plan.rows += 1
        fid = extract_file_id(cells[spec.db_pdf_col])
        tag, now = "", cells[spec.db_sync_col]
        if status_col >= 0 and cells[status_col] == DB_STATUS_LEGACY_VALUE:
            plan.status_updates[number] = MIGRATED_STATUS                            # an old-name row: rename it (dropped again below when the row is about to be removed)
            plan.touched[number] = fid or ""
        if not fid:
            plan.no_pdf += 1
        elif fid in in_queue:
            tag, contradicts = DB_SYNC_VALUE, False
            for found, col, same, updates in ((cps.get(fid), spec.db_cp_col, lambda a, b: a == b, plan.cp_updates),
                                              (folders.get(fid), spec.db_folder_col, same_link, plan.folder_updates)):
                if not found:
                    continue
                if len(found) > 1:                                                    # the queue has several values for this PDF (the same file is used by several items)
                    if not any(same(cells[col], value) for value in found):          # the DB has one of them: fine. It has none: cannot be decided
                        contradicts = True
                    continue
                value = next(iter(found))
                if not same(cells[col], value):
                    updates[number] = value
                    plan.touched[number] = fid
            if contradicts:
                tag = DB_OUTDATED_VALUE
                plan.ambiguous += 1
        elif fid in waiting:
            plan.pending += 1
            tag = DB_OUTDATED_VALUE                                                   # the old version waits for the AI to replace it: it came from the legacy sheet but does not match it now
        elif status_col < 0 or cells[status_col] not in MIGRATED_STATUSES:
            plan.protected += 1
        elif now not in (DB_SYNC_VALUE, DB_OUTDATED_VALUE):
            plan.unsynced += 1                                                        # never came from the legacy sheet (e.g. added by another system): kept as it is
        else:
            plan.removals[number] = f"it came from the legacy sheet, but its PDF ({fid}) is not in {spec.queue_tab} any more"
            plan.touched[number] = fid
            plan.status_updates.pop(number, None)
            continue                                                                  # about to be removed: its tag does not matter
        if tag == DB_SYNC_VALUE:
            plan.synced += 1
        elif tag == DB_OUTDATED_VALUE:
            plan.outdated += 1
        if now != tag:
            plan.tag_updates[number] = tag
            plan.touched[number] = fid or ""
    return plan


def verify_unchanged(sess: Any, sid: str, spec: QueueSpec, touched: dict[int, str]) -> None:
    """
    쓰거나 지우기 직전에 DB 의 PDF 열을 다시 읽어, 계획한 행들이 아직 같은 PDF 를 가지고 있는지 확인. 다르면(그사이 행이 추가/삭제됨) 아무것도 하지 않고 중단
    [TH] ก่อนเขียนหรือลบ ให้อ่านคอลัมน์ PDF ของ DB อีกครั้งเพื่อเช็กว่าแถวที่วางแผนไว้ยังมี PDF เดิมอยู่ ถ้าไม่ตรง (มีแถวเพิ่ม/ลบระหว่างนั้น) จะหยุดโดยไม่ทำอะไร
    [EN] Right before writing or removing, reads the PDF column of the DB again and checks that the planned rows still hold the same PDF.
         If not (a row was added or removed in between) nothing is done and the run stops
    """
    col = col_letter(spec.db_pdf_col + 1)
    now = read_values(sess, sid, f"'{spec.db_tab}'!{col}2:{col}")
    for number, fid in touched.items():
        index = number - 2
        current = extract_file_id(now[index][0]) if index < len(now) and now[index] else None
        if (current or "") != fid:
            raise SheetsError(f"{spec.db_tab} changed while it was being synced (row {number} no longer holds the PDF that was planned) - nothing was changed; run it again")


def archive_removed(sess: Any, sid: str, spec: QueueSpec, db_values: list[list], removals: dict[int, str], tabs: dict[str, tuple[int, int]]) -> str:
    """
    삭제하기 전에 행을 보관 탭(_REMOVED_<DB 탭>)에 복사: 시각, 원래 탭, 원래 행 번호, 이유 + 원래 셀들. 탭이 없으면 만들고 머리글을 씀
    [TH] ก่อนลบจะคัดลอกแถวไปเก็บในแท็บ _REMOVED_<แท็บ DB>: เวลา, แท็บเดิม, เลขแถวเดิม, เหตุผล + เซลล์เดิม ถ้าไม่มีแท็บจะสร้างและเขียนหัวตาราง
    [EN] Copies the rows to the archive tab (_REMOVED_<DB tab>) before they are removed: the time, the original tab, the original row number, the reason
         + the original cells. The tab is created (with a header) when it is missing
    """
    name = archive_tab_name(spec)
    width = len(spec.columns)
    meta = ["Removed at", "From tab", "Row (before the removal)", "Reason"]
    if name not in tabs:
        sheets_call(sess, "POST", f"{SHEETS_BASE}/{sid}:batchUpdate", json={"requests": [{"addSheet": {"properties": {"title": name}}}]})
        header = [str(c) for c in (db_values[0] if db_values else [])]
        header += [""] * (width - len(header))
        quoted = name.replace("'", "''")
        sheets_call(sess, "PUT", values_url(sid, f"'{quoted}'!A1:{col_letter(len(meta) + width)}1"), params={"valueInputOption": "RAW"},
                    json={"majorDimension": "ROWS", "values": [meta + header[:width]]})
    stamp = time_stamp()
    rows = []
    for number in sorted(removals):
        cells = [str(c) for c in db_values[number - 1]]
        cells += [""] * (width - len(cells))
        rows.append([stamp, spec.db_tab, number, removals[number]] + cells[:width])
    for i in range(0, len(rows), ARCHIVE_ROWS_PER_WRITE):
        append_rows(sess, sid, name, rows[i:i + ARCHIVE_ROWS_PER_WRITE], value_input="RAW")
    return name


def delete_rows(sess: Any, sid: str, sheet_id: int, numbers: list[int]) -> None:
    """
    행 삭제 (아래쪽부터 지워 위쪽 행 번호가 밀리지 않게 함). 이어진 행은 한 요청으로 묶음
    [TH] ลบแถว (ลบจากล่างขึ้นบนเพื่อไม่ให้เลขแถวด้านบนเลื่อน) แถวที่ติดกันรวมเป็นคำขอเดียว
    [EN] Deletes rows (from the bottom up so that the row numbers above do not shift); consecutive rows go in one request
    """
    groups: list[list[int]] = []
    for n in sorted(numbers, reverse=True):
        if groups and groups[-1][-1] == n + 1:
            groups[-1].append(n)
        else:
            groups.append([n])
    requests = [{"deleteDimension": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": g[-1] - 1, "endIndex": g[0]}}} for g in groups]
    for i in range(0, len(requests), DELETES_PER_REQUEST):
        sheets_call(sess, "POST", f"{SHEETS_BASE}/{sid}:batchUpdate", json={"requests": requests[i:i + DELETES_PER_REQUEST]})


@dataclass
class DbSyncSummary:
    plan: DbSyncPlan = field(default_factory=DbSyncPlan)
    cp_updated: int = 0
    folder_updated: int = 0    # Folder Link 를 갱신한 행 / [TH] แถวที่อัปเดต Folder Link / [EN] rows whose Folder Link was updated
    status_renamed: int = 0    # Status 를 "AI Migrated" 에서 "AI Synced" 로 바꾼 행 / [TH] แถวที่เปลี่ยน Status จาก "AI Migrated" เป็น "AI Synced" / [EN] rows whose Status was renamed from "AI Migrated" to "AI Synced"
    tagged: int = 0            # "Synced" 를 새로 붙인 행 / [TH] แถวที่ติด "Synced" ใหม่ / [EN] rows that got the "Synced" tag
    outdated: int = 0          # "Outdated" 를 새로 붙인 행 / [TH] แถวที่ติด "Outdated" ใหม่ / [EN] rows that got the "Outdated" tag
    untagged: int = 0          # 표시를 지운 행 / [TH] แถวที่ล้างป้าย / [EN] rows whose tag was cleared
    removed: int = 0
    refused: str = ""


def sync_db_for_queue(args: argparse.Namespace, clients: Clients, key: str, queue_rows: list[list], log: Callable[[str], None] = print) -> DbSyncSummary:
    """
    한 큐의 DB 탭을 큐 행(queue_rows, 머리글 제외)에 맞춤 (AI 를 쓰지 않음). --dry-run 이면 계획만 보여 줌
    안전장치: 큐가 비었거나 지울 행이 한도(전체의 10% 와 100행 중 큰 값)를 넘으면 DB 에 아무것도 쓰지 않음 (--allow-large-removal 로 허용)
    [TH] ทำให้แท็บ DB ของคิวหนึ่งตรงกับแถวของคิว (queue_rows ไม่รวมหัวตาราง) โดยไม่ใช้ AI ถ้าเป็น --dry-run จะแสดงแค่แผน
         ตัวกันพลาด: ถ้าคิวว่างหรือแถวที่จะลบเกินเพดาน (ค่าที่มากกว่าระหว่าง 10% ของทั้งหมดกับ 100 แถว) จะไม่เขียนอะไรลง DB (อนุญาตได้ด้วย --allow-large-removal)
    [EN] Makes the DB tab of one queue match the queue rows (queue_rows, without the header), without the AI. With --dry-run it only shows the plan.
         Safety: when the queue is empty or the rows to remove exceed the limit (the larger of 10% of all rows and 100), nothing is written to the DB
         (allow it with --allow-large-removal)
    """
    spec = SPECS[key]
    sess, sid = clients.sheets, args.spreadsheet_id
    last_col = col_letter(len(spec.columns))
    status_col = db_status_col(spec)
    db_values = read_values(sess, sid, f"'{spec.db_tab}'!A1:{last_col}")
    plan = plan_db_sync(queue_rows, db_values, spec)
    summary = DbSyncSummary(plan=plan)
    limit = removal_limit(plan.rows)
    to_synced = {n: v for n, v in plan.tag_updates.items() if v == DB_SYNC_VALUE}
    to_outdated = {n: v for n, v in plan.tag_updates.items() if v == DB_OUTDATED_VALUE}
    to_clear = {n: v for n, v in plan.tag_updates.items() if not v}
    table = [("In sync with the queue", str(plan.synced), ""),
             ("C/P to update", str(len(plan.cp_updates)), ""),
             ("Folder Link to update", str(len(plan.folder_updates)), ""),
             (f"\"{DB_SYNC_HEADER}\" tag", f"set {DB_SYNC_VALUE} {len(to_synced)}, set {DB_OUTDATED_VALUE} {len(to_outdated)}, clear {len(to_clear)}", ""),
             ("Waiting for the AI (Outdated)", str(plan.pending), "(their PDF changed; the AI part replaces them)"),
             ("To remove", str(len(plan.removals)), "(came from the legacy sheet, PDF not in the queue any more)"),
             ("Kept as they are", str(plan.unsynced + plan.protected), "")]
    if plan.status_updates:
        table.insert(3, ("Status to rename", str(len(plan.status_updates)), f"({DB_STATUS_LEGACY_VALUE} -> {MIGRATED_STATUS})"))
    table += [(f"  {label}", str(n), "") for label, n in (("never synced with the legacy sheet", plan.unsynced), (f"Status is not '{MIGRATED_STATUS}'", plan.protected)) if n]
    if plan.ambiguous:
        table.append(("Outdated: C/P or Folder Link contradicts itself in the queue", str(plan.ambiguous), ""))
    log(f"[{spec.name}] Sync {spec.db_tab}: {plan.rows} rows\n{count_lines(table)}")

    if not any(str(c).strip() for row in queue_rows for c in row[:1]):
        summary.refused = "empty queue"
        log(f"  ✗ Not syncing {spec.db_tab}: the queue has no rows, so every DB row would look removed")
        return summary
    if len(plan.removals) > limit and not getattr(args, "allow_large_removal", False):
        summary.refused = "too many removals"
        log(f"  ✗ Not syncing {spec.db_tab}: {len(plan.removals)} rows would be removed, more than the limit of {limit} (10% of the DB, at least 100). "
            f"Check the legacy sheet, then add --allow-large-removal if this is right")
        return summary

    if args.dry_run:
        for number in sorted(plan.removals)[:5]:
            log(f"  sample (remove): DB row {number}: {str(db_values[number - 1][1]) if len(db_values[number - 1]) > 1 else ''} - {plan.removals[number]}")
        log(f"  (--dry-run) nothing was written to {spec.db_tab}: a real run would do the counts above")
        return summary

    if plan.touched:
        verify_unchanged(sess, sid, spec, plan.touched)
    # 데이터를 쓰기 전에 필요한 머리글을 모두 확인 (다른 머리글이 있으면 아무것도 쓰기 전에 여기서 중단)
    # [TH] เช็กหัวตารางที่ต้องใช้ให้ครบก่อนเขียนข้อมูล (ถ้ามีหัวอื่นจะหยุดตรงนี้ก่อนเขียนอะไร)
    # [EN] All the headers that are needed are checked before any data is written (a different header stops the run here, before anything is written)
    headers = []
    if plan.cp_updates:
        headers.append((DB_CP_HEADER, spec.db_cp_col, ensure_cp_header))
    if plan.tag_updates:
        headers.append((DB_SYNC_HEADER, spec.db_sync_col, ensure_sync_header))
    for header, index, ensure in headers:
        if ensure(sess, sid, spec) == "written":
            log(f"  ✓ Wrote the header '{header}' to {spec.db_tab}!{col_letter(index + 1)}1")
    if plan.cp_updates:
        apply_column_updates(sess, sid, spec, spec.db_cp_col, plan.cp_updates)
        summary.cp_updated = len(plan.cp_updates)
        log(f"  ✓ Updated C/P in {spec.db_tab}!{col_letter(spec.db_cp_col + 1)}: {summary.cp_updated} rows")
    if plan.folder_updates:
        apply_column_updates(sess, sid, spec, spec.db_folder_col, plan.folder_updates, value_input="USER_ENTERED")      # the same input mode as the rows the AI part appends
        summary.folder_updated = len(plan.folder_updates)
        log(f"  ✓ Updated Folder Link in {spec.db_tab}!{col_letter(spec.db_folder_col + 1)}: {summary.folder_updated} rows")
    if plan.tag_updates:
        apply_column_updates(sess, sid, spec, spec.db_sync_col, plan.tag_updates)
        summary.tagged, summary.outdated, summary.untagged = len(to_synced), len(to_outdated), len(to_clear)
        log(f"  ✓ Legacy sync tag in {spec.db_tab}!{col_letter(spec.db_sync_col + 1)}: {DB_SYNC_VALUE} on {summary.tagged} rows, {DB_OUTDATED_VALUE} on {summary.outdated} rows, cleared on {summary.untagged} rows")
    if plan.status_updates:
        apply_column_updates(sess, sid, spec, status_col, plan.status_updates)
        summary.status_renamed = len(plan.status_updates)
        log(f"  ✓ Renamed Status in {spec.db_tab}!{col_letter(status_col + 1)}: {summary.status_renamed} rows ('{DB_STATUS_LEGACY_VALUE}' -> '{MIGRATED_STATUS}')")
    if plan.removals:
        tabs = list_tabs(sess, sid)
        verify_unchanged(sess, sid, spec, {n: plan.touched[n] for n in plan.removals})
        archived = archive_removed(sess, sid, spec, db_values, plan.removals, tabs)
        delete_rows(sess, sid, tabs[spec.db_tab][0], list(plan.removals))
        summary.removed = len(plan.removals)
        log(f"  ✓ Removed {summary.removed} rows from {spec.db_tab} (copied to {archived} first)")
    if not plan.cp_updates and not plan.folder_updates and not plan.tag_updates and not plan.status_updates and not plan.removals:
        log(f"  ✓ {spec.db_tab} already matches the queue")
    return summary


def run_db_sync(args: argparse.Namespace, clients: Clients, keys: list[str], log: Callable[[str], None] = print, sheet_title: str = "",
                planned: Optional[dict[str, list[list]]] = None) -> int:
    """
    큐별로 `sync db` 의 AI 를 쓰지 않는 부분을 실행 (큐 시트를 읽어 그 내용에 DB 를 맞춤). planned 에 있는 큐는 시트 대신 그 행(--dry-run 에서 `sync queue` 가 계획한 행)을 사용.
    종료 코드: 0 = 정상, 2 = 읽거나 쓰지 못했거나 안전장치로 거부한 큐가 있음
    [TH] รันส่วนของ `sync db` ที่ไม่ใช้ AI ทีละคิว (อ่านชีตคิวแล้วทำให้ DB ตรงกับเนื้อหานั้น) คิวที่อยู่ใน planned จะใช้แถวนั้น (แถวที่ `sync queue` วางแผนไว้ตอน --dry-run) แทนการอ่านชีต
         รหัสออก: 0 = ปกติ, 2 = มีคิวที่อ่าน/เขียนไม่ได้หรือถูกตัวกันพลาดปฏิเสธ
    [EN] Runs the part of `sync db` that does not use the AI, queue by queue (reads the queue sheet and makes the DB match it). A queue that is in planned uses those rows (the rows `sync queue`
         planned in a --dry-run) instead of reading the sheet. Exit code: 0 = fine, 2 = a queue could not be read / written or was refused by the safety net
    """
    multi = len(keys) > 1
    exit_code = 0
    for key in keys:
        spec = SPECS[key]
        log(("\n" if multi else "") + f"===== Sync DB {spec.name}: {spec.db_tab} <- {spec.queue_tab}" + (f" | sheet: {sheet_title}" if sheet_title else "") + " =====")
        try:
            if planned is not None and key in planned:
                queue_rows = planned[key]
            else:
                queue_rows = read_values(clients.sheets, args.spreadsheet_id, f"'{spec.queue_tab}'!A1:H")[1:]
            summary = sync_db_for_queue(args, clients, key, queue_rows, log=log)
        except SheetsError as e:
            log(f"✗ Could not sync {spec.db_tab}: {e}")
            exit_code = 2
            continue
        if summary.refused and not args.dry_run:
            exit_code = 2
    return exit_code
