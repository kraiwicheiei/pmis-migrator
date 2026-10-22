"""
Sheets API 헬퍼 테스트
[TH] เทสต์ตัวช่วย Sheets API
[EN] Tests for the Sheets API helpers
"""

from migrator.config import SHEETS_BASE
from migrator.sheets import spreadsheet_title

from tests.fakes import Resp


def test_spreadsheet_title_and_failure_fallback():
    class Sess:
        def __init__(self, resp):
            self.resp = resp
            self.calls = []

        def request(self, method, url, **kw):
            self.calls.append((method, url, kw.get("params")))
            if isinstance(self.resp, Exception):
                raise self.resp
            return self.resp

    ok = Sess(Resp(200, {"properties": {"title": "Example Spreadsheet"}}))
    assert spreadsheet_title(ok, "SID") == ("Example Spreadsheet", "")
    assert ok.calls == [("GET", f"{SHEETS_BASE}/SID", {"fields": "properties.title"})]
    title, err = spreadsheet_title(Sess(Resp(403, text="denied")), "SID")
    assert title == "" and "403" in err and "denied" in err
    title, err = spreadsheet_title(Sess(RuntimeError("net")), "SID")
    assert title == "" and "net" in err
    assert spreadsheet_title(None, "SID")[0] == ""
