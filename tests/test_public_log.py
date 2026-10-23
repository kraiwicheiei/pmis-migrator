"""
--public-log 테스트: 공개 로그에 이름/ID/오류 상세가 새어 나가지 않는지, 개수와 상태는 남는지
[TH] เทสต์ --public-log: ชื่อ/ID/รายละเอียด error ต้องไม่หลุดไปใน log สาธารณะ แต่ตัวเลขและสถานะต้องยังอยู่
[EN] Tests for --public-log: no names / IDs / error details may leak into the public log, while the counts and statuses stay
"""
from migrator.cp_column import run_backfill
from migrator.db_sync import run_db_sync
from migrator.public_log import MAX_STOP_REASON, make_log, sanitize
from migrator.reports import build_db_row
from migrator.specs import SPECS
from migrator.extract import run_extract
from migrator.runner import run, run_queues

from tests.db_fakes import db_row, db_world, qrow8
from tests.fakes import DWR_JSON, FID, FOLDER, Resp, URL, ns, world
from tests.legacy_fakes import FakeLegacy, LEGACY_FILE, extract_logs, extract_main, xargs


def public(lines):
    """각 줄을 sanitize 에 통과시킨 결과 / [TH] ผลหลังผ่าน sanitize ทีละบรรทัด / [EN] The lines after going through sanitize"""
    out = []
    for message in lines:
        for line in str(message).split("\n"):
            safe = sanitize(line)
            if safe is not None:
                out.append(safe)
    return "\n".join(out)


# 공개되면 안 되는 값 / [TH] ค่าที่ต้องไม่หลุด / [EN] Values that must never show up
SECRETS = ["sa@x", "LEGACY-DWR", "LEGACY-DRQ", "Example Spreadsheet", "Legacy Spreadsheet", LEGACY_FILE, "R-001", "fileId=", FID(1), FID(2),
           "service account", "boom"]


def assert_no_secret(text):
    for secret in SECRETS:
        assert secret not in text, f"{secret!r} leaked:\n{text}"


# --- sanitize 규칙 / the rules / กฎ ---
def test_sanitize_keeps_counts_and_statuses_as_they_are():
    for line in ("Queue MIGRATION_QUEUE: Done=2, Waiting=3", "Rows to process: 3", "Finished: Done=3 (+reconciled 0), Error=0 , Skipped=0",
                 "All queues: Done=5, Error=1", "Gemini calls: 3, input 3,000 tok, output(+thinking) 750 tok", "(dry-run: no Drive/Gemini calls, no sheet writes)",
                 "Migration time written to MIGRATION_QUEUE!K2: 2026-10-22 14:05:33 (UTC+7)", "[MIGRATION_QUEUE] Reachable: 3/3 files",
                 "  ✓ Synced MIGRATION_QUEUE: 3 kept (1 changed, 0 renumbered), +1 new, 0 duplicate rows merged, 0 rows not in the legacy sheet any more removed; statuses kept: Done 1, Error 0, Waiting 2 | 4 rows",
                 "  ℹ 2 Done rows got a new PDF link from the legacy sheet (the status stays Done, so they are not migrated again; set the row to Waiting to migrate it)",
                 "  ⚠ 1 removed rows were Done / Error: they have no item in the legacy sheet any more",
                 "  ✓ Wrote queue MIGRATION_QUEUE: 4 rows (Waiting)", "  ⚠ 2 items have no PDF link (they will get 'Error: No URL' when migrated)"):
        assert sanitize(line) == line


def test_sanitize_cuts_names_ids_and_error_details():
    assert sanitize("[MIGRATION_QUEUE] Reachable: 2/3 files - share the files/folder with sa@x.iam.gserviceaccount.com") == "[MIGRATION_QUEUE] Reachable: 2/3 files"
    assert sanitize("===== Queue DWR: MIGRATION_QUEUE -> DB_DWR | sheet: Secret Title =====") == "===== Queue DWR: MIGRATION_QUEUE -> DB_DWR ====="
    assert sanitize("===== Build queue DWR: Secret Tab (DWR) -> MIGRATION_QUEUE | sheet: Secret Title =====") == "===== Build queue DWR -> MIGRATION_QUEUE ====="
    assert sanitize("[DWR] Read tab 'Secret Tab': 12 data rows, 10 with a Date -> 8 unique items (C=5, P=3), 1 duplicate cells merged") == \
        "[DWR] Read legacy tab: 12 data rows, 10 with a Date -> 8 unique items (C=5, P=3), 1 duplicate cells merged"
    assert sanitize("✗ Could not process queue MIGRATION_QUEUE: HTTP 403: secret detail") == "✗ Could not process queue MIGRATION_QUEUE - run the script locally for the reason"
    assert sanitize("✗ Could not build queue MIGRATION_QUEUE_DRQ: tab 'X' not found") == "✗ Could not build queue MIGRATION_QUEUE_DRQ - run the script locally for the reason"
    assert sanitize("Stopped early: Gemini answered API 402: {\"error\": \"secret body\"}") == "Stopped early: Gemini answered API 402"
    assert sanitize("12 rows are already in DB_DWR -> marked Done without calling the AI (rows: [2, 3])") == "12 rows are already in DB_DWR -> marked Done without calling the AI"
    assert sanitize("Stopped early: " + "x" * 500) == ("Stopped early: " + "x" * 500)[:MAX_STOP_REASON]


def test_sanitize_drops_everything_else():
    for line in ("service account: sa@x | sheet: Example Spreadsheet (SID) | model: m", "  row 2: fileId=abc | previous status='Waiting'",
                 "  sample: R-001-CIV-001 | 2025-11-28 | CIV | C | PDF link: yes", "  ✗ row 2: Error: Drive Access | fileId=abc | x", "", "-" * 60,
                 "  ✓ 3 rows -> DB_DWR (queue rows [2, 3, 4])", "  sample (renumbered): row 2: R-001-CIV-001 -> R-777-CIV-001", "[DWR] Legacy spreadsheet: Legacy Spreadsheet (ID) -> queue is written to the target spreadsheet (SID)"):
        assert sanitize(line) is None


def test_make_log_prints_only_the_safe_lines_when_public(capsys):
    make_log(False)("secret line")
    assert capsys.readouterr().out == "secret line\n"
    log = make_log(True)
    log("Rows to process: 2\n  row 2: fileId=abc | previous status='Waiting'\nFinished: Done=2")
    assert capsys.readouterr().out == "Rows to process: 2\nFinished: Done=2\n"


# --- 실제 출력으로 확인 / checked against the real output / ตรวจกับผลลัพธ์จริง ---
def test_a_real_run_has_no_secret_in_its_public_log():
    w = world(["Waiting"] * 3)
    logs = []
    run(ns(), w.clients(), log=logs.append)
    text = public(logs)
    assert_no_secret(text)
    assert "Queue MIGRATION_QUEUE: Waiting=3" in text and "Finished: Done=3" in text and "Migration time written to MIGRATION_QUEUE!K2" in text


def test_a_failing_access_check_and_a_dry_run_do_not_leak_the_email_or_file_ids():
    w = world(["Waiting"] * 3)
    del w.drive_files[FID(3)]
    logs = []
    run(ns(check_access=True), w.clients(), log=logs.append)
    run(ns(dry_run=True), w.clients(), log=logs.append)
    text = public(logs)
    assert_no_secret(text)
    assert "[MIGRATION_QUEUE] Reachable: 2/3 files" in text and "(dry-run: no Drive/Gemini calls, no sheet writes)" in text


def test_a_stopped_run_and_row_errors_do_not_leak_the_error_text():
    w = world(["Waiting"] * 3)
    w.gemini_default = lambda: Resp(402, text='{"error":{"message":"boom: secret billing detail"}}')
    logs = []
    run_queues(ns(workers=1, queue=None, hint_other_queue=False), w.clients(), ["dwr"], log=logs.append, sheet_title="Example Spreadsheet")
    text = public(logs)
    assert_no_secret(text)
    assert "billing" not in text and "Stopped early: Gemini answered" in text


def test_a_queue_that_cannot_be_read_does_not_leak_the_error_text():
    w = world(["Waiting"])
    w.sheets_request_original = w.sheets_request

    def failing(method, url, **kw):
        return Resp(403, text="boom: The caller does not have permission for Example Spreadsheet")
    w.sheets_request = failing
    logs = []
    run_queues(ns(queue=None), w.clients(), ["dwr"], log=logs.append, sheet_title="Example Spreadsheet")
    text = public(logs)
    assert_no_secret(text)
    assert "Could not process queue MIGRATION_QUEUE - run the script locally for the reason" in text


def test_extract_has_no_secret_in_its_public_log_for_every_outcome():
    logs = []
    for over in ({}, {"dry_run": True}, {"overwrite": True}, {"legacy_spreadsheet_id": LEGACY_FILE}):
        w = FakeLegacy(legacy_sid=LEGACY_FILE) if "legacy_spreadsheet_id" in over else FakeLegacy()
        logs += extract_logs(w, **over)[1]
        logs += extract_logs(w, **over)[1]                                           # the second time it syncs
    bad_header = FakeLegacy(queues={"MIGRATION_QUEUE": [["Doc", "Date"], ["x"]]})
    logs += extract_logs(bad_header)[1]
    text = public(logs)
    assert_no_secret(text)
    assert "Synced MIGRATION_QUEUE" in text and "Wrote queue MIGRATION_QUEUE" in text and "(--dry-run) nothing was written" in text
    assert "The header row of MIGRATION_QUEUE is not the expected one" in text and "Read tab" not in text and "Read legacy tab" in text


def test_extract_run_extract_header_and_errors_do_not_leak():
    w = FakeLegacy(grids={"LEGACY-DRQ": {}})
    logs = []
    run_extract(xargs(queue=None), w.clients(), ["dwr", "drq"], log=logs.append, sheet_title="Example Spreadsheet")
    text = public(logs)
    assert_no_secret(text)
    assert "===== Build queue DWR -> MIGRATION_QUEUE =====" in text and "Could not build queue MIGRATION_QUEUE - run the script locally for the reason" in text


# --- 명령줄 / the command line / บรรทัดคำสั่ง ---
def test_the_option_makes_main_print_only_safe_lines(monkeypatch, capsys):
    code, w = extract_main(monkeypatch, ["--extract", "--dry-run", "--public-log"])
    out = capsys.readouterr().out
    assert code == 0 and w.events == []
    assert_no_secret(out)
    assert "Read legacy tab:" in out and "(--dry-run) nothing was written" in out and "service account:" not in out


def test_without_the_option_main_still_prints_the_details(monkeypatch, capsys):
    code, _ = extract_main(monkeypatch, ["--extract", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0 and "sheet: Example Spreadsheet (SID)" in out and "Read tab 'LEGACY-DWR'" in out


# --- --backfill-cp / the C/P backfill / การเติม C/P ---
def test_sanitize_handles_the_backfill_lines():
    keep = ("[DWR] Backfill C/P in DB_DWR: 10 to fill (C=6, P=4), 0 already have a C/P, 3 not found in MIGRATION_QUEUE, 1 with different C/P values in MIGRATION_QUEUE, 0 without a PDF link",
            "  ✓ Filled C/P in DB_DWR!T: 10 rows", "  ✓ Wrote the header 'Status (C/P)' to DB_DWR!T1", "  (--dry-run) nothing was written: would fill 10 cells of DB_DWR!T")
    for line in keep:
        assert sanitize(line) == line
    assert sanitize("===== Backfill C/P DWR: DB_DWR <- MIGRATION_QUEUE | sheet: Secret Title =====") == "===== Backfill C/P DWR: DB_DWR <- MIGRATION_QUEUE ====="
    assert sanitize("✗ Could not backfill C/P in DB_DWR: DB_DWR!T1 already has the header 'Remarks'") == "✗ Could not backfill C/P in DB_DWR - run the script locally for the reason"


def test_backfill_has_no_secret_in_its_public_log():
    w = world(["Done"] * 2)
    w.queue[1][5], w.queue[2][5] = "C", "P"
    w.db.extend([build_db_row(SPECS["dwr"], DWR_JSON, URL(1), FOLDER), build_db_row(SPECS["dwr"], DWR_JSON, URL(2), FOLDER)])
    logs = []
    run_backfill(ns(dry_run=True), w.clients(), ["dwr"], log=logs.append, sheet_title="Example Spreadsheet")
    run_backfill(ns(), w.clients(), ["dwr"], log=logs.append, sheet_title="Example Spreadsheet")
    w.cp_header = "Remarks"
    w.db[1][19] = ""
    run_backfill(ns(), w.clients(), ["dwr"], log=logs.append, sheet_title="Example Spreadsheet")      # a header problem: the error text must be cut
    text = public(logs)
    assert_no_secret(text)
    assert "Backfill C/P in DB_DWR: 2 to fill (C=1, P=1)" in text and "Filled C/P in DB_DWR!T: 2 rows" in text
    assert "===== Backfill C/P DWR: DB_DWR <- MIGRATION_QUEUE =====" in text and "Remarks" not in text and "Could not backfill C/P in DB_DWR - run the script locally" in text


# --- --sync-db / replace in place ---
def test_sanitize_handles_the_db_sync_and_replacement_lines():
    keep = ("[DWR] Sync DB_DWR: 2409 rows | C/P to update 5, to remove 2 (PDF not in the queue any more), kept for a pending replacement 1, "
            "not removed because Status is not 'AI Migrated' 0, different C/P values in the queue 0",
            "  ✓ Updated C/P in DB_DWR!T: 5 rows", "  ✓ Removed 2 rows from DB_DWR (copied to _REMOVED_DB_DWR first)", "  ✓ DB_DWR already matches the queue",
            "  ✗ Not syncing DB_DWR: the queue has no rows, so every DB row would look removed", "Replaced in place: 3 rows of DB_DWR",
            "  ℹ 2 Done rows got a new PDF link from the legacy sheet: set back to Waiting so that the next migration reads the new file "
            "(the DB row that used the old file is replaced when it succeeds)")
    for line in keep:
        assert sanitize(line) == line
    assert sanitize("===== Sync DB DWR: DB_DWR <- MIGRATION_QUEUE | sheet: Secret Title =====") == "===== Sync DB DWR: DB_DWR <- MIGRATION_QUEUE ====="
    assert sanitize("✗ Could not sync DB_DRQ: HTTP 403 secret") == "✗ Could not sync DB_DRQ - run the script locally for the reason"
    assert sanitize("  ✗ Not syncing DB_DWR: 500 rows would be removed, more than the limit of 271 (10% of the DB, at least 100). Check the legacy sheet") == \
        "  ✗ Not syncing DB_DWR: 500 rows would be removed, more than the limit of 271 - check the legacy sheet, then use --allow-large-removal"
    assert sanitize("  sample (remove): DB row 5: ITD-DWR-1 - its PDF (abc) is not in MIGRATION_QUEUE any more") is None


def test_the_db_sync_has_no_secret_in_its_public_log():
    queue = [qrow8(1, "C"), qrow8(2, "P")]
    w = db_world(queue, [db_row(1), db_row(2), db_row(7, tag="Synced")])
    logs = []
    run_db_sync(ns(dry_run=True), w.clients(), ["dwr"], log=logs.append, sheet_title="Example Spreadsheet")
    run_db_sync(ns(), w.clients(), ["dwr"], log=logs.append, sheet_title="Example Spreadsheet")
    big = db_world([qrow8(1)], [db_row(1)] + [db_row(n, tag="Synced") for n in range(1000, 1150)])
    run_db_sync(ns(), big.clients(), ["dwr"], log=logs.append, sheet_title="Example Spreadsheet")            # refused by the safety net
    text = public(logs)
    assert_no_secret(text)
    assert "DWR-7" not in text and "DOC-1" not in text and "_REMOVED" in text and "DB_DWR" in text
    assert 'Sync DB_DWR: 3 rows | in sync with the queue 2, "Legacy sync" tag to set 2 / to clear 0, C/P to update 2, to remove 1' in text
    assert "Removed 1 rows from DB_DWR (copied to _REMOVED_DB_DWR first)" in text and "Legacy sync tag in DB_DWR!U: set on 2 rows, cleared on 0 rows" in text
    assert "===== Sync DB DWR: DB_DWR <- MIGRATION_QUEUE =====" in text and "more than the limit of 100 - check the legacy sheet" in text
