"""
명령줄 옵션과 main() 테스트
[TH] เทสต์ออปชันบรรทัดคำสั่งและ main()
[EN] Tests for the command-line options and main()
"""

import glob
import os
import re

import pytest

from migrator import cli, config, runner, settings
from migrator.cli import build_parser, main
from migrator.config import LARGE_PDF_MB
from migrator.models import Clients
from migrator.runner import Summary, queues_to_run
from migrator.sheets import SheetsError

from tests.fakes import RunRecorder, main_with
from tests.legacy_fakes import DRQ_ROWS, DWR_ROWS, FakeLegacy, LEGACY_FILE, extract_main


def test_default_limit_matches_apps_script_extractDriveFileForAI():
    # Apps Script: fileSize > 15000000 -> cover thumbnail
    assert LARGE_PDF_MB == 15.0 and build_parser().parse_args([]).max_mb == 15.0
    assert build_parser().parse_args([]).no_thumbnail is False


# --- --queue 를 생략하면 두 큐 모두 (dwr -> drq) ---
# [TH] ถ้าไม่ระบุ --queue จะทำทั้งสองคิว (dwr -> drq)
# [EN] Without --queue both queues are processed (dwr -> drq)
def test_queue_defaults_to_both_and_can_pick_one():
    assert build_parser().parse_args([]).queue is None
    assert queues_to_run(None) == ["dwr", "drq"]
    assert queues_to_run("drq") == ["drq"] and queues_to_run("dwr") == ["dwr"]
    assert build_parser().parse_args(["--queue", "drq"]).queue == "drq"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--queue", "bogus"])


def test_main_without_queue_runs_dwr_then_drq(monkeypatch, capsys):
    rec = RunRecorder()
    assert main_with(monkeypatch, ["--limit", "7"], rec) == 0
    assert rec.calls == [("dwr", False, 7), ("drq", False, 7)]   # --limit 은 큐마다 적용
    out = capsys.readouterr().out
    assert out.index("MIGRATION_QUEUE ->") < out.index("MIGRATION_QUEUE_DRQ ->") and "All queues: Done=2, Error=0" in out


def test_main_with_queue_runs_only_that_queue(monkeypatch, capsys):
    rec = RunRecorder()
    assert main_with(monkeypatch, ["--queue", "drq"], rec) == 0
    assert rec.calls == [("drq", True, 30)]
    assert "All queues" not in capsys.readouterr().out


def test_main_stops_before_second_queue_when_first_stops(monkeypatch, capsys):
    rec = RunRecorder({"dwr": Summary(done=1, stopped_reason="Gemini answered API 402")})
    assert main_with(monkeypatch, [], rec) == 2
    assert [c[0] for c in rec.calls] == ["dwr"]
    assert "skipping the remaining queues: MIGRATION_QUEUE_DRQ" in capsys.readouterr().out


def test_main_continues_with_other_queue_when_one_cannot_be_read(monkeypatch, capsys):
    rec = RunRecorder({"dwr": SheetsError("HTTP 400: Unable to parse range")})
    assert main_with(monkeypatch, [], rec) == 2
    assert [c[0] for c in rec.calls] == ["dwr", "drq"]
    assert "Could not process queue MIGRATION_QUEUE" in capsys.readouterr().out


def test_only_rows_requires_a_single_queue(monkeypatch):
    rec = RunRecorder()
    with pytest.raises(SystemExit) as e:
        main_with(monkeypatch, ["--only-rows", "83"], rec)
    assert "--queue" in str(e.value) and rec.calls == []
    assert main_with(monkeypatch, ["--only-rows", "83", "--queue", "dwr"], rec) == 0 and [c[0] for c in rec.calls] == ["dwr"]


def test_main_prints_the_sheet_title_first(monkeypatch, capsys):
    rec = RunRecorder()
    monkeypatch.setattr(cli, "spreadsheet_title", lambda sess, sid: ("Example Spreadsheet", ""))
    assert main_with(monkeypatch, ["--queue", "dwr"], rec, sid="ABC") == 0
    out = capsys.readouterr().out
    assert "sheet: Example Spreadsheet (ABC)" in out
    # 큐 제목 줄에도 시트 이름이 나옴 / [TH] หัวของคิวก็มีชื่อชีต / [EN] the queue heading also carries the sheet name
    assert "===== Queue DWR: MIGRATION_QUEUE -> DB_DWR | sheet: Example Spreadsheet =====" in out
    main_with(monkeypatch, [], rec, sid="ABC")
    out = capsys.readouterr().out
    assert "===== Queue DRQ: MIGRATION_QUEUE_DRQ -> DB_DRQ | sheet: Example Spreadsheet =====" in out
    monkeypatch.setattr(cli, "spreadsheet_title", lambda sess, sid: ("", "HTTP 403: The caller does not have permission"))
    main_with(monkeypatch, ["--queue", "dwr"], rec, sid="ABC")
    out = capsys.readouterr().out
    assert "could not read the sheet name" in out and "HTTP 403" in out and "===== Queue DWR: MIGRATION_QUEUE -> DB_DWR =====\n" in out


# 시트 ID: 환경 변수만 / Sheet ID: environment variable only / ID ชีต: ตัวแปรสภาพแวดล้อมเท่านั้น
def test_sheet_id_has_no_default_and_no_cli_option(capsys):
    assert not any(hasattr(m, "DEFAULT_SPREADSHEET_ID") for m in (config, settings, cli))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paths = [os.path.join(root, "migrate_queue.py")] + sorted(glob.glob(os.path.join(root, "migrator", "*.py")))
    src = "\n".join(open(p, encoding="utf-8").read() for p in paths)
    # no Google-Sheets-ID-like token (44 chars, starts with "1") is hard-coded
    assert not re.search(r"(?<![A-Za-z0-9_-])1[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])", src)
    assert not hasattr(build_parser().parse_args([]), "spreadsheet_id")
    with pytest.raises(SystemExit) as e:
        build_parser().parse_args(["--spreadsheet-id", "ABC"])
    assert e.value.code == 2 and "unrecognized arguments" in capsys.readouterr().err


def test_main_requires_the_sheet_id_before_touching_credentials(monkeypatch, capsys):
    def boom(*a, **k):
        raise AssertionError("make_clients must not be reached without a sheet ID")

    monkeypatch.setattr(cli, "make_clients", boom)
    monkeypatch.delenv("PMIS_SPREADSHEET_ID", raising=False)
    for argv in ([], ["--dry-run"], ["--check-access"], ["--queue", "drq", "--limit", "5"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert "PMIS_SPREADSHEET_ID" in str(e.value.code)


def test_main_passes_the_env_sheet_id_to_the_run(monkeypatch):
    seen = []

    def spy(args, clients, log=print):
        seen.append(args.spreadsheet_id)
        return Summary()

    monkeypatch.setattr(cli, "spreadsheet_title", lambda sess, sid: ("T", ""))
    monkeypatch.setattr(cli, "make_clients", lambda args, need_gemini: Clients(sheets=None, drive_factory=None, gemini=None, api_key="K", sa_email="sa@x"))
    monkeypatch.setattr(runner, "run", spy)
    monkeypatch.setenv("PMIS_SPREADSHEET_ID", "FROM-ENV")
    assert main(["--queue", "dwr"]) == 0
    assert seen == ["FROM-ENV"]


def test_main_extract_builds_both_queues_without_a_gemini_key(monkeypatch, capsys):
    need = []
    code, w = extract_main(monkeypatch, ["--extract"], need=need)
    out = capsys.readouterr().out
    assert code == 0 and need == [False]
    assert w.queues["MIGRATION_QUEUE"][1:] == DWR_ROWS and w.queues["MIGRATION_QUEUE_DRQ"][1:] == DRQ_ROWS
    assert out.index("Build queue DWR") < out.index("Build queue DRQ") and "sheet: Example Spreadsheet (SID)" in out and "model:" not in out


def test_main_extract_one_queue_needs_only_its_own_tab_name(monkeypatch):
    code, w = extract_main(monkeypatch, ["--extract", "--queue", "drq"], env={"PMIS_LEGACY_DRQ_SHEET": "LEGACY-DRQ"})
    assert code == 0 and list(w.queues) == ["MIGRATION_QUEUE_DRQ"]


def test_main_extract_checks_tab_names_before_touching_credentials(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("make_clients must not be reached without the legacy tab names")

    monkeypatch.setenv("PMIS_SPREADSHEET_ID", "SID")
    monkeypatch.delenv("PMIS_LEGACY_DWR_SHEET", raising=False)
    monkeypatch.delenv("PMIS_LEGACY_DRQ_SHEET", raising=False)
    monkeypatch.setattr(cli, "make_clients", boom)
    with pytest.raises(SystemExit) as e:
        main(["--extract", "--dry-run"])
    assert "PMIS_LEGACY_DWR_SHEET" in str(e.value.code) and "PMIS_LEGACY_DRQ_SHEET" in str(e.value.code)


def test_main_extract_dry_run_and_flag_conflicts(monkeypatch):
    code, w = extract_main(monkeypatch, ["--extract", "--dry-run"])
    assert code == 0 and w.queues == {} and w.events == []
    for argv in (["--extract", "--only-rows", "5", "--queue", "dwr"], ["--extract", "--check-access"], ["--overwrite"]):
        with pytest.raises(SystemExit) as e:
            extract_main(monkeypatch, argv)
        assert "--extract" in str(e.value.code) or "--overwrite" in str(e.value.code)


def test_the_normal_run_is_unchanged_without_extract(monkeypatch, capsys):
    rec = RunRecorder()
    assert main_with(monkeypatch, ["--queue", "dwr"], rec) == 0 and rec.calls == [("dwr", True, 30)]
    assert "model:" in capsys.readouterr().out


def test_main_extract_takes_the_legacy_spreadsheet_from_the_option_or_the_environment(monkeypatch):
    url = f"https://docs.google.com/spreadsheets/d/{LEGACY_FILE}/edit#gid=0"
    code, w = extract_main(monkeypatch, ["--extract", "--queue", "dwr", "--legacy-spreadsheet", url], w=FakeLegacy(legacy_sid=LEGACY_FILE))
    assert code == 0 and w.queues["MIGRATION_QUEUE"][1:] == DWR_ROWS
    code, w = extract_main(monkeypatch, ["--extract", "--queue", "dwr"], w=FakeLegacy(legacy_sid=LEGACY_FILE),
                           env={"PMIS_LEGACY_DWR_SHEET": "LEGACY-DWR", "PMIS_LEGACY_SPREADSHEET_ID": LEGACY_FILE})
    assert code == 0 and w.queues["MIGRATION_QUEUE"][1:] == DWR_ROWS


def test_legacy_spreadsheet_option_needs_extract(monkeypatch):
    with pytest.raises(SystemExit) as e:
        main_with(monkeypatch, ["--queue", "dwr", "--legacy-spreadsheet", "abc"], RunRecorder())
    assert "--legacy-spreadsheet only works together with --extract" in str(e.value.code)


# --- --sync-db / --allow-large-removal ---
def sync_main(monkeypatch, argv):
    calls = {}
    monkeypatch.setenv("PMIS_SPREADSHEET_ID", "SID")
    monkeypatch.setattr(cli, "make_clients", lambda args, need_gemini: calls.setdefault("need", need_gemini) is None or Clients(sheets=None, drive_factory=None, gemini=None, api_key="", sa_email="sa@x"))
    monkeypatch.setattr(cli, "spreadsheet_title", lambda sess, sid: ("T", ""))
    monkeypatch.setattr(cli, "run_db_sync", lambda args, clients, keys, log, sheet_title: calls.update(db_sync=(args.sync_db, args.allow_large_removal, keys)) or 0)
    monkeypatch.setattr(cli, "run_queues", lambda *a, **k: calls.update(queues=True) or 0)
    return main(argv), calls


def test_sync_db_runs_the_db_sync_without_a_gemini_key(monkeypatch):
    code, calls = sync_main(monkeypatch, ["--sync-db", "--queue", "dwr", "--allow-large-removal"])
    assert code == 0 and calls == {"need": False, "db_sync": (True, True, ["dwr"])}


def test_sync_db_with_extract_goes_through_the_extract_run(monkeypatch):
    seen = {}
    monkeypatch.setenv("PMIS_SPREADSHEET_ID", "SID")
    monkeypatch.setenv("PMIS_LEGACY_DWR_SHEET", "A")
    monkeypatch.setenv("PMIS_LEGACY_DRQ_SHEET", "B")
    monkeypatch.setattr(cli, "make_clients", lambda args, need_gemini: Clients(sheets=None, drive_factory=None, gemini=None, api_key="", sa_email="sa@x"))
    monkeypatch.setattr(cli, "spreadsheet_title", lambda sess, sid: ("T", ""))
    monkeypatch.setattr(cli, "run_extract", lambda args, clients, keys, log, sheet_title: seen.update(sync_db=args.sync_db, large=args.allow_large_removal) or 0)
    assert main(["--extract", "--sync-db"]) == 0 and seen == {"sync_db": True, "large": False}


def test_sync_db_rejects_conflicting_options(monkeypatch):
    monkeypatch.setenv("PMIS_SPREADSHEET_ID", "SID")
    for argv, text in ((["--sync-db", "--backfill-cp"], "either --sync-db or --backfill-cp"),
                       (["--sync-db", "--only-rows", "5", "--queue", "dwr"], "cannot be combined with --only-rows or --check-access"),
                       (["--sync-db", "--check-access"], "cannot be combined with --only-rows or --check-access"),
                       (["--sync-db", "--overwrite"], "--overwrite only works together with --extract"),
                       (["--allow-large-removal"], "--allow-large-removal only works together with --extract or --sync-db"),
                       (["--extract", "--backfill-cp"], "--backfill-cp cannot be combined with --extract")):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert text in str(e.value.code)


def test_the_new_options_are_off_by_default():
    args = build_parser().parse_args([])
    assert args.sync_db is False and args.allow_large_removal is False
