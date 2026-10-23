"""
테스트용 가짜 시트/Drive/Gemini 와 공용 도우미
[TH] ชีต/Drive/Gemini จำลองสำหรับเทสต์และตัวช่วยที่ใช้ร่วมกัน
[EN] The fake sheet / Drive / Gemini and the shared helpers for the tests
"""

import json
import re
import threading
import urllib.parse

from migrator import cli, runner
from migrator.cli import main
from migrator.config import DRIVE_THUMBNAIL_URL
from migrator.models import Clients
from migrator.runner import Summary, run


# 길이 33 의 가짜 file id
# [TH] file id ปลอมความยาว 33
# [EN] Fake file id of length 33
FID = lambda n: ("F" + str(n)).ljust(33, "x")

URL = lambda n: f"https://drive.google.com/open?id={FID(n)}&usp=drive_copy"

FOLDER = "https://drive.google.com/drive/folders/FOLDERIDxxxxxxxxxxxxxxxxxxxxxxx"

DWR_JSON = {
    "DWR_No": "ACME-DWR-X", "Date": "2025-11-28", "Discipline": "Civil", "Location": "Site A", "WBS": "C1",
    "Weather_Day": "Fair", "Weather_Night": "", "Total_Manpower": 28, "Total_Equipment": 6,
    "Manpower_Details": [{"role": "CM", "count": 10}], "Equipment_Details": [{"name": "Gen", "count": 1}],
    "Activities": "work", "Issues_Delays": None, "AI_Risk_Flag": "Low", "Linked_RITs": "",
}


class Resp:
    def __init__(self, status=200, body=None, content=b"", text=None, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self.content = content
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def column_index(letters):
    """A -> 0, T -> 19, AA -> 26 / [TH] ตัวอักษรคอลัมน์เป็น index (เริ่ม 0) / [EN] Column letters to a 0-based index"""
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


class FakeWorld:
    """
    메모리 안의 가짜 시트 + Drive + Gemini
    [TH] ชีต + Drive + Gemini จำลองในหน่วยความจำ
    [EN] In-memory fake sheet + Drive + Gemini
    """

    def __init__(self, queue_rows, db_rows=None, db_header=None):
        self.queue = [["Doc No", "Date", "Discipline", "PDF URL", "Folder URL", "Status(C/P)", "AI Processed"]] + queue_rows
        self.db = [db_header or ["h"] * 19] + (db_rows or [])
        self.drive_files = {}  # fid -> dict(size, content, mime)
        # 순서대로 반환할 Resp 목록 (소진되면 default 사용)
        # [TH] รายการ Resp ที่จะตอบตามลำดับ (ถ้าหมดใช้ default)
        # [EN] Resp objects to return in order (falls back to the default when exhausted)
        self.gemini_script = []
        self.gemini_calls = 0
        self.gemini_default = lambda: Resp(200, {"candidates": [{"content": {"parts": [{"text": "```json\n" + json.dumps(DWR_JSON) + "\n```"}]}}],
                                                  "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 200, "thoughtsTokenCount": 50}})
        self.gemini_mimes = []
        self.gemini_configs = []
        self.gemini_prompts = []
        self.thumbnail_calls = []
        self.downloads = []
        self.appends = []
        self.batch_updates = []
        self.fail_append = False
        # 마이그레이션 시각(K1:K2): 쓴 값 목록 / 실패 시뮬레이션 / [TH] เวลา migration (K1:K2): ค่าที่เขียน / จำลองให้ล้มเหลว / [EN] The migration time (K1:K2): the values written / simulate a failure
        self.migration_time = []
        self.fail_migration_time = False
        # DB 탭의 C/P 열 머리글(DWR 은 T1, DRQ 는 S1) 현재 값 / [TH] ค่าปัจจุบันของหัวตารางคอลัมน์ C/P (T1) ในแท็บ DB / [EN] The current value of the header (T1) of the C/P column of the DB tab
        self.cp_header = ""
        self.cp_header_writes = []
        # "Legacy sync" 표시 열의 머리글 (DB_DWR 은 U1) / [TH] หัวตารางของคอลัมน์ป้าย "Legacy sync" (DB_DWR คือ U1) / [EN] The header of the "Legacy sync" tag column (U1 in DB_DWR)
        self.sync_header = ""
        self.sync_header_writes = []
        self.batch_inputs = []
        self.lock = threading.Lock()

    # --- sheets session ---
    def sheets_request(self, method, url, **kw):
        path = urllib.parse.unquote(url)
        if method == "GET" and "/values/" in path:
            a1 = path.split("/values/")[1]
            if a1.startswith("'MIGRATION_QUEUE'") or a1.startswith("'MIGRATION_QUEUE_DRQ'"):
                return Resp(200, {"values": self.queue})
            if a1.startswith("'DB_DWR'!R2"):
                return Resp(200, {"values": [[r[17]] for r in self.db[1:] if len(r) > 17]})
            if a1 in ("'DB_DWR'!T1", "'DB_DRQ'!S1"):
                return Resp(200, {"values": [[self.cp_header]]} if self.cp_header else {})
            if a1 in ("'DB_DWR'!U1", "'DB_DRQ'!T1"):
                return Resp(200, {"values": [[self.sync_header]]} if self.sync_header else {})
            if a1 == "'DB_DWR'!A1:T":
                return Resp(200, {"values": self.db})
            raise AssertionError(a1)
        if method == "PUT" and re.search(r"'DB_(DWR'!(T|U)|DRQ'!(S|T))1$", path):
            assert kw["params"]["valueInputOption"] == "RAW"
            if re.search(r"'DB_DWR'!U1$|'DB_DRQ'!T1$", path):                       # the "Legacy sync" header
                self.sync_header = kw["json"]["values"][0][0]
                self.sync_header_writes.append(self.sync_header)
            else:                                                                      # the C/P header
                self.cp_header = kw["json"]["values"][0][0]
                self.cp_header_writes.append(self.cp_header)
            return Resp(200, {})
        if method == "PUT" and path.endswith("!K1:K2"):
            assert kw["params"]["valueInputOption"] == "RAW"
            if self.fail_migration_time:
                return Resp(400, text="Range exceeds grid limits")
            self.migration_time.append([r[0] for r in kw["json"]["values"]])
            return Resp(200, {})
        if method == "POST" and path.endswith(":append"):
            if self.fail_append:
                return Resp(500, text="boom")
            rows = kw["json"]["values"]
            assert kw["params"]["insertDataOption"] == "INSERT_ROWS"
            self.appends.append(rows)
            self.db.extend(rows)
            return Resp(200, {})
        if method == "POST" and path.endswith("values:batchUpdate"):
            self.batch_updates.append(kw["json"]["data"])
            self.batch_inputs.append(kw["json"]["valueInputOption"])
            for item in kw["json"]["data"]:
                m = re.fullmatch(r"'(.+)'!([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?", item["range"])
                tab, first_col, row = m.group(1), column_index(m.group(2)), int(m.group(3))
                assert m.group(5) in (None, m.group(3))                                # one row per range
                store = self.queue if tab.startswith("MIGRATION_QUEUE") else self.db      # the queue statuses / column H, or cells of a DB tab (C/P, a replaced row)
                assert tab.startswith(("MIGRATION_QUEUE", "DB_"))
                values = item["values"][0]
                while len(store[row - 1]) < first_col + len(values):
                    store[row - 1].append("")
                for offset, value in enumerate(values):
                    store[row - 1][first_col + offset] = value
            return Resp(200, {})
        raise AssertionError((method, url))

    # --- drive session ---
    def drive_request(self, method, url, **kw):
        if url == DRIVE_THUMBNAIL_URL:
            # Apps Script 의 extractDriveFileForAI 와 같은 주소/크기 (drive.google.com/thumbnail?id=..&sz=w2500)
            # [TH] ที่อยู่/ขนาดเดียวกับ extractDriveFileForAI ของ Apps Script
            # [EN] Same URL/size as extractDriveFileForAI in Apps Script
            assert kw["params"]["sz"] == "w2500"
            fid = kw["params"]["id"]
            self.thumbnail_calls.append(fid)
            f = self.drive_files.get(fid)
            if f is None or f.get("thumb") is None:
                return Resp(f.get("thumb_status", 404) if f else 404, text="no thumbnail")
            return Resp(200, content=f["thumb"], headers={"Content-Type": f.get("thumb_type", "image/png")})
        fid = url.rsplit("/", 1)[1]
        f = self.drive_files.get(fid)
        if f is None:
            return Resp(404, text='{"error":{"code":404,"message":"File not found"}}')
        if kw["params"].get("alt") == "media":
            self.downloads.append(fid)
            return Resp(200, content=f["content"])
        return Resp(200, {"id": fid, "name": "x.pdf", "mimeType": f.get("mime", "application/pdf"), "size": str(f["size"])})

    # --- gemini session ---
    def gemini_post(self, url, **kw):
        with self.lock:
            self.gemini_calls += 1
            body = json.loads(kw["data"])
            assert kw["headers"]["x-goog-api-key"] == "KEY"
            assert body["generationConfig"]["response_mime_type"] == "application/json"
            self.gemini_configs.append(body["generationConfig"])
            self.gemini_prompts.append(body["contents"][0]["parts"][0]["text"])
            self.gemini_mimes.append(body["contents"][0]["parts"][1]["inline_data"]["mime_type"])
            nxt = self.gemini_script.pop(0) if self.gemini_script else None
        return nxt if nxt is not None else self.gemini_default()

    def clients(self, **extra):
        w = self

        class S:
            def __init__(self, fn):
                self.request = fn

        class G:
            post = staticmethod(w.gemini_post)

        return Clients(sheets=S(w.sheets_request), drive_factory=lambda: S(w.drive_request), gemini=G(), api_key="KEY", sa_email="sa@x", **extra)


def ns(**over):
    base = dict(queue="dwr", spreadsheet_id="SID", limit=0, batch=3, workers=3, only_rows=None, no_retry_errors=False,
                include_blank=False, no_reconcile=False, max_mb=15.0, no_thumbnail=False, dry_run=False, check_access=False)
    base.update(over)
    import argparse
    return argparse.Namespace(**base)


def qrow(n, status="Waiting"):
    return [f"DOC-{n}", "2025-01-01", "Civil", URL(n), FOLDER, "C", status]


def world(statuses):
    w = FakeWorld([qrow(i, s) for i, s in enumerate(statuses, start=1)])
    for i in range(1, len(statuses) + 1):
        w.drive_files[FID(i)] = {"size": 1000, "content": b"%PDF-" + bytes([i])}
    return w


class RunRecorder:
    def __init__(self, behaviour=None):
        self.calls = []
        self.behaviour = behaviour or {}

    def __call__(self, args, clients, log=print):
        self.calls.append((args.queue, args.hint_other_queue, args.limit))
        outcome = self.behaviour.get(args.queue)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome or Summary(done=1)


def main_with(monkeypatch, argv, recorder, sid="SID"):
    # 시트 ID 는 환경 변수로만 지정 (sid=None 이면 변수를 지움)
    # [TH] ID ชีตกำหนดผ่านตัวแปรสภาพแวดล้อมเท่านั้น (sid=None คือลบตัวแปร)
    # [EN] The sheet ID is set through the environment variable only (sid=None removes the variable)
    if sid is None:
        monkeypatch.delenv("PMIS_SPREADSHEET_ID", raising=False)
    else:
        monkeypatch.setenv("PMIS_SPREADSHEET_ID", sid)
    monkeypatch.setattr(cli, "make_clients", lambda args, need_gemini: Clients(sheets=None, drive_factory=None, gemini=None, api_key="K", sa_email="sa@x"))
    monkeypatch.setattr(runner, "run", recorder)
    return main(argv)


# --- 처리할 행이 0 일 때의 안내 / 시트 이름 표시 ---
# [TH] ข้อความอธิบายเมื่อได้ 0 แถว / แสดงชื่อชีต
# [EN] Explanation when 0 rows are found / showing the sheet name
def run_logs(w, **over):
    logs = []
    run(ns(**over), w.clients(), log=logs.append)
    return logs


# JSON Parse (예: 보고서가 여러 개 든 PDF) / JSON Parse hardening / ทนต่อ JSON พัง
def gresp(text, finish="STOP"):
    cand = {"content": {"parts": [{"text": text}]}}
    if finish:
        cand["finishReason"] = finish
    return Resp(200, {"candidates": [cand], "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}})


# AI 가 보고서를 여러 개(list) 돌려준 경우 ("expected a JSON object, got list")
# [TH] กรณี AI ตอบกลับมาหลายรายงาน (list) เช่น "expected a JSON object, got list"
# [EN] The AI answered with several reports (a list), e.g. "expected a JSON object, got list"
def rep_json(no, **over):
    return {**DWR_JSON, "DWR_No": no, **over}


# 시트 오류 셀은 영어만 / Sheet error cells are English only / เซลล์ Error ในชีตเป็นภาษาอังกฤษเท่านั้น
NON_ENGLISH = re.compile(r"[\u0E00-\u0E7F\uAC00-\uD7A3]")  # Thai, Hangul


def cell_after(w, **over):
    run(ns(workers=1, **over), w.clients(), log=lambda m: None)
    return w.queue[1][6]


def error_cell_scenarios():
    """(이름, 셀 문구) 목록: 시트에 Error 를 남기는 모든 경로 / every path that leaves an Error in the sheet"""
    out = []
    w = world(["Waiting"]); w.queue[1][3] = ""
    out.append(("No URL", cell_after(w)))
    w = world(["Waiting"]); del w.drive_files[FID(1)]
    out.append(("Drive Access (hint)", cell_after(w)))
    w = world(["Waiting"]); w.drive_files[FID(1)]["size"] = 30_000_000
    out.append(("Too Large", cell_after(w, no_thumbnail=True)))
    w = world(["Waiting"]); w.drive_files[FID(1)]["size"] = 30_000_000
    out.append(("Cover capture failed", cell_after(w)))
    w = world(["Waiting"]); w.gemini_script = [gresp("x" * 3000, finish="MAX_TOKENS")] * 2
    out.append(("JSON Parse, truncated, retried", cell_after(w, parse_retries=1)))
    w = world(["Waiting"]); w.queue[1][0] = "ACME-DWR-CIV-231"
    w.gemini_script = [gresp(json.dumps([rep_json("ACME-DWR-CIV-0232"), rep_json("ACME-DWR-CIV-0233")]))] * 2
    out.append(("Multiple reports", cell_after(w, parse_retries=1)))
    w = world(["Waiting"]); w.gemini_script = [Resp(200, {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}})]
    out.append(("No Result", cell_after(w)))
    w = world(["Waiting"]); w.gemini_script = [Resp(400, text="bad request")]
    out.append(("API 400", cell_after(w)))
    w = world(["Waiting"]); w.gemini_script = [Resp(402, text="payment required")]
    out.append(("API 402 (fatal)", cell_after(w)))
    w = world(["Waiting"]); w.fail_append = True
    out.append(("DB Write", cell_after(w)))
    return out
