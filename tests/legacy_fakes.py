"""
--extract 테스트용 가짜 옛 시트와 도우미
[TH] ชีตเก่าจำลองและตัวช่วยสำหรับเทสต์ --extract
[EN] The fake legacy sheet and the helpers for the --extract tests
"""

import datetime
import re
import urllib.parse

from migrator import cli
from migrator.cli import main
from migrator.config import SHEETS_BASE
from migrator.extract import extract_queue
from migrator.legacy_sheet import build_queue_items
from migrator.models import Clients
from migrator.queue_writer import QUEUE_HEADER
from migrator.specs import SPECS

from tests.fakes import FOLDER, Resp, URL, ns


# 고정된 시각 (conftest.py 가 clock.NOW 로 사용) / [TH] เวลาคงที่ (conftest.py ใช้เป็น clock.NOW) / [EN] The fixed time (conftest.py uses it as clock.NOW)
FIXED_NOW = datetime.datetime(2026, 10, 22, 7, 5, 33, tzinfo=datetime.timezone.utc)
STAMP = "2026-10-22 14:05:33 (UTC+7)"


def serial(y, m, d):
    return (datetime.date(y, m, d) - datetime.date(1899, 12, 30)).days


def txt(s):
    return {"effectiveValue": {"stringValue": s}, "formattedValue": s}


def num(n, fmt=None):
    cell = {"effectiveValue": {"numberValue": n}, "formattedValue": str(n)}
    if fmt:
        cell["effectiveFormat"] = {"numberFormat": {"type": fmt}}
    return cell


def linked(cell, url):
    return {**cell, "hyperlink": url}


def mk(cells):
    """{열 index: 셀} -> 0열부터 빈 칸 {} 으로 채운 목록 / [TH] {index คอลัมน์: เซลล์} -> รายการที่เติม {} ตั้งแต่คอลัมน์ 0 / [EN] {column index: cell} -> list padded with {} from column 0"""
    return [cells.get(i, {}) for i in range(max(cells) + 1)] if cells else []


LEGACY_HEADER = mk({0: txt("No."), 1: txt("Date"), 4: txt("CIV(P)"), 5: txt("CIV(C)"), 6: txt("ELE(P)"),
                    7: txt("REM (DMS)(P)"), 8: txt(""), 56: txt("OTH(P)")})


def legacy_grid():
    return {
        3: LEGACY_HEADER,
        # E(P) 와 F(C) 가 같은 키 -> 한 항목으로 합쳐지고 (C) 의 링크가 이김. 5 는 숫자 칸, I 열은 헤더가 비어 건너뜀
        # [TH] E(P) กับ F(C) คีย์เดียวกัน -> รวมเป็นรายการเดียวและลิงก์ของ (C) ชนะ, 5 คือช่องตัวเลข, คอลัมน์ I หัวว่างจึงข้าม
        # [EN] E(P) and F(C) share a key -> merged into one item, the (C) link wins; 5 is a numeric cell; column I has an empty header so it is skipped
        4: mk({0: txt("R-001"), 1: linked(num(serial(2025, 11, 28), "DATE"), FOLDER), 4: linked(txt("001"), URL(1)),
               5: linked(txt("001"), URL(2)), 6: num(5.0), 7: txt("9"), 8: txt("junk")}),
        # Date 가 비어 있는 행은 건너뜀 / [TH] แถวที่ Date ว่างถูกข้าม / [EN] A row with an empty Date is skipped
        5: mk({0: txt("R-002"), 4: linked(txt("002"), URL(9))}),
        # BE열(index 56)은 DWR 범위(E~BD) 밖, DRQ 범위(E~BF) 안
        # [TH] คอลัมน์ BE (index 56) อยู่นอกช่วง DWR (E~BD) แต่อยู่ในช่วง DRQ (E~BF)
        # [EN] Column BE (index 56) is outside the DWR range (E~BD) but inside the DRQ range (E~BF)
        6: mk({0: txt("R-003"), 1: txt("2025-11-29"), 4: linked(txt("003"), URL(3)), 56: txt("OTH-1")}),
    }


DWR_ROWS = [
    ["R-001-CIV-001", "2025-11-28", "CIV", URL(2), FOLDER, "C", "Waiting"],
    ["R-001-ELE-5", "2025-11-28", "ELE", "", FOLDER, "P", "Waiting"],
    ["R-001-REM (DMS)-9", "2025-11-28", "REM (DMS)", "", FOLDER, "P", "Waiting"],
    ["R-003-CIV-003", "2025-11-29", "CIV", URL(3), "", "P", "Waiting"],
]

DRQ_ROWS = DWR_ROWS + [["R-003-OTH-OTH-1", "2025-11-29", "OTH", "", "", "P", "Waiting"]]

LEGACY_TABS = {"dwr": "LEGACY-DWR", "drq": "LEGACY-DRQ"}


class FakeLegacy:
    """
    옛 시트(셀 단위) + 큐 시트가 들어 있는 가짜 스프레드시트
    [TH] สเปรดชีตจำลองที่มีชีตเก่า (ระดับเซลล์) + ชีตคิว
    [EN] Fake spreadsheet holding the legacy sheets (cell level) + the queue sheets
    """

    def __init__(self, grids=None, queues=None, row_count=12, legacy_sid=None):
        self.grids = grids if grids is not None else {"LEGACY-DWR": legacy_grid(), "LEGACY-DRQ": legacy_grid()}
        self.queues = queues if queues is not None else {}
        # 값이 있으면 옛 탭은 그 ID 의 별도 스프레드시트에만, 큐는 SID 에만 있음
        # [TH] ถ้ามีค่า แท็บเก่าจะอยู่เฉพาะในสเปรดชีตแยก ID นั้น ส่วนคิวอยู่ที่ SID เท่านั้น
        # [EN] When set, the legacy tabs live only in that separate spreadsheet and the queues only in SID
        self.legacy_sid = legacy_sid
        self.sheets_calls = []
        # 탭 -> [라벨, 시각] / [TH] แท็บ -> [ป้ายชื่อ, เวลา] / [EN] tab -> [label, time]
        self.refresh = {}
        self.refresh_error = False
        self.updated_rows = []
        self.row_count = row_count
        self.events = []
        self.grid_reads = []
        self.written = []

    def tab_names(self, sid="SID"):
        if self.legacy_sid:
            return list(self.grids) if sid == self.legacy_sid else list(self.queues)
        return list(self.grids) + list(self.queues)

    def sheets_request(self, method, url, **kw):
        path, params = urllib.parse.unquote(url), kw.get("params") or {}
        base = f"{SHEETS_BASE}/SID"
        self.sheets_calls.append((method, path))
        if self.legacy_sid and path == f"{SHEETS_BASE}/{self.legacy_sid}":
            if params.get("fields") == "properties.title":
                return Resp(200, {"properties": {"title": "Legacy Spreadsheet"}})
            if "includeGridData" not in params:
                return Resp(200, {"sheets": [{"properties": {"sheetId": i, "title": t, "gridProperties": {"rowCount": self.row_count}}}
                                              for i, t in enumerate(self.tab_names(self.legacy_sid))]})
            base = path
        if method == "GET" and path == base:
            if params.get("fields") == "properties.title":
                return Resp(200, {"properties": {"title": "Example Spreadsheet"}})
            if "includeGridData" in params:
                m = re.fullmatch(r"'(.+)'!A(\d+):([A-Z]+)(\d+)", params["ranges"])
                tab, r1, col, r2 = m.group(1).replace("''", "'"), int(m.group(2)), m.group(3), int(m.group(4))
                self.grid_reads.append((tab, r1, col, r2))
                rows = [self.grids[tab].get(r, []) for r in range(r1, r2 + 1)]
                while rows and not rows[-1]:        # 실제 API 처럼 뒤쪽의 빈 행은 생략 / [TH] ตัดแถวว่างท้ายออกเหมือน API จริง / [EN] trailing empty rows are omitted, like the real API
                    rows.pop()
                block = {"startRow": r1 - 1, "rowData": [{"values": r} if r else {} for r in rows]}
                return Resp(200, {"sheets": [{"data": [block]}]})
            return Resp(200, {"sheets": [{"properties": {"sheetId": i, "title": t, "gridProperties": {"rowCount": self.row_count}}}
                                          for i, t in enumerate(self.tab_names("SID"))]})
        if method == "GET" and "/values/" in path:
            tab = re.match(r"'(.+)'!A:G$", path.split("/values/")[1]).group(1)
            return Resp(200, {"values": self.queues[tab]}) if tab in self.queues else Resp(400, text="Unable to parse range")
        if method == "POST" and path == base + ":batchUpdate":
            title = kw["json"]["requests"][0]["addSheet"]["properties"]["title"]
            self.events.append(("addSheet", title))
            self.queues[title] = []
            return Resp(200, {})
        if method == "POST" and path == base + "/values:batchUpdate":
            assert kw["json"]["valueInputOption"] == "RAW"
            for item in kw["json"]["data"]:
                m = re.fullmatch(r"'(.+)'!B(\d+):F(\d+)", item["range"])
                tab, row = m.group(1), int(m.group(2))
                assert m.group(2) == m.group(3)                      # one row per range; column G is never written
                cells = self.queues[tab][row - 1]
                cells.extend([""] * (7 - len(cells)))
                cells[1:6] = item["values"][0]
                self.updated_rows.append((tab, row))
                self.written += item["values"]
            self.events.append(("update", tab, len(kw["json"]["data"])))
            return Resp(200, {})
        if method == "POST" and path.endswith(":clear"):
            tab = path.split("/values/")[1][:-len(":clear")].strip("'")
            self.events.append(("clear", tab))
            self.queues[tab] = []
            return Resp(200, {})
        if method == "PUT" and path.endswith("!I1:I2"):
            assert params["valueInputOption"] == "RAW"
            if self.refresh_error:
                return Resp(400, text="Range exceeds grid limits")
            tab = re.match(r"'(.+)'!I1:I2$", path.split("/values/")[1]).group(1)
            self.events.append(("refreshed", tab))
            self.refresh[tab] = [r[0] for r in kw["json"]["values"]]
            self.written += kw["json"]["values"]
            return Resp(200, {})
        if method == "PUT" and "/values/" in path:
            assert params["valueInputOption"] == "RAW"
            tab = re.match(r"'(.+)'!A1:G1$", path.split("/values/")[1]).group(1)
            self.events.append(("header", tab))
            self.queues[tab] = [list(kw["json"]["values"][0])]
            self.written += kw["json"]["values"]
            return Resp(200, {})
        if method == "POST" and path.endswith(":append"):
            assert params["valueInputOption"] == "RAW" and params["insertDataOption"] == "INSERT_ROWS"
            tab = re.match(r"'(.+)'!A1:append$", path.split("/values/")[1]).group(1)
            rows = kw["json"]["values"]
            self.events.append(("append", tab, len(rows)))
            self.queues[tab].extend(rows)
            self.written += rows
            return Resp(200, {})
        raise AssertionError((method, url, params))

    def clients(self):
        class S:
            def __init__(self, fn):
                self.request = fn

        return Clients(sheets=S(self.sheets_request), drive_factory=None, gemini=None, api_key="", sa_email="sa@x")


def xargs(**over):
    base = dict(legacy_tabs=dict(LEGACY_TABS), overwrite=False)
    base.update(over)
    return ns(**base)


def extract_logs(w, key="dwr", **over):
    logs = []
    summary = extract_queue(xargs(queue=key, **over), w.clients(), key, log=logs.append)
    return summary, logs


# --- 항목 만들기 규칙 / item rules / กฎการสร้างรายการ ---
def build(key, grid=None):
    grid = grid or legacy_grid()
    spec = SPECS[key]
    items, stats = build_queue_items(grid[3], sorted((n, c) for n, c in grid.items() if n >= 4), spec.legacy_end_col)
    return [it.to_row() for it in items], stats


def merge_queue():
    """
    DWR_ROWS 와 맞물리는 진행 중인 큐 / [TH] คิวที่กำลังทำอยู่ซึ่งจับคู่กับ DWR_ROWS / [EN] A queue in progress that lines up with DWR_ROWS:
      row 2 = Done (different PDF link), row 3 = Error with stale B..F, row 4 = Waiting and identical, row 5 = only in the queue
    """
    done = list(DWR_ROWS[0])
    done[3], done[6] = URL(7), "Done"
    error = list(DWR_ROWS[1])
    error[3], error[4], error[5], error[6] = URL(8), "", "C", "Error: Drive Access | x"
    waiting = list(DWR_ROWS[2])
    return [QUEUE_HEADER, done, error, waiting, ["OLD-1", "2024-01-01", "CIV", "", "", "P", "Done"]]


def extract_main(monkeypatch, argv, w=None, env=None, need=None):
    w = w or FakeLegacy()
    monkeypatch.setenv("PMIS_SPREADSHEET_ID", "SID")
    for name in ("PMIS_LEGACY_DWR_SHEET", "PMIS_LEGACY_DRQ_SHEET", "PMIS_LEGACY_SPREADSHEET_ID"):
        monkeypatch.delenv(name, raising=False)
    for name, value in (env if env is not None else {"PMIS_LEGACY_DWR_SHEET": "LEGACY-DWR", "PMIS_LEGACY_DRQ_SHEET": "LEGACY-DRQ"}).items():
        monkeypatch.setenv(name, value)

    def fake_make_clients(args, need_gemini):
        if need is not None:
            need.append(need_gemini)
        return w.clients()

    monkeypatch.setattr(cli, "make_clients", fake_make_clients)
    return main(argv), w


# --- 옛 시트를 다른 스프레드시트 파일에서 읽기 / legacy tabs in another file / อ่านแท็บเก่าจากไฟล์สเปรดชีตอื่น ---
LEGACY_FILE = "LEGACYFILEID123"
