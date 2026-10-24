"""
`sync` 명령의 단계: queue (옛 시트 -> 큐) -> db (큐 -> DB: 데이터를 맞추고, AI 가 Waiting 행의 PDF 를 읽음). 단계를 이 순서로 이어서 실행
[TH] ขั้นตอนของคำสั่ง `sync`: queue (ชีตเก่า -> คิว) -> db (คิว -> DB: อัปเดตข้อมูล และให้ AI อ่าน PDF ของแถว Waiting) รันต่อกันตามลำดับนี้
[EN] The steps of the `sync` command: queue (legacy sheet -> queue) -> db (queue -> DB: update the data, and the AI reads the PDFs of the Waiting rows), run in this order
"""
from __future__ import annotations

import argparse
from typing import Callable, Iterable, Optional

from .db_sync import run_db_sync
from .extract import extract_queues, run_extract
from .models import Clients
from .queue_writer import QUEUE_HEADER, REPLACES_LABEL
from .runner import run_queues
from .specs import SPECS

# 단계의 고정된 순서 / [TH] ลำดับคงที่ของขั้นตอน / [EN] The fixed order of the steps
STEPS = ("queue", "db")

# 단계 제목에 붙는 설명 (고정 문구: 공개 로그에도 그대로 나감)
# [TH] คำอธิบายต่อท้ายหัวขั้นตอน (ข้อความคงที่: ออกใน log สาธารณะได้)
# [EN] The text after a step title (fixed wording: safe in the public log)
STEP_TEXT = {
    "queue": "legacy sheet -> queue",
    "db": "queue -> DB, with the AI reading the PDFs of the Waiting rows",
}
STEP_TEXT_NO_AI = "queue -> DB, update the data only (--no-ai)"


def step_text(step: str, no_ai: bool = False) -> str:
    """단계 제목에 붙는 설명 (--no-ai 면 db 단계에 AI 가 없다고 적음) / [TH] คำอธิบายต่อท้ายหัวขั้นตอน (ถ้า --no-ai ขั้น db จะบอกว่าไม่มี AI) / [EN] The text after a step title (with --no-ai the db step says there is no AI)"""
    return STEP_TEXT_NO_AI if step == "db" and no_ai else STEP_TEXT[step]

# `sync db` 의 두 부분 제목 (고정 문구) / [TH] หัวของสองส่วนใน `sync db` (ข้อความคงที่) / [EN] The titles of the two parts of `sync db` (fixed wording)
PART_UPDATE = "  Part 1/2 - update the DB rows (no AI): C/P, Folder Link, tags, removals"
PART_AI = "  Part 2/2 - AI: read the PDFs of the Waiting rows"


def ordered_steps(wanted: Optional[Iterable[str]]) -> list[str]:
    """
    단계를 고정된 순서(queue, db)로 정렬. 하나도 지정하지 않으면 두 단계 모두
    [TH] เรียงขั้นตอนตามลำดับคงที่ (queue, db) ถ้าไม่ระบุเลยจะได้ทั้งสองขั้น
    [EN] Puts the steps in their fixed order (queue, db); none given = both
    """
    chosen = set(wanted or ())
    return [s for s in STEPS if s in chosen] if chosen else list(STEPS)


def planned_values(rows: list[list]) -> list[list]:
    """
    계획된 큐 행에 머리글을 붙여 '큐 시트를 읽은 값' 모양으로 만듦 (`sync db --dry-run` 의 AI 부분이 계획된 큐를 보여 줄 때 사용)
    [TH] ใส่หัวตารางให้แถวคิวตามแผน ให้หน้าตาเหมือน "ค่าที่อ่านจากชีตคิว" (ใช้ตอนส่วน AI ของ `sync db --dry-run` แสดงคิวตามแผน)
    [EN] Puts the header on the planned queue rows so they look like "the values read from the queue sheet" (used when the AI part of `sync db --dry-run` shows the planned queue)
    """
    return [QUEUE_HEADER + [REPLACES_LABEL]] + [list(r) for r in rows]


def run_db_step(args: argparse.Namespace, clients: Clients, keys: list[str], log: Callable[[str], None] = print, sheet_title: str = "",
                planned: Optional[dict[str, list[list]]] = None) -> int:
    """
    `sync db` 단계: 1부 = 큐에 맞춰 DB 행을 고침 (AI 없음: C/P, Folder Link, 표시, 삭제), 2부 = AI 가 Waiting 행의 PDF 를 읽어 DB 에 씀.
    1부가 먼저라서 Gemini 가 멈추거나 키가 없어도 1부의 일은 끝나 있음. --no-ai 면 1부만, --only-rows 면 그 행만 AI 로 다시 시도하는 것이므로 2부만 실행
    planned 가 있으면(--dry-run 에서 queue 단계가 계획한 큐) 시트 대신 그 행으로 계획을 보여 줌. 종료 코드는 두 부분 중 큰 값
    [TH] ขั้น `sync db`: ส่วน 1 = แก้แถวใน DB ให้ตรงคิว (ไม่ใช้ AI: C/P, Folder Link, ป้าย, ลบแถว) ส่วน 2 = AI อ่าน PDF ของแถว Waiting แล้วเขียนลง DB
         ส่วน 1 ทำก่อน แม้ Gemini หยุดหรือไม่มีคีย์งานของส่วน 1 ก็เสร็จไปแล้ว ถ้าใส่ --no-ai ทำแค่ส่วน 1 ถ้าใส่ --only-rows คือลองแถวนั้นกับ AI ใหม่ จึงทำแค่ส่วน 2
         ถ้ามี planned (คิวที่ขั้น queue วางแผนไว้ตอน --dry-run) จะแสดงแผนจากแถวนั้นแทนชีต รหัสออกคือค่าที่มากกว่าของสองส่วน
    [EN] The `sync db` step: part 1 = fix the DB rows to match the queue (no AI: C/P, Folder Link, tags, removals), part 2 = the AI reads the PDFs of the Waiting rows and writes the DB.
         Part 1 goes first, so its work is done even when Gemini stops or the key is missing. --no-ai runs only part 1; --only-rows retries those rows with the AI, so only part 2 runs.
         With planned (the queue the queue step planned in a --dry-run) the plan is shown on those rows instead of the sheet. The exit code is the larger of the two parts
    """
    update = not getattr(args, "only_rows", None)
    ai = not getattr(args, "no_ai", False)
    both = update and ai
    code = 0
    if update:
        if both:
            log(PART_UPDATE)
        code = max(code, run_db_sync(args, clients, keys, log=log, sheet_title=sheet_title, planned=planned or None))
    if ai:
        if both:
            log("\n" + PART_AI)
        code = max(code, run_queues(args, clients, keys, log=log, sheet_title=sheet_title,
                                    planned={k: planned_values(rows) for k, rows in planned.items()} if planned else None))
    return code


def run_sync(args: argparse.Namespace, clients: Clients, keys: list[str], steps: Optional[Iterable[str]] = None,
             log: Callable[[str], None] = print, sheet_title: str = "") -> int:
    """
    `sync` 실행. 단계가 하나면 그 단계만 (출력은 단계 제목 없이 그대로). 둘이면 queue -> db 순서로 이어서 실행:
      * queue 단계에서 실패하거나 거부된(또는 만들 항목이 없어 건드리지 않은) 큐는 db 단계에서 뺌
      * --dry-run 이면 queue 단계가 쓰지 않고 계획만 한 큐 행을 db 단계가 그대로 받아 전체 계획을 보여 줌
      * 실제 실행이면 db 단계는 큐 시트를 다시 읽음 (queue 단계가 쓴 것을 그대로 읽는 것이며, 그사이 다른 곳에서 바뀐 것도 반영)
    종료 코드: 단계들의 종료 코드 중 큰 값 (0 = 정상, 2 = 실패/중단/거부가 있음)
    [TH] รัน `sync` ถ้ามีขั้นเดียวจะรันขั้นนั้นอย่างเดียว (ผลลัพธ์ไม่มีหัวขั้นตอน เหมือนเดิม) ถ้ามีสองขั้นจะรันต่อกันตามลำดับ queue -> db:
         * คิวที่ล้มเหลว ถูกปฏิเสธ (หรือไม่มีรายการให้สร้างจนไม่ได้แตะ) ในขั้น queue จะถูกตัดออกจากขั้น db
         * ถ้า --dry-run ขั้น queue ไม่ได้เขียนแค่วางแผนไว้ ขั้น db จะรับแถวคิวตามแผนนั้นไปแสดงแผนทั้งหมด
         * ถ้ารันจริง ขั้น db จะอ่านชีตคิวใหม่ (อ่านสิ่งที่ขั้น queue เขียน และสิ่งที่มีคนแก้ที่อื่นระหว่างนั้น)
         รหัสออก: ค่าที่มากที่สุดของรหัสออกของแต่ละขั้น (0 = ปกติ, 2 = มีการล้มเหลว/หยุด/ปฏิเสธ)
    [EN] Runs `sync`. One step: just that step (its output has no step title, as before). Two steps run one after another in the order queue -> db:
         * a queue that failed, was refused (or was left untouched because there was nothing to build) in the queue step is left out of the db step
         * with --dry-run the queue step does not write but only plans, and the db step takes those planned queue rows, so the whole plan is shown
         * in a real run the db step reads the queue sheet again (what the queue step wrote, and anything somebody changed elsewhere in between)
         Exit code: the largest of the exit codes of the steps (0 = fine, 2 = something failed / stopped / was refused)
    """
    chosen = ordered_steps(steps)
    if len(chosen) == 1:
        if chosen[0] == "queue":
            return run_extract(args, clients, keys, log=log, sheet_title=sheet_title)
        return run_db_step(args, clients, keys, log=log, sheet_title=sheet_title)

    exit_code = 0
    live = list(keys)                              # the queues the next step still works on / [TH] คิวที่ขั้นถัดไปยังทำต่อ / [EN] ...
    planned: dict[str, list[list]] = {}            # --dry-run only: queue key -> the rows the queue step planned / [TH] เฉพาะ --dry-run / [EN] ...
    result: dict[str, str] = {}
    for number, step in enumerate(chosen, start=1):
        log(("" if number == 1 else "\n") + f"▶ Step {number}/{len(chosen)} - sync {step}: {step_text(step, getattr(args, 'no_ai', False))}")
        if not live:
            log("  ↷ skipped: no queue is left (the queue step built nothing)")
            result[step] = "skipped"
            continue
        if step == "queue":
            code, built = extract_queues(args, clients, live, log=log, sheet_title=sheet_title)
            left_out = [k for k in live if k not in built]
            if left_out:
                log(f"  ↷ {', '.join(SPECS[k].name for k in left_out)}: not built, so the next step skips it")
            live = [k for k in live if k in built]
            if args.dry_run:
                planned = {k: list(built[k].queue_rows or []) for k in live}
        else:
            code = run_db_step(args, clients, live, log=log, sheet_title=sheet_title, planned=planned or None)
        result[step] = "OK" if code == 0 else "FAILED"
        exit_code = max(exit_code, code)
    log("\nSync steps: " + ", ".join(f"{step} {result[step]}" for step in chosen))
    return exit_code
