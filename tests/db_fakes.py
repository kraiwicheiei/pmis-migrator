"""
DB 맞추기 테스트용 도우미: 8열 큐 행, DB_DWR 행, 큐 탭 + DB 탭을 가진 가짜 스프레드시트
[TH] ตัวช่วยสำหรับเทสต์การทำให้ DB ตรงกัน: แถวคิว 8 คอลัมน์, แถว DB_DWR, สเปรดชีตจำลองที่มีแท็บคิวและแท็บ DB
[EN] Helpers for the DB sync tests: 8-column queue rows, DB_DWR rows, and a fake spreadsheet with a queue tab and a DB tab
"""
from migrator.db_sync import MIGRATED_STATUS, db_status_col
from migrator.reports import build_db_row
from migrator.specs import SPECS

from tests.fakes import DWR_JSON, FOLDER, URL
from tests.memory_sheets import MemorySheets

DWR = SPECS["dwr"]
QUEUE_HEADER8 = ["Doc No", "Date", "Discipline", "PDF URL", "Folder URL", "Status(C/P)", "AI Processed", "Replaces PDF (old file ID)"]


def qrow8(n, cp="C", status="Done", replaces=""):
    """큐 행 (A~H) / [TH] แถวคิว (A~H) / [EN] A queue row (A-H)"""
    return [f"DOC-{n}", "2025-01-01", "CIV", URL(n), FOLDER, cp, status, replaces]


def db_row(n, cp="", status=MIGRATED_STATUS, pdf=None, tag=""):
    """
    DB_DWR 행. "Legacy sync" 표시는 기본이 비어 있음 (표시가 생기기 전의 행)
    [TH] แถวของ DB_DWR ป้าย "Legacy sync" ว่างเป็นค่าเริ่มต้น (แถวก่อนมีป้าย)
    [EN] A DB_DWR row. The "Legacy sync" tag is empty by default (a row from before the tag existed)
    """
    row = build_db_row(DWR, {**DWR_JSON, "DWR_No": f"DWR-{n}"}, URL(n) if pdf is None else pdf, FOLDER, cp)
    row[db_status_col(DWR)] = status
    row[DWR.db_sync_col] = tag
    return row


def db_world(queue_rows, db_rows, db_header=None):
    """큐 탭(MIGRATION_QUEUE)과 DB 탭(DB_DWR)을 가진 스프레드시트 / [TH] สเปรดชีตที่มีแท็บคิว (MIGRATION_QUEUE) และแท็บ DB (DB_DWR) / [EN] A spreadsheet with the queue tab and the DB tab"""
    header = db_header or [f"h{i}" for i in range(19)]                                  # A..S: the C/P header (T1) is empty
    return MemorySheets({"MIGRATION_QUEUE": [list(QUEUE_HEADER8)] + [list(r) for r in queue_rows], "DB_DWR": [list(header)] + [list(r) for r in db_rows]})
