"""
옛 시트(셀 단위)를 읽어 큐 항목으로 바꾸기 (--extract 의 읽기 쪽)
[TH] อ่านชีตเก่า (ระดับเซลล์) แล้วแปลงเป็นรายการคิว (ฝั่งอ่านของ --extract)
[EN] Reading the legacy sheet (cell level) and turning it into queue items (the reading half of --extract)
"""
from __future__ import annotations

import datetime
import math
import re
from dataclasses import dataclass
from typing import Any

from .config import SHEETS_BASE
from .sheets import sheets_call
from .utils import col_letter


# 옛 시트의 구조: 3행 = 헤더, 4행부터 데이터. A열 = No., B열 = Date(+폴더 링크), E열부터 = 분야별 칸
# [TH] โครงสร้างชีตเก่า: แถว 3 = หัวตาราง ข้อมูลเริ่มแถว 4 คอลัมน์ A = No., B = Date (+ลิงก์โฟลเดอร์), ตั้งแต่ E = ช่องแต่ละ discipline
# [EN] Legacy sheet layout: row 3 = header, data from row 4. Column A = No., B = Date (+ folder link), from E = one cell per discipline
LEGACY_HEADER_ROW = 3

LEGACY_FIRST_DATA_ROW = 4

LEGACY_NO_COL = 0

LEGACY_DATE_COL = 1

LEGACY_FIRST_COL = 4

# 한 번에 읽는 행 수 / 한 번에 쓰는 행 수 (요청 하나가 너무 커지지 않도록)
# [TH] จำนวนแถวที่อ่านต่อครั้ง / เขียนต่อครั้ง (กัน request เดียวใหญ่เกินไป)
# [EN] Rows read per request / written per request (keeps a single request from getting too large)
EXTRACT_ROWS_PER_READ = 400

# Google Sheets 의 날짜 일련번호 기준일
# [TH] วันอ้างอิงของ serial date ใน Google Sheets
# [EN] Base date of Google Sheets date serial numbers
_SHEETS_EPOCH = datetime.date(1899, 12, 30)

# getSafeLink 와 같은 정규식
# [TH] regex เดียวกับ getSafeLink
# [EN] Same regex as getSafeLink
_HYPERLINK_FORMULA = re.compile(r"""HYPERLINK\(\s*["']([^"']+)["']""", re.IGNORECASE)

# 셀마다 필요한 필드만 요청
# [TH] ขอเฉพาะฟิลด์ของเซลล์ที่ต้องใช้
# [EN] Only the cell fields that are needed are requested
_GRID_FIELDS = (
    "sheets(data(startRow,rowData(values(effectiveValue,formattedValue,hyperlink,"
    "userEnteredValue(formulaValue),textFormatRuns(format(link(uri))),effectiveFormat(numberFormat(type))))))"
)


def sheet_cell_text(cell: dict) -> str:
    """
    Apps Script 의 `v ? v.toString().trim() : ""` 와 같은 규칙으로 셀 값을 문자열로 변환
    (0 / false / 빈 값 = 빈 문자열, 날짜 서식 셀 = yyyy-MM-dd, 정수는 소수점 없이)
    [TH] แปลงค่าเซลล์เป็นข้อความตามกฎเดียวกับ `v ? v.toString().trim() : ""` ของ Apps Script
         (0 / false / ว่าง = ข้อความว่าง, เซลล์รูปแบบวันที่ = yyyy-MM-dd, จำนวนเต็มไม่มีทศนิยม)
    [EN] Converts a cell value to text with the same rules as Apps Script's `v ? v.toString().trim() : ""`
         (0 / false / empty = empty string, date-formatted cell = yyyy-MM-dd, integers without a decimal point)
    """
    ev = cell.get("effectiveValue") or {}
    if "errorValue" in ev:
        return str(cell.get("formattedValue", "")).strip()
    if "stringValue" in ev:
        return str(ev["stringValue"]).strip()
    if "boolValue" in ev:
        return "true" if ev["boolValue"] else ""
    if "numberValue" in ev:
        num = float(ev["numberValue"])
        if num == 0:
            return ""
        fmt = ((cell.get("effectiveFormat") or {}).get("numberFormat") or {}).get("type", "")
        if fmt in ("DATE", "DATE_TIME"):
            return (_SHEETS_EPOCH + datetime.timedelta(days=math.floor(num))).isoformat()
        return str(int(num)) if num.is_integer() else repr(num)
    return ""


def sheet_cell_link(cell: dict) -> str:
    """
    getSafeLink 와 같은 우선순위: 셀 전체 링크 -> 일부 텍스트의 링크 -> HYPERLINK("...") 수식
    [TH] ลำดับเดียวกับ getSafeLink: ลิงก์ทั้งเซลล์ -> ลิงก์ของข้อความบางส่วน -> สูตร HYPERLINK("...")
    [EN] Same priority as getSafeLink: whole-cell link -> link on part of the text -> HYPERLINK("...") formula
    """
    url = cell.get("hyperlink")
    if url:
        return str(url)
    for run in cell.get("textFormatRuns") or []:
        uri = ((run.get("format") or {}).get("link") or {}).get("uri")
        if uri:
            return str(uri)
    formula = (cell.get("userEnteredValue") or {}).get("formulaValue") or ""
    m = _HYPERLINK_FORMULA.search(formula)
    return m.group(1) if m else ""


@dataclass
class LegacyItem:
    doc_no: str
    date: str
    discipline: str
    pdf_url: str
    folder_url: str
    completed: bool
    # 칸의 값 (Doc No = A열 + "-" + Discipline + "-" + 이 값). A열(No.)이 바뀌어도 (Date, Discipline, 이 값)은 같은 항목을 가리킴
    # [TH] ค่าในช่อง (Doc No = คอลัมน์ A + "-" + Discipline + "-" + ค่านี้) ถ้าคอลัมน์ A (No.) เปลี่ยน (Date, Discipline, ค่านี้) ก็ยังชี้รายการเดิม
    # [EN] The cell value (Doc No = column A + "-" + Discipline + "-" + this value). When column A (No.) changes, (Date, Discipline, this value) still identifies the same item
    key_text: str = ""

    def to_row(self) -> list:
        return [self.doc_no, self.date, self.discipline, self.pdf_url, self.folder_url, "C" if self.completed else "P", "Waiting"]


@dataclass
class ExtractStats:
    rows: int = 0          # 읽은 데이터 행 / [TH] แถวข้อมูลที่อ่าน / [EN] data rows read
    dated_rows: int = 0    # Date 가 있는 행 / [TH] แถวที่มี Date / [EN] rows that have a Date
    merged: int = 0        # 중복이라 합친 칸 / [TH] ช่องที่ซ้ำและรวมแล้ว / [EN] cells merged as duplicates
    no_discipline: int = 0  # 헤더가 비어 있어 건너뛴 칸 / [TH] ช่องที่ข้ามเพราะหัวคอลัมน์ว่าง / [EN] cells skipped because the column header is empty
    no_link: int = 0       # PDF 링크가 없는 항목 / [TH] รายการที่ไม่มีลิงก์ PDF / [EN] items without a PDF link


def build_queue_items(header_cells: list, data_rows: Any, end_col: int) -> tuple[list[LegacyItem], ExtractStats]:
    """
    extractLegacyDwrLinks / extractLegacyDrqLinks 의 본문과 같은 규칙:
      * Date(B열)가 비어 있는 행은 건너뜀
      * E열 ~ end_col 직전 열의 값이 있는 칸마다 한 항목. 헤더에서 (P)/(C) 를 떼어 낸 것이 Discipline, (C) 가 있으면 완료
      * 키 = Date_Discipline_칸 값. 같은 키가 또 나오면 새 항목을 만들지 않고, 그것이 (C) 이면 PDF 링크(있을 때)와 완료 표시를 덮어씀
      * Doc No = A열 + "-" + Discipline + "-" + 칸 값
    [TH] กฎเดียวกับตัวฟังก์ชัน extractLegacyDwrLinks / extractLegacyDrqLinks:
         * ข้ามแถวที่ Date (คอลัมน์ B) ว่าง
         * ทุกช่องที่มีค่าในคอลัมน์ E ถึงก่อน end_col = 1 รายการ Discipline = หัวคอลัมน์ที่ตัด (P)/(C) ออก, ถ้ามี (C) ถือว่าเสร็จ
         * คีย์ = Date_Discipline_ค่าในช่อง ถ้าคีย์ซ้ำจะไม่สร้างรายการใหม่ แต่ถ้าเป็น (C) จะเขียนทับลิงก์ PDF (ถ้ามี) และทำเครื่องหมายว่าเสร็จ
         * Doc No = คอลัมน์ A + "-" + Discipline + "-" + ค่าในช่อง
    [EN] Same rules as the bodies of extractLegacyDwrLinks / extractLegacyDrqLinks:
         * rows whose Date (column B) is empty are skipped
         * every non-empty cell from column E up to (not including) end_col is one item; Discipline = the header without (P)/(C), and a (C) means completed
         * key = Date_Discipline_cellValue; a repeated key does not create a new item, but if it is a (C) it overwrites the PDF link (when present) and marks it completed
         * Doc No = column A + "-" + Discipline + "-" + cell value
    data_rows: (행 번호, 셀 목록) 의 반복 가능 객체 / [TH] iterable ของ (เลขแถว, รายการเซลล์) / [EN] iterable of (row number, list of cells)
    """
    headers = [sheet_cell_text(c) for c in header_cells]
    stats = ExtractStats()
    queue: dict[str, LegacyItem] = {}
    empty: dict = {}
    for _row_no, cells in data_rows:
        stats.rows += 1
        date_cell = cells[LEGACY_DATE_COL] if len(cells) > LEGACY_DATE_COL else empty
        date_text = sheet_cell_text(date_cell)
        if not date_text:
            continue
        stats.dated_rows += 1
        folder_url = sheet_cell_link(date_cell)
        base_no = sheet_cell_text(cells[LEGACY_NO_COL]) if len(cells) > LEGACY_NO_COL else ""
        for j in range(LEGACY_FIRST_COL, min(len(cells), end_col)):
            text = sheet_cell_text(cells[j])
            if not text:
                continue
            pdf_url = sheet_cell_link(cells[j])
            header = headers[j] if j < len(headers) else ""
            completed = "(C)" in header
            discipline = header.replace("(P)", "").replace("(C)", "").strip()
            if not discipline:
                stats.no_discipline += 1
                continue
            key = f"{date_text}_{discipline}_{text}"
            item = queue.get(key)
            if item is None:
                queue[key] = LegacyItem(f"{base_no}-{discipline}-{text}", date_text, discipline, pdf_url, folder_url, completed, text)
            else:
                stats.merged += 1
                if completed:
                    if pdf_url:
                        item.pdf_url = pdf_url
                    item.completed = True
    items = list(queue.values())
    stats.no_link = sum(1 for it in items if not it.pdf_url)
    return items, stats


def read_legacy_rows(sess: Any, sid: str, tab: str, end_col: int, row_count: int) -> list[tuple[int, list]]:
    """
    옛 시트의 3행(헤더)부터 끝까지 셀(값/링크/서식)을 읽어 (행 번호, 셀 목록) 으로 반환. 큰 시트를 위해 여러 번에 나눠 읽음
    [TH] อ่านเซลล์ (ค่า/ลิงก์/รูปแบบ) ของชีตเก่าตั้งแต่แถว 3 (หัวตาราง) จนจบ คืนเป็น (เลขแถว, รายการเซลล์) แบ่งอ่านหลายรอบสำหรับชีตใหญ่
    [EN] Reads the legacy sheet's cells (value/link/format) from row 3 (the header) to the end and returns (row number, list of cells);
         read in several requests so a big sheet is fine
    """
    last_col = col_letter(end_col)
    quoted = tab.replace("'", "''")
    rows: list[tuple[int, list]] = []
    first = LEGACY_HEADER_ROW
    while first <= row_count:
        last = min(first + EXTRACT_ROWS_PER_READ - 1, row_count)
        data = sheets_call(sess, "GET", f"{SHEETS_BASE}/{sid}", params={
            "ranges": f"'{quoted}'!A{first}:{last_col}{last}", "includeGridData": "true", "fields": _GRID_FIELDS})
        for sheet in (data.get("sheets") or [])[:1]:
            for block in (sheet.get("data") or [])[:1]:
                start = int(block.get("startRow", 0))
                for k, row in enumerate(block.get("rowData") or []):
                    rows.append((start + k + 1, row.get("values") or []))
        first = last + 1
    return rows
