"""
--public-log 테스트: 공개 로그에 이름/ID/오류 상세가 새어 나가지 않는지, 개수와 상태는 남는지
[TH] เทสต์ --public-log: ชื่อ/ID/รายละเอียด error ต้องไม่หลุดไปใน log สาธารณะ แต่ตัวเลขและสถานะต้องยังอยู่
[EN] Tests for --public-log: no names / IDs / error details may leak into the public log, while the counts and statuses stay
"""
from migrator.public_log import MAX_STOP_REASON, make_log, sanitize
from migrator.extract import run_extract
from migrator.runner import run, run_queues

from tests.fakes import FID, Resp, ns, world
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
                 "  ✓ Merged into MIGRATION_QUEUE: +1 new, 1 updated, 1 unchanged, 1 Done kept as is",
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
                 "  ✓ 3 rows -> DB_DWR (queue rows [2, 3, 4])", "[DWR] Legacy spreadsheet: Legacy Spreadsheet (ID) -> queue is written to the target spreadsheet (SID)"):
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
        logs += extract_logs(w, **over)[1]                                           # the second time it merges
    bad_header = FakeLegacy(queues={"MIGRATION_QUEUE": [["Doc", "Date"], ["x"]]})
    logs += extract_logs(bad_header)[1]
    text = public(logs)
    assert_no_secret(text)
    assert "Merged into MIGRATION_QUEUE" in text and "Wrote queue MIGRATION_QUEUE" in text and "(--dry-run) nothing was written" in text
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
