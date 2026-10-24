"""
PMIS 의 DB(DB_DWR / DB_DRQ)를 큐(= 옛 시트와 같은 내용)에 맞추기: 옛 시트와 맞춘 적이 있는데 큐에서 사라진 PDF 의 행 삭제, C/P 갱신, 옛 시트와 맞는 행에 "Legacy sync" 표시, 바뀐 PDF 의 행을 제자리에서 교체
[TH] ทำให้ DB ของ PMIS (DB_DWR / DB_DRQ) ตรงกับคิว (= เนื้อหาเดียวกับชีตเก่า): ลบแถวที่เคย sync กับชีตเก่าแต่ PDF หายไปจากคิว, อัปเดต C/P, ติดป้าย "Legacy sync" ให้แถวที่ตรงกับชีตเก่า, แทนที่แถวของ PDF ที่เปลี่ยนในที่เดิม
[EN] Making the PMIS DB (DB_DWR / DB_DRQ) match the queue (= the same content as the legacy sheet): removing the rows that were synced with the legacy sheet but whose PDF left the queue,
     updating C/P, tagging the rows that match the legacy sheet with "Legacy sync", and replacing the row of a changed PDF in place
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .config import SHEETS_BASE
from .cp_column import BackfillPlan, apply_backfill, apply_column_updates, ensure_cp_header, ensure_sync_header
from .models import Clients
from .sheets import SheetsError, append_rows, list_tabs, read_values, sheets_call, values_url
from .specs import DB_CP_HEADER, DB_SYNC_HEADER, DB_SYNC_VALUE, QueueSpec, SPECS
from .stamps import time_stamp
from .utils import col_letter, count_lines, extract_file_id, removal_limit

# 이 도구가 쓴 행의 Status 값. 이 값이 아닌 행(사람이 넣은 행 등)은 절대 삭제하지 않음
# [TH] ค่า Status ของแถวที่เครื่องมือนี้เขียน แถวที่ Status เป็นค่าอื่น (เช่นแถวที่คนใส่เอง) จะไม่ถูกลบเด็ดขาด
# [EN] The Status value of the rows this tool wrote. A row with any other Status (e.g. one a person added) is never removed
MIGRATED_STATUS = "AI Migrated"
DB_CELLS_PER_WRITE = 500
DELETES_PER_REQUEST = 200
ARCHIVE_ROWS_PER_WRITE = 2000


def db_status_col(spec: QueueSpec) -> int:
    """DB 에서 Status(AI Migrated) 열의 인덱스 / [TH] index ของคอลัมน์ Status (AI Migrated) ใน DB / [EN] The index of the Status (AI Migrated) column of the DB"""
    for i, col in enumerate(spec.columns):
        if col == ("const", MIGRATED_STATUS):
            return i
    return -1


def archive_tab_name(spec: QueueSpec) -> str:
    """삭제한 행을 보관하는 탭 이름 (DB 마다 하나) / [TH] ชื่อแท็บที่เก็บแถวที่ลบ (หนึ่งแท็บต่อ DB) / [EN] The tab that keeps the removed rows (one per DB)"""
    return f"_REMOVED_{spec.db_tab}"


# ---------------------------------------------------------------------------
# 바뀐 PDF 의 DB 행을 제자리에서 교체 (migrate 가 사용)
# [TH] แทนที่แถวใน DB ของ PDF ที่เปลี่ยนในที่เดิม (migrate ใช้)
# [EN] Replacing the DB row of a changed PDF in place (used by migrate)
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
    # DB 행 번호 -> 새 "Legacy sync" 값 ("Synced" 를 붙이거나 "" 로 지움). 지금 값과 다른 행만
    # [TH] เลขแถว DB -> ค่าใหม่ของ "Legacy sync" (ติด "Synced" หรือล้างเป็น "") เฉพาะแถวที่ต่างจากค่าปัจจุบัน
    # [EN] DB row number -> the new "Legacy sync" value ("Synced" to tag, "" to clear); only the rows whose value differs from the current one
    tag_updates: dict[int, str] = field(default_factory=dict)
    removals: dict[int, str] = field(default_factory=dict)           # DB 행 번호 -> 삭제 이유 / [TH] เลขแถว DB -> เหตุผลที่ลบ / [EN] DB row number -> why it is removed
    synced: int = 0            # PDF 가 큐에 있어 "Synced" 여야 하는 행 (이미 붙어 있는 행 포함) / [TH] แถวที่ PDF อยู่ในคิวจึงต้องเป็น "Synced" (รวมแถวที่ติดอยู่แล้ว) / [EN] rows whose PDF is in the queue, so they should be "Synced" (including the ones that already are)
    pending: int = 0           # 바뀐 PDF 의 교체를 기다려 남겨 둔 행 / [TH] แถวที่เก็บไว้เพราะรอการแทนที่จาก PDF ที่เปลี่ยน / [EN] rows kept because they wait to be replaced by a changed PDF
    protected: int = 0         # 큐에 없지만 이 도구가 쓴 행이 아니라 삭제하지 않는 행 / [TH] แถวที่ไม่อยู่ในคิวแต่ไม่ใช่แถวที่เครื่องมือนี้เขียน จึงไม่ลบ / [EN] rows not in the queue that this tool did not write, so they are not removed
    unsynced: int = 0          # 큐에 없지만 옛 시트와 맞춘 적이 없어(표시 없음) 남겨 두는 행 / [TH] แถวที่ไม่อยู่ในคิวแต่ไม่เคย sync กับชีตเก่า (ไม่มีป้าย) จึงเก็บไว้ / [EN] rows not in the queue that were never synced with the legacy sheet (no tag), so they are kept
    ambiguous: int = 0         # 큐에서 같은 PDF 의 C/P 가 서로 다른 행 / [TH] แถวที่ PDF เดียวกันในคิวมี C/P ไม่ตรงกัน / [EN] rows whose PDF has different C/P values in the queue
    no_pdf: int = 0            # PDF 링크가 없는 행 (판단할 수 없어 건드리지 않음) / [TH] แถวที่ไม่มีลิงก์ PDF (ตัดสินไม่ได้ จึงไม่แตะ) / [EN] rows without a PDF link (cannot be judged, so they are left alone)
    # 쓰거나 지울 DB 행 번호 -> 그 행의 PDF file ID (PDF 가 없는 행은 ""). 쓰기 직전에 DB 가 그대로인지 확인하는 데 사용 (그사이 행이 끼어들어 번호가 밀렸으면 중단)
    # [TH] เลขแถว DB ที่จะเขียนหรือลบ -> file ID ของ PDF ในแถวนั้น ใช้เช็กก่อนเขียนว่า DB ยังเหมือนเดิม (ถ้ามีแถวแทรกเข้ามาจนเลขเลื่อนจะหยุด)
    # [EN] DB row number to be written or removed -> the PDF file ID of that row. Used right before writing to check that the DB is unchanged (stop if a row was inserted and the numbers shifted)
    touched: dict[int, str] = field(default_factory=dict)


def plan_db_sync(queue_rows: list[list], db_values: list[list], spec: QueueSpec) -> DbSyncPlan:
    """
    큐의 PDF(Drive file ID)를 기준으로 DB 행을 판단:
      * PDF 가 큐에 있으면 남기고, 큐의 C/P 가 하나뿐이고 DB 와 다르면 C/P 를 갱신 (큐의 C/P 가 비었으면 DB 값을 지우지 않음)
      * 큐 H열(바뀌기 전 PDF)에 있는 PDF 는 교체를 기다리므로 남김
      * 그 밖에 PDF 가 큐에 없으면 삭제 대상. 단 Status 가 "AI Migrated" 이고 "Legacy sync" 가 "Synced" 인 행만 (= 옛 시트와 맞춘 적이 있는데 옛 시트에서 사라진 행).
        옛 시트와 맞춘 적이 없는 행(표시 없음, 예: 옛 시트와 무관하게 다른 시스템이 넣은 문서)과 사람이 넣은 행은 남김
      * "Legacy sync" 표시: PDF 가 큐에 있는 행만 "Synced". 큐에 없는 행(고아), 지우지 않는 행, PDF 링크가 없는 행은 비어 있어야 함 (교체를 기다리는 행은 지금 값 그대로)
        (지금과 다른 행만 계획에 넣음. 지워질 행은 표시를 건드리지 않음)
      * PDF 링크가 없는 행은 그 밖에는 건드리지 않음
    [TH] ตัดสินแถวใน DB ด้วย PDF (Drive file ID) ของคิว:
         * ถ้า PDF อยู่ในคิวก็เก็บไว้ และถ้า C/P ของคิวมีค่าเดียวและต่างจาก DB จะอัปเดต C/P (ถ้า C/P ของคิวว่างจะไม่ล้างค่าใน DB)
         * PDF ที่อยู่ในคอลัมน์ H ของคิว (PDF ก่อนเปลี่ยน) รอการแทนที่ จึงเก็บไว้
         * นอกจากนั้นถ้า PDF ไม่อยู่ในคิวจะเป็นแถวที่จะลบ แต่เฉพาะแถวที่ Status เป็น "AI Migrated" และ "Legacy sync" เป็น "Synced" (= เคย sync กับชีตเก่าแล้วหายไปจากชีตเก่า)
           แถวที่ไม่เคย sync กับชีตเก่า (ไม่มีป้าย เช่นเอกสารที่ระบบอื่นเพิ่มโดยไม่เกี่ยวกับชีตเก่า) และแถวที่คนใส่เองจะถูกเก็บไว้
         * ป้าย "Legacy sync": เฉพาะแถวที่ PDF อยู่ในคิวเป็น "Synced" แถวที่ไม่อยู่ในคิว (แถวกำพร้า), แถวที่ไม่ลบ และแถวที่ไม่มีลิงก์ PDF ต้องว่าง (แถวที่รอแทนที่คงค่าเดิม)
           (ใส่ในแผนเฉพาะแถวที่ต่างจากตอนนี้ แถวที่จะถูกลบไม่แตะป้าย)
         * แถวที่ไม่มีลิงก์ PDF นอกนั้นไม่แตะ
    [EN] Judges the DB rows by the PDF (Drive file ID) of the queue:
         * a PDF that is in the queue is kept, and when the queue has exactly one C/P for it and it differs from the DB, the C/P is updated
           (an empty C/P in the queue never clears the DB value)
         * a PDF listed in column H of the queue (the PDF before a change) waits to be replaced, so it is kept
         * any other PDF that is not in the queue is to be removed, but only a row whose Status is "AI Migrated" and whose "Legacy sync" is "Synced"
           (= it was synced with the legacy sheet and then left it). A row that was never synced with the legacy sheet (no tag, e.g. a document another system
           added that has nothing to do with the legacy sheet) and a row a person added are kept
         * the "Legacy sync" tag: only a row whose PDF is in the queue is "Synced". A row that is not in the queue (an orphan), a row that is not removed and
           a row without a PDF link must be empty; a row that waits to be replaced keeps its current value (only the rows that differ from now go into the plan; a row that is about to be removed is not tagged)
         * a row without a PDF link is not touched otherwise
    """
    cps: dict[str, set[str]] = {}
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
        if not fid:
            plan.no_pdf += 1
        elif fid in in_queue:
            plan.synced += 1
            tag = DB_SYNC_VALUE
            found = cps.get(fid)
            if found and len(found) > 1:
                plan.ambiguous += 1
            elif found:
                value = next(iter(found))
                if cells[spec.db_cp_col] != value:
                    plan.cp_updates[number] = value
                    plan.touched[number] = fid
        elif fid in waiting:
            plan.pending += 1
            tag = now                                                                 # the old version waits for its replacement: it stays what it was (a synced row stays tagged)
        elif status_col < 0 or cells[status_col] != MIGRATED_STATUS:
            plan.protected += 1
        elif now != DB_SYNC_VALUE:
            plan.unsynced += 1                                                        # never came from the legacy sheet (e.g. added by another system): kept as it is
        else:
            plan.removals[number] = f"it was synced with the legacy sheet, but its PDF ({fid}) is not in {spec.queue_tab} any more"
            plan.touched[number] = fid
            continue                                                                  # about to be removed: its tag does not matter
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
    tagged: int = 0            # "Synced" 를 새로 붙인 행 / [TH] แถวที่ติด "Synced" ใหม่ / [EN] rows that got the "Synced" tag
    untagged: int = 0          # 표시를 지운 행 / [TH] แถวที่ล้างป้าย / [EN] rows whose tag was cleared
    removed: int = 0
    refused: str = ""


def sync_db_for_queue(args: argparse.Namespace, clients: Clients, key: str, queue_rows: list[list], log: Callable[[str], None] = print) -> DbSyncSummary:
    """
    한 큐의 DB 탭을 큐 행(queue_rows, 머리글 제외)에 맞춤. --dry-run 이면 계획만 보여 줌
    안전장치: 큐가 비었거나 지울 행이 한도(전체의 10% 와 100행 중 큰 값)를 넘으면 DB 에 아무것도 쓰지 않음 (--allow-large-removal 로 허용)
    [TH] ทำให้แท็บ DB ของคิวหนึ่งตรงกับแถวของคิว (queue_rows ไม่รวมหัวตาราง) ถ้าเป็น --dry-run จะแสดงแค่แผน
         ตัวกันพลาด: ถ้าคิวว่างหรือแถวที่จะลบเกินเพดาน (ค่าที่มากกว่าระหว่าง 10% ของทั้งหมดกับ 100 แถว) จะไม่เขียนอะไรลง DB (อนุญาตได้ด้วย --allow-large-removal)
    [EN] Makes the DB tab of one queue match the queue rows (queue_rows, without the header). With --dry-run it only shows the plan.
         Safety: when the queue is empty or the rows to remove exceed the limit (the larger of 10% of all rows and 100), nothing is written to the DB
         (allow it with --allow-large-removal)
    """
    spec = SPECS[key]
    sess, sid = clients.sheets, args.spreadsheet_id
    last_col = col_letter(len(spec.columns))
    db_values = read_values(sess, sid, f"'{spec.db_tab}'!A1:{last_col}")
    plan = plan_db_sync(queue_rows, db_values, spec)
    summary = DbSyncSummary(plan=plan)
    limit = removal_limit(plan.rows)
    to_tag = {n: v for n, v in plan.tag_updates.items() if v}
    to_untag = {n: v for n, v in plan.tag_updates.items() if not v}
    kept = plan.pending + plan.unsynced + plan.protected
    table = [("In sync with the queue", str(plan.synced), ""),
             (f"\"{DB_SYNC_HEADER}\" tag", f"set {len(to_tag)}, clear {len(to_untag)}", ""),
             ("C/P to update", str(len(plan.cp_updates)), ""),
             ("To remove", str(len(plan.removals)), "(were synced, PDF not in the queue any more)"),
             ("Kept as they are", str(kept), "")]
    table += [(f"  {label}", str(n), "") for label, n in (("waiting for a replacement", plan.pending), ("never synced with the legacy sheet", plan.unsynced),
                                                         (f"Status is not '{MIGRATED_STATUS}'", plan.protected)) if n]
    if plan.ambiguous:
        table.append(("Skipped: different C/P in the queue", str(plan.ambiguous), ""))
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
        apply_backfill(sess, sid, spec, BackfillPlan(updates=dict(plan.cp_updates)))
        summary.cp_updated = len(plan.cp_updates)
        log(f"  ✓ Updated C/P in {spec.db_tab}!{col_letter(spec.db_cp_col + 1)}: {summary.cp_updated} rows")
    if plan.tag_updates:
        apply_column_updates(sess, sid, spec, spec.db_sync_col, plan.tag_updates)
        summary.tagged, summary.untagged = len(to_tag), len(to_untag)
        log(f"  ✓ Legacy sync tag in {spec.db_tab}!{col_letter(spec.db_sync_col + 1)}: set on {summary.tagged} rows, cleared on {summary.untagged} rows")
    if plan.removals:
        tabs = list_tabs(sess, sid)
        verify_unchanged(sess, sid, spec, {n: plan.touched[n] for n in plan.removals})
        archived = archive_removed(sess, sid, spec, db_values, plan.removals, tabs)
        delete_rows(sess, sid, tabs[spec.db_tab][0], list(plan.removals))
        summary.removed = len(plan.removals)
        log(f"  ✓ Removed {summary.removed} rows from {spec.db_tab} (copied to {archived} first)")
    if not plan.cp_updates and not plan.tag_updates and not plan.removals:
        log(f"  ✓ {spec.db_tab} already matches the queue")
    return summary


def run_db_sync(args: argparse.Namespace, clients: Clients, keys: list[str], log: Callable[[str], None] = print, sheet_title: str = "") -> int:
    """
    큐별로 --sync-db 실행 (큐 시트를 읽어 그 내용에 DB 를 맞춤). 종료 코드: 0 = 정상, 2 = 읽거나 쓰지 못했거나 안전장치로 거부한 큐가 있음
    [TH] รัน --sync-db ทีละคิว (อ่านชีตคิวแล้วทำให้ DB ตรงกับเนื้อหานั้น) รหัสออก: 0 = ปกติ, 2 = มีคิวที่อ่าน/เขียนไม่ได้หรือถูกตัวกันพลาดปฏิเสธ
    [EN] Runs --sync-db queue by queue (reads the queue sheet and makes the DB match it). Exit code: 0 = fine, 2 = a queue could not be read / written
         or was refused by the safety net
    """
    multi = len(keys) > 1
    exit_code = 0
    for key in keys:
        spec = SPECS[key]
        log(("\n" if multi else "") + f"===== Sync DB {spec.name}: {spec.db_tab} <- {spec.queue_tab}" + (f" | sheet: {sheet_title}" if sheet_title else "") + " =====")
        try:
            queue_values = read_values(clients.sheets, args.spreadsheet_id, f"'{spec.queue_tab}'!A1:H")
            summary = sync_db_for_queue(args, clients, key, queue_values[1:], log=log)
        except SheetsError as e:
            log(f"✗ Could not sync {spec.db_tab}: {e}")
            exit_code = 2
            continue
        if summary.refused and not args.dry_run:
            exit_code = 2
    return exit_code
