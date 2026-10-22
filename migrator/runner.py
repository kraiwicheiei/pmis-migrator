"""
큐에서 처리할 행을 고르고 메인 루프를 돌림 (--dry-run / --check-access / 실제 실행)
[TH] เลือกแถวที่ต้องทำจากคิวและรันวงรอบหลัก (--dry-run / --check-access / รันจริง)
[EN] Selects the rows to process from a queue and runs the main loop (--dry-run / --check-access / a real run)
"""
from __future__ import annotations

import argparse
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

from .config import PRICES_USD_PER_M
from .models import Clients, Job, RowResult, RunControl, Usage
from .processing import process_job
from .reports import build_db_row
from .sheets import SheetsError, append_rows, read_values, write_statuses
from .specs import QueueSpec, SPECS
from .utils import chunked, col_letter, error_text, extract_file_id, parse_row_spec


def select_jobs(
    values: list[list[str]], *, retry_errors: bool, include_blank: bool, only_rows: Optional[set[int]]
) -> list[Job]:
    jobs: list[Job] = []
    # 1행 = 헤더이므로 2행부터
    # [TH] แถว 1 = หัวตาราง จึงเริ่มที่แถว 2
    # [EN] Row 1 is the header, so start at row 2
    for idx, row in enumerate(values[1:], start=2):
        cells = list(row) + [""] * (7 - len(row))
        status = str(cells[6]).strip()
        pdf_url, folder_url = str(cells[3]).strip(), str(cells[4]).strip()
        if only_rows is not None:
            if idx not in only_rows or status == "Done":
                continue
        else:
            pending = status == "Waiting" or (retry_errors and status.startswith("Error"))
            if include_blank and status == "" and (pdf_url or folder_url):
                pending = True
            if not pending:
                continue
        jobs.append(Job(row=idx, pdf_url=pdf_url, folder_url=folder_url, prior_status=status, doc_no=str(cells[0]).strip()))
    return jobs


@dataclass
class Summary:
    done: int = 0
    reconciled: int = 0
    # 큰 PDF 를 표지 이미지로 대체해 처리한 행 수
    # [TH] จำนวนแถวที่ PDF ใหญ่ถูกแทนด้วยภาพหน้าปก
    # [EN] Number of rows where a large PDF was replaced by its cover image
    cover_only: int = 0
    # AI 가 보고서를 여러 개 돌려줘서 하나를 골라 처리한 행 수
    # [TH] จำนวนแถวที่ AI ตอบกลับมาหลายรายงานแล้วเลือกมาหนึ่งรายการ
    # [EN] Number of rows where the AI returned several reports and one was picked
    multi_report: int = 0
    errors: dict = field(default_factory=dict)
    skipped: int = 0
    stopped_reason: str = ""


def explain_no_jobs(values: list[list[str]], sid: str, spec: QueueSpec, *, retry_errors: bool, include_blank: bool,
                    only_rows: Optional[set[int]]) -> list[str]:
    """
    처리할 행이 0 일 때, 왜 그런지(큐가 비었는지 / 상태값이 맞지 않는지 / 엉뚱한 시트인지) 알려주는 안내문
    [TH] ข้อความอธิบายเมื่อไม่มีแถวให้ทำ ว่าเพราะอะไร (คิวว่าง / ค่าสถานะไม่ตรง / ชี้ผิดชีต)
    [EN] Explains why there are 0 rows to process (empty queue / status values that do not match / wrong sheet)
    """
    lines: list[str] = []
    data = values[1:] if values else []
    if not data:
        lines.append(f"Tab {spec.queue_tab} has no data rows - if the sheet you have open does, the script is reading a different spreadsheet")
        lines.append(f"Check the ID: {sid} (see the 'sheet:' line above; the ID comes from the PMIS_SPREADSHEET_ID variable)")
        return lines
    if only_rows is not None:
        lines.append("--only-rows is set: only those rows are processed and Done rows are skipped - check the row numbers")
        return lines
    header_g = (values[0][6].strip() if len(values[0]) > 6 else "")
    if header_g != "AI Processed":
        lines.append(f"The column G header is {header_g!r} (normally 'AI Processed') - the script always reads the status from column G; check that the status column is G")
    counts: dict[str, int] = {}
    blank_with_url = 0
    for row in data:
        cells = list(row) + [""] * (7 - len(row))
        status = str(cells[6]).strip()
        counts[status] = counts.get(status, 0) + 1
        if status == "" and (str(cells[3]).strip() or str(cells[4]).strip()):
            blank_with_url += 1
    shown = ", ".join(f"{k!r}×{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:8])
    if len(counts) > 8:
        shown += ", …"
    wanted = "'Waiting' or starting with 'Error'" if retry_errors else "'Waiting'"
    lines.append(f"The script processes only rows whose column G is {wanted} (case-sensitive), but this queue has: {shown}")
    if blank_with_url and not include_blank:
        lines.append(f"{blank_with_url} rows have a blank column G but a URL - add --include-blank to process them too")
    if set(counts) <= {"Done"}:
        lines.append("Every row is already Done - if work is still pending, rebuild the queue (--extract --overwrite) or set the status back to Waiting")
    return lines


def run(args: argparse.Namespace, clients: Clients, log: Callable[[str], None] = print) -> Summary:
    spec = SPECS[args.queue]
    sid = args.spreadsheet_id
    allow_thumbnail = not getattr(args, "no_thumbnail", False)
    parse_retries = max(0, int(getattr(args, "parse_retries", 0) or 0))
    accept_first = bool(getattr(args, "accept_first_report", False))
    summary = Summary()
    ctl = RunControl()
    usage = Usage()

    values = read_values(clients.sheets, sid, f"'{spec.queue_tab}'!A1:G")
    only_rows = parse_row_spec(args.only_rows) if args.only_rows else None
    jobs = select_jobs(values, retry_errors=not args.no_retry_errors, include_blank=args.include_blank, only_rows=only_rows)

    by_status: dict[str, int] = {}
    for row in values[1:]:
        s = (row[6].strip() if len(row) > 6 else "") or "(blank)"
        key = "Error" if s.startswith("Error") else s
        by_status[key] = by_status.get(key, 0) + 1
    if by_status:
        log(f"Queue {spec.queue_tab}: " + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())))
    else:
        log(f"Queue {spec.queue_tab}: empty (header only, no data rows)")
    log(f"Rows to process: {len(jobs)}" + (f" (this run is limited to {args.limit})" if args.limit and len(jobs) > args.limit else ""))
    if not jobs:
        for line in explain_no_jobs(values, sid, spec, retry_errors=not args.no_retry_errors, include_blank=args.include_blank,
                                    only_rows=only_rows):
            log("  " + line)
    if args.limit:
        jobs = jobs[: args.limit]

    # --dry-run: 대상 행만 출력하고 종료 (Drive/Gemini 호출 없음, 시트 쓰기 없음)
    # [TH] --dry-run: แสดงแถวที่จะทำแล้วจบ (ไม่เรียก Drive/Gemini ไม่เขียนชีต)
    # [EN] --dry-run: only list the rows that would be processed (no Drive/Gemini calls, no sheet writes)
    if args.dry_run:
        for j in jobs[:50]:
            log(f"  row {j.row}: fileId={extract_file_id(j.pdf_url)} | previous status={j.prior_status[:60]!r}")
        if len(jobs) > 50:
            log(f"  ... and {len(jobs) - 50} more rows")
        log("(dry-run: no Drive/Gemini calls, no sheet writes)")
        return summary

    tls = threading.local()

    # --check-access: Drive 접근 가능 여부만 확인 (Gemini 호출 없음, 시트 쓰기 없음)
    # [TH] --check-access: เช็กแค่ว่าเข้าถึงไฟล์ใน Drive ได้ไหม (ไม่เรียก Gemini ไม่เขียนชีต)
    # [EN] --check-access: only check whether the Drive files are reachable (no Gemini call, no sheet writes)
    if args.check_access:
        # 모든 출력 줄에 어느 큐 시트(MIGRATION_QUEUE / MIGRATION_QUEUE_DRQ)의 결과인지 표시
        # [TH] ทุกบรรทัดของผลลัพธ์ระบุชื่อชีตคิว (MIGRATION_QUEUE / MIGRATION_QUEUE_DRQ) ว่าผลมาจากชีตไหน
        # [EN] Every output line names the queue sheet (MIGRATION_QUEUE / MIGRATION_QUEUE_DRQ) the result belongs to
        tag = f"[{spec.queue_tab}]"
        log(f"{tag} Checking Drive access to the PDF files of the {spec.name} queue (--queue {args.queue}; no Gemini calls, no sheet writes) ...")
        bad = 0
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for res in pool.map(lambda j: process_job(j, spec, clients, tls, usage, ctl, args.max_mb * 1_000_000, True, allow_thumbnail), jobs):
                if res.kind == "error":
                    bad += 1
                    log(f"  ✗ {tag} row {res.job.row}: {res.label} | {res.detail[:300]}")
                elif (res.data or {}).get("_cover"):
                    log(f"  ℹ {tag} row {res.job.row}: large PDF {(res.data or {}).get('_size', 0) / 1e6:.1f} MB (> {args.max_mb:g} MB) -> only the cover image will be sent")
        log(f"{tag} Reachable: {len(jobs) - bad}/{len(jobs)} files" + (f" - share the files/folder with {clients.sa_email}" if bad and clients.sa_email else ""))
        # 다른 큐는 이번 실행에서 확인하지 않았으므로, 해당 큐를 확인하는 명령어를 안내
        # [TH] รอบนี้ไม่ได้ตรวจคิวอีกอัน จึงบอกคำสั่งสำหรับตรวจคิวนั้น
        # [EN] The other queue was not checked in this run, so print the command that checks it
        other_key = next((k for k in sorted(SPECS) if k != args.queue), None)
        if other_key and getattr(args, "hint_other_queue", True):
            log(f"(this run checked only {spec.queue_tab}; for {SPECS[other_key].queue_tab}: python migrate_queue.py --check-access --queue {other_key} --limit 0)")
        return summary

    # 중복 방지: 이미 DB 에 있는 PDF -> AI 호출 없이 Done 처리
    # [TH] กันซ้ำ: PDF ที่อยู่ใน DB แล้ว -> ตั้ง Done โดยไม่เรียก AI
    # [EN] Duplicate guard: a PDF already in the DB -> mark Done without calling the AI
    existing_ids: set[str] = set()
    if not args.no_reconcile:
        col = col_letter(spec.db_pdf_col + 1)
        for r in read_values(clients.sheets, sid, f"'{spec.db_tab}'!{col}2:{col}"):
            fid = extract_file_id(r[0]) if r else None
            if fid:
                existing_ids.add(fid)
    to_run: list[Job] = []
    reconciled_updates: dict[int, str] = {}
    for j in jobs:
        fid = extract_file_id(j.pdf_url)
        if fid and fid in existing_ids:
            reconciled_updates[j.row] = "Done"
        else:
            to_run.append(j)
    if reconciled_updates:
        write_statuses(clients.sheets, sid, spec.queue_tab, reconciled_updates)
        summary.reconciled = len(reconciled_updates)
        log(f"{summary.reconciled} rows are already in {spec.db_tab} -> marked Done without calling the AI (rows: {sorted(reconciled_updates)[:20]})")

    max_bytes = args.max_mb * 1_000_000
    interrupted = False
    for chunk in chunked(to_run, args.batch):
        if ctl.stop.is_set():
            break
        results: list[RowResult] = []
        futures: list = []
        pool = ThreadPoolExecutor(max_workers=args.workers)
        try:
            futures = [pool.submit(process_job, j, spec, clients, tls, usage, ctl, max_bytes, False, allow_thumbnail, parse_retries, accept_first) for j in chunk]
            for fut in futures:
                results.append(fut.result())
        except KeyboardInterrupt:
            interrupted = True
            ctl.request_stop("stopped by the user (Ctrl+C)")
            # 진행 중인 작업은 끝까지 기다림 (아직 시작 안 한 작업은 건너뜀)
            # [TH] ให้งานที่ค้างอยู่ทำเสร็จ (ตัวที่ยังไม่เริ่มจะถูกข้าม)
            # [EN] Let in-flight jobs finish (jobs that have not started are skipped)
            for fut in futures:
                try:
                    results.append(fut.result())
                except BaseException:  # noqa: BLE001
                    pass
        finally:
            pool.shutdown(wait=True)

        ok = [r for r in results if r.kind == "ok"]
        updates: dict[int, str] = {}
        for r in results:
            if r.kind == "error":
                updates[r.job.row] = error_text(r.label, r.detail)
                summary.errors[r.label] = summary.errors.get(r.label, 0) + 1
                log(f"  ✗ row {r.job.row}: {updates[r.job.row][:200]}")
            elif r.kind == "skipped":
                summary.skipped += 1

        # 순서: 먼저 DB 에 쓰고, 성공한 뒤에만 큐에 Done 표시
        # (현재 Apps Script 는 G열 상태를 먼저 쓰고 DB 를 나중에 쓰므로, DB 쓰기가 실패하면 Done 만 남을 수 있음)
        # [TH] ลำดับ: เขียน DB ก่อน แล้วค่อยตั้ง Done ในคิวเมื่อสำเร็จ
        #      (Apps Script ปัจจุบันเขียนสถานะคอลัมน์ G ก่อนแล้วค่อยเขียน DB ถ้าเขียน DB พลาดอาจเหลือ Done โดยไม่มีข้อมูล)
        # [EN] Order: write the DB first, mark Done in the queue only after it succeeds
        #      (the current Apps Script writes the column-G statuses first and the DB afterwards, so a failed DB write
        #      can leave rows marked Done with no data)
        if ok:
            db_rows = [build_db_row(spec, r.data or {}, r.job.pdf_url, r.job.folder_url) for r in ok]
            try:
                append_rows(clients.sheets, sid, spec.db_tab, db_rows)
            except SheetsError as e:
                for r in ok:
                    updates[r.job.row] = error_text("DB Write", f"{e} | rows={len(db_rows)}, cols={len(db_rows[0])}")
                summary.errors["DB Write"] = summary.errors.get("DB Write", 0) + len(ok)
                log(f"  ✗ writing {spec.db_tab} failed: {e}")
                ctl.request_stop("DB write failed (safe to re-run - duplicates are checked)")
            else:
                for r in ok:
                    updates[r.job.row] = "Done"
                    if r.note:
                        summary.cover_only += 1
                        log(f"  ℹ row {r.job.row}: large PDF ({r.note}) - the data comes from the first page only, please review")
                    if r.retried:
                        log(f"  ℹ row {r.job.row}: the AI's JSON was broken at first; it parsed after retrying {r.retried}x")
                    if r.multi:
                        summary.multi_report += 1
                        log(f"  ⚠ row {r.job.row}: {r.multi} - please review")
                summary.done += len(ok)
                log(f"  ✓ {len(ok)} rows -> {spec.db_tab} (queue rows {[r.job.row for r in ok][:10]}{'…' if len(ok) > 10 else ''})")
        try:
            write_statuses(clients.sheets, sid, spec.queue_tab, updates)
        except SheetsError as e:
            log(f"  !! writing the queue statuses failed: {e}\n     the data is already in {spec.db_tab} - re-run and those rows are marked Done without calling the AI")
            ctl.request_stop("writing the queue statuses failed")
        if interrupted:
            break

    summary.stopped_reason = ctl.stop_reason
    log("-" * 60)
    log(f"Finished: Done={summary.done} (+reconciled {summary.reconciled}), Error={sum(summary.errors.values())} {summary.errors or ''}, Skipped={summary.skipped}")
    if summary.cover_only:
        log(f"Of the Done rows, {summary.cover_only} used the cover image instead of a large PDF (like the Apps Script) - data from the first page only")
    if summary.multi_report:
        log(f"Of the Done rows, {summary.multi_report} had several reports in one PDF and one was picked - review the rows marked ⚠ above")
    if ctl.stop_reason:
        log(f"Stopped early: {ctl.stop_reason}")
    if usage.calls:
        pin, pout = PRICES_USD_PER_M.get(clients.model, (None, None))
        cost = f" ≈ ${usage.prompt / 1e6 * pin + usage.output / 1e6 * pout:.3f} (list price)" if pin else ""
        log(f"Gemini calls: {usage.calls}, input {usage.prompt:,} tok, output(+thinking) {usage.output:,} tok{cost}")
    return summary


def queues_to_run(queue_arg: Optional[str]) -> list[str]:
    """
    --queue 를 지정하지 않으면 두 큐 모두 (dwr -> drq 순서), 지정하면 그 큐만
    [TH] ถ้าไม่ระบุ --queue จะทำทั้งสองคิว (dwr แล้วตามด้วย drq) ถ้าระบุจะทำเฉพาะคิวนั้น
    [EN] Without --queue both queues are processed (dwr first, then drq); with --queue only that queue
    """
    return [queue_arg] if queue_arg else list(SPECS)


def run_queues(args: argparse.Namespace, clients: Clients, keys: list[str], log: Callable[[str], None] = print,
               sheet_title: str = "") -> int:
    """
    큐를 차례로 실행하고 종료 코드를 반환 (0 = 정상, 2 = 중단/실패가 있음)
    [TH] รันทีละคิวตามลำดับ แล้วคืนรหัสออก (0 = ปกติ, 2 = มีการหยุดกลางคัน/ล้มเหลว)
    [EN] Runs the queues one after another and returns the exit code (0 = fine, 2 = something stopped or failed)
    """
    multi = len(keys) > 1
    exit_code = 0
    total_done = total_err = 0
    for i, key in enumerate(keys):
        spec = SPECS[key]
        sub = copy.copy(args)
        sub.queue = key
        # 두 큐를 모두 확인할 때는 '다른 큐 확인 명령어' 안내가 필요 없음
        # [TH] ถ้าตรวจทั้งสองคิวอยู่แล้ว ไม่ต้องบอกคำสั่งสำหรับตรวจอีกคิว
        # [EN] When both queues are being checked anyway, the "command for the other queue" hint is not needed
        sub.hint_other_queue = not multi
        # 큐마다 제목 줄을 출력 (스프레드시트 이름 포함)
        # [TH] พิมพ์หัวข้อของแต่ละคิว (พร้อมชื่อสเปรดชีต)
        # [EN] Print a heading for each queue (including the spreadsheet name)
        log(("\n" if multi else "") + f"===== Queue {spec.name}: {spec.queue_tab} -> {spec.db_tab}"
            + (f" | sheet: {sheet_title}" if sheet_title else "") + " =====")
        try:
            summary = run(sub, clients, log=log)
        except SheetsError as e:
            # 한 큐를 읽지 못해도(예: 시트 탭이 없음) 다음 큐는 계속 진행
            # [TH] อ่านคิวหนึ่งไม่ได้ (เช่นไม่มีแท็บชีต) ก็ยังทำคิวถัดไปต่อ
            # [EN] If one queue cannot be read (e.g. the sheet tab is missing), the next queue is still processed
            log(f"✗ Could not process queue {spec.queue_tab}: {e}")
            exit_code = 2
            continue
        total_done += summary.done + summary.reconciled
        total_err += sum(summary.errors.values())
        if summary.stopped_reason:
            exit_code = 2
            rest = [SPECS[k].queue_tab for k in keys[i + 1:]]
            if rest:
                # 중단 사유(크레딧 소진, 키 오류 등)는 다음 큐에도 그대로 적용되므로 건너뜀
                # [TH] สาเหตุที่หยุด (เครดิตหมด คีย์ผิด ฯลฯ) จะเกิดกับคิวถัดไปเหมือนกัน จึงข้าม
                # [EN] The reason for stopping (credits exhausted, bad key, ...) would hit the next queue too, so it is skipped
                log(f"Stopped early -> skipping the remaining queues: {', '.join(rest)}")
            break
    if multi and not (args.dry_run or args.check_access):
        log(f"\nAll queues: Done={total_done}, Error={total_err}")
    return exit_code
