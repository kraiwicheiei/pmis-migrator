"""
--extract 테스트: 새로 만들기, 새로 계산해 기존 행과 맞추기(sync), 다른 파일에서 읽기, 새로고침 시각
[TH] เทสต์ --extract: สร้างใหม่, คำนวณใหม่แล้วจับคู่กับแถวเดิม (sync), อ่านจากไฟล์อื่น, เวลา refresh
[EN] Tests for --extract: rebuild, recompute and match with the existing rows (sync), reading from another file, the refresh time
"""

import json

import pytest

from migrator import extract as extract_module, legacy_sheet, queue_writer
from migrator.db_sync import DbSyncSummary
from migrator.extract import extract_queue, run_extract
from migrator.legacy_sheet import LegacyItem
from migrator.queue_writer import QUEUE_HEADER, clear_replaces, plan_sync, status_rank
from migrator.sheets import SheetsError
from migrator.stamps import QUEUE_REFRESH_LABEL, REFRESH_COL

from tests.fakes import FID, NON_ENGLISH, URL, count_rows, qrow
from tests.memory_sheets import MemorySheets
from tests.legacy_fakes import DRQ_ROWS, DWR_ROWS, FakeLegacy, LEGACY_FILE, LEGACY_HEADER, STAMP, core, extract_logs, legacy_grid, merge_queue, mk, txt, xargs


# --- 읽고 쓰기 / read and write / อ่านและเขียน ---
def test_extract_creates_the_queue_with_header_waiting_rows_and_raw_values():
    w = FakeLegacy()
    summary, logs = extract_logs(w)
    assert w.queues["MIGRATION_QUEUE"] == [QUEUE_HEADER] + DWR_ROWS
    assert [e[0] for e in w.events] == ["addSheet", "clear", "header", "append", "refreshed"]
    assert (summary.items, summary.completed, summary.written, summary.created_tab) == (4, 1, 4, True)
    assert not NON_ENGLISH.search(json.dumps(w.written, ensure_ascii=False))     # 시트에 쓰는 값은 영어 / [TH] ค่าที่เขียนลงชีตเป็นภาษาอังกฤษ / [EN] values written to the sheet are English
    assert any("Wrote queue MIGRATION_QUEUE: 4 rows" in m for m in logs) and any("2 items have no PDF link" in m for m in logs)


def test_extract_drq_uses_its_own_tab_columns_and_queue():
    w = FakeLegacy()
    extract_logs(w, "drq")
    assert w.queues["MIGRATION_QUEUE_DRQ"] == [QUEUE_HEADER] + DRQ_ROWS and "MIGRATION_QUEUE" not in w.queues


def test_extract_reuses_an_existing_queue_tab_without_creating_it():
    old = ["old", "", "", "", "", "P", "Waiting"]
    w = FakeLegacy(queues={"MIGRATION_QUEUE": [QUEUE_HEADER, list(old)]})
    summary, _ = extract_logs(w)
    assert not summary.created_tab and ("addSheet", "MIGRATION_QUEUE") not in w.events and ("clear", "MIGRATION_QUEUE") not in w.events
    assert core(w.queues["MIGRATION_QUEUE"]) == [QUEUE_HEADER] + DWR_ROWS            # recomputed from the legacy sheet: the row that is not in it is gone
    assert (summary.added, summary.removed) == (4, 1)


def test_extract_dry_run_reads_but_writes_nothing():
    w = FakeLegacy()
    summary, logs = extract_logs(w, dry_run=True)
    assert w.events == [] and w.queues == {} and w.grid_reads
    assert summary.items == 4 and summary.written == 0
    assert any("--dry-run" in m and "new tab" in m for m in logs) and any("sample: R-001-CIV-001" in m for m in logs)


def test_extract_recomputes_a_queue_in_progress_and_keeps_the_status_of_the_existing_rows():
    w = FakeLegacy(queues={"MIGRATION_QUEUE": [list(r) for r in merge_queue()]})
    summary, logs = extract_logs(w)
    queue = w.queues["MIGRATION_QUEUE"]
    relinked, error, waiting, new = (list(r) + [""] for r in DWR_ROWS)
    relinked[7] = FID(7)                                                                 # the Done row whose PDF changed (URL(7) -> URL(2)): Waiting again, the old file ID in H
    error[6] = "Error: Drive Access | x"
    assert queue[1:] == [relinked, error, waiting, new]                                  # A..F recomputed, G kept (except the relinked row); OLD-1 is gone
    assert [e[0] for e in w.events] == ["header", "put", "refreshed"] and "clear" not in [e[0] for e in w.events]
    assert (summary.added, summary.updated, summary.unchanged, summary.kept_done, summary.removed, summary.relinked, summary.written) == (1, 2, 1, 0, 1, 1, 4)
    assert any("1 Done rows got a new PDF link from the legacy sheet: set back to Waiting" in m for m in logs)
    assert any("1 removed rows were Done / Error" in m for m in logs)
    assert any("Synced MIGRATION_QUEUE: 4 rows" in m for m in logs)
    assert count_rows(logs) == {"Kept from the queue": "3  (changed 2, renumbered 0, set back to Waiting 1)", "New": "1", "Duplicate rows merged": "0",
                                "Removed (not in the legacy sheet)": "1", "Statuses kept": "Done 0, Error 1, Waiting 2"}


def test_a_second_sync_changes_nothing_but_the_refresh_time():
    w = FakeLegacy(queues={"MIGRATION_QUEUE": [list(r) for r in merge_queue()]})
    extract_logs(w)
    after_first = [list(r) for r in w.queues["MIGRATION_QUEUE"]]
    w.events.clear()
    summary, logs = extract_logs(w)
    assert w.queues["MIGRATION_QUEUE"] == after_first
    assert [e[0] for e in w.events] == ["refreshed"] and (summary.added, summary.updated, summary.written) == (0, 0, 0)
    assert any("Synced MIGRATION_QUEUE: no change (4 rows)" in m for m in logs)
    assert count_rows(logs)["Kept from the queue"] == "4  (changed 0, renumbered 0, set back to Waiting 0)" and count_rows(logs)["New"] == "0"


def test_extract_sync_dry_run_reports_the_plan_and_writes_nothing():
    existing = merge_queue()
    w = FakeLegacy(queues={"MIGRATION_QUEUE": [list(r) for r in existing]})
    summary, logs = extract_logs(w, dry_run=True)
    assert w.events == [] and w.queues["MIGRATION_QUEUE"] == existing and w.refresh == {}
    assert (summary.added, summary.updated, summary.removed, summary.written) == (1, 2, 1, 0)
    assert any("(--dry-run) nothing was written: would sync MIGRATION_QUEUE: 4 rows" in m for m in logs)
    assert count_rows(logs)["Kept from the queue"].startswith("3  (changed 2") and count_rows(logs)["New"] == "1"


def test_extract_overwrite_rebuilds_the_queue_and_wipes_the_statuses():
    w = FakeLegacy(queues={"MIGRATION_QUEUE": [list(r) for r in merge_queue()]})
    summary, logs = extract_logs(w, overwrite=True)
    assert w.queues["MIGRATION_QUEUE"] == [QUEUE_HEADER] + DWR_ROWS and summary.written == 4 and "clear" in [e[0] for e in w.events]
    assert any("--overwrite: the existing statuses (Done 2, Error 1) will be wiped" in m for m in logs)


def test_extract_refuses_to_sync_a_tab_with_an_unexpected_header():
    existing = [["Doc", "Date"], qrow(1, "Done")]
    w = FakeLegacy(queues={"MIGRATION_QUEUE": [list(r) for r in existing]})
    summary, logs = extract_logs(w)
    assert summary.refused and summary.written == 0 and w.events == [] and w.queues["MIGRATION_QUEUE"] == existing and w.refresh == {}
    assert any("header row of MIGRATION_QUEUE is not the expected one" in m and "--overwrite" in m for m in logs)
    summary, _ = extract_logs(w, overwrite=True)
    assert summary.refused == "" and w.queues["MIGRATION_QUEUE"] == [QUEUE_HEADER] + DWR_ROWS


def test_an_empty_existing_tab_is_filled_like_a_new_queue():
    w = FakeLegacy(queues={"MIGRATION_QUEUE": []})
    summary, _ = extract_logs(w)
    assert w.queues["MIGRATION_QUEUE"] == [QUEUE_HEADER] + DWR_ROWS and summary.written == 4 and not summary.created_tab


def test_plan_sync_matches_on_doc_no_and_date_and_a_changed_date_is_a_new_item():
    item = LegacyItem("R-9-CIV-1", "2025-01-01", "CIV", URL(1), "", False)
    other_day = LegacyItem("R-9-CIV-1", "2025-01-02", "CIV", URL(1), "", False)
    existing = [QUEUE_HEADER, ["R-9-CIV-1", "2025-01-01", "CIV", "", "", "P", "Error: x"]]
    plan = plan_sync(existing, [item, other_day])
    assert plan.rows == [["R-9-CIV-1", "2025-01-01", "CIV", URL(1), "", "P", "Error: x", ""], other_day.to_row() + [""]]     # A..F recomputed, the status of the old row kept
    assert (plan.matched, plan.new, plan.updated, plan.removed, plan.changed) == (1, 1, 1, 0, True)


def test_extract_with_no_items_leaves_the_queue_alone():
    empty = {"LEGACY-DWR": {3: LEGACY_HEADER, 4: mk({0: txt("R-1"), 4: txt("x")})}, "LEGACY-DRQ": {}}
    w = FakeLegacy(grids=empty, queues={"MIGRATION_QUEUE": [QUEUE_HEADER, qrow(1, "Done")]})
    summary, logs = extract_logs(w)
    assert summary.items == 0 and w.events == [] and w.queues["MIGRATION_QUEUE"] == [QUEUE_HEADER, qrow(1, "Done")]
    assert any("No items found -> the MIGRATION_QUEUE queue is left untouched" in m for m in logs)
    _, logs = extract_logs(FakeLegacy(grids=empty), "drq")
    assert any("row 3, the header row, is empty" in m for m in logs)                    # 헤더 행이 비어 있다는 힌트 / [TH] บอกใบ้ว่าหัวตารางว่าง / [EN] hint that the header row is empty


def test_extract_reads_and_writes_in_chunks(monkeypatch):
    monkeypatch.setattr(legacy_sheet, "EXTRACT_ROWS_PER_READ", 2)
    monkeypatch.setattr(queue_writer, "EXTRACT_ROWS_PER_WRITE", 3)
    w = FakeLegacy(row_count=7)
    extract_logs(w, "drq")
    assert [(r1, r2) for _, r1, _, r2 in w.grid_reads] == [(3, 4), (5, 6), (7, 7)]
    assert all(col == "BF" for _, _, col, _ in w.grid_reads)
    assert [e[2] for e in w.events if e[0] == "append"] == [3, 2]
    assert w.queues["MIGRATION_QUEUE_DRQ"] == [QUEUE_HEADER] + DRQ_ROWS               # 나눠 읽고 써도 결과는 같음 / [TH] อ่านและเขียนแบบแบ่งก็ได้ผลเท่าเดิม / [EN] chunked reads/writes give the same result


def test_extract_quotes_unusual_tab_names():
    tab = "Legacy (DWR) 'old'"
    w = FakeLegacy(grids={tab: legacy_grid()})
    extract_queue(xargs(queue="dwr", legacy_tabs={"dwr": tab}), w.clients(), "dwr", log=lambda m: None)
    assert w.grid_reads[0][0] == tab and w.queues["MIGRATION_QUEUE"][1:] == DWR_ROWS


def test_extract_missing_legacy_tab_names_the_tabs_and_other_queue_still_runs():
    w = FakeLegacy(grids={"LEGACY-DRQ": legacy_grid()})
    logs = []
    code = run_extract(xargs(queue=None), w.clients(), ["dwr", "drq"], log=logs.append, sheet_title="Example Spreadsheet")
    text = "\n".join(logs)
    assert code == 2 and "tab 'LEGACY-DWR' not found" in text and "LEGACY-DRQ" in text and "PMIS_LEGACY_DWR_SHEET" in text
    assert w.queues["MIGRATION_QUEUE_DRQ"][1:] == DRQ_ROWS and "MIGRATION_QUEUE" not in w.queues
    assert "sheet: Example Spreadsheet" in text


def test_run_extract_exit_codes():
    w = FakeLegacy(queues={"MIGRATION_QUEUE": [list(r) for r in merge_queue()]})
    assert run_extract(xargs(queue="dwr"), w.clients(), ["dwr"], log=lambda m: None) == 0                 # a queue in progress is merged, not refused
    bad = FakeLegacy(queues={"MIGRATION_QUEUE": [["Doc", "Date"], qrow(1, "Done")]})
    assert run_extract(xargs(queue="dwr"), bad.clients(), ["dwr"], log=lambda m: None) == 2               # unexpected header: refused, nothing written
    assert run_extract(xargs(queue="dwr", dry_run=True), bad.clients(), ["dwr"], log=lambda m: None) == 0   # dry-run returns 0
    assert run_extract(xargs(queue="dwr", overwrite=True), bad.clients(), ["dwr"], log=lambda m: None) == 0


def test_extract_reads_the_legacy_tab_from_another_spreadsheet_and_writes_to_the_target():
    w = FakeLegacy(legacy_sid=LEGACY_FILE)
    summary, logs = extract_logs(w, legacy_spreadsheet_id=LEGACY_FILE, spreadsheet_id="SID")
    assert w.queues["MIGRATION_QUEUE"] == [QUEUE_HEADER] + DWR_ROWS and summary.written == 4
    assert [e[0] for e in w.events] == ["addSheet", "clear", "header", "append", "refreshed"]                 # the queue is written to the target file only
    assert any("Legacy spreadsheet: Legacy Spreadsheet (LEGACYFILEID123)" in m for m in logs)
    assert all(LEGACY_FILE not in path for method, path in w.sheets_calls if method != "GET")


def test_extract_from_another_spreadsheet_still_syncs_with_the_target_queue():
    done_rows = [list(r) for r in DWR_ROWS[:2]]
    done_rows[0][6], done_rows[1][6] = "Done", "Done"
    w = FakeLegacy(legacy_sid=LEGACY_FILE, queues={"MIGRATION_QUEUE": [list(QUEUE_HEADER)] + [list(r) for r in done_rows]})
    summary, _ = extract_logs(w, legacy_spreadsheet_id=LEGACY_FILE)
    assert [r[6] for r in w.queues["MIGRATION_QUEUE"][1:]] == ["Done", "Done", "Waiting", "Waiting"] and "clear" not in [e[0] for e in w.events]     # the target's Done rows keep their status
    assert (summary.added, summary.kept_done) == (2, 2)


def test_extract_without_a_legacy_spreadsheet_id_keeps_reading_the_target_file():
    w = FakeLegacy()
    summary, logs = extract_logs(w, legacy_spreadsheet_id=None)
    assert summary.written == 4 and not any("Legacy spreadsheet:" in m for m in logs)


def test_extract_from_another_spreadsheet_explains_a_missing_tab_and_an_unshared_file():
    w = FakeLegacy(legacy_sid=LEGACY_FILE, grids={"OTHER": legacy_grid()})
    with pytest.raises(SheetsError) as e:
        extract_logs(w, legacy_spreadsheet_id=LEGACY_FILE)
    assert f"in the legacy spreadsheet {LEGACY_FILE}" in str(e.value) and "OTHER" in str(e.value) and "PMIS_LEGACY_DWR_SHEET" in str(e.value)
    with pytest.raises(SheetsError) as e:                                                   # an unshared file answers 404/403
        extract_logs(FakeLegacy(), legacy_spreadsheet_id="NOT_SHARED")
    assert "cannot open the legacy spreadsheet NOT_SHARED" in str(e.value) and "share it with the service account sa@x" in str(e.value)


# --- 새로고침 시각 / refresh time / เวลา refresh ---
def test_extract_writes_the_refresh_time_to_each_queue_sheet():
    w = FakeLegacy()
    summary, logs = extract_logs(w, "dwr")
    assert w.refresh["MIGRATION_QUEUE"] == [QUEUE_REFRESH_LABEL, STAMP] and summary.refreshed_at == STAMP
    assert any(f"Refresh time written to MIGRATION_QUEUE!I2: {STAMP}" in m for m in logs)
    extract_logs(w, "drq")
    assert w.refresh["MIGRATION_QUEUE_DRQ"] == [QUEUE_REFRESH_LABEL, STAMP]
    assert [e for e in w.events if e[0] == "refreshed"] == [("refreshed", "MIGRATION_QUEUE"), ("refreshed", "MIGRATION_QUEUE_DRQ")]
    first_refresh = w.events.index(("refreshed", "MIGRATION_QUEUE"))
    assert [e[0] for e in w.events[:first_refresh + 1]] == ["addSheet", "clear", "header", "append", "refreshed"]   # after the sheet is cleared and filled


def test_refresh_time_is_not_written_when_nothing_was_written():
    w = FakeLegacy()
    extract_logs(w, dry_run=True)
    assert w.refresh == {}
    w = FakeLegacy(queues={"MIGRATION_QUEUE": [["Doc", "Date"], qrow(1, "Done")]})
    summary, _ = extract_logs(w)                                                     # refused (unexpected header): left untouched
    assert w.refresh == {} and summary.refreshed_at == ""
    empty = {"LEGACY-DWR": {3: LEGACY_HEADER, 4: mk({0: txt("R-1"), 4: txt("x")})}, "LEGACY-DRQ": {}}
    w = FakeLegacy(grids=empty)
    extract_logs(w)
    assert w.refresh == {}


def test_a_failed_refresh_time_write_does_not_fail_the_extract():
    w = FakeLegacy()
    w.refresh_error = True
    summary, logs = extract_logs(w)
    assert summary.written == 4 and summary.refreshed_at == "" and w.queues["MIGRATION_QUEUE"][1:] == DWR_ROWS
    assert any("rows are written, but the refresh time could not be written" in m for m in logs)
    assert run_extract(xargs(queue="dwr"), w.clients(), ["dwr"], log=lambda m: None) == 0


def test_the_refresh_time_cells_are_outside_the_queue_columns():
    assert REFRESH_COL == "I" and len(QUEUE_HEADER) == 7                        # A-G data, H empty, I label/time
    w = FakeLegacy(legacy_sid=LEGACY_FILE)
    extract_logs(w, legacy_spreadsheet_id=LEGACY_FILE)
    assert w.refresh["MIGRATION_QUEUE"][1] == STAMP and all(STAMP not in str(r) for r in w.queues["MIGRATION_QUEUE"])


# --- 옛 시트의 A열(No.)이 바뀐 경우 / the legacy "No." changes / เมื่อ No. ของชีตต้นทางเปลี่ยน ---
def renumbered_world():
    """큐를 한 번 만든 뒤 옛 시트 첫 데이터 행의 No. 를 R-001 -> R-777 로 바꾼 세계 / [TH] สร้างคิวครั้งแรกแล้วเปลี่ยน No. ของแถวข้อมูลแถวแรกในชีตเก่าจาก R-001 เป็น R-777
    [EN] A world where the queue was built once and then the No. of the first legacy data row changed from R-001 to R-777"""
    grid = legacy_grid()
    w = FakeLegacy(grids={"LEGACY-DWR": grid, "LEGACY-DRQ": legacy_grid()})
    extract_logs(w)
    queue = w.queues["MIGRATION_QUEUE"]
    queue[1][6], queue[1][3] = "Done", URL(99)                      # the first item is Done and its link differs from the legacy one
    queue[2][6] = "Error: Drive Access | x"
    grid[4][0] = txt("R-777")
    w.events.clear()
    return w


def test_a_changed_no_in_the_legacy_sheet_renames_the_doc_no_of_the_same_items():
    w = renumbered_world()
    queue = w.queues["MIGRATION_QUEUE"]
    summary, logs = extract_logs(w)
    assert [r[0] for r in queue[1:]] == ["R-777-CIV-001", "R-777-ELE-5", "R-777-REM (DMS)-9", "R-003-CIV-003"]
    assert len(queue) == 5 and [r[6] for r in queue[1:]] == ["Waiting", "Error: Drive Access | x", "Waiting", "Waiting"]       # no duplicate row; only the relinked Done row went back to Waiting
    assert queue[1][3] == URL(2) and queue[1][7] == FID(99)                                # its PDF is recomputed; the old file ID waits in H
    assert summary.renumbered == 3 and summary.added == 0 and summary.removed == 0 and summary.relinked == 1
    assert "renumbered 3" in count_rows(logs)["Kept from the queue"] and any("1 Done rows got a new PDF link" in m for m in logs)
    again, _ = extract_logs(w)                                                             # nothing left to rename
    assert again.renumbered == 0 and again.added == 0 and again.relinked == 0 and [r[0] for r in queue[1:]][0] == "R-777-CIV-001"


def test_a_renamed_row_keeps_its_status_and_gets_the_recomputed_columns():
    w = renumbered_world()
    queue = w.queues["MIGRATION_QUEUE"]
    queue[3][6] = "Waiting"
    queue[3][3] = URL(55)                                                                  # a stale link on a Waiting row
    extract_logs(w)
    assert queue[3][0] == "R-777-REM (DMS)-9" and queue[3][3] == "" and queue[3][6] == "Waiting"      # A..F follow the legacy sheet, G stays


def test_renumber_dry_run_reports_the_plan_and_writes_nothing():
    w = renumbered_world()
    before = [list(r) for r in w.queues["MIGRATION_QUEUE"]]
    summary, logs = extract_logs(w, dry_run=True)
    assert w.queues["MIGRATION_QUEUE"] == before and w.events == [] and summary.renumbered == 3
    assert any("sample (renumbered): row 2: R-001-CIV-001 -> R-777-CIV-001" in m for m in logs)
    assert any("would sync MIGRATION_QUEUE" in m for m in logs) and "renumbered 3" in count_rows(logs)["Kept from the queue"]


def test_a_changed_date_is_still_a_new_item_and_an_unknown_cell_text_never_renames():
    existing = [QUEUE_HEADER, ["R-1-CIV-001", "2025-01-01", "CIV", "", "", "P", "Waiting"]]
    same_item_new_no = LegacyItem("R-2-CIV-001", "2025-01-01", "CIV", "", "", False, "001")
    other_date = LegacyItem("R-2-CIV-001", "2025-01-02", "CIV", "", "", False, "001")
    other_text = LegacyItem("R-2-CIV-002", "2025-01-01", "CIV", "", "", False, "002")
    no_text = LegacyItem("R-2-CIV-001", "2025-01-01", "CIV", "", "", False)                 # built by hand without the cell text
    plan = plan_sync(existing, [same_item_new_no])
    assert plan.renumbered == 1 and plan.renumbered_samples == [(2, "R-1-CIV-001", "R-2-CIV-001")] and plan.new == 0 and plan.removed == 0
    for item in (other_date, other_text, no_text):
        plan = plan_sync(existing, [item])
        assert plan.renumbered == 0 and plan.new == 1 and plan.removed == 1 and plan.rows == [item.to_row() + [""]]


def test_one_queue_row_is_never_claimed_by_two_items():
    existing = [QUEUE_HEADER, ["R-1-CIV-001", "2025-01-01", "CIV", "", "", "P", "Done"]]
    a = LegacyItem("R-2-CIV-001", "2025-01-01", "CIV", "", "", False, "001")
    b = LegacyItem("R-3-CIV-001", "2025-01-01", "CIV", "", "", False, "001")
    plan = plan_sync(existing, [a, b])
    assert [r[6] for r in plan.rows] == ["Done", "Waiting"] and (plan.matched, plan.new) == (1, 1)


# --- 같은 항목이 두 행으로 들어 있던 경우 / the same item twice / รายการเดียวมีสองแถว ---
def duplicated_world():
    """
    옛 Doc No(처리됨)와 새 Doc No(Waiting)로 두 번 들어간 큐 (옛 시트의 No. 가 바뀐 뒤 이전 방식의 extract 가 만든 상태)
    [TH] คิวที่รายการเดียวเข้าสองครั้ง ด้วย Doc No เก่า (ประมวลผลแล้ว) และ Doc No ใหม่ (Waiting) (สภาพหลัง extract แบบเก่าที่ No. ของชีตเก่าเปลี่ยน)
    [EN] A queue where every item went in twice, under the old Doc No (processed) and the new one (Waiting): what an older extract left after the legacy No. changed
    """
    grid = legacy_grid()
    w = FakeLegacy(grids={"LEGACY-DWR": grid, "LEGACY-DRQ": legacy_grid()})
    extract_logs(w)
    queue = w.queues["MIGRATION_QUEUE"]
    queue[1][6], queue[2][6] = "Done", "Error: Drive Access | x"                   # the original rows were processed
    grid[4][0] = txt("R-777")                                                      # the legacy No. of the first data row changes
    for row in [list(r) for r in queue[1:4]]:                                      # the copies an older extract appended under the new Doc No
        copy = list(row)
        copy[0] = copy[0].replace("R-001", "R-777")
        copy[6] = "Waiting"
        queue.append(copy)
    w.events.clear()
    return w


def test_the_two_rows_of_one_item_become_one_and_keep_the_processed_status():
    w = duplicated_world()
    queue = w.queues["MIGRATION_QUEUE"]
    assert len(queue) == 8
    summary, logs = extract_logs(w)
    assert [r[0] for r in queue[1:]] == ["R-777-CIV-001", "R-777-ELE-5", "R-777-REM (DMS)-9", "R-003-CIV-003"]
    assert [r[6] for r in queue[1:]] == ["Done", "Error: Drive Access | x", "Waiting", "Waiting"]      # the processed copy wins, never the Waiting copy
    assert len(queue) == 5 and summary.duplicates == 3 and summary.removed == 0 and summary.renumbered == 3       # the old (earlier / processed) row of every item is the one that stays, and it gets the new Doc No
    assert [e[0] for e in w.events] == ["header", "put", "clear_tail", "refreshed"]                          # shorter than before: only the tail is cleared
    assert count_rows(logs)["Duplicate rows merged"] == "3"


def test_status_rank_prefers_the_processed_row_and_the_earlier_row_on_a_tie():
    assert [status_rank(s) for s in ("Done", "Error: x", "Something", "Waiting", "")] == [3, 2, 1, 0, 0]
    existing = [QUEUE_HEADER, ["A-CIV-1", "d", "CIV", "", "", "P", "Waiting"], ["B-CIV-1", "d", "CIV", "", "", "P", "Error: a"], ["C-CIV-1", "d", "CIV", "", "", "P", "Error: b"]]
    item = LegacyItem("D-CIV-1", "d", "CIV", "", "", False, "1")
    plan = plan_sync(existing, [item])
    assert plan.rows == [["D-CIV-1", "d", "CIV", "", "", "P", "Error: a", ""]] and plan.duplicates == 2


def test_sync_writes_in_place_in_chunks_and_never_clears_the_whole_sheet(monkeypatch):
    monkeypatch.setattr(queue_writer, "EXTRACT_ROWS_PER_WRITE", 3)
    drq = FakeLegacy(queues={"MIGRATION_QUEUE_DRQ": [list(QUEUE_HEADER), ["x", "d", "CIV", "", "", "P", "Done"]]})
    summary, _ = extract_logs(drq, "drq")
    assert [e[:2] + e[3:] for e in drq.events if e[0] == "put"] == [("put", "MIGRATION_QUEUE_DRQ", 3), ("put", "MIGRATION_QUEUE_DRQ", 2)]      # 5 rows -> 3 + 2
    assert "clear" not in [e[0] for e in drq.events] and core(drq.queues["MIGRATION_QUEUE_DRQ"][1:]) == DRQ_ROWS and summary.written == 5


def test_sync_grows_the_sheet_first_when_it_has_too_few_rows():
    w = FakeLegacy(queues={"MIGRATION_QUEUE": [list(QUEUE_HEADER), ["x", "d", "CIV", "", "", "P", "Done"]]})
    w.queue_row_count = 3                                                           # header + 2 rows fit, the 4 new rows do not
    extract_logs(w)
    assert w.events[0] == ("grow", 2) and w.queue_row_count == 5 and core(w.queues["MIGRATION_QUEUE"][1:]) == DWR_ROWS


def test_the_time_cells_are_not_touched_when_the_queue_is_synced():
    w = FakeLegacy(queues={"MIGRATION_QUEUE": [list(QUEUE_HEADER), list(DWR_ROWS[0])]})
    extract_logs(w)
    assert all(not (e[0] == "clear") for e in w.events) and all("!I" not in str(e) and "!K" not in str(e) for e in w.events)


# --- 링크가 바뀐 Done 행 / a Done row whose PDF changed / แถว Done ที่ PDF เปลี่ยน ---
def stored(url, status, replaces="", cp="P"):
    """큐에 이미 있는 행 (A~H) / [TH] แถวที่มีอยู่แล้วในคิว (A~H) / [EN] A row that is already in the queue (A-H)"""
    return ["R-1-CIV-1", "d", "CIV", url, "", cp, status, replaces]


def legacy_item(url):
    return LegacyItem("R-1-CIV-1", "d", "CIV", url, "", False, "1")


def one_row(existing_row, url):
    plan = plan_sync([QUEUE_HEADER, existing_row], [legacy_item(url)])
    return plan.rows[0], plan


def test_a_done_row_with_a_new_pdf_goes_back_to_waiting_and_remembers_the_old_file():
    row, plan = one_row(stored(URL(1), "Done"), URL(2))
    assert row == ["R-1-CIV-1", "d", "CIV", URL(2), "", "P", "Waiting", FID(1)] and plan.relinked == 1
    assert plan.kept == {"done": 0, "error": 0, "other": 0, "waiting": 1}


def test_the_same_file_in_another_url_format_is_not_a_new_pdf():
    other_format = f"https://drive.google.com/file/d/{FID(1)}/view?usp=sharing"
    row, plan = one_row(stored(URL(1), "Done"), other_format)
    assert row[3] == other_format and row[6:] == ["Done", ""] and plan.relinked == 0


def test_a_done_row_whose_link_disappears_from_the_legacy_sheet_stays_done():
    row, plan = one_row(stored(URL(1), "Done"), "")
    assert row[3] == "" and row[6:] == ["Done", ""] and plan.relinked == 0


def test_error_and_waiting_rows_keep_their_status_and_get_no_old_file_id():
    for status in ("Error: Drive Access | x", "Waiting", ""):
        row, plan = one_row(stored(URL(1), status), URL(2))
        assert row[3] == URL(2) and row[6:] == [status, ""] and plan.relinked == 0


def test_a_row_that_waits_for_a_replacement_keeps_its_old_file_id_when_the_link_changes_again():
    row, plan = one_row(stored(URL(2), "Waiting", replaces=FID(1)), URL(3))
    assert row[3] == URL(3) and row[6:] == ["Waiting", FID(1)] and plan.relinked == 0
    row, _ = one_row(stored(URL(2), "Error: Drive Access | x", replaces=FID(1)), URL(3))
    assert row[6:] == ["Error: Drive Access | x", FID(1)]                                  # a failed migration keeps protecting the old DB row


def test_the_link_going_back_to_the_migrated_file_cancels_the_replacement():
    row, plan = one_row(stored(URL(2), "Waiting", replaces=FID(1)), URL(1))
    assert row[3] == URL(1) and row[6:] == ["Done", ""] and plan.relinked == 0


def test_a_done_row_never_keeps_an_old_file_id():
    row, _ = one_row(stored(URL(1), "Done", replaces=FID(9)), URL(1))
    assert row[6:] == ["Done", ""]


def test_clear_replaces_empties_column_h_of_the_given_rows_only():
    w = MemorySheets({"MIGRATION_QUEUE": [list(QUEUE_HEADER) + ["Replaces"], ["a"] * 7 + ["f1"], ["b"] * 7 + ["f2"], ["c"] * 7 + ["f3"]]})
    clear_replaces(w.clients().sheets, "SID", "MIGRATION_QUEUE", [4, 2, 4])
    assert [r[7] for r in w.tabs["MIGRATION_QUEUE"][1:]] == ["", "f2", ""] and [r[6] for r in w.tabs["MIGRATION_QUEUE"][1:]] == ["a", "b", "c"]
    assert w.inputs == ["RAW"]


# --- 큐 안전장치 / the queue safety net / ตัวกันพลาดของคิว ---
def queue_with_junk(count):
    junk = [[f"junk-{i}", "2020-01-01", "X", "", "", "P", "Done"] for i in range(count)]
    return {"MIGRATION_QUEUE": [list(QUEUE_HEADER)] + [list(r) for r in DWR_ROWS] + junk}


def test_extract_refuses_to_remove_more_rows_than_the_limit_unless_it_is_allowed():
    w = FakeLegacy(queues=queue_with_junk(130))
    before = [list(r) for r in w.queues["MIGRATION_QUEUE"]]
    summary, logs = extract_logs(w)
    assert summary.refused == "too many removals" and summary.queue_rows is None and w.events == [] and w.queues["MIGRATION_QUEUE"] == before
    assert any("Not syncing MIGRATION_QUEUE: 130 rows would be removed, more than the limit of 100" in m and "--allow-large-removal" in m for m in logs)
    summary, _ = extract_logs(w, allow_large_removal=True)
    assert summary.refused == "" and summary.removed == 130 and len(w.queues["MIGRATION_QUEUE"]) == 5


def test_removing_exactly_the_limit_is_allowed_and_a_dry_run_shows_the_refusal_without_failing():
    w = FakeLegacy(queues=queue_with_junk(100))
    assert extract_logs(w)[0].removed == 100
    big = FakeLegacy(queues=queue_with_junk(130))
    assert run_extract(xargs(queue="dwr"), big.clients(), ["dwr"], log=lambda m: None) == 2
    assert run_extract(xargs(queue="dwr", dry_run=True), big.clients(), ["dwr"], log=lambda m: None) == 0


# --- 이번 extract 후의 큐 행 / the queue rows after this extract / แถวคิวหลัง extract รอบนี้ ---
def test_the_summary_carries_the_queue_rows_the_db_sync_works_on():
    w = FakeLegacy(queues={"MIGRATION_QUEUE": [list(r) for r in merge_queue()]})
    summary, _ = extract_logs(w)
    assert summary.queue_rows == w.queues["MIGRATION_QUEUE"][1:] and len(summary.queue_rows[0]) == 8
    dry = FakeLegacy(queues={"MIGRATION_QUEUE": [list(r) for r in merge_queue()]})
    summary, _ = extract_logs(dry, dry_run=True)
    assert summary.queue_rows is not None and [r[0] for r in summary.queue_rows] == [r[0] for r in DWR_ROWS] and dry.events == []        # the planned rows, nothing written
    fresh = FakeLegacy()
    summary, _ = extract_logs(fresh)
    assert summary.queue_rows == DWR_ROWS
    refused = FakeLegacy(queues={"MIGRATION_QUEUE": [["Doc", "Date"], qrow(1, "Done")]})
    assert extract_logs(refused)[0].queue_rows is None
    empty = FakeLegacy(grids={"LEGACY-DWR": {3: LEGACY_HEADER, 4: mk({0: txt("R-1"), 4: txt("x")})}, "LEGACY-DRQ": {}})
    assert extract_logs(empty)[0].queue_rows is None


class DbSyncRecorder:
    """sync_db_for_queue 대신 호출을 기록 / [TH] บันทึกการเรียกแทน sync_db_for_queue / [EN] Records the calls instead of sync_db_for_queue"""

    def __init__(self, result=None, error=None):
        self.calls, self.result, self.error = [], result or DbSyncSummary(), error

    def __call__(self, args, clients, key, queue_rows, log=print):
        self.calls.append((key, [list(r) for r in queue_rows], args.dry_run))
        if self.error:
            raise self.error
        return self.result


def test_extract_with_sync_db_makes_the_db_match_the_rows_of_the_synced_queue(monkeypatch):
    recorder = DbSyncRecorder()
    monkeypatch.setattr(extract_module, "sync_db_for_queue", recorder)
    w = FakeLegacy(queues={"MIGRATION_QUEUE": [list(r) for r in merge_queue()]})
    assert run_extract(xargs(queue="dwr", sync_db=True), w.clients(), ["dwr"], log=lambda m: None) == 0
    assert [c[0] for c in recorder.calls] == ["dwr"] and recorder.calls[0][1] == w.queues["MIGRATION_QUEUE"][1:] and recorder.calls[0][2] is False
    assert "update" not in [e[0] for e in w.events] and w.events[0][0] == "header"                         # the queue was written before the DB was looked at


def test_extract_with_sync_db_and_dry_run_hands_over_the_planned_rows(monkeypatch):
    recorder = DbSyncRecorder()
    monkeypatch.setattr(extract_module, "sync_db_for_queue", recorder)
    w = FakeLegacy(queues={"MIGRATION_QUEUE": [list(r) for r in merge_queue()]})
    before = [list(r) for r in w.queues["MIGRATION_QUEUE"]]
    assert run_extract(xargs(queue="dwr", sync_db=True, dry_run=True), w.clients(), ["dwr"], log=lambda m: None) == 0
    assert w.queues["MIGRATION_QUEUE"] == before and recorder.calls[0][2] is True and [r[0] for r in recorder.calls[0][1]] == [r[0] for r in DWR_ROWS]


def test_extract_without_sync_db_never_touches_the_db(monkeypatch):
    recorder = DbSyncRecorder()
    monkeypatch.setattr(extract_module, "sync_db_for_queue", recorder)
    run_extract(xargs(queue="dwr"), FakeLegacy().clients(), ["dwr"], log=lambda m: None)
    assert recorder.calls == []


def test_extract_skips_the_db_sync_of_a_queue_that_was_refused_or_failed(monkeypatch):
    recorder = DbSyncRecorder()
    monkeypatch.setattr(extract_module, "sync_db_for_queue", recorder)
    bad_header = FakeLegacy(queues={"MIGRATION_QUEUE": [["Doc", "Date"], qrow(1, "Done")]})
    assert run_extract(xargs(queue="dwr", sync_db=True), bad_header.clients(), ["dwr"], log=lambda m: None) == 2
    too_many = FakeLegacy(queues=queue_with_junk(130))
    assert run_extract(xargs(queue="dwr", sync_db=True), too_many.clients(), ["dwr"], log=lambda m: None) == 2
    missing = FakeLegacy(grids={"LEGACY-DRQ": legacy_grid()})
    assert run_extract(xargs(queue="dwr", sync_db=True), missing.clients(), ["dwr"], log=lambda m: None) == 2
    assert recorder.calls == []


def test_extract_with_sync_db_reports_a_refused_or_failed_db_sync_in_the_exit_code(monkeypatch):
    logs = []
    monkeypatch.setattr(extract_module, "sync_db_for_queue", DbSyncRecorder(result=DbSyncSummary(refused="too many removals")))
    assert run_extract(xargs(queue="dwr", sync_db=True), FakeLegacy().clients(), ["dwr"], log=logs.append) == 2
    assert run_extract(xargs(queue="dwr", sync_db=True, dry_run=True), FakeLegacy().clients(), ["dwr"], log=logs.append) == 0
    monkeypatch.setattr(extract_module, "sync_db_for_queue", DbSyncRecorder(error=SheetsError("HTTP 403: boom")))
    assert run_extract(xargs(queue="dwr", sync_db=True), FakeLegacy().clients(), ["dwr"], log=logs.append) == 2
    assert any("✗ Could not sync DB_DWR: HTTP 403: boom" in m for m in logs)
