"""
큐에서 처리할 행을 고르고 메인 루프를 돌림 (--dry-run / --check-access / 실제 실행)
[TH] เลือกแถวที่ต้องทำจากคิวและรันวงรอบหลัก (--dry-run / --check-access / รันจริง)
[EN] Selects the rows to process from a queue and runs the main loop (--dry-run / --check-access / a real run)
"""
from __future__ import annotations

import argparse
import copy
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

from .config import PRICES_USD_PER_M
from .cp_column import ensure_db_headers
from .db_sync import replace_db_rows
from .models import Clients, Job, RowResult, RunControl, Usage
from .processing import process_job
from .queue_writer import clear_replaces
from .reports import build_db_row
from .sheets import SheetsError, append_rows, read_values, write_statuses
from .specs import QueueSpec, SPECS
from .stamps import MIGRATION_COL, time_stamp, write_migration_time
from .utils import chunked, col_letter, error_text, extract_file_id, parse_row_spec


def select_jobs(
    values: list[list[str]], *, retry_errors: bool, include_blank: bool, only_rows: Optional[set[int]]
) -> list[Job]:
    jobs: list[Job] = []
    # 1행 = 헤더이므로 2행부터
    # [TH] แถว 1 = หัวตาราง จึงเริ่มที่แถว 2
    # [EN] Row 1 is the header, so start at row 2
    for idx, row in enumerate(values[1:], start=2):
        cells = list(row) + [""] * (8 - len(row))
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
        jobs.append(Job(row=idx, pdf_url=pdf_url, folder_url=folder_url, prior_status=status, doc_no=str(cells[0]).strip(), cp=str(cells[5]).strip(),
                        replaces=str(cells[7]).strip()))
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
    # 큐 시트에 기록한 마이그레이션 종료 시각 (쓰지 못했으면 빈 문자열)
    # [TH] เวลาที่ migration จบซึ่งเขียนลงชีตคิว (ถ้าเขียนไม่ได้เป็นข้อความว่าง)
    # [EN] The migration finish time written to the queue sheet (empty when it could not be written)
    migrated_at: str = ""
    # 링크가 바뀐 PDF 의 새 데이터로 제자리에서 바꾼 DB 행 수
    # [TH] จำนวนแถวใน DB ที่ถูกแทนที่ในที่เดิมด้วยข้อมูลใหม่ของ PDF ที่ลิงก์เปลี่ยน
    # [EN] Number of DB rows that were replaced in place with the new data of a PDF whose link changed
    replaced: int = 0


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

    values = read_values(clients.sheets, sid, f"'{spec.queue_tab}'!A1:H")
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

    # 중복 방지: 이미 DB 에 있는 PDF -> AI 호출 없이 Done 처리. 같은 읽기로 링크가 바뀐 PDF 의 옛 DB 행도 찾음
    # [TH] กันซ้ำ: PDF ที่อยู่ใน DB แล้ว -> ตั้ง Done โดยไม่เรียก AI และใช้การอ่านเดียวกันนี้หาแถวเก่าใน DB ของ PDF ที่ลิงก์เปลี่ยนด้วย
    # [EN] Duplicate guard: a PDF already in the DB -> mark Done without calling the AI. The same read also finds the old DB row of a PDF whose link changed
    db_rows_by_fid: dict[str, list[int]] = {}
    if not args.no_reconcile or any(j.replaces for j in jobs):
        col = col_letter(spec.db_pdf_col + 1)
        for number, r in enumerate(read_values(clients.sheets, sid, f"'{spec.db_tab}'!{col}2:{col}"), start=2):
            fid = extract_file_id(r[0]) if r else None
            if fid:
                db_rows_by_fid.setdefault(fid, []).append(number)
    existing_ids: set[str] = set() if args.no_reconcile else set(db_rows_by_fid)
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
        # 새 PDF 가 이미 DB 에 있으므로 옛 행을 교체할 필요가 없음: H열을 비움
        # [TH] PDF ใหม่อยู่ใน DB แล้ว จึงไม่ต้องแทนที่แถวเก่า: ล้างคอลัมน์ H
        # [EN] The new PDF is already in the DB, so there is no old row to replace: column H is cleared
        stale = [j.row for j in jobs if j.row in reconciled_updates and j.replaces]
        if stale:
            clear_replaces(clients.sheets, sid, spec.queue_tab, stale)

    # 바뀐 PDF 의 옛 DB 행을 새 데이터로 제자리에서 바꿀 대상 (큐 행 -> DB 행 번호).
    # 다른 큐 행이 아직 옛 PDF 를 쓰고 있으면 그 행의 DB 데이터를 지키려고 교체하지 않고 새 행으로 추가
    # [TH] เป้าหมายที่จะแทนที่แถวเก่าใน DB ของ PDF ที่ลิงก์เปลี่ยนด้วยข้อมูลใหม่ในที่เดิม (แถวคิว -> เลขแถว DB)
    #      ถ้ามีแถวคิวอื่นที่ยังใช้ PDF เก่าอยู่ จะไม่แทนที่ (เพื่อรักษาข้อมูล DB ของแถวนั้น) แต่ต่อท้ายเป็นแถวใหม่
    # [EN] The targets for replacing the old DB row of a PDF whose link changed with the new data, in place (queue row -> DB row number).
    #      When another queue row still uses the old PDF the DB row is not replaced (to keep the data of that row) and a new row is appended instead
    queue_refs = Counter(f for f in (extract_file_id(row[3]) if len(row) > 3 else None for row in values[1:]) if f)
    replace_target: dict[int, int] = {}
    claimed_db_rows: set[int] = set()
    for j in to_run:
        old = extract_file_id(j.replaces) if j.replaces else None
        if not old or old == extract_file_id(j.pdf_url) or queue_refs.get(old, 0) > 0:
            continue
        target = next((n for n in db_rows_by_fid.get(old, []) if n not in claimed_db_rows), None)
        if target:
            replace_target[j.row] = target
            claimed_db_rows.add(target)

    # 새 행을 DB 에 쓰기 전에 DB 의 C/P 열과 "Legacy sync" 열의 머리글을 확인 (비어 있으면 채우고, 다른 머리글이면 이 큐를 중단)
    # [TH] ก่อนเขียนแถวใหม่ลง DB ให้เช็กหัวตารางคอลัมน์ C/P และ "Legacy sync" (ถ้าว่างจะเติมให้ ถ้าเป็นหัวอื่นจะหยุดคิวนี้)
    # [EN] Before new rows are written to the DB, check the headers of its C/P column and its "Legacy sync" column (an empty one is filled, a different one stops this queue)
    if to_run:
        for header, cell, state in ensure_db_headers(clients.sheets, sid, spec):
            if state == "written":
                log(f"  ✓ Wrote the header '{header}' to {spec.db_tab}!{cell}")

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
            db_rows = [build_db_row(spec, r.data or {}, r.job.pdf_url, r.job.folder_url, r.job.cp) for r in ok]
            replace_items = [(replace_target[r.job.row], row) for r, row in zip(ok, db_rows) if r.job.row in replace_target]
            append_items = [row for r, row in zip(ok, db_rows) if r.job.row not in replace_target]
            try:
                if replace_items:
                    replace_db_rows(clients.sheets, sid, spec, replace_items)
                if append_items:
                    append_rows(clients.sheets, sid, spec.db_tab, append_items)
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
                summary.replaced += len(replace_items)
                log(f"  ✓ {len(ok)} rows -> {spec.db_tab} (queue rows {[r.job.row for r in ok][:10]}{'…' if len(ok) > 10 else ''})")
                if replace_items:
                    log(f"  ✓ {len(replace_items)} of them replaced the old row in place (the PDF of the item changed in the legacy sheet)")
        try:
            write_statuses(clients.sheets, sid, spec.queue_tab, updates)
        except SheetsError as e:
            log(f"  !! writing the queue statuses failed: {e}\n     the data is already in {spec.db_tab} - re-run and those rows are marked Done without calling the AI")
            ctl.request_stop("writing the queue statuses failed")
        else:
            # Done 이 된 행의 H열(교체 대기)을 비움. 실패한 행은 H열을 남겨 옛 DB 행이 보호되고 다음에 다시 교체를 시도함
            # [TH] ล้างคอลัมน์ H (รอแทนที่) ของแถวที่เป็น Done แล้ว แถวที่พลาดจะเก็บค่า H ไว้ แถวเก่าใน DB จึงยังถูกปกป้องและลองแทนที่ใหม่รอบหน้า
            # [EN] Clears column H (waiting to replace) of the rows that are Done now. A row that failed keeps its H, so the old DB row stays protected and the replacement is tried again
            finished = [j.row for j in to_run if updates.get(j.row) == "Done" and j.replaces]
            if finished:
                try:
                    clear_replaces(clients.sheets, sid, spec.queue_tab, finished)
                except SheetsError as e:
                    log(f"  ⚠ could not clear column H of the queue rows {finished[:10]}: {e}")
        if interrupted:
            break

    summary.stopped_reason = ctl.stop_reason
    log("-" * 60)
    log(f"Finished: Done={summary.done} (+reconciled {summary.reconciled}), Error={sum(summary.errors.values())} {summary.errors or ''}, Skipped={summary.skipped}")
    if summary.replaced:
        log(f"Replaced in place: {summary.replaced} rows of {spec.db_tab}")
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
    # 실제 실행이 끝날 때마다 이 큐 시트에 마이그레이션 종료 시각을 기록 (--dry-run / --check-access 는 위에서 이미 끝남).
    # 쓰지 못해도 실행 결과는 그대로이므로 경고만 남김
    # [TH] ทุกครั้งที่รันจริงจบ จะเขียนเวลาที่ migration จบลงชีตคิวนี้ (--dry-run / --check-access จบไปก่อนหน้านี้แล้ว)
    #      ถ้าเขียนไม่ได้ ผลการรันไม่เปลี่ยน จึงแค่เตือน
    # [EN] Every time a real run finishes, the migration finish time is written to this queue sheet (--dry-run / --check-access returned above).
    #      A failed write does not change the result of the run, so it only warns
    stamp = time_stamp()
    try:
        write_migration_time(clients.sheets, sid, spec.queue_tab, stamp)
        summary.migrated_at = stamp
        log(f"Migration time written to {spec.queue_tab}!{MIGRATION_COL}2: {stamp}")
    except SheetsError as e:
        log(f"⚠ The run finished, but the migration time could not be written to {spec.queue_tab}!{MIGRATION_COL}1:{MIGRATION_COL}2: {e}")
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
