"""
명령줄 인터페이스: 명령(sync / check), 옵션, 클라이언트 만들기, main
[TH] อินเทอร์เฟซบรรทัดคำสั่ง: คำสั่ง (sync / check), ออปชัน, สร้าง client, main
[EN] Command-line interface: the commands (sync / check), the options, building the clients, main
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from .config import DEFAULT_API_VERSION, DEFAULT_MODEL, LARGE_PDF_MB, SCOPES
from .models import Clients
from .pipeline import STEPS, ordered_steps, run_sync, step_text
from .public_log import make_log
from .runner import queues_to_run, run_queues
from .settings import load_env_file, require_legacy_sheet_names, require_spreadsheet_id, resolve_legacy_spreadsheet_id
from .sheets import spreadsheet_title
from .specs import SPECS

PROG = "pmis.py"

# 옛 옵션(migrate_queue.py 시절) -> 지금의 명령. 옛 이름으로 pmis.py 를 부르면 이 안내와 함께 멈추고, migrate_queue.py 는 자동으로 바꿔 줌
# [TH] ออปชันเก่า (สมัย migrate_queue.py) -> คำสั่งปัจจุบัน ถ้าเรียก pmis.py ด้วยชื่อเก่าจะหยุดพร้อมคำแนะนำนี้ ส่วน migrate_queue.py จะแปลงให้อัตโนมัติ
# [EN] The old options (from the migrate_queue.py days) -> the commands of today. pmis.py stops with this hint when it is given an old name; migrate_queue.py translates it
OLD_OPTIONS = {"--extract": "sync queue", "--sync-db": "sync db --no-ai", "--backfill-cp": "sync db --no-ai", "--check-access": "check access"}

# --backfill-cp 와 `fix cp` 는 없어짐: `sync db` 가 C/P 를 모든 행에 맞춰 줌 / [TH] --backfill-cp และ `fix cp` ถูกลบแล้ว: `sync db` ทำ C/P ให้ตรงทุกแถว / [EN] --backfill-cp and `fix cp` are gone: `sync db` makes the C/P of every row match
BACKFILL_REMOVED = (f"--backfill-cp / `fix cp` was removed: `python {PROG} sync db --no-ai` fills and updates the C/P of every DB row "
                    f"(it also sets the Legacy sync tag and removes the rows that left the legacy sheet - look at it with --dry-run first)")

EXAMPLES = f"""\
examples:
  python {PROG} sync --dry-run              plan the whole chain (legacy sheet -> queue -> DB, with the AI); nothing is written
  python {PROG} sync queue                  legacy sheet -> queue (a new or changed PDF becomes a Waiting row)
  python {PROG} sync db --no-ai             update the DB rows from the queue: C/P, Folder Link, tags, removals (no AI, no Gemini key)
  python {PROG} sync db --limit 5           the same, then the AI reads up to 5 Waiting rows from each queue
  python {PROG} check access --limit 0      can the service account open every PDF?

Put the options after the command. The steps of `sync` always run in the order queue -> db, whatever order you type them in.
"""

SYNC_HELP = """\
Keep PMIS in sync with the legacy sheets. `sync` is a chain of two steps:

  queue   legacy sheet -> queue.  Build / sync MIGRATION_QUEUE and MIGRATION_QUEUE_DRQ from the legacy tabs.
          A new item or a changed PDF becomes a Waiting row, so that the AI reads it.
  db      queue -> DB.  Part 1 (no AI): update C/P and Folder Link, set the Legacy sync tag (Synced / Outdated),
          remove the rows that left the legacy sheet.  Part 2: the AI (Gemini) reads the PDFs of the Waiting rows
          and writes DB_DWR / DB_DRQ.

Name the steps you want (`sync queue`, `sync db`); with none, both run in this order. --no-ai runs only part 1 of the db step;
--only-rows runs only part 2, for those rows. A queue that failed or was refused in the queue step is left out of the db step.
"""


def step_name(value: str) -> str:
    """`sync` 뒤에 오는 단계 이름 확인 / [TH] ตรวจชื่อขั้นตอนที่ตามหลัง `sync` / [EN] Checks a step name after `sync`"""
    if value == "ai":
        raise argparse.ArgumentTypeError("the ai step was merged into db: use `sync db` (its part 2 is the AI; --no-ai skips it)")
    if value not in STEPS:
        raise argparse.ArgumentTypeError(f"unknown step {value!r} (choose from: {', '.join(STEPS)}; no step = both)")
    return value


def add_common_options(p: argparse.ArgumentParser) -> None:
    """모든 명령에 공통인 옵션 / [TH] ออปชันที่ทุกคำสั่งใช้ร่วมกัน / [EN] The options every command has"""
    p.add_argument("--queue", choices=sorted(SPECS), default=None,
                   help="dwr = MIGRATION_QUEUE->DB_DWR, drq = MIGRATION_QUEUE_DRQ->DB_DRQ; omitted = both queues (dwr first, then drq)")
    p.add_argument("--sa-key", default=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"), help="path to the service account key (.json)")
    p.add_argument("--dry-run", action="store_true", help="only show what would be done: nothing is written to the sheet and there are no Drive / Gemini calls")
    p.add_argument("--public-log", action="store_true",
                   help="print only counts and statuses (no tab names, sheet names / IDs, Doc Nos or error details), so the output is safe in the Actions log of a public repository; run without it locally for the details")


def add_ai_options(p: argparse.ArgumentParser, title: str) -> None:
    """AI 부분(과 check access)의 옵션 / [TH] ออปชันของส่วน AI (และ check access) / [EN] The options of the AI part (and of check access)"""
    g = p.add_argument_group(title)
    g.add_argument("--model", default=os.environ.get("GEMINI_MODEL", DEFAULT_MODEL))
    g.add_argument("--api-version", default=DEFAULT_API_VERSION)
    g.add_argument("--limit", type=int, default=30, help="max rows per run, per queue (0 = no limit); default 30")
    g.add_argument("--batch", type=int, default=30, help="rows per chunk: the DB and the statuses are written one chunk at a time")
    g.add_argument("--workers", type=int, default=6, help="parallel workers (lower it if you get 429)")
    g.add_argument("--only-rows", help="only these sheet rows, e.g. 83,102,127-128 (needs --queue, because row numbers belong to that queue sheet; works with `sync db` and `check access` only; "
                                       "with `sync db` only the AI part runs, for those rows)")
    g.add_argument("--no-retry-errors", action="store_true", help="do not retry Error rows (process only Waiting)")
    g.add_argument("--include-blank", action="store_true", help="also process rows whose column G is blank (but that have a URL)")
    g.add_argument("--no-reconcile", action="store_true", help="do not check whether the PDF is already in the DB")
    g.add_argument("--max-mb", type=float, default=LARGE_PDF_MB,
                   help="largest PDF sent whole (MB); default 15 = same as the Apps Script; a larger one is sent as its cover image (see --no-thumbnail)")
    g.add_argument("--parse-retries", type=int, default=1,
                   help="how many times to call Gemini again when its JSON is broken (Error: JSON Parse); default 1 (0 = no retry)")
    g.add_argument("--accept-first-report", action="store_true",
                   help="if the AI returns several reports for one PDF and none matches the queue's Doc No, use the first one (default: Error JSON Parse listing the numbers the AI found)")
    g.add_argument("--max-output-tokens", type=int, default=None,
                   help="Gemini maxOutputTokens (not set by default, same as the Apps Script); use it when JSON Parse fails because the answer was cut off (finishReason=MAX_TOKENS)")
    g.add_argument("--no-thumbnail", action="store_true",
                   help="for a PDF over --max-mb write Error: Too Large instead of sending the cover image (the Apps Script always sends the cover image)")


def add_queue_options(p: argparse.ArgumentParser) -> None:
    """queue 단계의 옵션 / [TH] ออปชันของขั้น queue / [EN] The options of the queue step"""
    g = p.add_argument_group("queue step")
    g.add_argument("--legacy-spreadsheet", default=None, metavar="ID_OR_URL",
                   help="the spreadsheet file (ID or URL) that holds the legacy tabs; the queues are still written to PMIS_SPREADSHEET_ID. "
                        "Default: PMIS_LEGACY_SPREADSHEET_ID, or the same file as PMIS_SPREADSHEET_ID. Share it with the service account")
    g.add_argument("--overwrite", action="store_true",
                   help="rebuild the whole queue from scratch instead of merging (clears it, every row becomes Waiting and every Done/Error status is wiped - what the Apps Script always did)")


def add_db_options(p: argparse.ArgumentParser) -> None:
    """db 단계의 옵션 / [TH] ออปชันของขั้น db / [EN] The options of the db step"""
    g = p.add_argument_group("db step")
    g.add_argument("--no-ai", action="store_true",
                   help="only part 1 (update the DB rows from the queue); the AI reads no PDF, so no Gemini key is needed and nothing is sent to Gemini")
    g.add_argument("--allow-large-removal", action="store_true",
                   help="also for the queue step: allow removing more rows than the safety limit (the larger of 10%% of the rows and 100); without it a run that "
                        "would remove more is refused, which protects the queue / DB when the legacy sheet is wrongly empty")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=PROG, formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EXAMPLES,
                                description="Keep PMIS in sync with the legacy sheets: legacy sheet -> queue (MIGRATION_QUEUE / MIGRATION_QUEUE_DRQ) -> DB (DB_DWR / DB_DRQ, read by the AI / Gemini)")
    sub = p.add_subparsers(dest="command", metavar="command", required=True)

    sync = sub.add_parser("sync", formatter_class=argparse.RawDescriptionHelpFormatter, description=SYNC_HELP, epilog=EXAMPLES,
                          help="keep PMIS in sync with the legacy sheets (steps: queue, db; none = both)")
    sync.add_argument("given_steps", nargs="*", type=step_name, metavar="step", help=f"{' | '.join(STEPS)} (none = both)")
    add_common_options(sync)
    add_queue_options(sync)
    add_ai_options(sync, "db step - the AI part")
    add_db_options(sync)

    check = sub.add_parser("check", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EXAMPLES,
                           description="Checks, without changing anything.\n\n  access   check that the service account can open the PDF files (no Gemini calls, no sheet writes)",
                           help="check something without changing anything (access)")
    check.add_argument("what", choices=["access"], help="access = can the service account open the PDF files?")
    add_common_options(check)
    add_ai_options(check, "which rows (same as the AI part of `sync db`)")
    return p


def make_clients(args: argparse.Namespace, need_gemini: bool) -> Clients:
    import requests
    from google.auth.transport.requests import AuthorizedSession, Request
    from google.oauth2 import service_account

    if not args.sa_key or not os.path.isfile(args.sa_key):
        sys.exit("Service account key not found: pass --sa-key <path> or set GOOGLE_APPLICATION_CREDENTIALS")
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if need_gemini and not api_key:
        sys.exit("GEMINI_API_KEY is not set (PowerShell:  $env:GEMINI_API_KEY = \"...\"). Without the AI: add --no-ai")
    creds = service_account.Credentials.from_service_account_file(args.sa_key, scopes=SCOPES)
    creds.refresh(Request())
    return Clients(
        sheets=AuthorizedSession(creds),
        drive_factory=lambda: AuthorizedSession(creds),
        gemini=requests.Session(),
        api_key=api_key,
        model=args.model,
        api_version=args.api_version,
        sa_email=creds.service_account_email,
        max_output_tokens=getattr(args, "max_output_tokens", None),
    )


def refuse_old_options(argv: list[str]) -> None:
    """
    옛 옵션(--extract 등), 없어진 명령(`fix cp`), 명령 없이 옵션부터 쓴 경우: 새 명령을 알려 주고 멈춤
    [TH] ถ้าใช้ออปชันเก่า (เช่น --extract) คำสั่งที่ถูกลบ (`fix cp`) หรือขึ้นต้นด้วยออปชันโดยไม่มีคำสั่ง จะบอกคำสั่งใหม่แล้วหยุด
    [EN] When an old option (--extract, ...), a command that is gone (`fix cp`), or arguments that start with an option instead of a command are used, says what to type now and stops
    """
    if "--backfill-cp" in argv or argv[:1] == ["fix"]:
        sys.exit(BACKFILL_REMOVED)
    for flag, command in OLD_OPTIONS.items():
        if flag in argv:
            sys.exit(f"{flag} was replaced by the command `{command}`: python {PROG} {command} ...  (see python {PROG} --help; "
                     f"migrate_queue.py still accepts the old options and translates them)")
    if argv and argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        sys.exit(f"Start with a command: python {PROG} sync [queue] [db] ... | check access  (see python {PROG} --help). "
                 f"The old form without a command is `sync db`")


def translate_old_options(argv: list[str]) -> list[str]:
    """
    migrate_queue.py 의 옛 인자를 새 명령으로 바꿈: --extract -> sync queue, --sync-db -> sync db --no-ai (옛 --sync-db 는 AI 를 쓰지 않았음), 둘 다 -> sync queue db --no-ai,
    --check-access -> check access, 아무것도 없으면(옛 기본 동작 = AI) sync db. 나머지 옵션은 그대로. 같이 쓸 수 없던 조합은 옛날처럼 오류. --backfill-cp 는 없어졌으므로 안내하고 멈춤
    [TH] แปลงอาร์กิวเมนต์เก่าของ migrate_queue.py เป็นคำสั่งใหม่: --extract -> sync queue, --sync-db -> sync db --no-ai (--sync-db เดิมไม่ใช้ AI), ทั้งคู่ -> sync queue db --no-ai,
         --check-access -> check access, ถ้าไม่มีอะไรเลย (พฤติกรรมเดิม = AI) -> sync db ออปชันที่เหลือคงเดิม ส่วนที่ใช้ร่วมกันไม่ได้ก็ error เหมือนเดิม --backfill-cp ถูกลบแล้ว จะบอกแล้วหยุด
    [EN] Translates the old arguments of migrate_queue.py into the new command: --extract -> sync queue, --sync-db -> sync db --no-ai (the old --sync-db never used the AI),
         both -> sync queue db --no-ai, --check-access -> check access, nothing (the old default = the AI) -> sync db. The other options stay; the combinations that could not be
         used together are still an error. --backfill-cp is gone, so it says so and stops
    """
    used = [flag for flag in OLD_OPTIONS if flag in argv]
    rest = [a for a in argv if a not in OLD_OPTIONS]
    if "--backfill-cp" in used:
        if len(used) > 1:
            sys.exit(f"{[f for f in used if f != '--backfill-cp'][0]} cannot be combined with --backfill-cp")
        sys.exit(BACKFILL_REMOVED)
    if "--check-access" in used:
        if len(used) > 1:
            sys.exit(f"{[f for f in used if f != '--check-access'][0]} cannot be combined with --check-access")
        return ["check", "access"] + rest
    steps = [name for flag, name in (("--extract", "queue"), ("--sync-db", "db")) if flag in used]
    if not steps:
        return ["sync", "db"] + rest
    return ["sync"] + steps + (["--no-ai"] if "--sync-db" in used else []) + rest


def prepare(args: argparse.Namespace) -> list[str]:
    """
    파싱한 인자에서 실행할 단계를 정함 (sync 는 고정된 순서, check 는 단계 없음). args.steps / args.check_access / args.no_ai 를 채움
    [TH] กำหนดขั้นตอนที่จะรันจากอาร์กิวเมนต์ที่แยกแล้ว (sync ตามลำดับคงที่, check ไม่มีขั้น) และเติม args.steps / args.check_access / args.no_ai
    [EN] Decides the steps to run from the parsed arguments (sync: the fixed order; check: no steps) and fills in args.steps / args.check_access / args.no_ai
    """
    steps = ordered_steps(args.given_steps) if args.command == "sync" else []
    args.steps = steps
    args.check_access = args.command == "check"
    if not hasattr(args, "no_ai"):
        args.no_ai = False
    return steps


def validate(args: argparse.Namespace, steps: list[str], keys: list[str]) -> None:
    """
    단계에 맞지 않는 옵션 조합을 시작 전에 거부 (키/네트워크 작업 전에)
    [TH] ปฏิเสธชุดออปชันที่ไม่เข้ากับขั้นตอนตั้งแต่ก่อนเริ่ม (ก่อนแตะคีย์/เครือข่าย)
    [EN] Rejects the option combinations that do not fit the steps before anything starts (before any key or network work)
    """
    if args.command == "sync":
        if args.only_rows and steps != ["db"]:
            sys.exit("--only-rows only works with `sync db` (and `check access`): row numbers belong to the queue sheet, and only the AI part retries rows")
        if args.only_rows and args.no_ai:
            sys.exit("--only-rows retries those rows with the AI, so it cannot be combined with --no-ai")
        if (args.overwrite or args.legacy_spreadsheet) and "queue" not in steps:
            option = "--overwrite" if args.overwrite else "--legacy-spreadsheet"
            sys.exit(f"{option} only works when the queue step runs (`sync queue`, or `sync` with no step)")
    if getattr(args, "only_rows", None) and len(keys) > 1:
        sys.exit("--only-rows works with one queue: add --queue dwr or --queue drq (row numbers belong to that queue sheet)")


def uses_ai(args: argparse.Namespace, steps: list[str]) -> bool:
    """이번 실행이 AI 를 쓰는지 (db 단계이고 --no-ai 가 아님) / [TH] การรันนี้ใช้ AI หรือไม่ (มีขั้น db และไม่ใช่ --no-ai) / [EN] Whether this run uses the AI (the db step, without --no-ai)"""
    return "db" in steps and not args.no_ai


def mode_text(args: argparse.Namespace, steps: list[str]) -> str:
    """시작 줄에 적는 실행 모드 / [TH] โหมดที่เขียนในบรรทัดแรก / [EN] The mode written on the first line"""
    if args.command == "check":
        return "check access (can the service account open the PDF files?)"
    if len(steps) > 1:
        return f"sync {' '.join(steps)}" + (" --no-ai" if args.no_ai else "")
    return f"sync {steps[0]}" + (" --no-ai" if steps[0] == "db" and args.no_ai else "") + f" ({step_text(steps[0], args.no_ai)})"


def main(argv: Optional[list[str]] = None) -> int:
    # Windows 에서 파일로 redirect 해도 태국어/기호가 깨지지 않도록 UTF-8 로 설정
    # [TH] ภาษาไทย/สัญลักษณ์ไม่พังเมื่อ redirect ไปไฟล์บน Windows
    # [EN] Force UTF-8 so Thai text and symbols do not break when output is redirected to a file on Windows
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not argv:
        parser.print_help(sys.stderr)
        return 2
    refuse_old_options(argv)
    args = parser.parse_args(argv)
    steps = prepare(args)
    # 가장 먼저 확인 (키/네트워크 작업 전에 설정 누락을 알려 줌)
    # [TH] เช็กเป็นอย่างแรก (แจ้งว่าตั้งค่าขาดก่อนจะแตะคีย์/เครือข่าย)
    # [EN] Checked first (a missing setting is reported before any key or network work)
    args.spreadsheet_id = require_spreadsheet_id()
    keys = queues_to_run(args.queue)
    validate(args, steps, keys)
    if "queue" in steps:
        args.legacy_tabs = require_legacy_sheet_names(keys)
        args.legacy_spreadsheet_id = resolve_legacy_spreadsheet_id(args.legacy_spreadsheet, args.spreadsheet_id)
    ai = uses_ai(args, steps)
    clients = make_clients(args, need_gemini=ai and not args.dry_run)
    title, title_error = spreadsheet_title(clients.sheets, args.spreadsheet_id)
    if title:
        sheet_label = f"{title} ({args.spreadsheet_id})"
    else:
        sheet_label = f"{args.spreadsheet_id} (could not read the sheet name - check the ID and that the sheet is shared with the service account: {title_error})"
    log = make_log(args.public_log)
    log(f"service account: {clients.sa_email} | sheet: {sheet_label} | mode: {mode_text(args, steps)}" + (f" | model: {clients.model}" if ai else ""))
    if args.command == "check":
        return run_queues(args, clients, keys, log=log, sheet_title=title)
    return run_sync(args, clients, keys, steps, log=log, sheet_title=title)


def entry(argv: Optional[list[str]] = None) -> None:
    """
    `python pmis.py` 의 진입점: .env 를 읽고 main() 을 실행 (Ctrl+C 는 종료 코드 130)
    [TH] จุดเริ่มต้นของ `python pmis.py`: โหลด .env แล้วรัน main() (Ctrl+C จบด้วยรหัส 130)
    [EN] The entry point of `python pmis.py`: loads .env, then runs main() (Ctrl+C exits with code 130)
    """
    # main() 이 아니라 여기서 읽음: 테스트가 main() 을 부를 때 개발자의 .env 가 섞이지 않도록
    # [TH] โหลดที่นี่ ไม่ใช่ใน main(): เวลา test เรียก main() จะได้ไม่ปนกับ .env ของนักพัฒนา
    # [EN] Loaded here, not in main(): so a developer's .env never leaks into tests that call main()
    load_env_file()
    try:
        sys.exit(main(argv))
    except KeyboardInterrupt:
        sys.exit(130)


def old_entry() -> None:
    """
    `python migrate_queue.py` (옛 진입점)의 진입점: 옛 인자를 새 명령으로 바꿔 실행하고, 새 명령을 stderr 로 알려 줌
    [TH] จุดเริ่มต้นของ `python migrate_queue.py` (จุดเริ่มต้นเก่า): แปลงอาร์กิวเมนต์เก่าเป็นคำสั่งใหม่แล้วรัน พร้อมบอกคำสั่งใหม่ทาง stderr
    [EN] The entry point of `python migrate_queue.py` (the old entry point): translates the old arguments into the new command, runs it, and says the new command on stderr
    """
    argv = sys.argv[1:]
    if argv and argv[0] in ("-h", "--help"):
        entry(argv)
        return
    new = translate_old_options(argv)
    words = new[:next((i for i, a in enumerate(new) if a.startswith("-")), len(new))]
    command = " ".join(words + (["--no-ai"] if "--no-ai" in new else []))
    print(f"note: migrate_queue.py is the old entry point; the same thing is now: python {PROG} {command} ...  (see README)", file=sys.stderr)
    entry(new)
