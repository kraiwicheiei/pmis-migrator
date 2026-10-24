"""
DB 를 큐에 맞추기 테스트 (AI 없는 부분): 옛 시트에서 온 행이 큐에서 사라지면 삭제(보관 후), 온 적 없는 행은 보존, C/P 와 Folder Link 갱신, "Legacy sync" 표시 Synced / Outdated, 안전장치, 바뀐 PDF 의 행 교체 범위
[TH] เทสต์การทำให้ DB ตรงกับคิว (ส่วนที่ไม่ใช้ AI): ลบแถวที่มาจากชีตเก่าแล้ว PDF หายไปจากคิว (เก็บสำรองก่อน), เก็บแถวที่ไม่เคยมาจากชีตเก่า, อัปเดต C/P และ Folder Link, ป้าย "Legacy sync" Synced / Outdated, ตัวกันพลาด, ช่วงที่ใช้แทนที่แถวของ PDF ที่เปลี่ยน
[EN] Tests for making the DB match the queue (the part without the AI): removing the rows that came from the legacy sheet and whose PDF left the queue (archived first), keeping the rows that never did,
     updating C/P and Folder Link, the "Legacy sync" tag Synced / Outdated, the safety net, and the ranges that replace the row of a changed PDF
"""
import pytest

from migrator import db_sync
from migrator.reports import build_db_row
from migrator.db_sync import (MIGRATED_STATUS, archive_tab_name, db_status_col, delete_rows, plan_db_sync, replace_db_rows, replacement_ranges, run_db_sync,
                              sync_db_for_queue)
from migrator.sheets import SheetsError
from migrator.specs import DB_CP_HEADER, SPECS
from migrator.utils import removal_limit

from tests.db_fakes import DWR, db_row, db_world, qrow8
from tests.fakes import DWR_JSON, FID, FOLDER, URL, count_rows, ns

DRQ = SPECS["drq"]


def sync(w, queue_rows, **over):
    logs = []
    summary = sync_db_for_queue(ns(**over), w.clients(), "dwr", queue_rows, log=logs.append)
    return summary, logs


# --- 계획 / the plan / แผน ---
def test_plan_removes_only_the_rows_that_were_synced_and_whose_pdf_is_gone_and_keeps_the_rest():
    queue = [qrow8(1, "C"), qrow8(2, "P")]
    db = [db_row(1, "C"), db_row(2, "C"), db_row(3, tag="Synced"), db_row(4, status="Manual"), db_row(6)]
    plan = plan_db_sync(queue, [["h"] * 20] + db, DWR)
    assert plan.removals.keys() == {4} and "is not in MIGRATION_QUEUE any more" in plan.removals[4]        # DB row 4 = db_row(3): it was synced and its PDF 3 is not in the queue
    assert plan.protected == 1 and plan.cp_updates == {3: "P"} and plan.rows == 5                           # db_row(4) is not an 'AI Synced' row: protected; db_row(2) has C but the queue says P
    assert plan.unsynced == 1 and 6 not in plan.removals and 6 not in plan.tag_updates                      # db_row(6) never came from the legacy sheet (no tag): kept, still untagged


def test_plan_keeps_a_pdf_that_waits_to_be_replaced_and_every_row_of_a_shared_pdf():
    queue = [qrow8(1, "C", "Waiting", replaces=FID(9)), qrow8(2, "C")]
    db = [db_row(9), db_row(2), db_row(2)]                                           # FID(9) is the old PDF of row 1; FID(2) has two DB rows
    plan = plan_db_sync(queue, [["h"] * 20] + db, DWR)
    assert plan.removals == {} and plan.pending == 1 and plan.cp_updates == {3: "C", 4: "C"}
    assert plan.tag_updates == {2: "Outdated", 3: "Synced", 4: "Synced"} and (plan.synced, plan.outdated) == (2, 1)         # the old version waits for the AI: Outdated; the shared PDF: both rows Synced


def test_plan_never_guesses_a_conflicting_cp_never_clears_a_value_and_leaves_rows_without_a_pdf_alone():
    queue = [qrow8(1, "C"), qrow8(1, "P"), qrow8(2, "")]
    db = [db_row(1), db_row(2, "P"), db_row(5, pdf="")]
    plan = plan_db_sync(queue, [["h"] * 20] + db, DWR)
    assert plan.ambiguous == 1 and plan.cp_updates == {} and plan.no_pdf == 1 and plan.removals == {}      # an empty C/P in the queue does not clear the DB's "P"
    assert plan.tag_updates == {2: "Outdated", 3: "Synced"}                                                # C and P for the same PDF cannot be decided: Outdated, not Synced


def test_plan_uses_the_columns_of_each_db():
    row = [""] * 19
    row[DRQ.db_pdf_col], row[db_status_col(DRQ)] = URL(1), MIGRATED_STATUS
    plan = plan_db_sync([qrow8(1, "P")], [["h"] * 19, row], DRQ)
    assert plan.cp_updates == {2: "P"} and db_status_col(DRQ) == 13 and db_status_col(DWR) == 16
    assert plan.folder_updates == {2: FOLDER}                                                              # DB_DRQ's Folder Link is column P (index 15), empty here


# --- 적용 / applying / การนำไปใช้ ---
def test_sync_updates_cp_removes_the_orphans_and_archives_them_first():
    queue = [qrow8(1, "C"), qrow8(2, "P")]
    w = db_world(queue, [db_row(1), db_row(2), db_row(7, tag="Synced"), db_row(8, tag="Synced")])
    summary, logs = sync(w, queue)
    db = w.tabs["DB_DWR"]
    assert [r[1] for r in db[1:]] == ["DWR-1", "DWR-2"] and [r[19] for r in db[1:]] == ["C", "P"]                  # orphans gone, C/P filled in
    assert db[0][19] == DB_CP_HEADER and (summary.cp_updated, summary.removed) == (2, 2)
    archive = w.tabs[archive_tab_name(DWR)]
    assert archive_tab_name(DWR) == "_REMOVED_DB_DWR" and archive[0][:4] == ["Removed at", "From tab", "Row (before the removal)", "Reason"]
    assert [(r[1], r[2], r[5]) for r in archive[1:]] == [("DB_DWR", 4, "DWR-7"), ("DB_DWR", 5, "DWR-8")]       # copied before the delete, with the original row numbers
    assert any("Updated C/P in DB_DWR!T: 2 rows" in m for m in logs) and any("Removed 2 rows from DB_DWR (copied to _REMOVED_DB_DWR first)" in m for m in logs)
    order = [c[1].rsplit("/", 1)[-1] for c in w.calls if c[0] == "POST" and "batchUpdate" in c[1]]
    assert order.index("values:batchUpdate") < len(order) - 1                         # the C/P update, then the archive tab, then the delete


def test_sync_is_idempotent_and_reports_when_nothing_is_left_to_do():
    queue = [qrow8(1, "C")]
    w = db_world(queue, [db_row(1), db_row(3)])
    sync(w, queue)
    before = [list(r) for r in w.tabs["DB_DWR"]]
    calls = len(w.calls)
    summary, logs = sync(w, queue)
    assert w.tabs["DB_DWR"] == before and (summary.cp_updated, summary.removed) == (0, 0)
    assert any("DB_DWR already matches the queue" in m for m in logs)
    assert not any(c[0] != "GET" for c in w.calls[calls:])                           # only reads the second time


def test_sync_dry_run_reports_and_writes_nothing():
    queue = [qrow8(1, "C")]
    w = db_world(queue, [db_row(1), db_row(3, tag="Synced")])
    before = [list(r) for r in w.tabs["DB_DWR"]]
    summary, logs = sync(w, queue, dry_run=True)
    assert w.tabs["DB_DWR"] == before and archive_tab_name(DWR) not in w.tabs and all(c[0] == "GET" for c in w.calls)
    assert (summary.cp_updated, summary.removed) == (0, 0) and summary.plan.removals.keys() == {3}
    assert any("(--dry-run) nothing was written to DB_DWR: a real run would do the counts above" in m for m in logs)
    assert count_rows(logs) == {"In sync with the queue": "1", "C/P to update": "1", "Folder Link to update": "0", '"Legacy sync" tag': "set Synced 1, set Outdated 0, clear 0",
                                "Waiting for the AI (Outdated)": "0  (their PDF changed; the AI part replaces them)",
                                "To remove": "1  (came from the legacy sheet, PDF not in the queue any more)", "Kept as they are": "0"}
    assert any("sample (remove): DB row 3: DWR-3" in m for m in logs)


def test_sync_never_removes_a_row_that_is_not_an_ai_migrated_row_and_keeps_pending_rows():
    queue = [qrow8(1, "C", "Waiting", replaces=FID(5))]
    w = db_world(queue, [db_row(1), db_row(5), db_row(6, status="Typed by a person")])
    summary, _ = sync(w, queue)
    assert [r[1] for r in w.tabs["DB_DWR"][1:]] == ["DWR-1", "DWR-5", "DWR-6"] and summary.removed == 0 and summary.plan.protected == 1


# --- "Legacy sync" 표시 / the "Legacy sync" tag / ป้าย "Legacy sync" ---
def tags(w):
    """DB_DWR 의 모든 데이터 행의 표시 (U열) / [TH] ป้าย (คอลัมน์ U) ของทุกแถวข้อมูลใน DB_DWR / [EN] The tag (column U) of every data row of DB_DWR"""
    return [r[20] if len(r) > 20 else "" for r in w.tabs["DB_DWR"][1:]]


def test_plan_tags_the_rows_that_are_in_the_queue_and_never_the_orphans():
    queue = [qrow8(1, "C"), qrow8(2, "C", "Waiting", replaces=FID(5))]
    db = [db_row(1), db_row(5), db_row(7, tag="Synced"), db_row(8, status="Typed by a person"), db_row(9, pdf=""), db_row(10)]
    plan = plan_db_sync(queue, [["h"] * 21] + db, DWR)
    assert plan.tag_updates == {2: "Synced", 3: "Outdated"}                                  # DB row 2 (PDF 1, in the queue) and the old version that waits for the AI; the orphan (7) is about to go, the others must stay empty
    assert plan.removals.keys() == {4} and plan.pending == 1 and plan.protected == 1 and plan.no_pdf == 1 and plan.synced == 1 and plan.unsynced == 1 and plan.outdated == 1


def test_plan_clears_a_tag_that_is_no_longer_true_and_leaves_a_correct_tag_alone():
    queue = [qrow8(1, "C"), qrow8(2, "C", "Waiting", replaces=FID(5))]
    db = [db_row(1, tag="Synced"), db_row(5, tag="Synced"), db_row(8, status="Typed by a person", tag="Synced"), db_row(9, pdf="", tag="Synced")]
    plan = plan_db_sync(queue, [["h"] * 21] + db, DWR)
    assert plan.tag_updates == {3: "Outdated", 4: "", 5: ""}                                 # the old version that waits for the AI is Outdated; the protected row and the row without a PDF lose the tag
    assert 2 not in plan.tag_updates                                                         # a correct tag is not written again


def test_plan_tags_the_drq_rows_in_column_t():
    row = [""] * 20
    row[DRQ.db_pdf_col], row[db_status_col(DRQ)] = URL(1), MIGRATED_STATUS
    plan = plan_db_sync([qrow8(1, "P")], [["h"] * 20, row], DRQ)
    assert plan.tag_updates == {2: "Synced"} and DRQ.db_sync_col == 19


def test_sync_tags_the_matching_rows_and_leaves_the_orphans_untagged():
    queue = [qrow8(1, "C"), qrow8(2, "P")]
    w = db_world(queue, [db_row(1), db_row(2), db_row(7, status="Typed by a person"), db_row(8, tag="Synced")])
    summary, logs = sync(w, queue)
    assert tags(w) == ["Synced", "Synced", ""]                                               # DWR-8 (an orphan) was removed; the person's row stays untagged
    assert [r[1] for r in w.tabs["DB_DWR"][1:]] == ["DWR-1", "DWR-2", "DWR-7"]
    assert w.tabs["DB_DWR"][0][20] == "Legacy sync" and (summary.tagged, summary.untagged, summary.removed) == (2, 0, 1)
    assert any("Wrote the header 'Legacy sync' to DB_DWR!U1" in m for m in logs) and any("Legacy sync tag in DB_DWR!U: Synced on 2 rows, Outdated on 0 rows, cleared on 0 rows" in m for m in logs)


def test_a_row_that_waits_for_the_ai_is_outdated_and_is_synced_again_once_the_new_file_is_in():
    queue = [qrow8(1, "C", "Waiting", replaces=FID(5))]
    w = db_world(queue, [db_row(5, tag="Synced"), db_row(6)])
    summary, logs = sync(w, queue)
    assert tags(w) == ["Outdated", ""]                                                       # the old version is kept but it no longer matches; the row that never came from the legacy sheet stays untagged
    assert (summary.outdated, summary.tagged, summary.removed) == (1, 0, 0) and count_rows(logs)["Waiting for the AI (Outdated)"].startswith("1")
    assert any("Legacy sync tag in DB_DWR!U: Synced on 0 rows, Outdated on 1 rows, cleared on 0 rows" in m for m in logs)
    w.tabs["DB_DWR"][1][17], w.tabs["DB_DWR"][1][20] = URL(1), "Synced"                       # the AI part replaced it in place: the new PDF, written with the Synced tag
    sync(w, [qrow8(1, "C", "Done")])
    assert tags(w) == ["Synced", ""] and [r[1] for r in w.tabs["DB_DWR"][1:]] == ["DWR-5", "DWR-6"]
    # the item leaves the legacy sheet before the AI replaced the old version: the old version goes too (Outdated still means "came from the legacy sheet")
    w2 = db_world([qrow8(2, "C")], [db_row(5, tag="Outdated"), db_row(2)])
    summary, _ = sync(w2, [qrow8(2, "C")])
    assert summary.removed == 1 and [r[1] for r in w2.tabs["DB_DWR"][1:]] == ["DWR-2"]


def test_a_row_that_never_came_from_the_legacy_sheet_is_kept_and_stays_untagged():
    queue = [qrow8(1, "C")]
    w = db_world(queue, [db_row(1), db_row(40), db_row(41)])                                 # DWR-40 / DWR-41: documents another system added, they are not in the legacy sheet
    summary, logs = sync(w, queue)
    assert [r[1] for r in w.tabs["DB_DWR"][1:]] == ["DWR-1", "DWR-40", "DWR-41"] and tags(w) == ["Synced", "", ""]
    assert (summary.removed, summary.plan.unsynced) == (0, 2) and archive_tab_name(DWR) not in w.tabs
    assert count_rows(logs)["Kept as they are"] == "2" and count_rows(logs)["never synced with the legacy sheet"] == "2"
    summary, _ = sync(w, queue)                                                              # and it stays that way on every later run
    assert summary.removed == 0 and len(w.tabs["DB_DWR"]) == 4


def test_the_tag_is_written_once_and_a_second_sync_has_nothing_to_do():
    queue = [qrow8(1, "C")]
    w = db_world(queue, [db_row(1)])
    sync(w, queue)
    calls = len(w.calls)
    summary, logs = sync(w, queue)
    assert (summary.tagged, summary.untagged, summary.cp_updated, summary.removed) == (0, 0, 0, 0) and any("already matches the queue" in m for m in logs)
    assert not any(c[0] != "GET" for c in w.calls[calls:])


def test_a_header_conflict_in_the_tag_column_stops_before_anything_is_written():
    queue = [qrow8(1, "C")]
    header = [f"h{i}" for i in range(19)] + ["Status (C/P)", "Remarks"]                      # U1 already holds another column
    w = db_world(queue, [db_row(1), db_row(3)], db_header=header)
    before = [list(r) for r in w.tabs["DB_DWR"]]
    with pytest.raises(SheetsError) as e:
        sync(w, queue)
    assert "DB_DWR!U1 already has the header 'Remarks'" in str(e.value) and "legacy sync tag" in str(e.value)
    assert w.tabs["DB_DWR"] == before and not any(c[0] != "GET" for c in w.calls)             # no tag, no C/P, no removal, no archive


def test_nothing_is_tagged_when_the_safety_net_refuses_the_sync():
    queue = [qrow8(1, "C")]
    w = db_world(queue, [db_row(1)] + [db_row(n, tag="Synced") for n in range(1000, 1150)])
    sync(w, queue)
    assert tags(w) == [""] + ["Synced"] * 150                                                # refused: not even the tag of row 1 was written


def test_an_orphan_that_still_carries_a_tag_is_removed_and_archived_with_it():
    queue = [qrow8(1, "C")]
    w = db_world(queue, [db_row(1, tag="Synced"), db_row(3, tag="Synced")])                   # DWR-3 is an orphan that still carries a tag
    summary, _ = sync(w, queue)
    assert [r[1] for r in w.tabs["DB_DWR"][1:]] == ["DWR-1"] and tags(w) == ["Synced"] and summary.untagged == 0            # it is removed (archived), not just untagged
    archive = w.tabs[archive_tab_name(DWR)]
    assert archive[1][5] == "DWR-3" and len(archive[1]) == 4 + 21 and archive[1][4 + 20] == "Synced"                         # the archive keeps the whole row, tag included


# --- Folder Link / the Folder Link / Folder Link ---
OLD_FOLDER = "https://drive.google.com/drive/folders/OLDFOLDERIDxxxxxxxxxxxxxxxxxxxxxxx"


def with_folder(row, folder, col=18):
    row = list(row)
    row[col] = folder
    return row


def queue_with_folder(n, folder, cp="C", **kw):
    row = qrow8(n, cp, **kw)
    row[4] = folder
    return row


def test_a_changed_folder_link_is_updated_like_the_cp():
    queue = [queue_with_folder(1, FOLDER), queue_with_folder(2, FOLDER)]
    w = db_world(queue, [with_folder(db_row(1), OLD_FOLDER), db_row(2)])
    plan = plan_db_sync(queue, w.tabs["DB_DWR"], DWR)
    assert plan.folder_updates == {2: FOLDER} and plan.cp_updates == {2: "C", 3: "C"}                       # only row 2 has another folder; both rows have no C/P yet
    summary, logs = sync(w, queue)
    assert [r[18] for r in w.tabs["DB_DWR"][1:]] == [FOLDER, FOLDER] and summary.folder_updated == 1
    assert any("Updated Folder Link in DB_DWR!S: 1 rows" in m for m in logs) and count_rows(logs)["Folder Link to update"] == "1"
    assert "USER_ENTERED" in w.inputs and tags(w) == ["Synced", "Synced"]                                    # written like the rows the AI part appends; both rows match the queue now
    assert sync(w, queue)[0].folder_updated == 0                                                            # and a second run has nothing to do


def test_the_same_folder_in_another_link_format_is_not_a_change_and_an_empty_one_never_clears():
    same_id = FOLDER + "?usp=sharing"
    queue = [queue_with_folder(1, same_id), queue_with_folder(2, "")]
    db = [db_row(1), db_row(2)]
    plan = plan_db_sync(queue, [["h"] * 21] + db, DWR)
    assert plan.folder_updates == {} and plan.tag_updates == {2: "Synced", 3: "Synced"}                      # same folder ID: nothing to write; no folder in the queue: the DB value stays


def test_a_folder_that_contradicts_itself_in_the_queue_is_not_guessed_and_the_row_is_outdated():
    queue = [queue_with_folder(1, FOLDER), queue_with_folder(1, OLD_FOLDER)]                                # the same PDF, two folders
    plan = plan_db_sync(queue, [["h"] * 21, with_folder(db_row(1, "C"), "https://drive.google.com/drive/folders/OTHERFOLDERIDxxxxxxxxxxxxxxxxxxxxx")], DWR)
    assert plan.folder_updates == {} and plan.ambiguous == 1 and plan.tag_updates == {2: "Outdated"} and (plan.synced, plan.outdated) == (0, 1)


def test_a_pdf_used_by_several_items_is_fine_when_the_db_has_one_of_their_values():
    queue = [queue_with_folder(1, FOLDER, "C"), queue_with_folder(1, OLD_FOLDER, "P")]                      # the same file is used by two items: two folders, C and P
    plan = plan_db_sync(queue, [["h"] * 21, db_row(1, "P")], DWR)                                           # the DB row has the folder of the first item and the C/P of the second
    assert plan.tag_updates == {2: "Synced"} and plan.ambiguous == 0 and plan.cp_updates == {} and plan.folder_updates == {} and (plan.synced, plan.outdated) == (1, 0)
    other = plan_db_sync(queue, [["h"] * 21, with_folder(db_row(1, "X"), "https://drive.google.com/drive/folders/THIRDFOLDERIDxxxxxxxxxxxxxxxxxxxxx")], DWR)
    assert other.ambiguous == 1 and other.tag_updates == {2: "Outdated"} and other.cp_updates == {} and other.folder_updates == {}     # a value that is none of them cannot be decided: not guessed


def test_the_folder_column_of_each_db_is_its_own():
    row = [""] * 20
    row[DRQ.db_pdf_col], row[db_status_col(DRQ)], row[DRQ.db_folder_col] = URL(1), MIGRATED_STATUS, OLD_FOLDER
    row[18] = FOLDER                                                                                        # the C/P column of DB_DRQ: never read as the folder
    plan = plan_db_sync([queue_with_folder(1, FOLDER, "P")], [["h"] * 20, row], DRQ)
    assert DRQ.db_folder_col == 15 and DWR.db_folder_col == 18 and plan.folder_updates == {2: FOLDER} and plan.cp_updates == {2: "P"}


def test_an_outdated_row_that_left_the_legacy_sheet_is_removed_and_one_that_was_never_tagged_is_kept():
    queue = [qrow8(1, "C")]
    w = db_world(queue, [db_row(1, "C", tag="Synced"), db_row(3, tag="Outdated"), db_row(4)])
    summary, _ = sync(w, queue)
    assert [r[1] for r in w.tabs["DB_DWR"][1:]] == ["DWR-1", "DWR-4"] and summary.removed == 1 and summary.plan.unsynced == 1


# --- 안전장치 / the safety net / ตัวกันพลาด ---
def test_removal_limit_is_the_larger_of_ten_percent_and_one_hundred():
    assert (removal_limit(0), removal_limit(500), removal_limit(1000), removal_limit(2707), removal_limit(5000)) == (100, 100, 100, 271, 500)


def test_sync_refuses_to_remove_more_than_the_limit_unless_it_is_allowed():
    queue = [qrow8(1, "C")]
    w = db_world(queue, [db_row(1)] + [db_row(n, tag="Synced") for n in range(1000, 1150)])           # 150 orphans > the limit of 100
    before = [list(r) for r in w.tabs["DB_DWR"]]
    summary, logs = sync(w, queue)
    assert summary.refused == "too many removals" and w.tabs["DB_DWR"] == before and all(c[0] == "GET" for c in w.calls)      # nothing at all is written, not even the C/P
    assert any("Not syncing DB_DWR: 150 rows would be removed, more than the limit of 100" in m and "--allow-large-removal" in m for m in logs)
    summary, _ = sync(w, queue, allow_large_removal=True)
    assert summary.removed == 150 and len(w.tabs["DB_DWR"]) == 2 and len(w.tabs[archive_tab_name(DWR)]) == 151


def test_sync_refuses_when_the_queue_is_empty():
    w = db_world([], [db_row(1), db_row(2)])
    before = [list(r) for r in w.tabs["DB_DWR"]]
    summary, logs = sync(w, [])
    assert summary.refused == "empty queue" and w.tabs["DB_DWR"] == before
    assert any("Not syncing DB_DWR: the queue has no rows" in m for m in logs)


def test_delete_rows_groups_consecutive_rows_and_deletes_from_the_bottom_up():
    w = db_world([qrow8(1)], [db_row(n) for n in range(1, 8)])                          # sheet rows 2..8
    delete_rows(w.clients().sheets, "SID", w.ids["DB_DWR"], [3, 4, 5, 8])
    requests = [r["deleteDimension"]["range"] for c in w.calls if c[0] == "POST" and c[2] for r in c[2].get("requests", [])]
    assert [(r["startIndex"], r["endIndex"]) for r in requests] == [(7, 8), (2, 5)]  # rows 8 first, then 3-5 (0-based, end exclusive)
    assert [r[1] for r in w.tabs["DB_DWR"][1:]] == ["DWR-1", "DWR-5", "DWR-6"]


def test_delete_rows_sends_the_requests_in_batches(monkeypatch):
    monkeypatch.setattr(db_sync, "DELETES_PER_REQUEST", 2)
    w = db_world([qrow8(1)], [db_row(n) for n in range(1, 10)])
    delete_rows(w.clients().sheets, "SID", w.ids["DB_DWR"], [2, 4, 6, 8])
    batches = [len(c[2]["requests"]) for c in w.calls if c[0] == "POST" and c[2] and "requests" in c[2]]
    assert batches == [2, 2] and [r[1] for r in w.tabs["DB_DWR"][1:]] == ["DWR-2", "DWR-4", "DWR-6", "DWR-8", "DWR-9"]      # sheet rows 2, 4, 6, 8 held DWR-1, 3, 5, 7


def test_a_failure_while_archiving_leaves_the_db_rows_in_place():
    queue = [qrow8(1, "C")]
    w = db_world(queue, [db_row(1, "C"), db_row(3, tag="Synced")])
    w.fail_on = "append"                                                             # the archive rows cannot be written
    with pytest.raises(SheetsError):
        sync(w, queue)
    assert [r[1] for r in w.tabs["DB_DWR"][1:]] == ["DWR-1", "DWR-3"]                # nothing was deleted without a copy


def test_sync_stops_without_writing_when_a_row_was_inserted_after_the_plan(monkeypatch):
    queue = [qrow8(1, "C")]
    w = db_world(queue, [db_row(1), db_row(3)])
    real_plan = db_sync.plan_db_sync

    def plan_then_insert(*args, **kwargs):
        plan = real_plan(*args, **kwargs)
        w.tabs["DB_DWR"].insert(1, db_row(99))                                       # somebody inserts a row at the top: every planned row number is off by one
        return plan

    monkeypatch.setattr(db_sync, "plan_db_sync", plan_then_insert)
    with pytest.raises(SheetsError) as e:
        sync(w, queue)
    assert "changed while it was being synced" in str(e.value) and "run it again" in str(e.value)
    assert [r[1] for r in w.tabs["DB_DWR"][1:]] == ["DWR-99", "DWR-1", "DWR-3"] and not any(c[0] != "GET" for c in w.calls)      # not a single write


def test_the_rows_are_checked_again_right_before_the_delete(monkeypatch):
    queue = [qrow8(1, "C")]
    w = db_world(queue, [db_row(1), db_row(3, tag="Synced")])
    real = db_sync.list_tabs

    def list_then_insert(sess, sid):
        tabs = real(sess, sid)
        w.tabs["DB_DWR"].insert(1, db_row(99))                                       # inserted after the C/P update but before the delete
        return tabs

    monkeypatch.setattr(db_sync, "list_tabs", list_then_insert)
    with pytest.raises(SheetsError):
        sync(w, queue)
    assert [r[1] for r in w.tabs["DB_DWR"][1:]] == ["DWR-99", "DWR-1", "DWR-3"] and archive_tab_name(DWR) not in w.tabs        # nothing was deleted, nothing was archived


# --- 단독 실행 / standalone / การรันเดี่ยว ---
def test_run_db_sync_reads_the_queue_sheet_and_returns_the_exit_code(monkeypatch):
    queue = [qrow8(1, "C"), qrow8(2, "P", "Waiting", replaces=FID(9))]
    w = db_world(queue, [db_row(1), db_row(9), db_row(7, tag="Synced")])
    logs = []
    code = run_db_sync(ns(queue=None), w.clients(), ["dwr"], log=logs.append, sheet_title="Example Spreadsheet")
    assert code == 0 and [r[1] for r in w.tabs["DB_DWR"][1:]] == ["DWR-1", "DWR-9"]   # DWR-7 is gone; DWR-9 waits for its replacement
    assert any("===== Sync DB DWR: DB_DWR <- MIGRATION_QUEUE | sheet: Example Spreadsheet =====" in m for m in logs)
    big = db_world([qrow8(1)], [db_row(1)] + [db_row(n, tag="Synced") for n in range(1000, 1150)])
    assert run_db_sync(ns(queue=None), big.clients(), ["dwr"], log=lambda m: None) == 2                      # refused by the safety net
    assert run_db_sync(ns(queue=None, dry_run=True), big.clients(), ["dwr"], log=lambda m: None) == 0       # a dry run never fails because of it
    broken = db_world([qrow8(1)], [db_row(1)])
    broken.fail_on = "values/"
    assert run_db_sync(ns(queue=None), broken.clients(), ["dwr"], log=lambda m: None) == 2


def test_run_db_sync_uses_the_planned_rows_of_a_queue_instead_of_reading_its_sheet():
    w = db_world([qrow8(1, "C")], [db_row(1), db_row(2, tag="Synced")])                     # the stored queue has only PDF 1, so PDF 2 would be removed
    logs = []
    assert run_db_sync(ns(dry_run=True), w.clients(), ["dwr"], log=logs.append) == 0
    assert count_rows(logs)["To remove"].startswith("1")
    reads = len(w.calls)
    logs = []
    assert run_db_sync(ns(dry_run=True), w.clients(), ["dwr"], log=logs.append, planned={"dwr": [qrow8(1, "C"), qrow8(2, "P")]}) == 0     # the plan keeps PDF 2 as well
    assert count_rows(logs)["To remove"].startswith("0") and count_rows(logs)["C/P to update"] == "2"
    assert not any("MIGRATION_QUEUE" in str(c[1]) for c in w.calls[reads:])                  # the queue sheet was not read this time
    other = db_world([qrow8(1, "C")], [db_row(1)])
    run_db_sync(ns(dry_run=True), other.clients(), ["dwr"], log=lambda m: None, planned={})   # an empty plan dict changes nothing: the sheet is read
    assert any("MIGRATION_QUEUE" in str(c[1]) for c in other.calls)


# --- 제자리 교체 범위 / the replace-in-place ranges / ช่วงสำหรับแทนที่ในที่เดิม ---
def test_replacement_leaves_column_a_and_the_empty_constant_columns_alone():
    dwr = replacement_ranges(DWR, 7, list(range(21)))
    assert [r["range"] for r in dwr] == ["'DB_DWR'!B7:U7"] and dwr[0]["values"] == [list(range(1, 21))]            # A (CSC Ref. No) is skipped; T = C/P, U = the tag
    drq = replacement_ranges(DRQ, 7, list(range(20)))
    assert [r["range"] for r in drq] == ["'DB_DRQ'!B7:P7", "'DB_DRQ'!S7:T7"]                                         # Q, R (Ext1, Ext2) are skipped; S = C/P, T = the tag
    assert drq[0]["values"] == [list(range(1, 16))] and drq[1]["values"] == [[18, 19]]


def test_replace_db_rows_overwrites_in_place_with_the_same_input_mode_as_an_append(monkeypatch):
    w = db_world([qrow8(1)], [db_row(1), db_row(2)])
    w.tabs["DB_DWR"][1][0] = "CSC-REF-1"                                             # a value a person typed in column A
    new = build_db_row(DWR, {**DWR_JSON, "DWR_No": "NEW-1"}, URL(11), FOLDER, "P")
    replace_db_rows(w.clients().sheets, "SID", DWR, [(2, new)])
    row = w.tabs["DB_DWR"][1]
    assert row[0] == "CSC-REF-1" and row[1] == "NEW-1" and row[17] == URL(11) and row[19] == "P" and row[20] == "Synced"
    assert w.inputs == ["USER_ENTERED"] and w.tabs["DB_DWR"][2][1] == "DWR-2"        # the other rows are untouched
    monkeypatch.setattr(db_sync, "DB_CELLS_PER_WRITE", 1)
    w2 = db_world([qrow8(1)], [db_row(1)])
    replace_db_rows(w2.clients().sheets, "SID", DWR, [(2, new)])
    assert w2.inputs == ["USER_ENTERED"]


# --- Status 이름 변경 / the Status rename / การเปลี่ยนชื่อ Status ---
def test_the_status_value_is_ai_synced_and_the_old_name_still_counts_as_written_by_this_tool():
    assert MIGRATED_STATUS == "AI Synced" and db_sync.MIGRATED_STATUSES == ("AI Synced", "AI Migrated")
    queue = [qrow8(1, "C"), qrow8(2, "C")]
    db = [db_row(1, "C", status="AI Migrated"), db_row(2, "C", status="AI Synced", tag="Synced"), db_row(3, status="AI Migrated", tag="Synced"), db_row(4, status="Typed by a person")]
    plan = plan_db_sync(queue, [["h"] * 21] + db, DWR)
    assert plan.status_updates == {2: "AI Synced"}                                          # the old name on a row that stays is renamed; db_row(3) is about to be removed, so it is not
    assert plan.removals.keys() == {4} and plan.protected == 1                              # the old name still lets the row be removed (it was synced); the person's row is kept


def test_sync_renames_the_old_status_in_place_logs_it_and_is_idempotent():
    queue = [qrow8(1, "C"), qrow8(2, "C")]
    w = db_world(queue, [db_row(1, "C", status="AI Migrated", tag="Synced"), db_row(2, "C", status="AI Synced", tag="Synced"), db_row(5, status="Typed by a person")])
    col = db_sync.db_status_col(DWR)
    summary, logs = sync(w, queue)
    assert [r[col] for r in w.tabs["DB_DWR"][1:]] == ["AI Synced", "AI Synced", "Typed by a person"]        # only the old-name row changed; the person's value is untouched
    assert summary.status_renamed == 1 and count_rows(logs)["Status to rename"].startswith("1")
    assert any("Renamed Status in DB_DWR!Q: 1 rows ('AI Migrated' -> 'AI Synced')" in m for m in logs)
    summary, logs = sync(w, queue)                                                         # a second run has nothing left to do
    assert summary.status_renamed == 0 and any("already matches the queue" in m for m in logs)


def test_a_dry_run_shows_the_rename_but_writes_nothing():
    queue = [qrow8(1, "C")]
    w = db_world(queue, [db_row(1, "C", status="AI Migrated", tag="Synced")])
    summary, logs = sync(w, queue, dry_run=True)
    assert w.tabs["DB_DWR"][1][db_sync.db_status_col(DWR)] == "AI Migrated" and summary.status_renamed == 0
    assert count_rows(logs)["Status to rename"].startswith("1")
