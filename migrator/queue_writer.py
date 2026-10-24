"""
큐 시트 쓰기 (`sync queue` 의 쓰기 쪽): 새로 만들기, 새로 계산해 기존 행과 맞추기(sync)
[TH] เขียนชีตคิว (ฝั่งเขียนของ `sync queue`): สร้างใหม่, คำนวณใหม่แล้วจับคู่กับแถวเดิม (sync)
[EN] Writing the queue sheet (the writing half of `sync queue`): rebuild, recompute and match with the existing rows (sync)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import SHEETS_BASE
from .legacy_sheet import LegacyItem
from .sheets import append_rows, sheets_call, values_url
from .utils import extract_file_id


# 큐 시트의 헤더 (Apps Script 와 동일, 영어)
# [TH] หัวตารางของชีตคิว (เหมือน Apps Script เป็นภาษาอังกฤษ)
# [EN] Header of the queue sheet (same as Apps Script, in English)
QUEUE_HEADER = ["Doc No", "Date", "Discipline", "PDF URL", "Folder URL", "Status(C/P)", "AI Processed"]
# H열: 링크가 바뀐 Done 행이 Waiting 으로 돌아갈 때 옛 PDF 의 file ID 를 적어 둠 (migrate 가 DB 의 옛 행을 바꾸는 데 쓰고, 끝나면 비움)
# [TH] คอลัมน์ H: เมื่อแถว Done ที่ลิงก์เปลี่ยนถูกตั้งกลับเป็น Waiting จะจด file ID ของ PDF เก่าไว้ (migrate ใช้แทนที่แถวเก่าใน DB แล้วล้างค่า)
# [EN] Column H: when a Done row whose link changed goes back to Waiting, the file ID of the old PDF is written here (migrate uses it to replace the old DB row, then clears it)
REPLACES_LABEL = "Replaces PDF (old file ID)"
QUEUE_WIDTH = len(QUEUE_HEADER) + 1

EXTRACT_ROWS_PER_WRITE = 2000

EXTRACT_UPDATES_PER_WRITE = 500


def queue_progress(values: list[list[str]]) -> dict[str, int]:
    """
    큐에서 이미 처리된 행의 수 (Waiting / 빈 상태는 제외). 큐를 다시 만들면 이 상태들이 사라짐
    [TH] จำนวนแถวในคิวที่ถูกประมวลผลไปแล้ว (ไม่นับ Waiting / ว่าง) ถ้าสร้างคิวใหม่สถานะเหล่านี้จะหายไป
    [EN] Number of queue rows that were already processed (Waiting / blank are not counted). Rebuilding the queue wipes these statuses
    """
    counts = {"done": 0, "error": 0, "other": 0}
    for row in values[1:]:
        status = str(row[6]).strip() if len(row) > 6 else ""
        if status in ("", "Waiting"):
            continue
        if status == "Done":
            counts["done"] += 1
        elif status.startswith("Error"):
            counts["error"] += 1
        else:
            counts["other"] += 1
    return counts


def write_queue(sess: Any, sid: str, tab: str, rows: list[list]) -> None:
    """
    큐 시트를 비우고 헤더 + 행을 기록 (RAW: 날짜/숫자처럼 보이는 문자열이 변환되지 않음). 행은 append 로 넣어 시트가 자동 확장됨
    [TH] ล้างชีตคิวแล้วเขียนหัวตาราง + แถว (RAW: ข้อความที่หน้าตาเหมือนวันที่/ตัวเลขจะไม่ถูกแปลง) แถวเพิ่มด้วย append ชีตจึงขยายให้เอง
    [EN] Clears the queue sheet and writes the header + rows (RAW: text that looks like a date/number is not converted).
         Rows go in with append, so the sheet grows by itself
    """
    quoted = tab.replace("'", "''")
    sheets_call(sess, "POST", values_url(sid, f"'{quoted}'") + ":clear", json={})
    sheets_call(sess, "PUT", values_url(sid, f"'{quoted}'!A1:G1"), params={"valueInputOption": "RAW"},
                 json={"majorDimension": "ROWS", "values": [QUEUE_HEADER]})
    for i in range(0, len(rows), EXTRACT_ROWS_PER_WRITE):
        append_rows(sess, sid, tab, rows[i:i + EXTRACT_ROWS_PER_WRITE], value_input="RAW")


def status_rank(status: str) -> int:
    """
    같은 항목에 행이 여러 개일 때 어느 행의 상태를 남길지: 처리된 쪽이 우선 (Done > Error > 그 밖의 값 > Waiting / 빈 값)
    [TH] เมื่อรายการเดียวมีหลายแถว จะคงสถานะของแถวไหน: ฝั่งที่ประมวลผลแล้วมาก่อน (Done > Error > ค่าอื่น > Waiting / ว่าง)
    [EN] When one item has several rows, whose status is kept: the processed one wins (Done > Error > any other value > Waiting / blank)
    """
    if status == "Done":
        return 3
    if status.startswith("Error"):
        return 2
    if status in ("", "Waiting"):
        return 0
    return 1


@dataclass
class SyncPlan:
    rows: list[list] = field(default_factory=list)   # 새로 계산한 큐 행 (A~H열), 옛 시트 순서 / [TH] แถวคิวที่คำนวณใหม่ (A~H) ตามลำดับชีตเก่า / [EN] The recomputed queue rows (A-H) in legacy order
    new: int = 0              # 큐에 없던 항목 / [TH] รายการที่ยังไม่เคยมีในคิว / [EN] items that were not in the queue
    matched: int = 0          # 기존 행이 있는 항목 / [TH] รายการที่มีแถวเดิมอยู่แล้ว / [EN] items that already had a row
    updated: int = 0          # 그중 A~F 열이 바뀐 행 / [TH] ในนั้นที่คอลัมน์ A~F เปลี่ยน / [EN] of those, rows whose columns A..F changed
    renumbered: int = 0       # 그중 Doc No 가 바뀐 행 / [TH] ในนั้นที่ Doc No เปลี่ยน / [EN] of those, rows whose Doc No changed
    duplicates: int = 0       # 같은 항목의 다른 행에 합쳐진 옛 행 / [TH] แถวเดิมที่ถูกรวมเข้ากับแถวอื่นของรายการเดียวกัน / [EN] old rows merged into another row of the same item
    removed: int = 0          # 옛 시트에 대응하는 항목이 없어 사라지는 옛 행 / [TH] แถวเดิมที่ไม่มีรายการคู่กันในชีตเก่า จึงหายไป / [EN] old rows with no item in the legacy sheet, which disappear
    removed_processed: int = 0  # 그중 Done / Error 였던 행 / [TH] ในนั้นที่เคยเป็น Done / Error / [EN] of those, rows that were Done / Error
    kept: dict[str, int] = field(default_factory=lambda: {"done": 0, "error": 0, "other": 0, "waiting": 0})  # 이어받은 상태 / [TH] สถานะที่คงไว้ / [EN] the statuses that were kept
    relinked: int = 0         # Done 이지만 옛 시트의 PDF 가 바뀌어 Waiting 으로 돌린 행 / [TH] แถว Done ที่ PDF ในชีตเก่าเปลี่ยนจึงตั้งกลับเป็น Waiting / [EN] Done rows whose PDF changed in the legacy sheet and went back to Waiting
    changed: bool = False     # 새 행이 지금 시트와 다른지 / [TH] แถวใหม่ต่างจากที่อยู่ในชีตตอนนี้หรือไม่ / [EN] whether the new rows differ from the sheet as it is now
    old_extent: int = 0       # 지금 시트의 사용 행 수 (머리글 포함) / [TH] จำนวนแถวที่ใช้อยู่ในชีตตอนนี้ (รวมหัวตาราง) / [EN] the used rows of the sheet now (incl. the header)
    renumbered_samples: list[tuple[int, str, str]] = field(default_factory=list)   # (옛 행 번호, 옛 Doc No, 새 Doc No) / [TH] (เลขแถวเดิม, Doc No เดิม, Doc No ใหม่) / [EN] (old row number, old Doc No, new Doc No)


def plan_sync(existing: list[list], items: list[LegacyItem]) -> SyncPlan:
    """
    큐의 모든 행을 옛 시트에서 새로 계산하고(A~F열), 지금 큐와 비교해 기존 행이 있으면 그 행의 상태(G열)만 이어받음:
      * 항목마다 지금 큐에서 같은 항목의 행을 찾음: (Doc No, Date) 가 같거나, 옛 시트의 A열(No.)만 바뀐 경우를 위해 (Date, Discipline, 칸의 값) 이 같은 행
      * 같은 항목의 행이 여러 개면(예: 옛 Doc No 와 새 Doc No 로 두 번 들어간 경우) 처리된 쪽의 상태를 남기고 나머지는 합침
      * 찾으면: A~F 열은 옛 시트 값으로 새로 계산하고(Done 행도 마찬가지), G열 상태는 기존 행 것을 그대로 유지
      * 예외: 옛 시트에서 PDF(Drive file ID)가 바뀐 Done 행은 Waiting 으로 되돌리고 옛 PDF 의 file ID 를 H열에 적음
        (다음 마이그레이션이 새 파일을 읽어 옛 파일을 쓰던 DB 행을 바꾸도록)
      * 못 찾으면: 새 행 (Waiting)
      * 옛 시트에 없는 기존 행은 새 큐에 들어가지 않음 (큐 = 옛 시트와 같은 내용)
    [TH] คำนวณทุกแถวของคิวใหม่จากชีตเก่า (คอลัมน์ A~F) แล้วเทียบกับคิวตอนนี้ ถ้ามีแถวเดิมอยู่แล้วจะคงเฉพาะสถานะ (คอลัมน์ G) ของแถวนั้นไว้:
         * แต่ละรายการหาแถวของรายการเดียวกันในคิวตอนนี้: (Doc No, Date) ตรงกัน หรือ (Date, Discipline, ค่าในช่อง) ตรงกัน (สำหรับกรณีที่คอลัมน์ A (No.) ของชีตเก่าเปลี่ยน)
         * ถ้ารายการเดียวมีหลายแถว (เช่นเข้าคิวสองครั้งด้วย Doc No เก่าและใหม่) จะคงสถานะของฝั่งที่ประมวลผลแล้วและรวมที่เหลือเข้าด้วยกัน
         * ถ้าเจอ: คอลัมน์ A~F คำนวณใหม่จากชีตเก่า (แถว Done ก็เช่นกัน) ส่วนสถานะ G คงของแถวเดิมตามเดิม
         * ข้อยกเว้น: แถว Done ที่ PDF (Drive file ID) ในชีตเก่าเปลี่ยน จะกลับเป็น Waiting และจด file ID ของ PDF เก่าไว้ในคอลัมน์ H
           เพื่อให้ migrate รอบถัดไปอ่านไฟล์ใหม่และแทนที่แถวใน DB ที่ใช้ไฟล์เก่า
         * ถ้าไม่เจอ: เป็นแถวใหม่ (Waiting)
         * แถวเดิมที่ไม่มีในชีตเก่าจะไม่อยู่ในคิวใหม่ (คิว = เนื้อหาเดียวกับชีตเก่า)
    [EN] Recomputes every row of the queue from the legacy sheet (columns A..F), compares it with the current queue, and where a row already exists
         keeps only that row's status (column G):
         * for each item, find the rows of the same item in the current queue: the same (Doc No, Date), or the same (Date, Discipline, cell value)
           for the case where only column A (No.) of the legacy sheet changed
         * when one item has several rows (e.g. it went in twice, once under the old and once under the new Doc No) the status of the processed one
           is kept and the rest are merged into it
         * found: columns A..F are recomputed from the legacy sheet (a Done row too) and the status in G is kept from the existing row
         * exception: a Done row whose PDF (Drive file ID) changed in the legacy sheet goes back to Waiting, and the file ID of the old PDF is
           written to column H, so that the next migration reads the new file and replaces the DB row that used the old one
         * not found: a new row (Waiting)
         * an existing row with no item in the legacy sheet does not go into the new queue (the queue has the same content as the legacy sheet)
    """
    old_rows: list[tuple[int, list[str]]] = []
    by_exact: dict[tuple[str, str], list[tuple[int, list[str]]]] = {}
    by_identity: dict[tuple[str, str], list[tuple[int, list[str]]]] = {}       # (Date, Discipline) -> rows, in sheet order
    for number, row in enumerate(existing[1:], start=2):
        cells = [str(c).strip() for c in row][:QUEUE_WIDTH]
        cells += [""] * (QUEUE_WIDTH - len(cells))
        if not cells[0]:
            continue                                                            # a blank row (no Doc No) is not part of the queue
        old_rows.append((number, cells))
        by_exact.setdefault((cells[0], cells[1]), []).append((number, cells))
        by_identity.setdefault((cells[1], cells[2]), []).append((number, cells))

    plan = SyncPlan(old_extent=len(existing))
    claimed: set[int] = set()
    for it in items:
        candidates: dict[int, list[str]] = {n: c for n, c in by_exact.get((it.doc_no, it.date), []) if n not in claimed}
        if it.key_text:
            suffix = f"-{it.discipline}-{it.key_text}"
            for n, c in by_identity.get((it.date, it.discipline), []):
                if n not in claimed and c[0].endswith(suffix):
                    candidates[n] = c
        row = it.to_row() + [""]
        if not candidates:
            plan.new += 1
            plan.rows.append(row)
            continue
        carrier_number, carrier = max(candidates.items(), key=lambda kv: (status_rank(kv[1][6]), -kv[0]))
        claimed.update(candidates)
        plan.matched += 1
        plan.duplicates += len(candidates) - 1
        status, replaces = carrier[6], carrier[7]                               # the status (and a pending replacement) of the existing row are kept
        old_fid, new_fid, pending_fid = extract_file_id(carrier[3]), extract_file_id(it.pdf_url), extract_file_id(replaces)
        if replaces and new_fid and new_fid == pending_fid and status in ("", "Waiting"):
            status, replaces = "Done", ""                                       # the link went back to the file that is in the DB: nothing to replace
        elif status == "Done" and new_fid and new_fid != old_fid:
            status, replaces = "Waiting", old_fid or ""                         # a new PDF for a migrated item: migrate it again and replace the old DB row
            plan.relinked += 1
        elif status == "Done":
            replaces = ""
        row[6], row[7] = status, replaces
        plan.kept["done" if status == "Done" else "error" if status.startswith("Error") else "waiting" if status in ("", "Waiting") else "other"] += 1
        if carrier[:6] != row[:6]:
            plan.updated += 1
        if carrier[0] != row[0]:
            plan.renumbered += 1
            plan.renumbered_samples.append((carrier_number, carrier[0], row[0]))
        plan.rows.append(row)

    for number, cells in old_rows:
        if number not in claimed:
            plan.removed += 1
            if cells[6] == "Done" or cells[6].startswith("Error"):
                plan.removed_processed += 1
    old_cells = [cells for _, cells in old_rows]
    plan.changed = plan.rows != old_cells or len(existing) - 1 != len(old_rows)
    return plan


def sync_queue(sess: Any, sid: str, tab: str, sheet_id: int, grid_rows: int, plan: SyncPlan) -> None:
    """
    계산한 행으로 큐의 A~H열을 덮어씀. 먼저 비우지 않고 제자리에서 덮어쓰므로 도중에 실패해도 큐가 비지 않음 (상태는 새 행에 이미 들어 있음).
    시트 행 수가 모자라면 먼저 늘리고, 새 행이 더 적으면 남는 꼬리 부분만 비움. I열/K열(시각)은 건드리지 않음
    [TH] เขียนทับคอลัมน์ A~H ของคิวด้วยแถวที่คำนวณไว้ เขียนทับในที่เดิมโดยไม่ล้างก่อน ถ้าพลาดกลางทางคิวจะไม่ว่าง (สถานะอยู่ในแถวใหม่แล้ว)
         ถ้าชีตมีแถวไม่พอจะขยายก่อน ถ้าแถวใหม่น้อยกว่าจะล้างเฉพาะส่วนท้ายที่เหลือ ไม่แตะคอลัมน์ I/K (เวลา)
    [EN] Overwrites columns A..H of the queue with the computed rows, in place and without clearing first, so a failure halfway never leaves the
         queue empty (the statuses are already in the new rows). The sheet is grown first when it has too few rows, and when there are fewer new
         rows only the leftover tail is cleared. Columns I / K (the times) are not touched
    """
    quoted = tab.replace("'", "''")
    total = len(plan.rows) + 1
    if total > grid_rows:
        sheets_call(sess, "POST", f"{SHEETS_BASE}/{sid}:batchUpdate",
                    json={"requests": [{"appendDimension": {"sheetId": sheet_id, "dimension": "ROWS", "length": total - grid_rows}}]})
    sheets_call(sess, "PUT", values_url(sid, f"'{quoted}'!A1:H1"), params={"valueInputOption": "RAW"},
                json={"majorDimension": "ROWS", "values": [QUEUE_HEADER + [REPLACES_LABEL]]})
    for i in range(0, len(plan.rows), EXTRACT_ROWS_PER_WRITE):
        chunk = plan.rows[i:i + EXTRACT_ROWS_PER_WRITE]
        first = 2 + i
        sheets_call(sess, "PUT", values_url(sid, f"'{quoted}'!A{first}:H{first + len(chunk) - 1}"), params={"valueInputOption": "RAW"},
                    json={"majorDimension": "ROWS", "values": chunk})
    if plan.old_extent > total:
        sheets_call(sess, "POST", values_url(sid, f"'{quoted}'!A{total + 1}:H{plan.old_extent}") + ":clear", json={})


def clear_replaces(sess: Any, sid: str, tab: str, rows: list[int]) -> None:
    """
    migrate 가 옛 DB 행을 새 데이터로 바꿨거나(또는 바꿀 필요가 없어진) 큐 행의 H열을 비움
    [TH] ล้างคอลัมน์ H ของแถวคิวที่ migrate แทนที่แถวเก่าใน DB แล้ว (หรือไม่จำเป็นต้องแทนที่แล้ว)
    [EN] Clears column H of the queue rows whose old DB row was replaced by migrate (or no longer needs replacing)
    """
    quoted = tab.replace("'", "''")
    ordered = sorted(set(rows))
    for i in range(0, len(ordered), EXTRACT_UPDATES_PER_WRITE):
        body = {"valueInputOption": "RAW",
                "data": [{"range": f"'{quoted}'!H{row}", "values": [[""]]} for row in ordered[i:i + EXTRACT_UPDATES_PER_WRITE]]}
        sheets_call(sess, "POST", f"{SHEETS_BASE}/{sid}/values:batchUpdate", json=body)
