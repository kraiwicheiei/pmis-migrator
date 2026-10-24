"""
DB_DWR / DB_DRQ 의 C/P 열과 "Legacy sync" 열: 머리글 확인, 한 열에 (행 번호 -> 값) 쓰기
[TH] คอลัมน์ C/P และ "Legacy sync" ของ DB_DWR / DB_DRQ: เช็กหัวตาราง และเขียน (เลขแถว -> ค่า) ลงหนึ่งคอลัมน์
[EN] The C/P column and the "Legacy sync" column of DB_DWR / DB_DRQ: checking their headers and writing (row number -> value) to one column
"""
from __future__ import annotations

from typing import Any

from .config import SHEETS_BASE
from .sheets import SheetsError, read_values, sheets_call, values_url
from .specs import DB_CP_HEADER, DB_SYNC_HEADER, QueueSpec
from .utils import col_letter

# 한 번에 쓰는 셀 수 / [TH] จำนวนเซลล์ที่เขียนต่อครั้ง / [EN] Cells written per request
COLUMN_CELLS_PER_WRITE = 500


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
    AI 단계가 새 행을 쓰기 전에 C/P 열과 "Legacy sync" 열의 머리글을 확인: [(머리글, 칸 주소, "present" | "written")]
    [TH] ก่อนขั้น AI เขียนแถวใหม่ จะเช็กหัวตารางของคอลัมน์ C/P และ "Legacy sync": [(หัวตาราง, ที่อยู่เซลล์, "present" | "written")]
    [EN] Before the AI part writes new rows, checks the headers of the C/P column and the "Legacy sync" column: [(header, cell, "present" | "written")]
    """
    checks = ((DB_CP_HEADER, spec.db_cp_col, ensure_cp_header), (DB_SYNC_HEADER, spec.db_sync_col, ensure_sync_header))
    return [(header, f"{col_letter(index + 1)}1", check(sess, sid, spec)) for header, index, check in checks]


def apply_column_updates(sess: Any, sid: str, spec: QueueSpec, col_index: int, updates: dict[int, str], value_input: str = "RAW") -> None:
    """
    DB 의 한 열(col_index)에 (행 번호 -> 값) 을 씀 (기본 RAW). 계획에 없는 셀은 건드리지 않음
    [TH] เขียน (เลขแถว -> ค่า) ลงคอลัมน์หนึ่ง (col_index) ของ DB (ค่าเริ่มต้น RAW) เซลล์ที่ไม่อยู่ในแผนไม่ถูกแตะ
    [EN] Writes (row number -> value) to one column (col_index) of the DB (RAW by default). A cell that is not in the plan is not touched
    """
    col = col_letter(col_index + 1)
    quoted = spec.db_tab.replace("'", "''")
    items = sorted(updates.items())
    for i in range(0, len(items), COLUMN_CELLS_PER_WRITE):
        body = {"valueInputOption": value_input,
                "data": [{"range": f"'{quoted}'!{col}{row}", "values": [[value]]} for row, value in items[i:i + COLUMN_CELLS_PER_WRITE]]}
        sheets_call(sess, "POST", f"{SHEETS_BASE}/{sid}/values:batchUpdate", json=body)
