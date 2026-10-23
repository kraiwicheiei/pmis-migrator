"""
DB_DWR / DB_DRQ 의 C/P 열: 머리글 확인, 기존 행에 값 채우기 (--backfill-cp)
[TH] คอลัมน์ C/P ของ DB_DWR / DB_DRQ: เช็กหัวตาราง และเติมค่าให้แถวเดิม (--backfill-cp)
[EN] The C/P column of DB_DWR / DB_DRQ: checking its header and filling the existing rows (--backfill-cp)
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import SHEETS_BASE
from .models import Clients
from .sheets import SheetsError, read_values, sheets_call, values_url
from .specs import DB_CP_HEADER, DB_SYNC_HEADER, QueueSpec, SPECS
from .utils import col_letter, extract_file_id

# 한 번에 쓰는 셀 수 / [TH] จำนวนเซลล์ที่เขียนต่อครั้ง / [EN] Cells written per request
BACKFILL_CELLS_PER_WRITE = 500


def ensure_db_header(sess: Any, sid: str, spec: QueueSpec, col_index: int, header: str, what: str) -> str:
    """
    DB 탭의 한 열(col_index)의 머리글(1행)을 확인: 이미 같으면 "present", 비어 있으면 써 넣고 "written".
    다른 머리글이 있으면 다른 열 위에 쓰지 않도록 SheetsError (그 큐는 처리하지 않음)
    [TH] เช็กหัวตาราง (แถว 1) ของคอลัมน์หนึ่ง (col_index) ในแท็บ DB: ถ้าตรงอยู่แล้วคืน "present" ถ้าว่างจะเขียนให้แล้วคืน "written"
         ถ้ามีหัวอื่นอยู่จะ raise SheetsError (ไม่ประมวลผลคิวนั้น) เพื่อไม่ให้เขียนทับคอลัมน์อื่น
    [EN] Checks the header (row 1) of one column (col_index) of a DB tab: "present" when it already matches, "written" after filling an empty one.
         A different header raises SheetsError (that queue is not processed) so another column is never written over
    """
    col = col_letter(col_index + 1)
    quoted = spec.db_tab.replace("'", "''")
    cell = f"'{quoted}'!{col}1"
    values = read_values(sess, sid, cell)
    current = str(values[0][0]).strip() if values and values[0] else ""
    if current == header:
        return "present"
    if current:
        raise SheetsError(f"{spec.db_tab}!{col}1 already has the header {current!r}, not {header!r} - {what} is not written over another column")
    sheets_call(sess, "PUT", values_url(sid, cell), params={"valueInputOption": "RAW"},
                json={"majorDimension": "ROWS", "values": [[header]]})
    return "written"


def ensure_cp_header(sess: Any, sid: str, spec: QueueSpec) -> str:
    """C/P 열의 머리글 확인 / [TH] เช็กหัวตารางของคอลัมน์ C/P / [EN] Checks the header of the C/P column"""
    return ensure_db_header(sess, sid, spec, spec.db_cp_col, DB_CP_HEADER, "the C/P value")


def ensure_sync_header(sess: Any, sid: str, spec: QueueSpec) -> str:
    """"Legacy sync" 표시 열의 머리글 확인 / [TH] เช็กหัวตารางของคอลัมน์ป้าย "Legacy sync" / [EN] Checks the header of the "Legacy sync" tag column"""
    return ensure_db_header(sess, sid, spec, spec.db_sync_col, DB_SYNC_HEADER, "the legacy sync tag")


def ensure_db_headers(sess: Any, sid: str, spec: QueueSpec) -> list[tuple[str, str, str]]:
    """
    migrate 가 새 행을 쓰기 전에 C/P 열과 "Legacy sync" 열의 머리글을 확인: [(머리글, 칸 주소, "present" | "written")]
    [TH] ก่อน migrate เขียนแถวใหม่ จะเช็กหัวตารางของคอลัมน์ C/P และ "Legacy sync": [(หัวตาราง, ที่อยู่เซลล์, "present" | "written")]
    [EN] Before migrate writes new rows, checks the headers of the C/P column and the "Legacy sync" column: [(header, cell, "present" | "written")]
    """
    checks = ((DB_CP_HEADER, spec.db_cp_col, ensure_cp_header), (DB_SYNC_HEADER, spec.db_sync_col, ensure_sync_header))
    return [(header, f"{col_letter(index + 1)}1", check(sess, sid, spec)) for header, index, check in checks]


@dataclass
class BackfillPlan:
    updates: dict[int, str] = field(default_factory=dict)   # DB 행 번호 -> C/P / [TH] เลขแถว DB -> C/P / [EN] DB row number -> C/P
    already: int = 0       # 이미 C/P 가 있는 행 / [TH] แถวที่มี C/P อยู่แล้ว / [EN] rows that already have a C/P
    no_pdf: int = 0        # PDF 링크가 없는 행 / [TH] แถวที่ไม่มีลิงก์ PDF / [EN] rows without a PDF link
    unmatched: int = 0     # 큐에서 같은 PDF 를 못 찾은 행 / [TH] แถวที่หา PDF เดียวกันในคิวไม่เจอ / [EN] rows whose PDF is not in the queue
    ambiguous: int = 0     # 큐에서 같은 PDF 의 C/P 가 서로 다른 행 / [TH] แถวที่ PDF เดียวกันในคิวมี C/P ไม่ตรงกัน / [EN] rows whose PDF has different C/P values in the queue


def plan_backfill(queue_values: list[list], db_values: list[list], spec: QueueSpec) -> BackfillPlan:
    """
    DB 행과 큐 행을 PDF 의 Drive file ID 로 맞춰 C/P 를 정함.
      * DB 에 이미 C/P 가 있으면 건드리지 않음
      * 큐에서 같은 PDF 의 C/P 가 하나뿐일 때만 채움 (서로 다르면 추측하지 않고 건너뜀)
    [TH] จับคู่แถว DB กับแถวคิวด้วย Drive file ID ของ PDF เพื่อหา C/P
         * ถ้า DB มี C/P อยู่แล้ว ไม่แตะ
         * เติมเมื่อ PDF เดียวกันในคิวมี C/P ค่าเดียวเท่านั้น (ถ้าต่างกันไม่เดา ข้ามไป)
    [EN] Matches the DB rows with the queue rows by the Drive file ID of the PDF to find the C/P.
         * a DB row that already has a C/P is not touched
         * it is filled only when the same PDF has exactly one C/P value in the queue (conflicting values are skipped, not guessed)
    """
    cp_by_file: dict[str, set[str]] = {}
    for row in queue_values[1:]:
        cells = [str(c).strip() for c in row]
        cells += [""] * (6 - len(cells))
        file_id = extract_file_id(cells[3])
        if file_id and cells[5]:
            cp_by_file.setdefault(file_id, set()).add(cells[5])

    plan = BackfillPlan()
    for number, row in enumerate(db_values[1:], start=2):
        cells = [str(c).strip() for c in row]
        cells += [""] * (spec.db_cp_col + 1 - len(cells))
        if cells[spec.db_cp_col]:
            plan.already += 1
            continue
        file_id = extract_file_id(cells[spec.db_pdf_col])
        if not file_id:
            plan.no_pdf += 1
            continue
        found = cp_by_file.get(file_id)
        if not found:
            plan.unmatched += 1
        elif len(found) > 1:
            plan.ambiguous += 1
        else:
            plan.updates[number] = next(iter(found))
    return plan


def apply_column_updates(sess: Any, sid: str, spec: QueueSpec, col_index: int, updates: dict[int, str]) -> None:
    """
    DB 의 한 열(col_index)에 (행 번호 -> 값) 을 RAW 로 씀. 계획에 없는 셀은 건드리지 않음
    [TH] เขียน (เลขแถว -> ค่า) ลงคอลัมน์หนึ่ง (col_index) ของ DB แบบ RAW เซลล์ที่ไม่อยู่ในแผนไม่ถูกแตะ
    [EN] Writes (row number -> value) to one column (col_index) of the DB, as RAW. A cell that is not in the plan is not touched
    """
    col = col_letter(col_index + 1)
    quoted = spec.db_tab.replace("'", "''")
    items = sorted(updates.items())
    for i in range(0, len(items), BACKFILL_CELLS_PER_WRITE):
        body = {"valueInputOption": "RAW",
                "data": [{"range": f"'{quoted}'!{col}{row}", "values": [[value]]} for row, value in items[i:i + BACKFILL_CELLS_PER_WRITE]]}
        sheets_call(sess, "POST", f"{SHEETS_BASE}/{sid}/values:batchUpdate", json=body)


def apply_backfill(sess: Any, sid: str, spec: QueueSpec, plan: BackfillPlan) -> None:
    """
    계획된 셀만 C/P 열에 씀 (RAW). 이미 값이 있는 셀은 계획에 없으므로 덮어쓰지 않음
    [TH] เขียนเฉพาะเซลล์ที่วางแผนไว้ลงคอลัมน์ C/P (RAW) เซลล์ที่มีค่าอยู่แล้วไม่อยู่ในแผน จึงไม่ถูกเขียนทับ
    [EN] Writes only the planned cells to the C/P column (RAW). A cell that already has a value is not in the plan, so it is never overwritten
    """
    apply_column_updates(sess, sid, spec, spec.db_cp_col, plan.updates)


@dataclass
class BackfillSummary:
    filled: int = 0
    plan: BackfillPlan = field(default_factory=BackfillPlan)


def backfill_cp(args: argparse.Namespace, clients: Clients, key: str, log: Callable[[str], None] = print) -> BackfillSummary:
    spec = SPECS[key]
    sess, sid = clients.sheets, args.spreadsheet_id
    last_col = col_letter(spec.db_cp_col + 1)
    queue_values = read_values(sess, sid, f"'{spec.queue_tab}'!A1:G")
    db_values = read_values(sess, sid, f"'{spec.db_tab}'!A1:{last_col}")
    plan = plan_backfill(queue_values, db_values, spec)
    summary = BackfillSummary(plan=plan)

    c = sum(1 for v in plan.updates.values() if v == "C")
    p = sum(1 for v in plan.updates.values() if v == "P")
    other = len(plan.updates) - c - p
    counts = (f"{len(plan.updates)} to fill (C={c}, P={p}" + (f", other={other}" if other else "") + f"), {plan.already} already have a C/P, "
              f"{plan.unmatched} not found in {spec.queue_tab}, {plan.ambiguous} with different C/P values in {spec.queue_tab}, {plan.no_pdf} without a PDF link")
    log(f"[{spec.name}] Backfill C/P in {spec.db_tab}: {counts}")
    if args.dry_run:
        log(f"  (--dry-run) nothing was written: would fill {len(plan.updates)} cells of {spec.db_tab}!{last_col}")
        return summary

    if plan.updates:
        state = ensure_cp_header(sess, sid, spec)
        if state == "written":
            log(f"  ✓ Wrote the header '{DB_CP_HEADER}' to {spec.db_tab}!{last_col}1")
        apply_backfill(sess, sid, spec, plan)
    summary.filled = len(plan.updates)
    log(f"  ✓ Filled C/P in {spec.db_tab}!{last_col}: {summary.filled} rows")
    return summary


def run_backfill(args: argparse.Namespace, clients: Clients, keys: list[str], log: Callable[[str], None] = print, sheet_title: str = "") -> int:
    """
    큐별로 --backfill-cp 실행. 종료 코드: 0 = 정상, 2 = 읽거나 쓰지 못한 큐가 있음
    [TH] รัน --backfill-cp ทีละคิว รหัสออก: 0 = ปกติ, 2 = มีคิวที่อ่านหรือเขียนไม่ได้
    [EN] Runs --backfill-cp queue by queue. Exit code: 0 = fine, 2 = a queue could not be read or written
    """
    multi = len(keys) > 1
    exit_code = 0
    for key in keys:
        spec = SPECS[key]
        log(("\n" if multi else "") + f"===== Backfill C/P {spec.name}: {spec.db_tab} <- {spec.queue_tab}" + (f" | sheet: {sheet_title}" if sheet_title else "") + " =====")
        try:
            backfill_cp(args, clients, key, log=log)
        except SheetsError as e:
            log(f"✗ Could not backfill C/P in {spec.db_tab}: {e}")
            exit_code = 2
    return exit_code
