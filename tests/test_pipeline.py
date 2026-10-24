"""
`sync` 의 단계(queue -> db)와 db 단계의 두 부분(1부 AI 없이 DB 고치기, 2부 AI)을 이어서 실행하는 테스트: 순서, 제목, 실패한 큐 빼기, --dry-run 계획 넘기기, --no-ai / --only-rows, 종료 코드
[TH] เทสต์การรันขั้นตอนของ `sync` (queue -> db) และสองส่วนของขั้น db (ส่วน 1 แก้ DB โดยไม่ใช้ AI, ส่วน 2 AI) ต่อกัน: ลำดับ, หัวข้อ, ตัดคิวที่ล้มเหลวออก, ส่งแผน --dry-run ต่อ, --no-ai / --only-rows, รหัสออก
[EN] Tests for running the steps of `sync` (queue -> db) and the two parts of the db step (part 1 fixes the DB without the AI, part 2 is the AI) one after another:
     the order, the titles, leaving out a failed queue, handing the --dry-run plan on, --no-ai / --only-rows, the exit code
"""

from migrator import pipeline
from migrator.pipeline import ordered_steps, planned_values, step_text
from migrator.queue_writer import QUEUE_HEADER, REPLACES_LABEL

from tests.legacy_fakes import DRQ_ROWS, DWR_ROWS, FakeLegacy, extract_main, legacy_grid


class StepRecorder:
    """DB 고치기(1부) / AI(2부)를 대신해 호출을 기록 / [TH] บันทึกการเรียกแทนส่วน 1 (แก้ DB) / ส่วน 2 (AI) / [EN] Records the calls instead of part 1 (fixing the DB) / part 2 (the AI)"""

    def __init__(self, name, code=0):
        self.name, self.code, self.calls = name, code, []

    def __call__(self, args, clients, keys, log=print, sheet_title="", planned=None):
        self.calls.append({"keys": list(keys), "planned": planned, "dry_run": args.dry_run})
        log(f"[{self.name} part ran on {', '.join(keys)}]")
        return self.code


def chain(monkeypatch, argv, w=None, update_code=0, ai_code=0):
    update, ai = StepRecorder("update", update_code), StepRecorder("ai", ai_code)
    monkeypatch.setattr(pipeline, "run_db_sync", update)
    monkeypatch.setattr(pipeline, "run_queues", ai)
    code, w = extract_main(monkeypatch, argv, w=w)
    return code, w, update, ai


def test_the_steps_are_always_in_the_order_queue_db():
    assert ordered_steps(None) == ["queue", "db"] and ordered_steps([]) == ["queue", "db"]
    assert ordered_steps(["db", "queue"]) == ["queue", "db"] and ordered_steps(["db", "db"]) == ["db"] and ordered_steps(["queue"]) == ["queue"]


def test_planned_values_look_like_the_values_read_from_the_queue_sheet():
    values = planned_values([DWR_ROWS[0]])
    assert values[0] == QUEUE_HEADER + [REPLACES_LABEL] and values[1] == DWR_ROWS[0] and values[1] is not DWR_ROWS[0]


def test_the_db_step_says_when_the_ai_does_not_run():
    assert "with the AI reading the PDFs of the Waiting rows" in step_text("db") and "--no-ai" in step_text("db", True)
    assert step_text("queue") == step_text("queue", True) == "legacy sheet -> queue"


def test_sync_runs_queue_then_the_two_parts_of_db_with_titles(monkeypatch, capsys):
    code, w, update, ai = chain(monkeypatch, ["sync"])
    out = capsys.readouterr().out
    assert code == 0
    assert w.queues["MIGRATION_QUEUE"][1:] == DWR_ROWS and w.queues["MIGRATION_QUEUE_DRQ"][1:] == DRQ_ROWS            # the queue step really wrote the queues
    order = ["▶ Step 1/2 - sync queue: legacy sheet -> queue", "Build queue DWR", "▶ Step 2/2 - sync db: queue -> DB, with the AI reading the PDFs of the Waiting rows",
             "Part 1/2 - update the DB rows (no AI)", "[update part ran on dwr, drq]", "Part 2/2 - AI: read the PDFs of the Waiting rows", "[ai part ran on dwr, drq]", "Sync steps: queue OK, db OK"]
    assert [out.index(t) for t in order] == sorted(out.index(t) for t in order)
    assert "mode: sync queue db" in out.splitlines()[0]
    assert [c["keys"] for c in update.calls + ai.calls] == [["dwr", "drq"], ["dwr", "drq"]]
    assert update.calls[0]["planned"] is None and ai.calls[0]["planned"] is None                  # a real run: the db step reads the queue sheet again


def test_sync_dry_run_hands_the_planned_queue_to_both_parts_and_writes_nothing(monkeypatch):
    code, w, update, ai = chain(monkeypatch, ["sync", "--dry-run"])
    assert code == 0 and w.queues == {} and w.events == []
    assert update.calls[0]["dry_run"] is True and ai.calls[0]["dry_run"] is True
    assert update.calls[0]["planned"] == {"dwr": DWR_ROWS, "drq": DRQ_ROWS}                                           # what part 1 would match the DB to
    assert ai.calls[0]["planned"] == {"dwr": planned_values(DWR_ROWS), "drq": planned_values(DRQ_ROWS)}               # what the AI part would see in the queue


def test_a_queue_that_could_not_be_built_is_left_out_of_the_db_step(monkeypatch, capsys):
    w = FakeLegacy(grids={"LEGACY-DWR": legacy_grid()})                                       # the DRQ legacy tab is missing
    code, w, update, ai = chain(monkeypatch, ["sync"], w=w)
    out = capsys.readouterr().out
    assert code == 2 and [c["keys"] for c in update.calls + ai.calls] == [["dwr"], ["dwr"]]
    assert "↷ DRQ: not built, so the next step skips it" in out and "Sync steps: queue FAILED, db OK" in out
    assert "MIGRATION_QUEUE_DRQ" not in w.queues


def test_when_no_queue_is_built_the_db_step_is_skipped(monkeypatch, capsys):
    w = FakeLegacy(grids={"SOMETHING-ELSE": legacy_grid()})                                   # neither legacy tab exists
    code, w, update, ai = chain(monkeypatch, ["sync"], w=w)
    out = capsys.readouterr().out
    assert code == 2 and update.calls == [] and ai.calls == []
    assert out.count("↷ skipped: no queue is left (the queue step built nothing)") == 1 and "Sync steps: queue FAILED, db skipped" in out


def test_a_failed_part_does_not_stop_the_other_but_the_exit_code_is_2(monkeypatch, capsys):
    code, _, update, ai = chain(monkeypatch, ["sync"], update_code=2)
    assert code == 2 and len(ai.calls) == 1 and "Sync steps: queue OK, db FAILED" in capsys.readouterr().out          # the AI still reads the Waiting rows
    code, _, update, ai = chain(monkeypatch, ["sync"], ai_code=2)
    assert code == 2 and len(update.calls) == 1 and "Sync steps: queue OK, db FAILED" in capsys.readouterr().out


def test_no_ai_runs_only_part_1_and_says_so(monkeypatch, capsys):
    code, w, update, ai = chain(monkeypatch, ["sync", "--no-ai"])
    out = capsys.readouterr().out
    assert code == 0 and len(update.calls) == 1 and ai.calls == []
    assert "▶ Step 2/2 - sync db: queue -> DB, update the data only (--no-ai)" in out and "Part 1/2" not in out and "mode: sync queue db --no-ai" in out.splitlines()[0]
    assert w.queues["MIGRATION_QUEUE"][1:] == DWR_ROWS


def test_only_rows_runs_only_the_ai_part_for_those_rows(monkeypatch, capsys):
    code, w, update, ai = chain(monkeypatch, ["sync", "db", "--only-rows", "5", "--queue", "dwr"])
    assert code == 0 and update.calls == [] and [c["keys"] for c in ai.calls] == [["dwr"]] and "Part 1/2" not in capsys.readouterr().out


def test_two_steps_run_in_the_fixed_order_whatever_order_they_are_typed_in(monkeypatch, capsys):
    code, w, update, ai = chain(monkeypatch, ["sync", "db", "queue", "--no-ai"])
    out = capsys.readouterr().out
    assert code == 0 and out.index("▶ Step 1/2 - sync queue") < out.index("▶ Step 2/2 - sync db") and w.queues["MIGRATION_QUEUE"][1:] == DWR_ROWS


def test_the_db_step_alone_reads_the_stored_queue_and_has_no_step_titles(monkeypatch, capsys):
    code, w, update, ai = chain(monkeypatch, ["sync", "db", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0 and w.queues == {} and update.calls[0]["planned"] is None and ai.calls[0]["planned"] is None
    assert "▶ Step" not in out and "Sync steps" not in out and "Part 1/2" in out and "Part 2/2" in out


def test_the_queue_step_alone_has_no_step_titles_or_a_summary(monkeypatch, capsys):
    code, w, update, ai = chain(monkeypatch, ["sync", "queue"])
    out = capsys.readouterr().out
    assert code == 0 and update.calls == [] and ai.calls == [] and "▶ Step" not in out and "Sync steps" not in out


def test_a_part_that_returns_2_alone_is_the_exit_code(monkeypatch):
    assert chain(monkeypatch, ["sync", "db"], update_code=2)[0] == 2 and chain(monkeypatch, ["sync", "db"], ai_code=2)[0] == 2
    assert chain(monkeypatch, ["sync", "db", "--no-ai"], update_code=2)[0] == 2


def test_the_public_log_of_a_chain_keeps_the_step_and_part_lines_and_nothing_secret(monkeypatch, capsys):
    code, w, update, ai = chain(monkeypatch, ["sync", "--dry-run", "--public-log"], w=FakeLegacy(grids={"LEGACY-DWR": legacy_grid()}))
    out = capsys.readouterr().out
    assert code == 2                                                                           # the DRQ legacy tab is missing, even in a dry run
    for line in ("▶ Step 1/2 - sync queue: legacy sheet -> queue", "▶ Step 2/2 - sync db: queue -> DB, with the AI reading the PDFs of the Waiting rows",
                 "  Part 1/2 - update the DB rows (no AI): C/P, Folder Link, tags, removals", "  Part 2/2 - AI: read the PDFs of the Waiting rows",
                 "  ↷ DRQ: not built, so the next step skips it", "Sync steps: queue FAILED, db OK"):
        assert line in out
    for secret in ("LEGACY-DWR", "service account", "Example Spreadsheet", "[update part ran", "[ai part ran"):
        assert secret not in out
