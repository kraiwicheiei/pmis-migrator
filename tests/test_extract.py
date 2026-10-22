"""
--extract 테스트: 새로 만들기, 합치기(merge), 다른 파일에서 읽기, 새로고침 시각
[TH] เทสต์ --extract: สร้างใหม่, รวม (merge), อ่านจากไฟล์อื่น, เวลา refresh
[EN] Tests for --extract: rebuild, merge, reading from another file, the refresh time
"""

import json

import pytest

from migrator import legacy_sheet, queue_writer
from migrator.extract import extract_queue, run_extract
from migrator.legacy_sheet import LegacyItem
from migrator.queue_writer import MergePlan, QUEUE_HEADER, apply_merge, plan_merge
from migrator.sheets import SheetsError
from migrator.stamps import QUEUE_REFRESH_LABEL, REFRESH_COL

from tests.fakes import NON_ENGLISH, URL, qrow
from tests.legacy_fakes import DRQ_ROWS, DWR_ROWS, FakeLegacy, LEGACY_FILE, LEGACY_HEADER, STAMP, extract_logs, legacy_grid, merge_queue, mk, txt, xargs


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
    assert w.queues["MIGRATION_QUEUE"] == [QUEUE_HEADER, old] + DWR_ROWS            # merged: the existing row stays, the new items are appended


def test_extract_dry_run_reads_but_writes_nothing():
    w = FakeLegacy()
    summary, logs = extract_logs(w, dry_run=True)
    assert w.events == [] and w.queues == {} and w.grid_reads
    assert summary.items == 4 and summary.written == 0
    assert any("--dry-run" in m and "new tab" in m for m in logs) and any("sample: R-001-CIV-001" in m for m in logs)


def test_extract_merges_into_a_queue_in_progress_and_keeps_the_statuses():
    existing = merge_queue()
    w = FakeLegacy(queues={"MIGRATION_QUEUE": [list(r) for r in existing]})
    summary, logs = extract_logs(w)
    queue = w.queues["MIGRATION_QUEUE"]
    assert queue[1] == existing[1]                                                      # Done: untouched even though the legacy link differs
    assert queue[2] == [*DWR_ROWS[1][:6], "Error: Drive Access | x"]                    # B..F refreshed from the legacy sheet, status kept
    assert queue[3] == existing[3] and queue[4] == existing[4]                          # identical row and a queue-only row are untouched
    assert queue[5:] == [DWR_ROWS[3]] and len(queue) == 6                               # only the new item is appended (as Waiting)
    assert w.updated_rows == [("MIGRATION_QUEUE", 3)] and "clear" not in [e[0] for e in w.events]
    assert [e[0] for e in w.events] == ["update", "append", "refreshed"]
    assert (summary.added, summary.updated, summary.unchanged, summary.kept_done, summary.written) == (1, 1, 1, 1, 1)
    assert summary.refused == ""
    assert any("1 Done rows have a different PDF link in the legacy sheet (kept as is)" in m for m in logs)
    assert any("Merged into MIGRATION_QUEUE: +1 new, 1 updated, 1 unchanged, 1 Done kept as is, 1 not in the legacy sheet any more" in m for m in logs)


def test_a_second_merge_changes_nothing_but_the_refresh_time():
    w = FakeLegacy(queues={"MIGRATION_QUEUE": [list(r) for r in merge_queue()]})
    extract_logs(w)
    after_first = [list(r) for r in w.queues["MIGRATION_QUEUE"]]
    w.events.clear()
    summary, logs = extract_logs(w)
    assert w.queues["MIGRATION_QUEUE"] == after_first
    assert [e[0] for e in w.events] == ["refreshed"] and (summary.added, summary.updated) == (0, 0)
    assert any("Merged into MIGRATION_QUEUE: +0 new, 0 updated, 3 unchanged, 1 Done kept as is" in m for m in logs)


def test_extract_merge_dry_run_reports_the_plan_and_writes_nothing():
    existing = merge_queue()
    w = FakeLegacy(queues={"MIGRATION_QUEUE": [list(r) for r in existing]})
    summary, logs = extract_logs(w, dry_run=True)
    assert w.events == [] and w.queues["MIGRATION_QUEUE"] == existing and w.refresh == {}
    assert (summary.added, summary.updated, summary.written) == (1, 1, 0)
    assert any("(--dry-run) nothing was written: would merge into MIGRATION_QUEUE: +1 new, 1 updated" in m for m in logs)
    assert any("sample (new): R-003-CIV-003" in m for m in logs)


def test_extract_overwrite_rebuilds_the_queue_and_wipes_the_statuses():
    w = FakeLegacy(queues={"MIGRATION_QUEUE": [list(r) for r in merge_queue()]})
    summary, logs = extract_logs(w, overwrite=True)
    assert w.queues["MIGRATION_QUEUE"] == [QUEUE_HEADER] + DWR_ROWS and summary.written == 4 and "clear" in [e[0] for e in w.events]
    assert any("--overwrite: the existing statuses (Done 2, Error 1) will be wiped" in m for m in logs)


def test_extract_refuses_to_merge_into_a_tab_with_an_unexpected_header():
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


def test_plan_merge_matches_on_doc_no_and_date_and_updates_in_chunks(monkeypatch):
    item = LegacyItem("R-9-CIV-1", "2025-01-01", "CIV", URL(1), "", False)
    other_day = LegacyItem("R-9-CIV-1", "2025-01-02", "CIV", URL(1), "", False)
    existing = [QUEUE_HEADER, ["R-9-CIV-1", "2025-01-01", "CIV", "", "", "P", "Waiting"]]
    plan = plan_merge(existing, [item, other_day])
    assert plan.new_items == [other_day] and plan.updates == {2: ["2025-01-01", "CIV", URL(1), "", "P"]}   # same Doc No on another Date is a new item
    monkeypatch.setattr(queue_writer, "EXTRACT_UPDATES_PER_WRITE", 2)
    w = FakeLegacy(queues={"MIGRATION_QUEUE": [list(QUEUE_HEADER)] + [[f"D{i}", "d", "CIV", "", "", "P", "Waiting"] for i in range(5)]})
    plan = MergePlan(updates={i: ["d", "CIV", URL(i), "", "P"] for i in range(2, 7)})
    apply_merge(w.clients().sheets, "SID", "MIGRATION_QUEUE", plan)
    assert [e[2] for e in w.events if e[0] == "update"] == [2, 2, 1]


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


def test_extract_from_another_spreadsheet_still_checks_the_target_for_existing_progress():
    existing = [QUEUE_HEADER, qrow(1, "Done")]
    w = FakeLegacy(legacy_sid=LEGACY_FILE, queues={"MIGRATION_QUEUE": [list(r) for r in existing]})
    summary, _ = extract_logs(w, legacy_spreadsheet_id=LEGACY_FILE)
    assert w.queues["MIGRATION_QUEUE"] == existing + DWR_ROWS and "clear" not in [e[0] for e in w.events]       # the target's Done row is kept
    assert (summary.added, summary.kept_done) == (4, 0)


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
    statuses = [r[6] for r in queue[1:]]
    summary, logs = extract_logs(w)
    assert [r[0] for r in queue[1:]] == ["R-777-CIV-001", "R-777-ELE-5", "R-777-REM (DMS)-9", "R-003-CIV-003"]
    assert len(queue) == 5 and [r[6] for r in queue[1:]] == statuses                       # no duplicate rows, every status is kept
    assert queue[1][3] == URL(99)                                                          # Done: only the Doc No changed, the link is kept
    assert summary.renumbered == 3 and summary.added == 0 and w.renamed_rows == [("MIGRATION_QUEUE", 2), ("MIGRATION_QUEUE", 3), ("MIGRATION_QUEUE", 4)]
    assert any("3 renumbered (Doc No changed because column A of the legacy sheet changed)" in m for m in logs)
    again, _ = extract_logs(w)                                                             # nothing left to rename
    assert again.renumbered == 0 and again.added == 0 and [r[0] for r in queue[1:]][0] == "R-777-CIV-001"


def test_a_renamed_row_that_is_not_done_also_gets_the_new_legacy_data():
    w = renumbered_world()
    queue = w.queues["MIGRATION_QUEUE"]
    queue[3][6] = "Waiting"
    queue[3][3] = URL(55)                                                                  # a stale link on a Waiting row
    extract_logs(w)
    assert queue[3][0] == "R-777-REM (DMS)-9" and queue[3][3] == "" and queue[3][6] == "Waiting"      # Doc No and B..F follow the legacy sheet, G stays


def test_renumber_dry_run_reports_the_plan_and_writes_nothing():
    w = renumbered_world()
    before = [list(r) for r in w.queues["MIGRATION_QUEUE"]]
    summary, logs = extract_logs(w, dry_run=True)
    assert w.queues["MIGRATION_QUEUE"] == before and w.events == [] and summary.renumbered == 3
    assert any("sample (renumbered): row 2: R-001-CIV-001 -> R-777-CIV-001" in m for m in logs)
    assert any("would merge into MIGRATION_QUEUE" in m and "3 renumbered" in m for m in logs)


def test_a_changed_date_is_still_a_new_item_and_an_unknown_cell_text_never_renames():
    existing = [QUEUE_HEADER, ["R-1-CIV-001", "2025-01-01", "CIV", "", "", "P", "Waiting"]]
    same_item_new_no = LegacyItem("R-2-CIV-001", "2025-01-01", "CIV", "", "", False, "001")
    other_date = LegacyItem("R-2-CIV-001", "2025-01-02", "CIV", "", "", False, "001")
    other_text = LegacyItem("R-2-CIV-002", "2025-01-01", "CIV", "", "", False, "002")
    no_text = LegacyItem("R-2-CIV-001", "2025-01-01", "CIV", "", "", False)                 # built by hand without the cell text
    plan = plan_merge(existing, [same_item_new_no])
    assert plan.renamed == {2: "R-2-CIV-001"} and plan.old_names == {2: "R-1-CIV-001"} and plan.new_items == [] and plan.missing == 0
    for item in (other_date, other_text, no_text):
        plan = plan_merge(existing, [item])
        assert plan.renamed == {} and plan.new_items == [item] and plan.missing == 1


def test_one_queue_row_is_never_claimed_by_two_items():
    existing = [QUEUE_HEADER, ["R-1-CIV-001", "2025-01-01", "CIV", "", "", "P", "Waiting"]]
    a = LegacyItem("R-2-CIV-001", "2025-01-01", "CIV", "", "", False, "001")
    b = LegacyItem("R-3-CIV-001", "2025-01-01", "CIV", "", "", False, "001")
    plan = plan_merge(existing, [a, b])
    assert plan.renamed == {2: "R-2-CIV-001"} and plan.new_items == [b]


def test_apply_merge_writes_a_renamed_doc_no_and_the_other_columns_separately(monkeypatch):
    monkeypatch.setattr(queue_writer, "EXTRACT_UPDATES_PER_WRITE", 2)
    w = FakeLegacy(queues={"MIGRATION_QUEUE": [list(QUEUE_HEADER)] + [[f"D{i}", "d", "CIV", "", "", "P", "Waiting"] for i in range(3)]})
    plan = MergePlan(renamed={2: "N2", 3: "N3"}, updates={3: ["d2", "CIV", "u", "", "C"]})
    apply_merge(w.clients().sheets, "SID", "MIGRATION_QUEUE", plan)
    rows = w.queues["MIGRATION_QUEUE"]
    assert [r[0] for r in rows[1:]] == ["N2", "N3", "D2"] and rows[2][1:6] == ["d2", "CIV", "u", "", "C"] and rows[2][6] == "Waiting"
    assert [e[2] for e in w.events if e[0] == "update"] == [2, 1]                          # three writes, two per request
