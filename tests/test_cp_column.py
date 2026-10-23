"""
DB_DWR(T열) / DB_DRQ(S열) 의 C/P 열 테스트: 새 행에 큐의 C/P 기록, 머리글 확인, 기존 행 채우기(--backfill-cp)
[TH] เทสต์คอลัมน์ C/P ของ DB_DWR (T) / DB_DRQ (S): เขียน C/P จากคิวลงแถวใหม่, เช็กหัวตาราง, เติมแถวเดิม (--backfill-cp)
[EN] Tests for the C/P column of DB_DWR (T) / DB_DRQ (S): the queue's C/P on new rows, the header check, filling the existing rows (--backfill-cp)
"""
import pytest

from migrator import cli
from migrator.cli import main
from migrator.cp_column import BACKFILL_CELLS_PER_WRITE, apply_backfill, backfill_cp, ensure_cp_header, plan_backfill, run_backfill
from migrator.models import Clients
from migrator.reports import build_db_row
from migrator.runner import run, run_queues
from migrator.sheets import SheetsError
from migrator.specs import DB_CP_HEADER, SPECS

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


# --- 새 행 / new rows / แถวใหม่ ---
def test_new_db_rows_carry_the_queue_cp_in_column_t():
    w = world(["Waiting"] * 3)
    set_cp(w, ["C", "P", ""])
    logs = []
    s = run(ns(), w.clients(), log=logs.append)
    assert s.done == 3
    assert [r[19] for r in w.db[1:]] == ["C", "P", ""]                                   # as the queue says, an empty one stays empty
    assert [r[17] for r in w.db[1:]] == [URL(1), URL(2), URL(3)] and [len(r) for r in w.db[1:]] == [21, 21, 21]
    assert [r[20] for r in w.db[1:]] == ["Synced"] * 3                                    # every row migrate writes is in sync with the legacy sheet
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


# --- 계획 / the plan / แผน ---
def queue_with(rows):
    return [["Doc No", "Date", "Discipline", "PDF URL", "Folder URL", "Status(C/P)", "AI Processed"]] + rows


def test_plan_backfill_matches_by_file_id_and_never_guesses():
    queue = queue_with([["a", "", "", URL(1), "", "C", "Done"], ["b", "", "", URL(2), "", "P", "Done"],
                        ["c", "", "", URL(3), "", "C", "Done"], ["d", "", "", URL(3), "", "P", "Done"],         # URL(3): C and P -> ambiguous
                        ["e", "", "", URL(4), "", "", "Done"]])                                                 # URL(4): no C/P in the queue
    db = [["h"] * 20] + [db_row(1), db_row(2, "P"), db_row(3), db_row(4), db_row(5), db_row(1), [""] * 3]
    db[1] = db[1][:19]                                                                                         # a short row: no T cell at all
    plan = plan_backfill(queue, db, SPECS["dwr"])
    assert plan.updates == {2: "C", 7: "C"}                                    # both DB rows of URL(1); the row that already has "P" is not touched
    assert (plan.already, plan.ambiguous, plan.unmatched, plan.no_pdf) == (1, 1, 2, 1)


def test_plan_backfill_uses_the_pdf_and_cp_columns_of_each_db():
    drq = SPECS["drq"]
    queue = queue_with([["a", "", "", URL(1), "", "P", "Done"]])
    db_drq = [["h"] * 19, [""] * 14 + [URL(1)] + [""] * 3]                          # PDF Link is column O, C/P is column S (index 18)
    assert plan_backfill(queue, db_drq, drq).updates == {2: "P"}
    assert plan_backfill(queue, [["h"] * 19, [""] * 17 + [URL(1)]], drq).updates == {}              # DWR's PDF column (R) is not the PDF column of DB_DRQ
    filled = [""] * 14 + [URL(1)] + [""] * 3 + ["C"]
    assert plan_backfill(queue, [["h"] * 19, filled[:19]], drq).already == 1 and plan_backfill(queue, [["h"] * 19, filled[:19]], drq).updates == {}


def test_drq_c_p_is_written_to_column_s_only():
    from migrator.cp_column import BackfillPlan
    seen = []

    class Sess:
        def request(self, method, url, **kw):
            seen.append((method, kw["json"]["data"][0]["range"]))
            return type("R", (), {"status_code": 200, "json": staticmethod(lambda: {})})()

    apply_backfill(Sess(), "SID", SPECS["drq"], BackfillPlan(updates={2: "C"}))
    assert seen == [("POST", "'DB_DRQ'!S2")]


def test_apply_backfill_writes_in_chunks_to_column_t_only(monkeypatch):
    w = world(["Done"] * 5)
    w.db.extend([db_row(i) for i in range(1, 6)])
    from migrator import cp_column
    from migrator.cp_column import BackfillPlan
    monkeypatch.setattr(cp_column, "BACKFILL_CELLS_PER_WRITE", 2)
    apply_backfill(w.clients().sheets, "SID", SPECS["dwr"], BackfillPlan(updates={2: "C", 3: "P", 4: "C", 5: "P", 6: "C"}))
    assert [len(chunk) for chunk in w.batch_updates] == [2, 2, 1] and BACKFILL_CELLS_PER_WRITE == 500
    assert [r[19] for r in w.db[1:6]] == ["C", "P", "C", "P", "C"] and all(item["range"].startswith("'DB_DWR'!T") for chunk in w.batch_updates for item in chunk)


# --- 기존 행 채우기 / filling the existing rows / เติมแถวเดิม ---
def backfill_world():
    w = world(["Done"] * 4)
    set_cp(w, ["C", "P", "C", "P"])
    w.queue.append(["extra", "2025-01-01", "CIV", URL(3), "", "P", "Done"])         # URL(3) is C in row 4 and P here -> ambiguous
    w.db.extend([db_row(1), db_row(2), db_row(3), db_row(4, "X"), db_row(9)])      # 4 already has a value, 9 is not in the queue
    return w


def test_backfill_dry_run_reports_the_plan_and_writes_nothing():
    w = backfill_world()
    before = [list(r) for r in w.db]
    logs = []
    summary = backfill_cp(ns(dry_run=True), w.clients(), "dwr", log=logs.append)
    assert w.db == before and w.cp_header_writes == [] and w.batch_updates == [] and summary.filled == 0
    assert any("[DWR] Backfill C/P in DB_DWR: 2 to fill (C=1, P=1), 1 already have a C/P, 1 not found in MIGRATION_QUEUE, 1 with different C/P values" in m for m in logs)
    assert any("(--dry-run) nothing was written: would fill 2 cells of DB_DWR!T" in m for m in logs)


def test_backfill_fills_only_the_matching_empty_cells_and_is_repeatable():
    w = backfill_world()
    logs = []
    summary = backfill_cp(ns(), w.clients(), "dwr", log=logs.append)
    cp = [r[19] for r in w.db[1:]]
    assert cp == ["C", "P", "", "X", ""] and summary.filled == 2                         # filled 1 and 2; the ambiguous / old / unmatched rows are left alone
    assert w.cp_header == DB_CP_HEADER
    assert any("Wrote the header 'Status (C/P)' to DB_DWR!T1" in m for m in logs) and any("Filled C/P in DB_DWR!T: 2 rows" in m for m in logs)
    assert [r[6] for r in w.queue[1:5]] == ["Done"] * 4                                  # the queue is only read
    again = backfill_cp(ns(), w.clients(), "dwr", log=lambda m: None)
    assert again.filled == 0 and [r[19] for r in w.db[1:]] == cp and w.cp_header_writes == [DB_CP_HEADER]


def test_backfill_does_not_touch_the_header_when_there_is_nothing_to_fill():
    w = world(["Done"])
    w.db.append(db_row(7))
    summary = backfill_cp(ns(), w.clients(), "dwr", log=lambda m: None)
    assert summary.filled == 0 and w.cp_header_writes == []


def test_backfill_with_a_different_header_stops_with_exit_code_2():
    w = backfill_world()
    w.cp_header = "Remarks"
    before = [list(r) for r in w.db]
    logs = []
    assert run_backfill(ns(), w.clients(), ["dwr"], log=logs.append) == 2
    assert w.db == before and w.batch_updates == [] and any("✗ Could not backfill C/P in DB_DWR" in m and "Remarks" in m for m in logs)


# --- 명령줄 / the command line / บรรทัดคำสั่ง ---
def backfill_main(monkeypatch, argv, w):
    monkeypatch.setenv("PMIS_SPREADSHEET_ID", "SID")
    need = []

    def fake_make_clients(args, need_gemini):
        need.append(need_gemini)
        return w.clients()

    monkeypatch.setattr(cli, "make_clients", fake_make_clients)
    monkeypatch.setattr(cli, "spreadsheet_title", lambda sess, sid: ("Example Spreadsheet", ""))
    return main(argv), need


def test_the_option_runs_the_backfill_without_a_gemini_key(monkeypatch, capsys):
    w = backfill_world()
    code, need = backfill_main(monkeypatch, ["--backfill-cp", "--queue", "dwr"], w)
    out = capsys.readouterr().out
    assert code == 0 and need == [False] and [r[19] for r in w.db[1:]][:2] == ["C", "P"] and w.gemini_calls == 0
    assert "mode: fill the C/P column of the DB tabs (--backfill-cp)" in out and "===== Backfill C/P DWR: DB_DWR <- MIGRATION_QUEUE" in out


def test_the_option_works_with_dry_run_and_rejects_conflicting_flags(monkeypatch):
    w = backfill_world()
    before = [list(r) for r in w.db]
    code, _ = backfill_main(monkeypatch, ["--backfill-cp", "--queue", "dwr", "--dry-run"], w)
    assert code == 0 and w.db == before
    for argv in (["--backfill-cp", "--only-rows", "5", "--queue", "dwr"], ["--backfill-cp", "--check-access"], ["--backfill-cp", "--overwrite"]):
        with pytest.raises(SystemExit) as e:
            backfill_main(monkeypatch, argv, w)
        assert "--backfill-cp" in str(e.value.code) or "--overwrite" in str(e.value.code)


def test_a_normal_run_is_unchanged_without_the_option(monkeypatch):
    seen = []
    monkeypatch.setenv("PMIS_SPREADSHEET_ID", "SID")
    monkeypatch.setattr(cli, "make_clients", lambda args, need_gemini: Clients(sheets=None, drive_factory=None, gemini=None, api_key="K", sa_email="sa@x"))
    monkeypatch.setattr(cli, "spreadsheet_title", lambda sess, sid: ("T", ""))
    monkeypatch.setattr(cli, "run_backfill", lambda *a, **k: seen.append("backfill") or 0)
    monkeypatch.setattr(cli, "run_queues", lambda *a, **k: seen.append("queues") or 0)
    assert main(["--dry-run"]) == 0 and seen == ["queues"]
