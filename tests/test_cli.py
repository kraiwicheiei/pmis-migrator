"""
명령줄(sync / check)과 main() 테스트
[TH] เทสต์บรรทัดคำสั่ง (sync / check) และ main()
[EN] Tests for the command line (sync / check) and main()
"""

import glob
import os
import re

import pytest

from migrator import cli, config, pipeline, runner, settings
from migrator.cli import BACKFILL_REMOVED, build_parser, main, old_entry, prepare, translate_old_options
from migrator.config import LARGE_PDF_MB
from migrator.extract import ExtractSummary
from migrator.models import Clients
from migrator.runner import Summary, queues_to_run
from migrator.sheets import SheetsError

from tests.fakes import RunRecorder, main_with
from tests.legacy_fakes import DRQ_ROWS, DWR_ROWS, FakeLegacy, LEGACY_FILE, extract_main


def parse(argv):
    """명령을 파싱하고 prepare() 까지 거친 인자 / [TH] อาร์กิวเมนต์ที่แยกแล้วและผ่าน prepare() / [EN] The parsed arguments, after prepare()"""
    args = build_parser().parse_args(argv)
    prepare(args)
    return args


def test_default_limit_matches_apps_script_extractDriveFileForAI():
    # Apps Script: fileSize > 15000000 -> cover thumbnail
    assert LARGE_PDF_MB == 15.0 and parse(["sync"]).max_mb == 15.0 and parse(["check", "access"]).max_mb == 15.0
    assert parse(["sync"]).no_thumbnail is False


# --- 명령과 단계 / the commands and the steps / คำสั่งและขั้นตอน ---
def test_sync_without_a_step_runs_both_in_order_and_the_steps_are_always_ordered():
    assert parse(["sync"]).steps == ["queue", "db"]
    assert parse(["sync", "db"]).steps == ["db"] and parse(["sync", "queue"]).steps == ["queue"]
    assert parse(["sync", "db", "queue"]).steps == ["queue", "db"] and parse(["sync", "db", "queue", "db"]).steps == ["queue", "db"]
    assert parse(["sync", "--limit", "5", "db"]).steps == ["db"]                         # the step may come after the options
    assert parse(["check", "access"]).steps == [] and parse(["check", "access"]).check_access is True
    assert parse(["sync"]).check_access is False


def test_unknown_steps_and_commands_are_a_usage_error_and_the_old_ai_step_says_where_it_went(capsys):
    for argv in (["sync", "bogus"], ["sync", "queue", "extract"], ["check"], ["check", "cp"], ["bogus"], []):
        with pytest.raises(SystemExit) as e:
            build_parser().parse_args(argv)
        assert e.value.code == 2
    assert "unknown step 'bogus' (choose from: queue, db" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        build_parser().parse_args(["sync", "ai"])
    assert "the ai step was merged into db: use `sync db`" in capsys.readouterr().err


def test_the_options_belong_to_the_steps_that_use_them(capsys):
    assert parse(["sync", "--overwrite", "--legacy-spreadsheet", "X", "--allow-large-removal", "--no-ai"]).no_ai is True
    assert parse(["sync"]).no_ai is False and parse(["check", "access"]).no_ai is False
    for argv in (["check", "access", "--overwrite"], ["check", "access", "--allow-large-removal"], ["check", "access", "--no-ai"], ["check", "access", "--legacy-spreadsheet", "X"]):
        with pytest.raises(SystemExit):
            build_parser().parse_args(argv)
    assert "unrecognized arguments" in capsys.readouterr().err


def test_the_help_shows_the_commands_and_examples(capsys):
    for argv in (["--help"], ["sync", "--help"], ["check", "--help"]):
        with pytest.raises(SystemExit) as e:
            build_parser().parse_args(argv)
        assert e.value.code == 0
    out = capsys.readouterr().out
    assert "python pmis.py sync db --no-ai" in out and "queue -> DB" in out and "db step - the AI part" in out and "--no-ai" in out and "--allow-large-removal" in out
    assert "fix" not in out.split("examples:")[0].replace("fixes", "")                                    # the `fix` command is gone


def test_main_without_arguments_prints_the_help_and_exits_with_2(capsys):
    assert main([]) == 2
    assert "usage: pmis.py" in capsys.readouterr().err


# --- --queue 를 생략하면 두 큐 모두 (dwr -> drq) ---
# [TH] ถ้าไม่ระบุ --queue จะทำทั้งสองคิว (dwr -> drq)
# [EN] Without --queue both queues are processed (dwr -> drq)
def test_queue_defaults_to_both_and_can_pick_one():
    assert parse(["sync"]).queue is None
    assert queues_to_run(None) == ["dwr", "drq"]
    assert queues_to_run("drq") == ["drq"] and queues_to_run("dwr") == ["dwr"]
    assert parse(["sync", "db", "--queue", "drq"]).queue == "drq"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["sync", "db", "--queue", "bogus"])


def test_main_without_queue_runs_dwr_then_drq(monkeypatch, capsys):
    rec = RunRecorder()
    assert main_with(monkeypatch, ["sync", "db", "--limit", "7"], rec) == 0
    assert rec.calls == [("dwr", False, 7), ("drq", False, 7)]   # --limit 은 큐마다 적용
    out = capsys.readouterr().out
    assert out.index("MIGRATION_QUEUE ->") < out.index("MIGRATION_QUEUE_DRQ ->") and "All queues: Done=2, Error=0" in out
    assert "▶ Step" not in out                                   # one step: no step titles
    assert "Part 1/2 - update the DB rows (no AI)" in out and "Part 2/2 - AI: read the PDFs of the Waiting rows" in out


def test_main_with_queue_runs_only_that_queue(monkeypatch, capsys):
    rec = RunRecorder()
    assert main_with(monkeypatch, ["sync", "db", "--queue", "drq"], rec) == 0
    assert rec.calls == [("drq", True, 30)]
    assert "All queues" not in capsys.readouterr().out


def test_main_stops_before_second_queue_when_first_stops(monkeypatch, capsys):
    rec = RunRecorder({"dwr": Summary(done=1, stopped_reason="Gemini answered API 402")})
    assert main_with(monkeypatch, ["sync", "db"], rec) == 2
    assert [c[0] for c in rec.calls] == ["dwr"]
    assert "skipping the remaining queues: MIGRATION_QUEUE_DRQ" in capsys.readouterr().out


def test_main_continues_with_other_queue_when_one_cannot_be_read(monkeypatch, capsys):
    rec = RunRecorder({"dwr": SheetsError("HTTP 400: Unable to parse range")})
    assert main_with(monkeypatch, ["sync", "db"], rec) == 2
    assert [c[0] for c in rec.calls] == ["dwr", "drq"]
    assert "Could not process queue MIGRATION_QUEUE" in capsys.readouterr().out


def test_only_rows_requires_a_single_queue_and_runs_only_the_ai_part(monkeypatch, capsys):
    rec = RunRecorder()
    with pytest.raises(SystemExit) as e:
        main_with(monkeypatch, ["sync", "db", "--only-rows", "83"], rec)
    assert "--queue" in str(e.value) and rec.calls == []
    assert main_with(monkeypatch, ["sync", "db", "--only-rows", "83", "--queue", "dwr"], rec) == 0 and [c[0] for c in rec.calls] == ["dwr"]
    assert "Part 1/2" not in capsys.readouterr().out                    # a retry of some rows does not touch the rest of the DB
    with pytest.raises(SystemExit) as e:
        main_with(monkeypatch, ["check", "access", "--only-rows", "83"], rec)
    assert "--queue" in str(e.value)


def test_main_prints_the_sheet_title_first(monkeypatch, capsys):
    rec = RunRecorder()
    monkeypatch.setattr(cli, "spreadsheet_title", lambda sess, sid: ("Example Spreadsheet", ""))
    assert main_with(monkeypatch, ["sync", "db", "--queue", "dwr"], rec, sid="ABC") == 0
    out = capsys.readouterr().out
    assert "sheet: Example Spreadsheet (ABC)" in out
    # 큐 제목 줄에도 시트 이름이 나옴 / [TH] หัวของคิวก็มีชื่อชีต / [EN] the queue heading also carries the sheet name
    assert "===== Queue DWR: MIGRATION_QUEUE -> DB_DWR | sheet: Example Spreadsheet =====" in out
    main_with(monkeypatch, ["sync", "db"], rec, sid="ABC")
    out = capsys.readouterr().out
    assert "===== Queue DRQ: MIGRATION_QUEUE_DRQ -> DB_DRQ | sheet: Example Spreadsheet =====" in out
    monkeypatch.setattr(cli, "spreadsheet_title", lambda sess, sid: ("", "HTTP 403: The caller does not have permission"))
    main_with(monkeypatch, ["sync", "db", "--queue", "dwr"], rec, sid="ABC")
    out = capsys.readouterr().out
    assert "could not read the sheet name" in out and "HTTP 403" in out and "===== Queue DWR: MIGRATION_QUEUE -> DB_DWR =====\n" in out


def test_the_first_line_names_the_mode_and_the_model_only_when_the_ai_runs(monkeypatch, capsys):
    rec = RunRecorder()
    main_with(monkeypatch, ["sync", "db", "--queue", "dwr"], rec)
    first = capsys.readouterr().out.splitlines()[0]
    assert "mode: sync db (queue -> DB, with the AI reading the PDFs of the Waiting rows)" in first and "| model:" in first
    main_with(monkeypatch, ["sync", "db", "--no-ai", "--queue", "dwr"], rec)
    first = capsys.readouterr().out.splitlines()[0]
    assert "mode: sync db --no-ai (queue -> DB, update the data only (--no-ai))" in first and "model:" not in first
    main_with(monkeypatch, ["check", "access", "--queue", "dwr"], rec)
    first = capsys.readouterr().out.splitlines()[0]
    assert "mode: check access" in first and "model:" not in first


# 시트 ID: 환경 변수만 / Sheet ID: environment variable only / ID ชีต: ตัวแปรสภาพแวดล้อมเท่านั้น
def test_sheet_id_has_no_default_and_no_cli_option(capsys):
    assert not any(hasattr(m, "DEFAULT_SPREADSHEET_ID") for m in (config, settings, cli))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paths = [os.path.join(root, "pmis.py"), os.path.join(root, "migrate_queue.py")] + sorted(glob.glob(os.path.join(root, "migrator", "*.py")))
    src = "\n".join(open(p, encoding="utf-8").read() for p in paths)
    # no Google-Sheets-ID-like token (44 chars, starts with "1") is hard-coded
    assert not re.search(r"(?<![A-Za-z0-9_-])1[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])", src)
    for argv in (["sync"], ["check", "access"]):
        assert not hasattr(parse(argv), "spreadsheet_id")
    with pytest.raises(SystemExit) as e:
        build_parser().parse_args(["check", "access", "--spreadsheet-id", "ABC"])
    assert e.value.code == 2 and "unrecognized arguments" in capsys.readouterr().err


def test_main_requires_the_sheet_id_before_touching_credentials(monkeypatch, capsys):
    def boom(*a, **k):
        raise AssertionError("make_clients must not be reached without a sheet ID")

    monkeypatch.setattr(cli, "make_clients", boom)
    monkeypatch.delenv("PMIS_SPREADSHEET_ID", raising=False)
    for argv in (["sync"], ["sync", "db", "--dry-run"], ["check", "access"], ["sync", "db", "--queue", "drq", "--limit", "5"]):
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
    monkeypatch.setattr(pipeline, "run_db_sync", lambda *a, **k: 0)
    monkeypatch.setenv("PMIS_SPREADSHEET_ID", "FROM-ENV")
    assert main(["sync", "db", "--queue", "dwr"]) == 0
    assert seen == ["FROM-ENV"]


# --- sync queue ---
def test_main_sync_queue_builds_both_queues_without_a_gemini_key(monkeypatch, capsys):
    need = []
    code, w = extract_main(monkeypatch, ["sync", "queue"], need=need)
    out = capsys.readouterr().out
    assert code == 0 and need == [False]
    assert w.queues["MIGRATION_QUEUE"][1:] == DWR_ROWS and w.queues["MIGRATION_QUEUE_DRQ"][1:] == DRQ_ROWS
    assert out.index("Build queue DWR") < out.index("Build queue DRQ") and "sheet: Example Spreadsheet (SID)" in out and "model:" not in out
    assert "mode: sync queue (legacy sheet -> queue)" in out and "▶ Step" not in out


def test_main_sync_queue_one_queue_needs_only_its_own_tab_name(monkeypatch):
    code, w = extract_main(monkeypatch, ["sync", "queue", "--queue", "drq"], env={"PMIS_LEGACY_DRQ_SHEET": "LEGACY-DRQ"})
    assert code == 0 and list(w.queues) == ["MIGRATION_QUEUE_DRQ"]


def test_main_sync_queue_checks_tab_names_before_touching_credentials(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("make_clients must not be reached without the legacy tab names")

    monkeypatch.setenv("PMIS_SPREADSHEET_ID", "SID")
    monkeypatch.delenv("PMIS_LEGACY_DWR_SHEET", raising=False)
    monkeypatch.delenv("PMIS_LEGACY_DRQ_SHEET", raising=False)
    monkeypatch.setattr(cli, "make_clients", boom)
    for argv in (["sync", "queue", "--dry-run"], ["sync", "--dry-run"], ["sync", "queue", "db"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert "PMIS_LEGACY_DWR_SHEET" in str(e.value.code) and "PMIS_LEGACY_DRQ_SHEET" in str(e.value.code) and "`sync queue`" in str(e.value.code)


def test_main_sync_queue_dry_run(monkeypatch):
    code, w = extract_main(monkeypatch, ["sync", "queue", "--dry-run"])
    assert code == 0 and w.queues == {} and w.events == []


def test_the_db_step_alone_does_not_need_the_legacy_tab_names(monkeypatch):
    seen = []
    monkeypatch.setenv("PMIS_SPREADSHEET_ID", "SID")
    monkeypatch.delenv("PMIS_LEGACY_DWR_SHEET", raising=False)
    monkeypatch.delenv("PMIS_LEGACY_DRQ_SHEET", raising=False)
    monkeypatch.setattr(cli, "make_clients", lambda args, need_gemini: Clients(sheets=None, drive_factory=None, gemini=None, api_key="K", sa_email="sa@x"))
    monkeypatch.setattr(cli, "spreadsheet_title", lambda sess, sid: ("T", ""))
    monkeypatch.setattr(pipeline, "run_queues", lambda *a, **k: seen.append("ai") or 0)
    monkeypatch.setattr(pipeline, "run_db_sync", lambda *a, **k: seen.append("update") or 0)
    assert main(["sync", "db"]) == 0 and seen == ["update", "ai"]


def test_main_takes_the_legacy_spreadsheet_from_the_option_or_the_environment(monkeypatch):
    url = f"https://docs.google.com/spreadsheets/d/{LEGACY_FILE}/edit#gid=0"
    code, w = extract_main(monkeypatch, ["sync", "queue", "--queue", "dwr", "--legacy-spreadsheet", url], w=FakeLegacy(legacy_sid=LEGACY_FILE))
    assert code == 0 and w.queues["MIGRATION_QUEUE"][1:] == DWR_ROWS
    code, w = extract_main(monkeypatch, ["sync", "queue", "--queue", "dwr"], w=FakeLegacy(legacy_sid=LEGACY_FILE),
                           env={"PMIS_LEGACY_DWR_SHEET": "LEGACY-DWR", "PMIS_LEGACY_SPREADSHEET_ID": LEGACY_FILE})
    assert code == 0 and w.queues["MIGRATION_QUEUE"][1:] == DWR_ROWS


# --- 단계에 맞지 않는 옵션 / options that do not fit the steps / ออปชันที่ไม่เข้ากับขั้นตอน ---
def test_options_of_one_step_are_rejected_when_that_step_does_not_run(monkeypatch):
    monkeypatch.setenv("PMIS_SPREADSHEET_ID", "SID")
    for argv, text in ((["sync", "db", "--legacy-spreadsheet", "abc"], "--legacy-spreadsheet only works when the queue step runs"),
                       (["sync", "db", "--overwrite"], "--overwrite only works when the queue step runs"),
                       (["sync", "queue", "--only-rows", "5", "--queue", "dwr"], "--only-rows only works with `sync db`"),
                       (["sync", "--only-rows", "5", "--queue", "dwr"], "--only-rows only works with `sync db`"),
                       (["sync", "db", "--only-rows", "5", "--no-ai", "--queue", "dwr"], "--only-rows retries those rows with the AI, so it cannot be combined with --no-ai")):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert text in str(e.value.code)


# --- 각 명령은 올바른 실행기로 간다 / every command goes to its own runner ---
def routed(monkeypatch, argv, env=None):
    """각 실행기를 기록기로 바꿔 어디로 갔는지 봄 / [TH] เปลี่ยนตัวรันแต่ละตัวเป็นตัวบันทึก เพื่อดูว่าไปทางไหน / [EN] Swaps every runner for a recorder to see where the call went"""
    calls = []
    monkeypatch.setenv("PMIS_SPREADSHEET_ID", "SID")
    for name, value in (env if env is not None else {"PMIS_LEGACY_DWR_SHEET": "A", "PMIS_LEGACY_DRQ_SHEET": "B"}).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli, "make_clients", lambda args, need_gemini: calls.append(("need_gemini", need_gemini)) or Clients(sheets=None, drive_factory=None, gemini=None, api_key="", sa_email="sa@x"))
    monkeypatch.setattr(cli, "spreadsheet_title", lambda sess, sid: ("T", ""))
    monkeypatch.setattr(pipeline, "run_extract", lambda args, clients, keys, log, sheet_title: calls.append(("queue", keys)) or 0)
    monkeypatch.setattr(pipeline, "extract_queues", lambda args, clients, keys, log, sheet_title: calls.append(("queue", keys)) or (0, {k: ExtractSummary(queue_rows=[]) for k in keys}))
    monkeypatch.setattr(pipeline, "run_db_sync", lambda args, clients, keys, log, sheet_title, planned=None: calls.append(("update", keys, args.allow_large_removal)) or 0)
    monkeypatch.setattr(pipeline, "run_queues", lambda args, clients, keys, log, sheet_title, planned=None: calls.append(("ai", keys)) or 0)
    monkeypatch.setattr(cli, "run_queues", lambda args, clients, keys, log, sheet_title: calls.append(("check", keys, args.check_access)) or 0)
    return main(argv), calls


def test_every_command_goes_to_its_own_runner_and_needs_a_gemini_key_only_when_the_ai_really_runs(monkeypatch):
    code, calls = routed(monkeypatch, ["sync", "db", "--queue", "dwr", "--allow-large-removal"])
    assert code == 0 and calls == [("need_gemini", True), ("update", ["dwr"], True), ("ai", ["dwr"])]            # part 1, then the AI
    code, calls = routed(monkeypatch, ["sync", "db", "--no-ai"])
    assert calls == [("need_gemini", False), ("update", ["dwr", "drq"], False)]
    code, calls = routed(monkeypatch, ["sync", "db", "--dry-run"])
    assert calls == [("need_gemini", False), ("update", ["dwr", "drq"], False), ("ai", ["dwr", "drq"])]
    code, calls = routed(monkeypatch, ["sync", "db", "--only-rows", "5", "--queue", "drq"])
    assert calls == [("need_gemini", True), ("ai", ["drq"])]                                                       # only the AI part, for those rows
    code, calls = routed(monkeypatch, ["sync", "queue"])
    assert calls == [("need_gemini", False), ("queue", ["dwr", "drq"])]
    code, calls = routed(monkeypatch, ["check", "access", "--queue", "dwr"])
    assert calls == [("need_gemini", False), ("check", ["dwr"], True)]
    code, calls = routed(monkeypatch, ["sync"])
    assert calls[0] == ("need_gemini", True) and [c[0] for c in calls[1:]] == ["queue", "update", "ai"]
    code, calls = routed(monkeypatch, ["sync", "--no-ai"])
    assert calls[0] == ("need_gemini", False) and [c[0] for c in calls[1:]] == ["queue", "update"]
    code, calls = routed(monkeypatch, ["sync", "--dry-run"])
    assert calls[0] == ("need_gemini", False) and [c[0] for c in calls[1:]] == ["queue", "update", "ai"]
    code, calls = routed(monkeypatch, ["sync", "queue", "db", "--no-ai"])
    assert calls[0] == ("need_gemini", False) and [c[0] for c in calls[1:]] == ["queue", "update"]


def test_a_missing_gemini_key_stops_before_anything_runs_unless_the_ai_does_not_run(monkeypatch):
    seen = []
    monkeypatch.setenv("PMIS_SPREADSHEET_ID", "SID")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(cli, "load_env_file", lambda: False)
    monkeypatch.setattr(pipeline, "run_db_sync", lambda *a, **k: seen.append("update") or 0)

    def fake_google_clients(args, need_gemini):
        if need_gemini:
            raise SystemExit("GEMINI_API_KEY is not set")
        return Clients(sheets=None, drive_factory=None, gemini=None, api_key="", sa_email="sa@x")

    monkeypatch.setattr(cli, "make_clients", fake_google_clients)
    monkeypatch.setattr(cli, "spreadsheet_title", lambda sess, sid: ("T", ""))
    with pytest.raises(SystemExit):
        main(["sync", "db"])
    assert seen == []                                                                                  # nothing ran, not even part 1
    assert main(["sync", "db", "--no-ai"]) == 0 and seen == ["update"]


# --- 옛 옵션 / the old options / ออปชันเก่า ---
def test_the_old_options_are_translated_into_the_new_commands():
    assert translate_old_options([]) == ["sync", "db"]                                                # the old default (the AI) is the db step now
    assert translate_old_options(["--limit", "5", "--queue", "dwr"]) == ["sync", "db", "--limit", "5", "--queue", "dwr"]
    assert translate_old_options(["--extract"]) == ["sync", "queue"]
    assert translate_old_options(["--extract", "--dry-run", "--sync-db"]) == ["sync", "queue", "db", "--no-ai", "--dry-run"]     # the old --sync-db never used the AI
    assert translate_old_options(["--sync-db", "--allow-large-removal"]) == ["sync", "db", "--no-ai", "--allow-large-removal"]
    assert translate_old_options(["--check-access", "--limit", "0"]) == ["check", "access", "--limit", "0"]
    assert translate_old_options(["--extract", "--overwrite"]) == ["sync", "queue", "--overwrite"]
    for argv, text in ((["--extract", "--check-access"], "cannot be combined with --check-access"), (["--check-access", "--sync-db"], "cannot be combined with --check-access"),
                       (["--backfill-cp", "--extract"], "cannot be combined with --backfill-cp")):
        with pytest.raises(SystemExit) as e:
            translate_old_options(argv)
        assert text in str(e.value.code)
    with pytest.raises(SystemExit) as e:
        translate_old_options(["--backfill-cp", "--queue", "dwr"])
    assert e.value.code == BACKFILL_REMOVED and "sync db --no-ai" in BACKFILL_REMOVED


def test_every_translated_old_command_is_a_valid_new_command():
    for old in ([], ["--extract"], ["--extract", "--sync-db"], ["--sync-db"], ["--check-access"], ["--extract", "--sync-db", "--dry-run", "--queue", "dwr"],
                ["--limit", "5", "--only-rows", "83", "--queue", "dwr"], ["--extract", "--overwrite", "--legacy-spreadsheet", "X", "--allow-large-removal"]):
        build_parser().parse_args(translate_old_options(old))


def test_pmis_py_refuses_the_old_options_and_says_what_to_type(monkeypatch):
    monkeypatch.setenv("PMIS_SPREADSHEET_ID", "SID")
    for argv, new in ((["--extract"], "sync queue"), (["sync", "--sync-db"], "sync db --no-ai"), (["--check-access", "--limit", "0"], "check access")):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert f"python pmis.py {new}" in str(e.value.code) and "migrate_queue.py still accepts" in str(e.value.code)
    for argv in (["--backfill-cp"], ["fix", "cp"], ["fix"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == BACKFILL_REMOVED
    for argv in (["--dry-run"], ["--limit", "5"], ["--queue", "dwr"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert "Start with a command" in str(e.value.code) and "sync [queue] [db]" in str(e.value.code)


def test_migrate_queue_py_translates_and_tells_the_new_command(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(cli, "entry", lambda argv=None: seen.append(argv))
    for old, new, said in ((["--extract", "--sync-db", "--dry-run", "--queue", "dwr"], ["sync", "queue", "db", "--no-ai", "--dry-run", "--queue", "dwr"], "sync queue db --no-ai"),
                           ([], ["sync", "db"], "sync db"), (["--sync-db"], ["sync", "db", "--no-ai"], "sync db --no-ai"),
                           (["--check-access", "--limit", "0"], ["check", "access", "--limit", "0"], "check access")):
        monkeypatch.setattr("sys.argv", ["migrate_queue.py"] + old)
        old_entry()
        assert seen[-1] == new
        err = capsys.readouterr().err
        assert f"python pmis.py {said} ..." in err
        assert "--dry-run" not in err and "--queue" not in err and "--limit" not in err      # the note names the command only, never the other options
    monkeypatch.setattr("sys.argv", ["migrate_queue.py", "--help"])
    old_entry()
    assert seen[-1] == ["--help"]


def test_the_entry_points_load_the_env_file_and_turn_ctrl_c_into_130(monkeypatch):
    loaded = []
    monkeypatch.setattr(cli, "load_env_file", lambda: loaded.append(True))
    monkeypatch.setattr(cli, "main", lambda argv=None: 0)
    with pytest.raises(SystemExit) as e:
        cli.entry(["sync"])
    assert e.value.code == 0 and loaded == [True]

    def interrupted(argv=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "main", interrupted)
    with pytest.raises(SystemExit) as e:
        cli.entry(["sync"])
    assert e.value.code == 130


# --- --allow-large-removal ---
def test_the_removal_option_is_off_by_default():
    assert parse(["sync"]).allow_large_removal is False and parse(["sync", "db", "--allow-large-removal"]).allow_large_removal is True
    assert parse(["sync", "queue", "--allow-large-removal"]).allow_large_removal is True
