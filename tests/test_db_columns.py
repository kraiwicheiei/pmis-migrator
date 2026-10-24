"""
DB_DWR / DB_DRQ 의 C/P 열(T / S)과 "Legacy sync" 열(U / T) 테스트: AI 부분이 쓰는 새 행의 C/P 와 표시, 머리글 확인, 한 열에 나눠 쓰기
[TH] เทสต์คอลัมน์ C/P (T / S) และ "Legacy sync" (U / T) ของ DB_DWR / DB_DRQ: C/P และป้ายของแถวใหม่ที่ส่วน AI เขียน, เช็กหัวตาราง, เขียนลงหนึ่งคอลัมน์เป็นก้อนๆ
[EN] Tests for the C/P column (T / S) and the "Legacy sync" column (U / T) of DB_DWR / DB_DRQ: the C/P and the tag of the new rows the AI part writes,
     the header check, and writing to one column in chunks
"""
import pytest

from migrator import db_columns
from migrator.db_columns import COLUMN_CELLS_PER_WRITE, apply_column_updates, ensure_cp_header, ensure_db_headers, ensure_sync_header
from migrator.reports import build_db_row
from migrator.runner import run, run_queues
from migrator.sheets import SheetsError
from migrator.specs import DB_CP_HEADER, DB_SYNC_HEADER, SPECS

from tests.fakes import DWR_JSON, FOLDER, URL, ns, world


def set_cp(w, values):
    """큐 시트 F열(Status(C/P)) 설정 / [TH] ตั้งคอลัมน์ F (Status(C/P)) ของชีตคิว / [EN] Sets column F (Status(C/P)) of the queue sheet"""
    for row, value in zip(w.queue[1:], values):
        row[5] = value


def db_row(i, cp=""):
    return build_db_row(SPECS["dwr"], DWR_JSON, URL(i), FOLDER, cp)


# --- 머리글 / the header / หัวตาราง ---
def test_ensure_cp_header_writes_an_empty_header_and_keeps_a_matching_one():
    w = world(["Waiting"])
    sess = w.clients().sheets
    assert ensure_cp_header(sess, "SID", SPECS["dwr"]) == "written" and w.cp_header == DB_CP_HEADER == "Status (C/P)"
    assert ensure_cp_header(sess, "SID", SPECS["dwr"]) == "present" and w.cp_header_writes == [DB_CP_HEADER]


def test_ensure_cp_header_refuses_to_write_over_another_column():
    w = world(["Waiting"])
    w.cp_header = "Remarks"
    with pytest.raises(SheetsError) as e:
        ensure_cp_header(w.clients().sheets, "SID", SPECS["dwr"])
    assert "DB_DWR!T1 already has the header 'Remarks'" in str(e.value) and w.cp_header == "Remarks" and w.cp_header_writes == []


def test_ensure_sync_header_and_both_headers_at_once():
    w = world(["Waiting"])
    sess = w.clients().sheets
    assert ensure_sync_header(sess, "SID", SPECS["dwr"]) == "written" and w.sync_header == DB_SYNC_HEADER == "Legacy sync"
    assert ensure_db_headers(sess, "SID", SPECS["dwr"]) == [("Status (C/P)", "T1", "written"), ("Legacy sync", "U1", "present")]
    w.sync_header = "Remarks"
    with pytest.raises(SheetsError) as e:
        ensure_sync_header(sess, "SID", SPECS["dwr"])
    assert "DB_DWR!U1 already has the header 'Remarks'" in str(e.value)


# --- 새 행 / new rows / แถวใหม่ ---
def test_new_db_rows_carry_the_queue_cp_in_column_t():
    w = world(["Waiting"] * 3)
    set_cp(w, ["C", "P", ""])
    logs = []
    s = run(ns(), w.clients(), log=logs.append)
    assert s.done == 3
    assert [r[19] for r in w.db[1:]] == ["C", "P", ""]                                   # as the queue says, an empty one stays empty
    assert [r[17] for r in w.db[1:]] == [URL(1), URL(2), URL(3)] and [len(r) for r in w.db[1:]] == [21, 21, 21]
    assert [r[20] for r in w.db[1:]] == ["Synced"] * 3                                    # every row the AI part writes was just made from the queue
    assert w.sync_header == "Legacy sync" and any("Wrote the header 'Legacy sync' to DB_DWR!U1" in m for m in logs)
    assert w.cp_header == DB_CP_HEADER and any("Wrote the header 'Status (C/P)' to DB_DWR!T1" in m for m in logs)


def test_the_header_is_written_once_and_only_when_there_is_something_to_write():
    w = world(["Waiting"] * 2)
    run(ns(), w.clients(), log=lambda m: None)
    run(ns(), w.clients(), log=lambda m: None)                                           # everything is Done now
    assert w.cp_header_writes == [DB_CP_HEADER]
    idle = world(["Done"])
    run(ns(), idle.clients(), log=lambda m: None)
    assert idle.cp_header_writes == []


def test_a_different_header_in_t1_stops_that_queue_before_anything_is_written():
    w = world(["Waiting"] * 2)
    w.cp_header = "Remarks"
    logs = []
    code = run_queues(ns(queue=None), w.clients(), ["dwr"], log=logs.append)
    assert code == 2 and w.gemini_calls == 0 and w.appends == [] and [r[6] for r in w.queue[1:]] == ["Waiting"] * 2
    assert any("Could not process queue MIGRATION_QUEUE" in m and "already has the header 'Remarks'" in m for m in logs)


# --- 한 열에 쓰기 / writing one column / เขียนลงหนึ่งคอลัมน์ ---
def test_a_column_write_goes_to_the_column_of_each_db_only():
    seen = []

    class Sess:
        def request(self, method, url, **kw):
            seen.append((method, kw["json"]["valueInputOption"], kw["json"]["data"][0]["range"]))
            return type("R", (), {"status_code": 200, "json": staticmethod(lambda: {})})()

    apply_column_updates(Sess(), "SID", SPECS["drq"], SPECS["drq"].db_cp_col, {2: "C"})
    apply_column_updates(Sess(), "SID", SPECS["drq"], SPECS["drq"].db_sync_col, {2: "Synced"})
    apply_column_updates(Sess(), "SID", SPECS["drq"], SPECS["drq"].db_folder_col, {3: FOLDER}, value_input="USER_ENTERED")
    assert seen == [("POST", "RAW", "'DB_DRQ'!S2"), ("POST", "RAW", "'DB_DRQ'!T2"), ("POST", "USER_ENTERED", "'DB_DRQ'!P3")]


def test_a_column_write_is_done_in_chunks_and_touches_only_the_planned_cells(monkeypatch):
    w = world(["Done"] * 5)
    w.db.extend([db_row(i) for i in range(1, 6)])
    monkeypatch.setattr(db_columns, "COLUMN_CELLS_PER_WRITE", 2)
    apply_column_updates(w.clients().sheets, "SID", SPECS["dwr"], SPECS["dwr"].db_cp_col, {2: "C", 3: "P", 4: "C", 5: "P", 6: "C"})
    assert [len(chunk) for chunk in w.batch_updates] == [2, 2, 1] and COLUMN_CELLS_PER_WRITE == 500
    assert [r[19] for r in w.db[1:6]] == ["C", "P", "C", "P", "C"] and all(item["range"].startswith("'DB_DWR'!T") for chunk in w.batch_updates for item in chunk)
    assert all(r[17] == URL(i) for i, r in enumerate(w.db[1:6], start=1))                  # nothing but column T changed
