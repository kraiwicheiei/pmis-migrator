"""
행 선택과 메인 루프 테스트 (가짜 시트/Drive/Gemini 사용)
[TH] เทสต์การเลือกแถวและวงรอบหลัก (ใช้ชีต/Drive/Gemini จำลอง)
[EN] Tests for the row selection and the main loop (fake sheet / Drive / Gemini)
"""

import pytest

from migrator.reports import build_db_row
from migrator.runner import run, select_jobs
from migrator.specs import SPECS
from migrator.stamps import MIGRATION_LABEL

from tests.fakes import DWR_JSON, FID, FOLDER, FakeWorld, Resp, URL, ns, qrow, run_logs, world
from tests.legacy_fakes import STAMP


def test_select_jobs():
    vals = [["h"] * 7, qrow(1, "Done"), qrow(2, "Waiting"), qrow(3, "Error: API 402 | x"), qrow(4, ""), ["only", "a"]]
    rows = lambda **kw: [j.row for j in select_jobs(vals, only_rows=None, **{"retry_errors": True, "include_blank": False, **kw})]
    assert rows() == [3, 4]
    assert rows(retry_errors=False) == [3]
    assert rows(include_blank=True) == [3, 4, 5]
    assert [j.row for j in select_jobs(vals, retry_errors=True, include_blank=False, only_rows={2, 3, 4})] == [3, 4]


def test_happy_path_batches_and_marks_done():
    w = world(["Waiting"] * 7)
    s = run(ns(batch=3), w.clients(), log=lambda m: None)
    assert s.done == 7 and not s.errors
    assert [r[6] for r in w.queue[1:]] == ["Done"] * 7
    assert [len(a) for a in w.appends] == [3, 3, 1]
    assert len(w.db) == 1 + 7
    assert {r[17] for r in w.db[1:]} == {URL(i) for i in range(1, 8)}


def test_limit_and_retry_errors_and_skip_done():
    w = world(["Done", "Error: API 402 | x", "Waiting", "Waiting"])
    s = run(ns(limit=2), w.clients(), log=lambda m: None)
    assert s.done == 2
    assert [r[6] for r in w.queue[1:]] == ["Done", "Done", "Done", "Waiting"]
    assert w.gemini_calls == 2


def test_drive_access_error_recorded_and_others_continue():
    w = world(["Waiting"] * 3)
    del w.drive_files[FID(2)]
    s = run(ns(), w.clients(), log=lambda m: None)
    assert s.done == 2 and s.errors == {"Drive Access": 1}
    assert w.queue[2][6].startswith("Error: Drive Access | fileId=" + FID(2)) and "service account" in w.queue[2][6]
    assert w.queue[1][6] == w.queue[3][6] == "Done"


def test_402_stops_run_and_leaves_rest_waiting():
    w = world(["Waiting"] * 9)
    w.gemini_default = lambda: Resp(402, text='{"error":{"status":"RESOURCE_EXHAUSTED"}}')
    s = run(ns(batch=3, workers=1), w.clients(), log=lambda m: None)
    assert s.stopped_reason and "402" in s.stopped_reason
    # 즉시 중단되어 이후 행은 호출하지 않음
    # [TH] หยุดทันที ไม่ไล่ยิงต่อ
    # [EN] Stops immediately and does not keep calling for the remaining rows
    assert w.gemini_calls == 1
    assert w.queue[1][6].startswith("Error: API 402")
    assert [r[6] for r in w.queue[2:]] == ["Waiting"] * 8
    assert not w.appends


def test_500_is_retried_then_succeeds():
    w = world(["Waiting"])
    w.gemini_script = [Resp(500, text='{"error":{"status":"INTERNAL"}}'), Resp(429, text='{"error":{"details":[{"retryDelay":"3s"}]}}')]
    s = run(ns(), w.clients(), log=lambda m: None)
    assert s.done == 1 and w.gemini_calls == 3


def test_persistent_500_becomes_error_row():
    w = world(["Waiting"])
    w.gemini_default = lambda: Resp(500, text='{"error":{"status":"INTERNAL"}}')
    s = run(ns(), w.clients(), log=lambda m: None)
    assert s.errors == {"API 500": 1} and w.gemini_calls == 5
    assert w.queue[1][6].startswith("Error: API 500 | ")


def test_repeated_429_stops_run():
    w = world(["Waiting"] * 10)
    w.gemini_default = lambda: Resp(429, text='{"error":{"status":"RESOURCE_EXHAUSTED"}}')
    s = run(ns(batch=2, workers=1), w.clients(), log=lambda m: None)
    assert "429" in s.stopped_reason
    assert sum(1 for r in w.queue[1:] if r[6].startswith("Error: API 429")) == 4
    assert sum(1 for r in w.queue[1:] if r[6] == "Waiting") == 6


def test_bad_json_and_no_result():
    w = world(["Waiting"] * 2)
    w.gemini_script = [
        Resp(200, {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}),
        Resp(200, {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}),
    ]
    s = run(ns(workers=1), w.clients(), log=lambda m: None)
    assert s.errors == {"JSON Parse": 1, "No Result": 1}


def test_db_write_failure_marks_rows_and_rerun_reconciles_without_ai():
    w = world(["Waiting"] * 2)
    w.fail_append = True
    s = run(ns(), w.clients(), log=lambda m: None)
    assert s.errors == {"DB Write": 2} and all(r[6].startswith("Error: DB Write") for r in w.queue[1:])
    # 실제로는 DB 쓰기가 성공했다고 가정 (응답만 유실) -> 다음 실행에서 AI 호출/중복 쓰기가 없어야 함
    # [TH] สมมติว่าจริงๆ แล้วเขียน DB สำเร็จ (response หาย) -> รอบถัดไปไม่ควรเรียก AI/เขียนซ้ำ
    # [EN] Pretend the DB write actually succeeded (only the response was lost) -> the next run must not call the AI or write again
    w.db.extend([build_db_row(SPECS["dwr"], DWR_JSON, URL(i), FOLDER) for i in (1, 2)])
    w.fail_append = False
    calls_before = w.gemini_calls
    s2 = run(ns(), w.clients(), log=lambda m: None)
    assert s2.reconciled == 2 and s2.done == 0 and w.gemini_calls == calls_before
    assert [r[6] for r in w.queue[1:]] == ["Done", "Done"] and len(w.db) == 3


def test_no_url_row():
    w = FakeWorld([["DOC-1", "", "", "", FOLDER, "C", "Waiting"]])
    s = run(ns(), w.clients(), log=lambda m: None)
    assert s.errors == {"No URL": 1} and w.queue[1][6].startswith("Error: No URL | PDF URL cell")


def test_dry_run_and_check_access_do_not_write():
    w = world(["Waiting"] * 3)
    del w.drive_files[FID(3)]
    logs = []
    run(ns(dry_run=True), w.clients(), log=logs.append)
    assert w.gemini_calls == 0 and not w.batch_updates and not w.appends
    run(ns(check_access=True), w.clients(), log=logs.append)
    assert w.gemini_calls == 0 and not w.batch_updates and not w.appends
    assert any("row 4" in m and "Drive Access" in m for m in logs)
    assert any("Reachable: 2/3" in m for m in logs)


# --check-access 의 모든 출력 줄에 큐 시트 이름이 표시되고, 다른 큐 안내가 나온다
# [TH] ทุกบรรทัดผลลัพธ์ของ --check-access ระบุชื่อชีตคิว และมีคำแนะนำสำหรับคิวอีกอัน
# [EN] Every --check-access result line names the queue sheet, and the other queue is pointed out
@pytest.mark.parametrize("queue, tab, other_tab, other_key", [
    ("dwr", "MIGRATION_QUEUE", "MIGRATION_QUEUE_DRQ", "drq"),
    ("drq", "MIGRATION_QUEUE_DRQ", "MIGRATION_QUEUE", "dwr"),
])
def test_check_access_names_the_queue_sheet(queue, tab, other_tab, other_key):
    w = world(["Waiting"] * 3)
    del w.drive_files[FID(3)]
    logs = []
    run(ns(queue=queue, check_access=True), w.clients(), log=logs.append)
    tagged = [m for m in logs if m.startswith(f"[{tab}]") or f"✗ [{tab}]" in m]
    assert any("Checking Drive access" in m and f"--queue {queue}" in m for m in tagged)
    assert any("row 4" in m and "Drive Access" in m for m in tagged)
    assert any("Reachable: 2/3" in m for m in tagged)
    # 다른 큐 시트 이름은 "다른 큐 안내" 줄에만 나타나야 한다 / the other sheet is named only in the hint line
    hint = [m for m in logs if f"--queue {other_key}" in m]
    assert len(hint) == 1 and other_tab in hint[0] and "--check-access" in hint[0]
    assert not any(f"[{other_tab}]" in m for m in logs)


def test_only_rows():
    w = world(["Error: x", "Waiting", "Waiting"])
    s = run(ns(only_rows="3"), w.clients(), log=lambda m: None)
    assert s.done == 1 and [r[6] for r in w.queue[1:]] == ["Error: x", "Done", "Waiting"]


def test_check_access_hint_for_other_queue_only_when_single_queue():
    w = world(["Waiting"])
    logs = []
    run(ns(check_access=True, hint_other_queue=False), w.clients(), log=logs.append)
    assert not any("--check-access --queue" in m for m in logs)
    logs = []
    run(ns(check_access=True), w.clients(), log=logs.append)
    assert any("--check-access --queue drq" in m for m in logs)


def test_zero_rows_in_empty_queue_points_to_the_spreadsheet_id():
    w = FakeWorld([])
    logs = run_logs(w)
    assert any("empty (header only" in m for m in logs)
    assert any("has no data rows" in m and "different spreadsheet" in m for m in logs) and any("ID: SID" in m for m in logs)
    assert w.gemini_calls == 0


def test_zero_rows_when_everything_is_done_lists_the_statuses():
    w = world(["Done", "Done", "Done"])
    logs = run_logs(w)
    assert any("Rows to process: 0" in m for m in logs)
    assert any("'Done'×3" in m and "'Waiting'" in m for m in logs) and any("Every row is already Done" in m and "--extract --overwrite" in m for m in logs)


def test_zero_rows_with_unexpected_status_text_shows_it_exactly():
    w = world(["waiting", "Pending", "Pending"])
    logs = run_logs(w)
    assert any("'Pending'×2" in m and "'waiting'×1" in m and "case-sensitive" in m for m in logs)


def test_zero_rows_with_blank_status_suggests_include_blank():
    w = world(["", ""])
    logs = run_logs(w)
    assert any("2 rows have a blank column G but a URL" in m and "--include-blank" in m for m in logs)
    assert w.gemini_calls == 0
    # --include-blank 을 주면 그 행들이 처리됨 / [TH] ใส่ --include-blank แล้วแถวเหล่านั้นถูกทำ / [EN] with --include-blank those rows are processed
    assert run(ns(include_blank=True), w.clients(), log=lambda m: None).done == 2


def test_zero_rows_warns_when_column_g_header_is_not_ai_processed():
    w = world(["Done"])
    w.queue[0][6] = "Status"
    assert any("The column G header is 'Status'" in m for m in run_logs(w))


def test_no_explanation_when_there_are_rows_to_process():
    w = world(["Waiting"])
    logs = run_logs(w)
    assert not any("The script processes only rows whose column G" in m or "has no data rows" in m for m in logs)


def test_select_jobs_carries_doc_no_from_column_a():
    vals = [["h"] * 7, qrow(1, "Waiting")]
    vals[1][0] = "  ACME-DWR-CIV-0231 "
    assert [j.doc_no for j in select_jobs(vals, retry_errors=True, include_blank=False, only_rows=None)] == ["ACME-DWR-CIV-0231"]
    assert SPECS["dwr"].no_key == "DWR_No" and SPECS["drq"].no_key == "DRQ_No"


# --- 마이그레이션 실행 시각 / migration run time / เวลาที่รัน migration ---
def test_a_real_run_writes_the_migration_time_to_the_queue_sheet():
    w = world(["Waiting"] * 2)
    logs = []
    s = run(ns(), w.clients(), log=logs.append)
    assert w.migration_time == [[MIGRATION_LABEL, STAMP]] and s.migrated_at == STAMP
    assert any(f"Migration time written to MIGRATION_QUEUE!K2: {STAMP}" in m for m in logs)
    assert [r[6] for r in w.queue[1:]] == ["Done", "Done"]                          # the time cells never touch the statuses


def test_the_migration_time_is_written_for_every_real_run_even_with_nothing_to_do_or_a_stopped_run():
    w = world(["Done"])
    run(ns(), w.clients(), log=lambda m: None)
    assert w.migration_time == [[MIGRATION_LABEL, STAMP]]
    w = world(["Waiting"] * 3)
    w.gemini_default = lambda: Resp(402, text='{"error":{"status":"RESOURCE_EXHAUSTED"}}')
    s = run(ns(workers=1), w.clients(), log=lambda m: None)
    assert s.stopped_reason and w.migration_time == [[MIGRATION_LABEL, STAMP]]
    w = world(["Waiting"])
    run(ns(), w.clients(), log=lambda m: None)
    run(ns(), w.clients(), log=lambda m: None)
    assert len(w.migration_time) == 2                                              # every run overwrites K1:K2


def test_dry_run_and_check_access_do_not_write_the_migration_time():
    w = world(["Waiting"] * 2)
    run(ns(dry_run=True), w.clients(), log=lambda m: None)
    run(ns(check_access=True), w.clients(), log=lambda m: None)
    assert w.migration_time == []


def test_a_failed_migration_time_write_does_not_change_the_result_of_the_run():
    w = world(["Waiting"] * 2)
    w.fail_migration_time = True
    logs = []
    s = run(ns(), w.clients(), log=logs.append)
    assert s.done == 2 and s.migrated_at == "" and w.migration_time == []
    assert any("The run finished, but the migration time could not be written to MIGRATION_QUEUE!K1:K2" in m for m in logs)


# --- 바뀐 PDF 의 DB 행을 제자리에서 교체 / replacing the DB row of a changed PDF in place / แทนที่แถว DB ของ PDF ที่เปลี่ยนในที่เดิม ---
OLD = 50                                                                            # the number of the old PDF (URL(50) / FID(50))


def replace_world(statuses=("Waiting",), old_in_db=True, replaces=(OLD,)):
    """
    큐 행 2.. 는 새 PDF(URL(1)..), H열에 옛 file ID. DB 2행은 옛 PDF 로 쓴 행 (A열에 사람이 쓴 값이 있음)
    [TH] แถวคิว 2.. ใช้ PDF ใหม่ (URL(1)..) และคอลัมน์ H เก็บ file ID เก่า แถว 2 ของ DB คือแถวที่เขียนจาก PDF เก่า (คอลัมน์ A มีค่าที่คนพิมพ์)
    [EN] Queue rows 2.. use the new PDF (URL(1)..) and column H holds the old file ID. DB row 2 is the row written from the old PDF (column A holds a value a person typed)
    """
    w = world(list(statuses))
    for row, old in zip(w.queue[1:], list(replaces) + [None] * len(statuses)):
        if old:
            row.append(FID(old))
    if old_in_db:
        old_row = build_db_row(SPECS["dwr"], {**DWR_JSON, "DWR_No": "OLD-VERSION"}, URL(OLD), FOLDER, "P")
        old_row[0] = "CSC-REF-7"
        w.db.append(old_row)
    return w


def test_a_changed_pdf_replaces_the_old_db_row_in_place():
    w = replace_world()
    logs = []
    s = run(ns(), w.clients(), log=logs.append)
    assert s.done == 1 and s.replaced == 1 and len(w.db) == 2 and w.appends == []                 # no new row at the end
    row = w.db[1]
    assert row[1] == "ACME-DWR-X" and row[17] == URL(1) and row[19] == "C"                         # the new data and the C/P of the queue row
    assert row[0] == "CSC-REF-7" and row[20] == "Synced"                                         # a value in column A is never overwritten; the new data is in sync with the legacy sheet
    assert w.queue[1][6] == "Done" and w.queue[1][7] == ""                                        # Done, and column H is cleared
    assert "USER_ENTERED" in w.batch_inputs                                                       # the same way an append writes
    assert any("1 of them replaced the old row in place" in m for m in logs) and any("Replaced in place: 1 rows of DB_DWR" in m for m in logs)


def test_the_old_db_row_is_kept_when_another_queue_row_still_uses_the_old_pdf():
    w = replace_world(statuses=("Waiting", "Done"))
    w.queue[2][3] = URL(OLD)                                                                      # queue row 3 still points to the old PDF
    s = run(ns(), w.clients(), log=lambda m: None)
    assert s.replaced == 0 and len(w.db) == 3 and w.db[1][1] == "OLD-VERSION" and w.db[2][1] == "ACME-DWR-X"      # appended, the old row is untouched
    assert w.queue[1][6] == "Done" and w.queue[1][7] == ""


def test_a_changed_pdf_whose_old_file_is_not_in_the_db_is_appended():
    w = replace_world(old_in_db=False)
    s = run(ns(), w.clients(), log=lambda m: None)
    assert s.replaced == 0 and len(w.db) == 2 and w.db[1][17] == URL(1) and w.queue[1][7] == ""


def test_two_rows_with_the_same_old_pdf_replace_the_row_once_and_append_the_other():
    w = replace_world(statuses=("Waiting", "Waiting"), replaces=(OLD, OLD))
    s = run(ns(), w.clients(), log=lambda m: None)
    assert s.done == 2 and s.replaced == 1 and len(w.db) == 3
    assert sorted(r[17] for r in w.db[1:]) == sorted([URL(1), URL(2)]) and [r[7] for r in w.queue[1:]] == ["", ""]


def test_a_failed_migration_keeps_the_old_db_row_and_the_old_file_id():
    w = replace_world()
    del w.drive_files[FID(1)]
    s = run(ns(), w.clients(), log=lambda m: None)
    assert s.errors == {"Drive Access": 1} and s.replaced == 0
    assert w.db[1][1] == "OLD-VERSION" and w.db[1][17] == URL(OLD) and w.queue[1][6].startswith("Error: Drive Access") and w.queue[1][7] == FID(OLD)


def test_a_new_pdf_that_is_already_in_the_db_needs_no_replacement():
    w = replace_world()
    w.db.append(build_db_row(SPECS["dwr"], DWR_JSON, URL(1), FOLDER, "C"))                       # somebody already migrated the new file
    s = run(ns(), w.clients(), log=lambda m: None)
    assert s.reconciled == 1 and w.gemini_calls == 0 and w.queue[1][6] == "Done" and w.queue[1][7] == ""
    assert w.db[1][1] == "OLD-VERSION"                                                            # the old row is left for --sync-db to remove


def test_replacement_works_with_no_reconcile_too():
    w = replace_world()
    s = run(ns(no_reconcile=True), w.clients(), log=lambda m: None)
    assert s.replaced == 1 and len(w.db) == 2 and w.db[1][17] == URL(1)


def test_select_jobs_reads_the_old_file_id_from_column_h():
    values = [["h"] * 8, qrow(1, "Waiting") + [FID(9)], qrow(2, "Waiting")]
    jobs = select_jobs(values, retry_errors=True, include_blank=False, only_rows=None)
    assert [j.replaces for j in jobs] == [FID(9), ""]
