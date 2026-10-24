"""
sync queue: 옛 시트에서 큐를 만들거나, 기존 큐를 새로 계산하되 기존 행의 상태는 유지 (Code_Migration.js 의 extractLegacyDwrLinks / extractLegacyDrqLinks 를 대체)
[TH] sync queue: สร้างคิวจากชีตเก่า หรือคำนวณคิวเดิมใหม่แต่คงสถานะของแถวเดิมไว้ (แทน extractLegacyDwrLinks / extractLegacyDrqLinks ใน Code_Migration.js)
[EN] sync queue: builds the queues from the legacy sheets, or recomputes an existing queue and keeps the status of its rows (replaces extractLegacyDwrLinks / extractLegacyDrqLinks in Code_Migration.js)
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Callable, Optional

from .config import SHEETS_BASE
from .legacy_sheet import ExtractStats, LEGACY_FIRST_DATA_ROW, LEGACY_HEADER_ROW, build_queue_items, read_legacy_rows, sheet_cell_text
from .models import Clients
from .queue_writer import QUEUE_HEADER, plan_sync, queue_progress, sync_queue, write_queue
from .sheets import SheetsError, list_tabs, read_values, sheets_call, spreadsheet_title
from .specs import SPECS
from .stamps import REFRESH_COL, time_stamp, write_refresh_time
from .utils import count_lines, removal_limit


@dataclass
class ExtractSummary:
    items: int = 0
    completed: int = 0
    stats: ExtractStats = field(default_factory=ExtractStats)
    written: int = 0
    created_tab: bool = False
    refused: str = ""
    refreshed_at: str = ""
    # 맞추기(sync) 결과 / [TH] ผลของการจับคู่ (sync) / [EN] Result of a sync
    added: int = 0
    renumbered: int = 0
    duplicates: int = 0
    removed: int = 0
    updated: int = 0
    unchanged: int = 0
    kept_done: int = 0
    relinked: int = 0
    # 이번 `sync queue` 후(--dry-run 이면 이번에 만들 계획의) 큐 행 (A~H열, 머리글 제외). 큐를 쓰지 않았거나 거부된 경우 None.
    # `sync db` 의 --dry-run 이 이 행을 기준으로 계획을 보여 줌
    # [TH] แถวคิวหลัง `sync queue` รอบนี้ (ถ้าเป็น --dry-run คือแถวที่จะเกิดขึ้นตามแผน) A~H ไม่รวมหัวตาราง เป็น None ถ้าไม่ได้เขียนคิวหรือถูกปฏิเสธ
    #      --dry-run ของ `sync db` แสดงแผนจากแถวนี้
    # [EN] The queue rows after this `sync queue` (with --dry-run: the rows the plan would produce), columns A-H without the header. None when the queue was not
    #      written or the step was refused. The --dry-run of `sync db` shows its plan on these rows
    queue_rows: Optional[list] = None


def extract_queue(args: argparse.Namespace, clients: Clients, key: str, log: Callable[[str], None] = print) -> ExtractSummary:
    spec = SPECS[key]
    sess, sid = clients.sheets, args.spreadsheet_id
    legacy_tab = args.legacy_tabs[key]
    summary = ExtractSummary()
    # 옛 탭을 읽는 스프레드시트 (기본은 큐를 쓰는 대상 스프레드시트와 같은 파일)
    # [TH] สเปรดชีตที่อ่านแท็บเก่า (ค่าเริ่มต้นคือไฟล์เดียวกับสเปรดชีตเป้าหมายที่เขียนคิว)
    # [EN] The spreadsheet the legacy tab is read from (by default the same file as the target spreadsheet the queue is written to)
    legacy_sid = getattr(args, "legacy_spreadsheet_id", None) or sid
    separate = legacy_sid != sid

    try:
        legacy_tabs_found = list_tabs(sess, legacy_sid)
    except SheetsError as e:
        if not separate:
            raise
        raise SheetsError(f"cannot open the legacy spreadsheet {legacy_sid} ({e}) - check the ID and share it with the service account"
                          + (f" {clients.sa_email}" if clients.sa_email else "")) from e
    if legacy_tab not in legacy_tabs_found:
        where = f"in the legacy spreadsheet {legacy_sid}" if separate else "in the spreadsheet"
        raise SheetsError(f"tab '{legacy_tab}' not found {where} (tabs found: {', '.join(legacy_tabs_found) or '-'}) - check {spec.legacy_env}")
    tabs = list_tabs(sess, sid) if separate else legacy_tabs_found
    rows = read_legacy_rows(sess, legacy_sid, legacy_tab, spec.legacy_end_col, legacy_tabs_found[legacy_tab][1])
    header_cells = next((cells for n, cells in rows if n == LEGACY_HEADER_ROW), [])
    data_rows = [(n, cells) for n, cells in rows if n >= LEGACY_FIRST_DATA_ROW]
    items, stats = build_queue_items(header_cells, data_rows, spec.legacy_end_col)
    summary.items, summary.stats = len(items), stats
    summary.completed = sum(1 for it in items if it.completed)

    if separate:
        legacy_title, _ = spreadsheet_title(sess, legacy_sid)
        log(f"[{spec.name}] Legacy spreadsheet: {legacy_title + ' ' if legacy_title else ''}({legacy_sid}) -> queue is written to the target spreadsheet ({sid})")
    log(f"[{spec.name}] Read tab '{legacy_tab}': {stats.rows} data rows, {stats.dated_rows} with a Date -> "
        f"{len(items)} unique items (C={summary.completed}, P={len(items) - summary.completed}), {stats.merged} duplicate cells merged")
    if stats.no_link:
        log(f"  ⚠ {stats.no_link} items have no PDF link (they will get 'Error: No URL' when migrated)")
    if stats.no_discipline:
        log(f"  ⚠ skipped {stats.no_discipline} cells whose column header is empty (column E onwards)")
    if not items:
        why = " (row 3, the header row, is empty?)" if not any(sheet_cell_text(c) for c in header_cells) else ""
        log(f"  No items found -> the {spec.queue_tab} queue is left untouched{why}")
        return summary

    exists = spec.queue_tab in tabs
    existing = read_values(sess, sid, f"'{spec.queue_tab.replace(chr(39), chr(39) * 2)}'!A:H") if exists else []
    blank_queue = not any(str(c).strip() for r in existing for c in r)
    # 새로 만들기: 탭이 없거나 비어 있거나 --overwrite 일 때. 그 외에는 큐를 옛 시트에서 새로 계산해 기존 행과 맞춤(sync): 기존 행의 상태는 그대로 유지
    # [TH] สร้างใหม่ทั้งคิว: เมื่อไม่มีแท็บ / แท็บว่าง / ใส่ --overwrite นอกนั้นคำนวณคิวใหม่จากชีตเก่าแล้วจับคู่กับแถวเดิม (sync): คงสถานะของแถวเดิมไว้
    # [EN] Rebuild the whole queue: when the tab is missing or empty, or with --overwrite. Otherwise recompute the queue from the legacy sheet
    #      and match it with the existing rows (sync): the status of an existing row is kept
    rebuild = args.overwrite or not exists or blank_queue

    if rebuild:
        if exists and not blank_queue:
            progress = queue_progress(existing)
            if any(progress.values()):
                parts = [f"Done {progress['done']}", f"Error {progress['error']}"] + ([f"other {progress['other']}"] if progress["other"] else [])
                log(f"  ⚠ --overwrite: the existing statuses ({', '.join(parts)}) will be wiped")
        summary.queue_rows = [it.to_row() for it in items]
        if args.dry_run:
            for it in items[:5]:
                log(f"  sample: {it.doc_no} | {it.date} | {it.discipline} | {'C' if it.completed else 'P'} | PDF link: {'yes' if it.pdf_url else 'no'}")
            log(f"  (--dry-run) nothing was written: would write {len(items)} rows to {spec.queue_tab}" + ("" if exists else " (new tab)"))
            return summary
        if not exists:
            sheets_call(sess, "POST", f"{SHEETS_BASE}/{sid}:batchUpdate",
                         json={"requests": [{"addSheet": {"properties": {"title": spec.queue_tab}}}]})
            summary.created_tab = True
        write_queue(sess, sid, spec.queue_tab, [it.to_row() for it in items])
        summary.written = len(items)
        log(f"  ✓ Wrote queue {spec.queue_tab}: {len(items)} rows (Waiting)" + (" - new tab created" if summary.created_tab else ""))
    else:
        header = [str(c).strip() for c in existing[0]][:len(QUEUE_HEADER)] if existing else []
        if header != QUEUE_HEADER:
            summary.refused = "unexpected header"
            log(f"  ✗ The header row of {spec.queue_tab} is not the expected one ({' | '.join(QUEUE_HEADER)}) - nothing was written\n"
                f"    Fix the header, or add --overwrite to rebuild the queue (every row becomes Waiting)")
            return summary
        plan = plan_sync(existing, items)
        summary.added, summary.updated, summary.renumbered = plan.new, plan.updated, plan.renumbered
        summary.unchanged = plan.matched - plan.updated
        summary.kept_done = plan.kept["done"]
        summary.duplicates, summary.removed, summary.relinked = plan.duplicates, plan.removed, plan.relinked
        # 안전장치: 옛 시트가 잘못 비어 큐가 대량으로 지워지는 사고를 막음 (한도: 큐 행의 10% 와 100행 중 큰 값)
        # [TH] ตัวกันพลาด: กันอุบัติเหตุที่ชีตเก่าว่างผิดปกติแล้วคิวถูกลบเป็นจำนวนมาก (เพดาน: ค่าที่มากกว่าระหว่าง 10% ของแถวคิวกับ 100 แถว)
        # [EN] Safety net: a wrongly emptied legacy sheet must not wipe the queue (the limit: the larger of 10% of the queue rows and 100)
        limit = removal_limit(plan.matched + plan.removed - plan.duplicates)
        if plan.removed > limit and not getattr(args, "allow_large_removal", False):
            summary.refused = "too many removals"
            log(f"  ✗ Not syncing {spec.queue_tab}: {plan.removed} rows would be removed, more than the limit of {limit} (10% of the queue, at least 100). "
                f"Check the legacy sheet, then add --allow-large-removal if this is right")
            return summary
        summary.queue_rows = plan.rows
        if plan.relinked:
            log(f"  ℹ {plan.relinked} Done rows got a new PDF link from the legacy sheet: set back to Waiting so that the next `sync db` reads the new file "
                f"(the DB row that used the old file is replaced when it succeeds)")
        if plan.removed_processed:
            log(f"  ⚠ {plan.removed_processed} removed rows were Done / Error: they have no item in the legacy sheet any more")
        counts = count_lines([
            ("Kept from the queue", str(plan.matched), f"(changed {plan.updated}, renumbered {plan.renumbered}, set back to Waiting {plan.relinked})"),
            ("New", str(plan.new), ""),
            ("Duplicate rows merged", str(plan.duplicates), ""),
            ("Removed (not in the legacy sheet)", str(plan.removed), ""),
            ("Statuses kept", f"Done {plan.kept['done']}, Error {plan.kept['error']}, Waiting {plan.kept['waiting']}"
                              + (f", other {plan.kept['other']}" if plan.kept["other"] else ""), ""),
        ])
        if args.dry_run:
            for number, old, new in plan.renumbered_samples[:5]:
                log(f"  sample (renumbered): row {number}: {old} -> {new}")
            log(f"  (--dry-run) nothing was written: would sync {spec.queue_tab}: {len(plan.rows)} rows" + ("" if plan.changed else " - no change") + f"\n{counts}")
            return summary
        if plan.changed:
            sheet_id, grid_rows = tabs[spec.queue_tab]
            sync_queue(sess, sid, spec.queue_tab, sheet_id, grid_rows, plan)
            summary.written = len(plan.rows)
            log(f"  ✓ Synced {spec.queue_tab}: {len(plan.rows)} rows\n{counts}")
        else:
            log(f"  ✓ Synced {spec.queue_tab}: no change ({len(plan.rows)} rows)\n{counts}")
    # 행은 이미 기록됐으므로 시각을 쓰지 못해도(예: 탭의 열이 7개뿐) extract 자체는 실패로 보지 않음
    # [TH] แถวถูกเขียนไปแล้ว ถ้าเขียนเวลาไม่ได้ (เช่นแท็บมีแค่ 7 คอลัมน์) ก็ไม่นับว่า extract ล้มเหลว
    # [EN] The rows are already written, so if the time cannot be written (e.g. the tab has only 7 columns) the extract itself is not treated as failed
    stamp = time_stamp()
    try:
        write_refresh_time(sess, sid, spec.queue_tab, stamp)
        summary.refreshed_at = stamp
        log(f"  ✓ Refresh time written to {spec.queue_tab}!{REFRESH_COL}2: {stamp}")
    except SheetsError as e:
        log(f"  ⚠ The rows are written, but the refresh time could not be written to {spec.queue_tab}!{REFRESH_COL}1:{REFRESH_COL}2: {e}")
    return summary


def extract_queues(args: argparse.Namespace, clients: Clients, keys: list[str], log: Callable[[str], None] = print,
                   sheet_title: str = "") -> tuple[int, dict[str, ExtractSummary]]:
    """
    큐별로 `sync queue` 를 실행하고 (종료 코드, 큐를 만든 큐들의 요약) 을 반환. 요약의 queue_rows 는 다음 단계(db)가 쓰는 큐 행
    (--dry-run 이면 계획된 행). 실패했거나 거부되었거나 만들 항목이 없어 큐를 건드리지 않은 큐는 요약에 들어가지 않음
    [TH] รัน `sync queue` ทีละคิว แล้วคืน (รหัสออก, สรุปของคิวที่สร้างได้) queue_rows ในสรุปคือแถวคิวที่ขั้นถัดไป (db) ใช้ (ถ้า --dry-run คือแถวตามแผน)
         คิวที่ล้มเหลว ถูกปฏิเสธ หรือไม่มีรายการให้สร้างจนไม่ได้แตะคิว จะไม่อยู่ในสรุป
    [EN] Runs `sync queue` queue by queue and returns (exit code, the summaries of the queues that were built). queue_rows of a summary are the queue rows the next
         step (db) works on (with --dry-run: the planned rows). A queue that failed, was refused, or was left untouched because there was nothing to build is not in it
    """
    multi = len(keys) > 1
    exit_code = 0
    built: dict[str, ExtractSummary] = {}
    for key in keys:
        spec = SPECS[key]
        log(("\n" if multi else "") + f"===== Build queue {spec.name}: {args.legacy_tabs[key]} -> {spec.queue_tab}"
            + (f" | sheet: {sheet_title}" if sheet_title else "") + " =====")
        try:
            summary = extract_queue(args, clients, key, log=log)
        except SheetsError as e:
            log(f"✗ Could not build queue {spec.queue_tab}: {e}")
            exit_code = 2
            continue
        if summary.refused and not args.dry_run:
            exit_code = 2
        if summary.queue_rows is not None and not summary.refused:
            built[key] = summary
    return exit_code, built


def run_extract(args: argparse.Namespace, clients: Clients, keys: list[str], log: Callable[[str], None] = print,
                sheet_title: str = "") -> int:
    """
    `sync queue` 단계만 실행. 종료 코드: 0 = 정상, 2 = 실패했거나 기존 큐의 헤더가 달라 (--overwrite 없이) 쓰지 않은 큐, 또는 안전장치가 거부한 큐가 있음
    [TH] รันเฉพาะขั้น `sync queue` รหัสออก: 0 = ปกติ, 2 = มีคิวที่ล้มเหลวหรือไม่ได้เขียนเพราะหัวตารางของคิวเดิมไม่ตรง (ไม่ได้ใส่ --overwrite) หรือคิวที่ตัวกันพลาดปฏิเสธ
    [EN] Runs just the `sync queue` step. Exit code: 0 = fine, 2 = a queue failed, was not written because the existing queue's header is not the expected one
         (no --overwrite), or a queue was refused by the safety net
    """
    return extract_queues(args, clients, keys, log=log, sheet_title=sheet_title)[0]
