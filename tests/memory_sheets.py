"""
탭들을 메모리에 가진 범용 가짜 스프레드시트 (Sheets API 의 필요한 부분만): 큐 탭과 DB 탭을 함께 다루는 테스트용
[TH] สเปรดชีตจำลองทั่วไปที่เก็บแท็บไว้ในหน่วยความจำ (เฉพาะส่วนของ Sheets API ที่ต้องใช้): สำหรับเทสต์ที่ใช้ทั้งแท็บคิวและแท็บ DB
[EN] A general in-memory fake spreadsheet holding the tabs (only the parts of the Sheets API that are needed): for tests that use a queue tab and DB tabs together
"""
import re
import urllib.parse

from migrator.config import SHEETS_BASE
from migrator.models import Clients

from tests.fakes import Resp


def column_number(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


class MemorySheets:
    """
    tabs: 탭 이름 -> 행 목록 (행은 셀 목록). 모든 호출을 calls 에 기록
    [TH] tabs: ชื่อแท็บ -> รายการแถว (แต่ละแถวเป็นรายการเซลล์) บันทึกทุกการเรียกไว้ใน calls
    [EN] tabs: tab name -> list of rows (a row is a list of cells). Every call is recorded in calls
    """

    def __init__(self, tabs, sid="SID"):
        self.sid = sid
        self.tabs = {name: [list(r) for r in rows] for name, rows in tabs.items()}
        self.ids = {name: 100 + i for i, name in enumerate(self.tabs)}
        self.next_id = 100 + len(self.tabs)
        self.calls = []
        self.inputs = []                      # the valueInputOption of every write / 쓰기마다의 valueInputOption
        self.fail_on = None                   # a substring of the url: the call answers HTTP 500 / [TH] ส่วนของ url ที่ทำให้การเรียกตอบ 500 / [EN] a substring of the url that makes the call answer 500

    # ------------------------------------------------------------------ clients
    def clients(self, **extra):
        outer = self

        class Session:
            request = staticmethod(outer.request)

        return Clients(sheets=Session(), drive_factory=None, gemini=None, api_key="", sa_email="sa@x", **extra)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def parse(a1):
        m = re.fullmatch(r"'(.+)'!([A-Z]+)(\d*)(?::([A-Z]+)(\d*))?", a1.replace("''", "'"))
        assert m, a1
        tab, c1, r1, c2, r2 = m.groups()
        return tab, column_number(c1) - 1, int(r1) if r1 else 1, (column_number(c2) - 1 if c2 else column_number(c1) - 1), int(r2) if r2 else None

    def read(self, a1):
        tab, c1, r1, c2, r2 = self.parse(a1)
        rows = self.tabs[tab]
        last = r2 if r2 else len(rows)
        out = []
        for n in range(r1, last + 1):
            row = rows[n - 1] if n <= len(rows) else []
            cells = list(row[c1:c2 + 1])
            while cells and cells[-1] == "":
                cells.pop()
            out.append(cells)
        while out and not out[-1]:
            out.pop()
        return out

    def write(self, a1, values):
        tab, c1, r1, _, _ = self.parse(a1)
        rows = self.tabs[tab]
        for dr, vals in enumerate(values):
            while len(rows) < r1 + dr:
                rows.append([])
            row = rows[r1 + dr - 1]
            while len(row) < c1 + len(vals):
                row.append("")
            for dc, v in enumerate(vals):
                row[c1 + dc] = v

    # ------------------------------------------------------------------ the API
    def request(self, method, url, **kw):
        path = urllib.parse.unquote(url)
        self.calls.append((method, path, kw.get("json")))
        if self.fail_on and self.fail_on in path:
            return Resp(500, text="boom")
        base = f"{SHEETS_BASE}/{self.sid}"
        params = kw.get("params") or {}
        if method == "GET" and path == base:
            return Resp(200, {"sheets": [{"properties": {"sheetId": self.ids[t], "title": t, "gridProperties": {"rowCount": max(len(r), 1000)}}}
                                         for t, r in self.tabs.items()]})
        if method == "GET" and "/values/" in path:
            return Resp(200, {"values": self.read(path.split("/values/")[1])})
        if method == "POST" and path == base + "/values:batchUpdate":
            self.inputs.append(kw["json"]["valueInputOption"])
            for item in kw["json"]["data"]:
                self.write(item["range"], item["values"])
            return Resp(200, {})
        if method == "POST" and path == base + ":batchUpdate":
            for request in kw["json"]["requests"]:
                if "addSheet" in request:
                    title = request["addSheet"]["properties"]["title"]
                    assert title not in self.tabs
                    self.tabs[title] = []
                    self.ids[title] = self.next_id
                    self.next_id += 1
                elif "deleteDimension" in request:
                    rng = request["deleteDimension"]["range"]
                    tab = next(t for t, i in self.ids.items() if i == rng["sheetId"])
                    assert rng["dimension"] == "ROWS"
                    del self.tabs[tab][rng["startIndex"]:rng["endIndex"]]
                elif "appendDimension" in request:
                    pass
                else:
                    raise AssertionError(request)
            return Resp(200, {})
        if method == "POST" and path.endswith(":append"):
            self.inputs.append(params["valueInputOption"])
            tab = re.match(r"'(.+)'!A1:append$", path.split("/values/")[1]).group(1)
            self.tabs[tab].extend([list(r) for r in kw["json"]["values"]])
            return Resp(200, {})
        if method == "POST" and path.endswith(":clear"):
            tab, c1, r1, c2, r2 = self.parse(path.split("/values/")[1][:-len(":clear")])
            for n in range(r1, (r2 or len(self.tabs[tab])) + 1):
                if n <= len(self.tabs[tab]):
                    row = self.tabs[tab][n - 1]
                    for c in range(c1, min(c2 + 1, len(row))):
                        row[c] = ""
            return Resp(200, {})
        if method == "PUT" and "/values/" in path:
            self.inputs.append(params["valueInputOption"])
            self.write(path.split("/values/")[1], kw["json"]["values"])
            return Resp(200, {})
        raise AssertionError((method, path))
