"""
명령줄 인터페이스: 옵션, 클라이언트 만들기, main
[TH] อินเทอร์เฟซบรรทัดคำสั่ง: ออปชัน, สร้าง client, main
[EN] Command-line interface: options, building the clients, main
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from .config import DEFAULT_API_VERSION, DEFAULT_MODEL, LARGE_PDF_MB, SCOPES
from .cp_column import run_backfill
from .db_sync import run_db_sync
from .extract import run_extract
from .models import Clients
from .public_log import make_log
from .runner import queues_to_run, run_queues
from .settings import load_env_file, require_legacy_sheet_names, require_spreadsheet_id, resolve_legacy_spreadsheet_id
from .sheets import spreadsheet_title
from .specs import SPECS


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Process the MIGRATION_QUEUE / MIGRATION_QUEUE_DRQ queues with Gemini (replaces the Apps Script)")
    p.add_argument("--queue", choices=sorted(SPECS), default=None,
                   help="dwr = MIGRATION_QUEUE->DB_DWR, drq = MIGRATION_QUEUE_DRQ->DB_DRQ; omitted = both queues (dwr first, then drq)")
    p.add_argument("--sa-key", default=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"), help="path to the service account key (.json)")
    p.add_argument("--model", default=os.environ.get("GEMINI_MODEL", DEFAULT_MODEL))
    p.add_argument("--api-version", default=DEFAULT_API_VERSION)
    p.add_argument("--limit", type=int, default=30, help="max rows per run, per queue (0 = no limit); default 30")
    p.add_argument("--batch", type=int, default=30, help="rows per chunk: the DB and the statuses are written one chunk at a time")
    p.add_argument("--workers", type=int, default=6, help="parallel workers (lower it if you get 429)")
    p.add_argument("--only-rows", help="only these sheet rows, e.g. 83,102,127-128 (needs --queue, because row numbers belong to that queue sheet)")
    p.add_argument("--no-retry-errors", action="store_true", help="do not retry Error rows (process only Waiting)")
    p.add_argument("--include-blank", action="store_true", help="also process rows whose column G is blank (but that have a URL)")
    p.add_argument("--no-reconcile", action="store_true", help="do not check whether the PDF is already in the DB")
    p.add_argument("--max-mb", type=float, default=LARGE_PDF_MB,
                   help="largest PDF sent whole (MB); default 15 = same as the Apps Script; a larger one is sent as its cover image (see --no-thumbnail)")
    p.add_argument("--parse-retries", type=int, default=1,
                   help="how many times to call Gemini again when its JSON is broken (Error: JSON Parse); default 1 (0 = no retry)")
    p.add_argument("--accept-first-report", action="store_true",
                   help="if the AI returns several reports for one PDF and none matches the queue's Doc No, use the first one (default: Error JSON Parse listing the numbers the AI found)")
    p.add_argument("--max-output-tokens", type=int, default=None,
                   help="Gemini maxOutputTokens (not set by default, same as the Apps Script); use it when JSON Parse fails because the answer was cut off (finishReason=MAX_TOKENS)")
    p.add_argument("--no-thumbnail", action="store_true",
                   help="for a PDF over --max-mb write Error: Too Large instead of sending the cover image (the Apps Script always sends the cover image)")
    p.add_argument("--dry-run", action="store_true", help="only list the rows that would be processed (no Drive/Gemini calls, no sheet writes)")
    p.add_argument("--check-access", action="store_true", help="check that the service account can open the PDF files (no Gemini calls, no sheet writes)")
    p.add_argument("--extract", action="store_true",
                   help="build the queues from the legacy sheets (replaces extractLegacyDwrLinks / extractLegacyDrqLinks); no Drive/Gemini calls; needs PMIS_LEGACY_DWR_SHEET / PMIS_LEGACY_DRQ_SHEET; works with --dry-run and --queue. "
                        "A queue that already has rows is SYNCED: every row is recomputed from the legacy sheet and the status of the existing row is kept (use --overwrite to rebuild with every row Waiting instead)")
    p.add_argument("--legacy-spreadsheet", default=None, metavar="ID_OR_URL",
                   help="with --extract: the spreadsheet file (ID or URL) that holds the legacy tabs; the queues are still written to PMIS_SPREADSHEET_ID. "
                        "Default: PMIS_LEGACY_SPREADSHEET_ID, or the same file as PMIS_SPREADSHEET_ID. Share it with the service account")
    p.add_argument("--backfill-cp", action="store_true",
                   help="fill the C/P column (T in DB_DWR, S in DB_DRQ) for the rows that are already there, matching each DB row with the queue row of the same PDF (Drive file ID); "
                        "rows that already have a C/P are never changed and conflicting values are skipped; no Drive/Gemini calls; works with --dry-run and --queue")
    p.add_argument("--sync-db", action="store_true",
                   help="make DB_DWR / DB_DRQ match the queue (= the legacy sheet): remove the DB rows whose PDF is not in the queue any more (only rows with Status 'AI Migrated' that were tagged 'Synced' before, so rows that never came from the legacy sheet stay; "
                        "they are copied to a _REMOVED_<tab> tab first), update the C/P column to the queue's value, tag the rows whose PDF is in the queue with 'Synced' in the 'Legacy sync' column (an orphan row gets no tag), keep the rows that wait to be replaced by a changed PDF. "
                        "Alone it reads the queue sheets; with --extract it runs right after each queue is synced (with --dry-run: on the planned rows). No Drive/Gemini calls. "
                        "Works with --dry-run and --queue")
    p.add_argument("--allow-large-removal", action="store_true",
                   help="with --extract or --sync-db: allow removing more rows than the safety limit (the larger of 10%% of the rows and 100); without it a run that "
                        "would remove more is refused, which protects the queue / DB when the legacy sheet is wrongly empty")
    p.add_argument("--public-log", action="store_true",
                   help="print only counts and statuses (no tab names, sheet names / IDs, Doc Nos or error details), so the output is safe in the Actions log of a public repository; run without it locally for the details")
    p.add_argument("--overwrite", action="store_true",
                   help="with --extract: rebuild the whole queue from scratch instead of merging (clears it, every row becomes Waiting and every Done/Error status is wiped - what the Apps Script always did)")
    return p


def make_clients(args: argparse.Namespace, need_gemini: bool) -> Clients:
    import requests
    from google.auth.transport.requests import AuthorizedSession, Request
    from google.oauth2 import service_account

    if not args.sa_key or not os.path.isfile(args.sa_key):
        sys.exit("Service account key not found: pass --sa-key <path> or set GOOGLE_APPLICATION_CREDENTIALS")
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if need_gemini and not api_key:
        sys.exit("GEMINI_API_KEY is not set (PowerShell:  $env:GEMINI_API_KEY = \"...\")")
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


def main(argv: Optional[list[str]] = None) -> int:
    # Windows 에서 파일로 redirect 해도 태국어/기호가 깨지지 않도록 UTF-8 로 설정
    # [TH] ภาษาไทย/สัญลักษณ์ไม่พังเมื่อ redirect ไปไฟล์บน Windows
    # [EN] Force UTF-8 so Thai text and symbols do not break when output is redirected to a file on Windows
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    # 가장 먼저 확인 (키/네트워크 작업 전에 설정 누락을 알려 줌)
    # [TH] เช็กเป็นอย่างแรก (แจ้งว่าตั้งค่าขาดก่อนจะแตะคีย์/เครือข่าย)
    # [EN] Checked first (a missing setting is reported before any key or network work)
    args.spreadsheet_id = require_spreadsheet_id()
    keys = queues_to_run(args.queue)
    if args.sync_db and args.backfill_cp:
        sys.exit("--sync-db already updates the C/P column: use either --sync-db or --backfill-cp, not both")
    if args.extract:
        if args.only_rows or args.check_access:
            sys.exit("--extract cannot be combined with --only-rows or --check-access")
        if args.backfill_cp:
            sys.exit("--backfill-cp cannot be combined with --extract (use --extract --sync-db)")
        args.legacy_tabs = require_legacy_sheet_names(keys)
        args.legacy_spreadsheet_id = resolve_legacy_spreadsheet_id(args.legacy_spreadsheet, args.spreadsheet_id)
    elif (args.backfill_cp or args.sync_db) and (args.only_rows or args.check_access):
        sys.exit("--backfill-cp / --sync-db cannot be combined with --only-rows or --check-access")
    elif args.overwrite:
        sys.exit("--overwrite only works together with --extract")
    elif args.legacy_spreadsheet:
        sys.exit("--legacy-spreadsheet only works together with --extract")
    if args.allow_large_removal and not (args.extract or args.sync_db):
        sys.exit("--allow-large-removal only works together with --extract or --sync-db")
    if args.only_rows and len(keys) > 1:
        sys.exit("--only-rows works with one queue: add --queue dwr or --queue drq (row numbers belong to that queue sheet)")
    clients = make_clients(args, need_gemini=not (args.dry_run or args.check_access or args.extract or args.backfill_cp or args.sync_db))
    title, title_error = spreadsheet_title(clients.sheets, args.spreadsheet_id)
    if title:
        sheet_label = f"{title} ({args.spreadsheet_id})"
    else:
        sheet_label = f"{args.spreadsheet_id} (could not read the sheet name - check the ID and that the sheet is shared with the service account: {title_error})"
    log = make_log(args.public_log)
    if args.extract:
        log(f"service account: {clients.sa_email} | sheet: {sheet_label} | mode: build the queues from the legacy sheets (--extract)"
            + (" and make the DB tabs match them (--sync-db)" if args.sync_db else ""))
        return run_extract(args, clients, keys, log=log, sheet_title=title)
    if args.sync_db:
        log(f"service account: {clients.sa_email} | sheet: {sheet_label} | mode: make the DB tabs match the queues (--sync-db)")
        return run_db_sync(args, clients, keys, log=log, sheet_title=title)
    if args.backfill_cp:
        log(f"service account: {clients.sa_email} | sheet: {sheet_label} | mode: fill the C/P column of the DB tabs (--backfill-cp)")
        return run_backfill(args, clients, keys, log=log, sheet_title=title)
    log(f"service account: {clients.sa_email} | sheet: {sheet_label} | model: {clients.model}")
    return run_queues(args, clients, keys, log=log, sheet_title=title)


def entry() -> None:
    """
    `python migrate_queue.py` 의 진입점: .env 를 읽고 main() 을 실행 (Ctrl+C 는 종료 코드 130)
    [TH] จุดเริ่มต้นของ `python migrate_queue.py`: โหลด .env แล้วรัน main() (Ctrl+C จบด้วยรหัส 130)
    [EN] The entry point of `python migrate_queue.py`: loads .env, then runs main() (Ctrl+C exits with code 130)
    """
    # main() 이 아니라 여기서 읽음: 테스트가 main() 을 부를 때 개발자의 .env 가 섞이지 않도록
    # [TH] โหลดที่นี่ ไม่ใช่ใน main(): เวลา test เรียก main() จะได้ไม่ปนกับ .env ของนักพัฒนา
    # [EN] Loaded here, not in main(): so a developer's .env never leaks into tests that call main()
    load_env_file()
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

