"""
옛 시트 셀 변환과 큐 항목 만들기 테스트 (--extract 의 읽기)
[TH] เทสต์การแปลงเซลล์ชีตเก่าและการสร้างรายการคิว (ฝั่งอ่านของ --extract)
[EN] Tests for the legacy cell conversion and the queue items (the reading half of --extract)
"""

from migrator.legacy_sheet import sheet_cell_link, sheet_cell_text
from migrator.specs import SPECS

from tests.fakes import URL
from tests.legacy_fakes import DRQ_ROWS, DWR_ROWS, build, linked, mk, num, serial, txt


# --- 셀 변환 / cell conversion / การแปลงเซลล์ ---
def test_sheet_cell_text_follows_apps_script_to_string_rules():
    t = sheet_cell_text
    assert t(txt("  abc ")) == "abc" and t(txt("")) == "" and t({}) == ""
    assert t(num(123.0)) == "123" and t(num(1.5)) == "1.5" and t(num(0)) == ""       # 0 은 거짓 -> 빈 값 / [TH] 0 เป็น falsy -> ว่าง / [EN] 0 is falsy -> empty
    assert t({"effectiveValue": {"boolValue": True}}) == "true" and t({"effectiveValue": {"boolValue": False}}) == ""
    d = serial(2025, 11, 28)
    assert t(num(d, "DATE")) == "2025-11-28" and t(num(d + 0.75, "DATE_TIME")) == "2025-11-28"
    assert t(num(d)) == str(d)                     # 날짜 서식이 아니면 숫자 그대로 / [TH] ไม่ใช่รูปแบบวันที่ก็เป็นตัวเลขตามเดิม / [EN] without a date format it stays a number
    assert t({"effectiveValue": {"errorValue": {"type": "REF"}}, "formattedValue": "#REF!"}) == "#REF!"


def test_sheet_cell_link_has_the_same_priority_as_getSafeLink():
    f = sheet_cell_link
    run = [{"startIndex": 1, "format": {"link": {"uri": "https://run"}}}]
    assert f({"hyperlink": "https://cell", "textFormatRuns": run, "userEnteredValue": {"formulaValue": '=HYPERLINK("https://f","x")'}}) == "https://cell"
    assert f({"textFormatRuns": [{"format": {}}] + run, "userEnteredValue": {"formulaValue": '=HYPERLINK("https://f","x")'}}) == "https://run"
    assert f({"userEnteredValue": {"formulaValue": '=hyperlink( "https://f/1" ; "x")'}}) == "https://f/1"
    assert f({"userEnteredValue": {"formulaValue": "=HYPERLINK('https://f/2','x')"}}) == "https://f/2"
    assert f({"userEnteredValue": {"formulaValue": "=HYPERLINK(A1,B1)"}}) == "" and f(txt("plain")) == "" and f({}) == ""


def test_build_queue_items_matches_the_apps_script_rules():
    rows, stats = build("dwr")
    assert rows == DWR_ROWS
    # 병합 1건, 헤더 없는 칸 1개, 링크 없는 항목 2개 / [TH] รวม 1, ช่องหัวว่าง 1, รายการไม่มีลิงก์ 2 / [EN] 1 merged, 1 cell with no header, 2 items without a link
    assert (stats.rows, stats.dated_rows, stats.merged, stats.no_discipline, stats.no_link) == (3, 2, 1, 1, 2)


def test_drq_scans_two_more_columns_than_dwr():
    assert SPECS["dwr"].legacy_end_col == 56 and SPECS["drq"].legacy_end_col == 58      # BD / BF
    assert build("drq")[0] == DRQ_ROWS


def test_duplicate_cells_merge_like_the_apps_script():
    def one(header, cells):
        grid = {3: mk(header), 4: mk({0: txt("R-001"), 1: txt("2025-11-28"), **cells})}
        rows, stats = build("dwr", grid)
        return rows, stats.merged

    pc = {0: txt("No."), 1: txt("Date"), 4: txt("CIV(P)"), 5: txt("CIV(C)")}
    cp = {0: txt("No."), 1: txt("Date"), 4: txt("CIV(C)"), 5: txt("CIV(P)")}
    # (P) 다음에 (C): (C) 의 링크가 있으면 덮어씀 / [TH] (P) แล้วตามด้วย (C): ถ้า (C) มีลิงก์จะเขียนทับ / [EN] (P) then (C): the (C) link overwrites when present
    rows, merged = one(pc, {4: linked(txt("001"), URL(1)), 5: linked(txt("001"), URL(2))})
    assert merged == 1 and len(rows) == 1 and rows[0][3] == URL(2) and rows[0][5] == "C"
    # (C) 에 링크가 없으면 기존 링크를 유지하고 완료만 표시 / [TH] (C) ไม่มีลิงก์ -> เก็บลิงก์เดิม แค่ทำเครื่องหมายเสร็จ / [EN] a (C) without a link keeps the old link and only marks it completed
    rows, _ = one(pc, {4: linked(txt("001"), URL(1)), 5: txt("001")})
    assert rows[0][3] == URL(1) and rows[0][5] == "C"
    # (C) 다음에 (P): (P) 는 아무것도 덮어쓰지 않음 / [TH] (C) แล้วตามด้วย (P): (P) ไม่เขียนทับอะไร / [EN] (C) then (P): the (P) overwrites nothing
    rows, merged = one(cp, {4: linked(txt("001"), URL(1)), 5: linked(txt("001"), URL(2))})
    assert merged == 1 and rows[0][3] == URL(1) and rows[0][5] == "C"
    # 값이 다르면 서로 다른 항목 / [TH] ค่าในช่องต่างกันคือคนละรายการ / [EN] different cell values are different items
    rows, merged = one(pc, {4: txt("001"), 5: txt("002")})
    assert merged == 0 and [r[0] for r in rows] == ["R-001-CIV-001", "R-001-CIV-002"]
