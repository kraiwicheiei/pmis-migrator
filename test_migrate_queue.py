"""
가짜 시트/Drive/Gemini 로 하는 테스트 (네트워크나 실제 키 불필요).  실행: python -m pytest -q
[TH] เทสต์ด้วยชีต/Drive/Gemini จำลอง (ไม่ต้องใช้เครือข่ายหรือคีย์จริง)  รัน: python -m pytest -q
[EN] Tests using a fake sheet/Drive/Gemini (no network or real keys needed).  Run: python -m pytest -q
"""
import json
import re
import threading
import urllib.parse

import pytest

import migrate_queue as mq

# 실제 backoff 대기 없음
# [TH] ไม่รอ backoff จริง
# [EN] No real backoff waiting
mq.SLEEP = lambda s: None

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
            raise AssertionError(a1)
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
            for item in kw["json"]["data"]:
                row = int(re.search(r"!G(\d+)$", item["range"]).group(1))
                while len(self.queue[row - 1]) < 7:
                    self.queue[row - 1].append("")
                self.queue[row - 1][6] = item["values"][0][0]
            return Resp(200, {})
        raise AssertionError((method, url))

    # --- drive session ---
    def drive_request(self, method, url, **kw):
        if url == mq.DRIVE_THUMBNAIL_URL:
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

        return mq.Clients(sheets=S(w.sheets_request), drive_factory=lambda: S(w.drive_request), gemini=G(), api_key="KEY", sa_email="sa@x", **extra)


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


def test_extract_file_id():
    assert mq.extract_file_id(URL(1)) == FID(1)
    assert mq.extract_file_id(f"https://drive.google.com/file/d/{FID(2)}/view?usp=sharing") == FID(2)
    assert mq.extract_file_id("") is None and mq.extract_file_id(None) is None
    assert mq.extract_file_id("short") is None


def test_build_db_row_matches_apps_script_layout():
    row = mq.build_db_row(mq.SPECS["dwr"], DWR_JSON, "PDF", "FOLDER")
    assert len(row) == 19
    assert row[0] == "" and row[1] == "ACME-DWR-X" and row[8] == 28 and row[9] == 6
    # 공백 없는 JSON.stringify 형식
    # [TH] JSON.stringify แบบไม่มีช่องว่าง
    # [EN] JSON.stringify style, no spaces
    assert row[10] == '[{"role":"CM","count":10}]'
    assert row[13] == "" and row[16] == "AI Migrated" and row[17] == "PDF" and row[18] == "FOLDER"
    drq = mq.build_db_row(mq.SPECS["drq"], {"DRQ_No": "D1", "Comments": "=SUM(1)"}, "P", "F")
    assert len(drq) == 18 and drq[1] == "D1" and drq[12] == "'=SUM(1)" and drq[13] == "AI Migrated" and drq[14] == "P" and drq[17] == ""


def test_parse_model_json_and_row_spec():
    assert mq.parse_model_json('```json\n{"a":1}\n```') == {"a": 1}
    assert mq.parse_model_json('[{"a":1}]') == {"a": 1}
    with pytest.raises(ValueError):
        mq.parse_model_json('"text"')
    assert mq.parse_row_spec("83, 127-129,5") == {83, 127, 128, 129, 5}


def test_select_jobs():
    vals = [["h"] * 7, qrow(1, "Done"), qrow(2, "Waiting"), qrow(3, "Error: API 402 | x"), qrow(4, ""), ["only", "a"]]
    rows = lambda **kw: [j.row for j in mq.select_jobs(vals, only_rows=None, **{"retry_errors": True, "include_blank": False, **kw})]
    assert rows() == [3, 4]
    assert rows(retry_errors=False) == [3]
    assert rows(include_blank=True) == [3, 4, 5]
    assert [j.row for j in mq.select_jobs(vals, retry_errors=True, include_blank=False, only_rows={2, 3, 4})] == [3, 4]
    # 2행 = Done 이므로 건너뜀
    # [TH] แถว 2 = Done ถูกข้าม
    # [EN] Row 2 is Done, so it is skipped


def test_happy_path_batches_and_marks_done():
    w = world(["Waiting"] * 7)
    s = mq.run(ns(batch=3), w.clients(), log=lambda m: None)
    assert s.done == 7 and not s.errors
    assert [r[6] for r in w.queue[1:]] == ["Done"] * 7
    assert [len(a) for a in w.appends] == [3, 3, 1]
    assert len(w.db) == 1 + 7
    assert {r[17] for r in w.db[1:]} == {URL(i) for i in range(1, 8)}


def test_limit_and_retry_errors_and_skip_done():
    w = world(["Done", "Error: API 402 | x", "Waiting", "Waiting"])
    s = mq.run(ns(limit=2), w.clients(), log=lambda m: None)
    assert s.done == 2
    assert [r[6] for r in w.queue[1:]] == ["Done", "Done", "Done", "Waiting"]
    assert w.gemini_calls == 2


def test_drive_access_error_recorded_and_others_continue():
    w = world(["Waiting"] * 3)
    del w.drive_files[FID(2)]
    s = mq.run(ns(), w.clients(), log=lambda m: None)
    assert s.done == 2 and s.errors == {"Drive Access": 1}
    assert w.queue[2][6].startswith("Error: Drive Access | fileId=" + FID(2)) and "service account" in w.queue[2][6]
    assert w.queue[1][6] == w.queue[3][6] == "Done"


def test_402_stops_run_and_leaves_rest_waiting():
    w = world(["Waiting"] * 9)
    w.gemini_default = lambda: Resp(402, text='{"error":{"status":"RESOURCE_EXHAUSTED"}}')
    s = mq.run(ns(batch=3, workers=1), w.clients(), log=lambda m: None)
    assert s.stopped_reason and "402" in s.stopped_reason
    # 즉시 중단되어 이후 행은 호출하지 않음
    # [TH] หยุดทันที ไม่ไล่ยิงต่อ
    # [EN] Stops immediately and does not keep calling for the remaining rows
    assert w.gemini_calls == 1
    assert w.queue[1][6].startswith("Error: API 402")
    assert [r[6] for r in w.queue[2:]] == ["Waiting"] * 8
    assert not w.appends


def test_500_is_retried_then_succeeds():
    w = world(["Waiting"])
    w.gemini_script = [Resp(500, text='{"error":{"status":"INTERNAL"}}'), Resp(429, text='{"error":{"details":[{"retryDelay":"3s"}]}}')]
    s = mq.run(ns(), w.clients(), log=lambda m: None)
    assert s.done == 1 and w.gemini_calls == 3


def test_persistent_500_becomes_error_row():
    w = world(["Waiting"])
    w.gemini_default = lambda: Resp(500, text='{"error":{"status":"INTERNAL"}}')
    s = mq.run(ns(), w.clients(), log=lambda m: None)
    assert s.errors == {"API 500": 1} and w.gemini_calls == 5
    assert w.queue[1][6].startswith("Error: API 500 | ")


def test_repeated_429_stops_run():
    w = world(["Waiting"] * 10)
    w.gemini_default = lambda: Resp(429, text='{"error":{"status":"RESOURCE_EXHAUSTED"}}')
    s = mq.run(ns(batch=2, workers=1), w.clients(), log=lambda m: None)
    assert "429" in s.stopped_reason
    assert sum(1 for r in w.queue[1:] if r[6].startswith("Error: API 429")) == 4
    assert sum(1 for r in w.queue[1:] if r[6] == "Waiting") == 6


def test_bad_json_and_no_result():
    w = world(["Waiting"] * 2)
    w.gemini_script = [
        Resp(200, {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}),
        Resp(200, {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}),
    ]
    s = mq.run(ns(workers=1), w.clients(), log=lambda m: None)
    assert s.errors == {"JSON Parse": 1, "No Result": 1}


def test_db_write_failure_marks_rows_and_rerun_reconciles_without_ai():
    w = world(["Waiting"] * 2)
    w.fail_append = True
    s = mq.run(ns(), w.clients(), log=lambda m: None)
    assert s.errors == {"DB Write": 2} and all(r[6].startswith("Error: DB Write") for r in w.queue[1:])
    # 실제로는 DB 쓰기가 성공했다고 가정 (응답만 유실) -> 다음 실행에서 AI 호출/중복 쓰기가 없어야 함
    # [TH] สมมติว่าจริงๆ แล้วเขียน DB สำเร็จ (response หาย) -> รอบถัดไปไม่ควรเรียก AI/เขียนซ้ำ
    # [EN] Pretend the DB write actually succeeded (only the response was lost) -> the next run must not call the AI or write again
    w.db.extend([mq.build_db_row(mq.SPECS["dwr"], DWR_JSON, URL(i), FOLDER) for i in (1, 2)])
    w.fail_append = False
    calls_before = w.gemini_calls
    s2 = mq.run(ns(), w.clients(), log=lambda m: None)
    assert s2.reconciled == 2 and s2.done == 0 and w.gemini_calls == calls_before
    assert [r[6] for r in w.queue[1:]] == ["Done", "Done"] and len(w.db) == 3


def test_too_large_pdf_not_sent_when_thumbnail_disabled():
    w = world(["Waiting"])
    w.drive_files[FID(1)]["size"] = 30_000_000
    s = mq.run(ns(no_thumbnail=True), w.clients(), log=lambda m: None)
    assert s.errors == {"Too Large": 1} and w.gemini_calls == 0 and w.thumbnail_calls == []


def test_default_limit_matches_apps_script_extractDriveFileForAI():
    # Apps Script: fileSize > 15000000 -> cover thumbnail
    assert mq.LARGE_PDF_MB == 15.0 and mq.build_parser().parse_args([]).max_mb == 15.0
    assert mq.build_parser().parse_args([]).no_thumbnail is False


def test_large_pdf_sent_as_cover_thumbnail_like_apps_script():
    w = world(["Waiting"])
    w.drive_files[FID(1)].update(size=30_000_000, thumb=b"\x89PNG-cover", thumb_type="image/png")
    logs = []
    s = mq.run(ns(), w.clients(), log=logs.append)
    assert (s.done, s.cover_only, s.errors) == (1, 1, {})
    assert w.queue[1][6] == "Done" and len(w.db) == 2
    # 원본 PDF 는 내려받지 않고 썸네일만, 이미지 MIME 으로 전송
    # [TH] ไม่ดาวน์โหลด PDF ต้นฉบับ ใช้แค่ thumbnail และส่งด้วย MIME ของภาพ
    # [EN] The original PDF is not downloaded; only the thumbnail is, and it is sent with an image MIME type
    assert w.downloads == [] and w.thumbnail_calls == [FID(1)] and w.gemini_mimes == ["image/png"]
    assert any("row 2" in m and "first page only" in m for m in logs)


def test_pdf_exactly_at_limit_is_downloaded_in_full():
    w = world(["Waiting", "Waiting"])
    w.drive_files[FID(1)]["size"] = 15_000_000  # Apps Script: fileSize > limit, so exactly 15,000,000 is NOT a cover case
    w.drive_files[FID(2)].update(size=15_000_001, thumb=b"jpg")
    s = mq.run(ns(), w.clients(), log=lambda m: None)
    assert s.done == 2 and s.cover_only == 1
    assert w.downloads == [FID(1)] and w.thumbnail_calls == [FID(2)]
    assert sorted(w.gemini_mimes) == ["application/pdf", "image/png"]


def test_cover_thumbnail_without_content_type_defaults_to_jpeg():
    w = world(["Waiting"])
    w.drive_files[FID(1)].update(size=30_000_000, thumb=b"jpgbytes", thumb_type="text/html")
    s = mq.run(ns(), w.clients(), log=lambda m: None)
    assert s.done == 1 and w.gemini_mimes == ["image/jpeg"]


def test_cover_thumbnail_failure_is_a_drive_access_error():
    w = world(["Waiting", "Waiting"])
    w.drive_files[FID(1)].update(size=30_000_000, thumb=None, thumb_status=403)
    s = mq.run(ns(), w.clients(), log=lambda m: None)
    assert s.errors == {"Drive Access": 1} and s.done == 1
    assert w.queue[1][6].startswith("Error: Drive Access | fileId=") and "cover capture failed" in w.queue[1][6] and "HTTP 403" in w.queue[1][6]
    assert w.queue[2][6] == "Done"


def test_large_non_pdf_is_still_too_large():
    w = world(["Waiting"])
    w.drive_files[FID(1)].update(size=30_000_000, mime="image/png", thumb=b"x")
    s = mq.run(ns(), w.clients(), log=lambda m: None)
    assert s.errors == {"Too Large": 1} and w.thumbnail_calls == [] and w.gemini_calls == 0


def test_check_access_reports_rows_that_will_use_the_cover():
    w = world(["Waiting", "Waiting"])
    w.drive_files[FID(2)]["size"] = 30_000_000
    logs = []
    mq.run(ns(check_access=True), w.clients(), log=logs.append)
    assert w.gemini_calls == 0 and w.downloads == [] and w.thumbnail_calls == []
    info = [m for m in logs if "cover image" in m]
    assert len(info) == 1 and "row 3" in info[0] and "[MIGRATION_QUEUE]" in info[0]
    assert any("Reachable: 2/2" in m for m in logs)


def test_no_url_row():
    w = FakeWorld([["DOC-1", "", "", "", FOLDER, "C", "Waiting"]])
    s = mq.run(ns(), w.clients(), log=lambda m: None)
    assert s.errors == {"No URL": 1} and w.queue[1][6].startswith("Error: No URL | PDF URL cell")


def test_dry_run_and_check_access_do_not_write():
    w = world(["Waiting"] * 3)
    del w.drive_files[FID(3)]
    logs = []
    mq.run(ns(dry_run=True), w.clients(), log=logs.append)
    assert w.gemini_calls == 0 and not w.batch_updates and not w.appends
    mq.run(ns(check_access=True), w.clients(), log=logs.append)
    assert w.gemini_calls == 0 and not w.batch_updates and not w.appends
    assert any("row 4" in m and "Drive Access" in m for m in logs)
    assert any("Reachable: 2/3" in m for m in logs)


# --check-access 의 모든 출력 줄에 큐 시트 이름이 표시되고, 다른 큐 안내가 나온다
# [TH] ทุกบรรทัดผลลัพธ์ของ --check-access ระบุชื่อชีตคิว และมีคำแนะนำสำหรับคิวอีกอัน
# [EN] Every --check-access result line names the queue sheet, and the other queue is pointed out
@pytest.mark.parametrize("queue, tab, other_tab, other_key", [
    ("dwr", "MIGRATION_QUEUE", "MIGRATION_QUEUE_DRQ", "drq"),
    ("drq", "MIGRATION_QUEUE_DRQ", "MIGRATION_QUEUE", "dwr"),
])
def test_check_access_names_the_queue_sheet(queue, tab, other_tab, other_key):
    w = world(["Waiting"] * 3)
    del w.drive_files[FID(3)]
    logs = []
    mq.run(ns(queue=queue, check_access=True), w.clients(), log=logs.append)
    tagged = [m for m in logs if m.startswith(f"[{tab}]") or f"✗ [{tab}]" in m]
    assert any("Checking Drive access" in m and f"--queue {queue}" in m for m in tagged)
    assert any("row 4" in m and "Drive Access" in m for m in tagged)
    assert any("Reachable: 2/3" in m for m in tagged)
    # 다른 큐 시트 이름은 "다른 큐 안내" 줄에만 나타나야 한다 / the other sheet is named only in the hint line
    hint = [m for m in logs if f"--queue {other_key}" in m]
    assert len(hint) == 1 and other_tab in hint[0] and "--check-access" in hint[0]
    assert not any(f"[{other_tab}]" in m for m in logs)


def test_only_rows():
    w = world(["Error: x", "Waiting", "Waiting"])
    s = mq.run(ns(only_rows="3"), w.clients(), log=lambda m: None)
    assert s.done == 1 and [r[6] for r in w.queue[1:]] == ["Error: x", "Done", "Waiting"]


def test_error_text_truncates():
    t = mq.error_text("API 500", "x" * 6000)
    assert t.startswith("Error: API 500 | ") and t.endswith("…[truncated]") and len(t) < 5100


# --- --queue 를 생략하면 두 큐 모두 (dwr -> drq) ---
# [TH] ถ้าไม่ระบุ --queue จะทำทั้งสองคิว (dwr -> drq)
# [EN] Without --queue both queues are processed (dwr -> drq)
def test_queue_defaults_to_both_and_can_pick_one():
    assert mq.build_parser().parse_args([]).queue is None
    assert mq.queues_to_run(None) == ["dwr", "drq"]
    assert mq.queues_to_run("drq") == ["drq"] and mq.queues_to_run("dwr") == ["dwr"]
    assert mq.build_parser().parse_args(["--queue", "drq"]).queue == "drq"
    with pytest.raises(SystemExit):
        mq.build_parser().parse_args(["--queue", "bogus"])


class RunRecorder:
    def __init__(self, behaviour=None):
        self.calls = []
        self.behaviour = behaviour or {}

    def __call__(self, args, clients, log=print):
        self.calls.append((args.queue, args.hint_other_queue, args.limit))
        outcome = self.behaviour.get(args.queue)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome or mq.Summary(done=1)


def main_with(monkeypatch, argv, recorder, sid="SID"):
    # 시트 ID 는 환경 변수로만 지정 (sid=None 이면 변수를 지움)
    # [TH] ID ชีตกำหนดผ่านตัวแปรสภาพแวดล้อมเท่านั้น (sid=None คือลบตัวแปร)
    # [EN] The sheet ID is set through the environment variable only (sid=None removes the variable)
    if sid is None:
        monkeypatch.delenv("PMIS_SPREADSHEET_ID", raising=False)
    else:
        monkeypatch.setenv("PMIS_SPREADSHEET_ID", sid)
    monkeypatch.setattr(mq, "make_clients", lambda args, need_gemini: mq.Clients(sheets=None, drive_factory=None, gemini=None, api_key="K", sa_email="sa@x"))
    monkeypatch.setattr(mq, "run", recorder)
    return mq.main(argv)


def test_main_without_queue_runs_dwr_then_drq(monkeypatch, capsys):
    rec = RunRecorder()
    assert main_with(monkeypatch, ["--limit", "7"], rec) == 0
    assert rec.calls == [("dwr", False, 7), ("drq", False, 7)]   # --limit 은 큐마다 적용
    out = capsys.readouterr().out
    assert out.index("MIGRATION_QUEUE ->") < out.index("MIGRATION_QUEUE_DRQ ->") and "All queues: Done=2, Error=0" in out


def test_main_with_queue_runs_only_that_queue(monkeypatch, capsys):
    rec = RunRecorder()
    assert main_with(monkeypatch, ["--queue", "drq"], rec) == 0
    assert rec.calls == [("drq", True, 30)]
    assert "All queues" not in capsys.readouterr().out


def test_main_stops_before_second_queue_when_first_stops(monkeypatch, capsys):
    rec = RunRecorder({"dwr": mq.Summary(done=1, stopped_reason="Gemini answered API 402")})
    assert main_with(monkeypatch, [], rec) == 2
    assert [c[0] for c in rec.calls] == ["dwr"]
    assert "skipping the remaining queues: MIGRATION_QUEUE_DRQ" in capsys.readouterr().out


def test_main_continues_with_other_queue_when_one_cannot_be_read(monkeypatch, capsys):
    rec = RunRecorder({"dwr": mq.SheetsError("HTTP 400: Unable to parse range")})
    assert main_with(monkeypatch, [], rec) == 2
    assert [c[0] for c in rec.calls] == ["dwr", "drq"]
    assert "Could not process queue MIGRATION_QUEUE" in capsys.readouterr().out


def test_only_rows_requires_a_single_queue(monkeypatch):
    rec = RunRecorder()
    with pytest.raises(SystemExit) as e:
        main_with(monkeypatch, ["--only-rows", "83"], rec)
    assert "--queue" in str(e.value) and rec.calls == []
    assert main_with(monkeypatch, ["--only-rows", "83", "--queue", "dwr"], rec) == 0 and [c[0] for c in rec.calls] == ["dwr"]


def test_check_access_hint_for_other_queue_only_when_single_queue():
    w = world(["Waiting"])
    logs = []
    mq.run(ns(check_access=True, hint_other_queue=False), w.clients(), log=logs.append)
    assert not any("--check-access --queue" in m for m in logs)
    logs = []
    mq.run(ns(check_access=True), w.clients(), log=logs.append)
    assert any("--check-access --queue drq" in m for m in logs)


# --- 처리할 행이 0 일 때의 안내 / 시트 이름 표시 ---
# [TH] ข้อความอธิบายเมื่อได้ 0 แถว / แสดงชื่อชีต
# [EN] Explanation when 0 rows are found / showing the sheet name
def run_logs(w, **over):
    logs = []
    mq.run(ns(**over), w.clients(), log=logs.append)
    return logs


def test_zero_rows_in_empty_queue_points_to_the_spreadsheet_id():
    w = FakeWorld([])
    logs = run_logs(w)
    assert any("empty (header only" in m for m in logs)
    assert any("has no data rows" in m and "different spreadsheet" in m for m in logs) and any("ID: SID" in m for m in logs)
    assert w.gemini_calls == 0


def test_zero_rows_when_everything_is_done_lists_the_statuses():
    w = world(["Done", "Done", "Done"])
    logs = run_logs(w)
    assert any("Rows to process: 0" in m for m in logs)
    assert any("'Done'×3" in m and "'Waiting'" in m for m in logs) and any("Every row is already Done" in m for m in logs)


def test_zero_rows_with_unexpected_status_text_shows_it_exactly():
    w = world(["waiting", "Pending", "Pending"])
    logs = run_logs(w)
    assert any("'Pending'×2" in m and "'waiting'×1" in m and "case-sensitive" in m for m in logs)


def test_zero_rows_with_blank_status_suggests_include_blank():
    w = world(["", ""])
    logs = run_logs(w)
    assert any("2 rows have a blank column G but a URL" in m and "--include-blank" in m for m in logs)
    assert w.gemini_calls == 0
    # --include-blank 을 주면 그 행들이 처리됨 / [TH] ใส่ --include-blank แล้วแถวเหล่านั้นถูกทำ / [EN] with --include-blank those rows are processed
    assert mq.run(ns(include_blank=True), w.clients(), log=lambda m: None).done == 2


def test_zero_rows_warns_when_column_g_header_is_not_ai_processed():
    w = world(["Done"])
    w.queue[0][6] = "Status"
    assert any("The column G header is 'Status'" in m for m in run_logs(w))


def test_no_explanation_when_there_are_rows_to_process():
    w = world(["Waiting"])
    logs = run_logs(w)
    assert not any("The script processes only rows whose column G" in m or "has no data rows" in m for m in logs)


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
    assert mq.spreadsheet_title(ok, "SID") == ("Example Spreadsheet", "")
    assert ok.calls == [("GET", f"{mq.SHEETS_BASE}/SID", {"fields": "properties.title"})]
    title, err = mq.spreadsheet_title(Sess(Resp(403, text="denied")), "SID")
    assert title == "" and "403" in err and "denied" in err
    title, err = mq.spreadsheet_title(Sess(RuntimeError("net")), "SID")
    assert title == "" and "net" in err
    assert mq.spreadsheet_title(None, "SID")[0] == ""


def test_main_prints_the_sheet_title_first(monkeypatch, capsys):
    rec = RunRecorder()
    monkeypatch.setattr(mq, "spreadsheet_title", lambda sess, sid: ("Example Spreadsheet", ""))
    assert main_with(monkeypatch, ["--queue", "dwr"], rec, sid="ABC") == 0
    out = capsys.readouterr().out
    assert "sheet: Example Spreadsheet (ABC)" in out
    # 큐 제목 줄에도 시트 이름이 나옴 / [TH] หัวของคิวก็มีชื่อชีต / [EN] the queue heading also carries the sheet name
    assert "===== Queue DWR: MIGRATION_QUEUE -> DB_DWR | sheet: Example Spreadsheet =====" in out
    main_with(monkeypatch, [], rec, sid="ABC")
    out = capsys.readouterr().out
    assert "===== Queue DRQ: MIGRATION_QUEUE_DRQ -> DB_DRQ | sheet: Example Spreadsheet =====" in out
    monkeypatch.setattr(mq, "spreadsheet_title", lambda sess, sid: ("", "HTTP 403: The caller does not have permission"))
    main_with(monkeypatch, ["--queue", "dwr"], rec, sid="ABC")
    out = capsys.readouterr().out
    assert "could not read the sheet name" in out and "HTTP 403" in out and "===== Queue DWR: MIGRATION_QUEUE -> DB_DWR =====\n" in out


# ---------------------------------------------------------------------------
# JSON Parse (예: 보고서가 여러 개 든 PDF) / JSON Parse hardening / ทนต่อ JSON พัง
# ---------------------------------------------------------------------------
def gresp(text, finish="STOP"):
    cand = {"content": {"parts": [{"text": text}]}}
    if finish:
        cand["finishReason"] = finish
    return Resp(200, {"candidates": [cand], "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}})


def test_parse_model_json_tolerates_raw_newlines_and_surrounding_prose():
    # 문자열 안의 진짜 줄바꿈/탭 (strict JSON 이면 "Invalid control character")
    # [TH] ขึ้นบรรทัดใหม่/แท็บดิบๆ ในสตริง (JSON แบบ strict จะ error "Invalid control character")
    # [EN] Raw newline/tab inside a string (strict JSON fails with "Invalid control character")
    assert mq.parse_model_json('{"Activities": "line1\nline2\tx"}') == {"Activities": "line1\nline2\tx"}
    # 앞뒤 설명 문장
    # [TH] มีข้อความอธิบายนำหน้า/ตามหลัง
    # [EN] Prose before and after the JSON
    assert mq.parse_model_json('Here is the result:\n{"a": {"b": [1, 2]}}\nHope this helps {not json}') == {"a": {"b": [1, 2]}}
    assert mq.parse_model_json('```json\n{"a":1}\n```\nDone.') == {"a": 1}


def test_parse_model_json_still_rejects_truncated_and_bad_quotes():
    truncated = '{"DWR_No": "X", "Manpower_Details": [{"role": "CM", "count": 10}, {"role": "S'
    with pytest.raises(json.JSONDecodeError):
        mq.parse_model_json(truncated)
    # 따옴표를 이스케이프 하지 않은 경우 (예: 6" pipe)
    # [TH] เครื่องหมายคำพูดที่ไม่ได้ escape (เช่น ท่อ 6")
    # [EN] Unescaped double quote (e.g. a 6" pipe)
    with pytest.raises(json.JSONDecodeError):
        mq.parse_model_json('{"Activities": "install 6" pipe", "Issues_Delays": null}')
    with pytest.raises(ValueError):
        mq.parse_model_json("sorry, I cannot read this file")


def test_json_parse_error_cell_has_reason_finish_reason_and_tail():
    w = world(["Waiting"])
    long_broken = '{"DWR_No": "X", "Activities": "' + "a" * 3000 + "ZZTAIL"
    w.gemini_script = [gresp(long_broken, finish="MAX_TOKENS")]
    s = mq.run(ns(workers=1), w.clients(), log=lambda m: None)
    assert s.errors == {"JSON Parse": 1} and w.db[1:] == []
    cell = w.queue[1][6]
    assert cell.startswith("Error: JSON Parse | ")
    assert "JSONDecodeError" in cell and "finishReason=MAX_TOKENS" in cell and "--max-output-tokens" in cell
    # 뒷부분이 보여야 어디서 잘렸는지 알 수 있음 / the tail shows where the answer was cut
    # [TH] ต้องเห็นส่วนท้ายถึงจะรู้ว่าถูกตัดตรงไหน
    assert f"AI text ({len(long_broken)} chars)" in cell and "ZZTAIL" in cell
    assert len(cell) <= mq.MAX_ERROR_CHARS + 50


def test_parse_retries_recovers_when_second_answer_is_valid():
    w = world(["Waiting"])
    w.gemini_script = [gresp('{"Activities": "install 6" pipe"}')]  # 첫 답은 깨짐, 두 번째는 기본 응답(정상) / first is broken, second is the valid default
    logs = []
    s = mq.run(ns(workers=1, parse_retries=1), w.clients(), log=logs.append)
    assert (s.done, s.errors) == (1, {}) and w.gemini_calls == 2
    assert w.queue[1][6] == "Done" and len(w.db) == 2
    assert any("after retrying 1x" in m for m in logs)


def test_parse_retries_gives_up_and_reports_count():
    w = world(["Waiting"])
    w.gemini_script = [gresp("nope")] * 3
    s = mq.run(ns(workers=1, parse_retries=2), w.clients(), log=lambda m: None)
    assert s.errors == {"JSON Parse": 1} and w.gemini_calls == 3
    assert "retried 2x" in w.queue[1][6]


def test_parse_retries_zero_means_single_call_and_cli_default_is_one():
    w = world(["Waiting"])
    w.gemini_script = [gresp("nope")]
    s = mq.run(ns(workers=1, parse_retries=0), w.clients(), log=lambda m: None)
    assert s.errors == {"JSON Parse": 1} and w.gemini_calls == 1
    p = mq.build_parser()
    assert p.parse_args([]).parse_retries == 1 and p.parse_args([]).max_output_tokens is None


def test_parse_retry_does_not_apply_to_other_errors():
    w = world(["Waiting"])
    w.gemini_script = [Resp(200, {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}})]
    s = mq.run(ns(workers=1, parse_retries=3), w.clients(), log=lambda m: None)
    assert s.errors == {"No Result": 1} and w.gemini_calls == 1


def test_max_output_tokens_only_sent_when_requested():
    w = world(["Waiting"])
    mq.run(ns(workers=1), w.clients(), log=lambda m: None)
    assert "maxOutputTokens" not in w.gemini_configs[0]
    w2 = world(["Waiting"])
    mq.run(ns(workers=1), w2.clients(max_output_tokens=16000), log=lambda m: None)
    assert w2.gemini_configs[0]["maxOutputTokens"] == 16000 and w2.gemini_configs[0]["response_mime_type"] == "application/json"


# ---------------------------------------------------------------------------
# AI 가 보고서를 여러 개(list) 돌려준 경우 ("expected a JSON object, got list")
# [TH] กรณี AI ตอบกลับมาหลายรายงาน (list) เช่น "expected a JSON object, got list"
# [EN] The AI answered with several reports (a list), e.g. "expected a JSON object, got list"
# ---------------------------------------------------------------------------
def rep_json(no, **over):
    return {**DWR_JSON, "DWR_No": no, **over}


def test_norm_doc_no_ignores_case_separators_and_leading_zeros():
    n = mq.norm_doc_no
    assert n("ACME-DWR-CIV-0231") == n("acme dwr civ 231") == n(" ACME_DWR_CIV_231 ") == "ACMEDWRCIV231"
    assert n("ACME-DWR-CIV-0231") != n("ACME-DWR-CIV-0232")
    assert n("DWR-100") == "DWR100" and n("DWR-010") == "DWR10" and n(None) == "" and n("") == ""
    assert n("CIV-0") == "CIV0"  # 숫자 0 하나는 그대로 / a lone 0 stays


def test_pick_report_rules():
    a, b = rep_json("ACME-DWR-CIV-0231"), rep_json("ACME-DWR-CIV-0232", Date="2026-07-15")
    pick = lambda obj, **kw: mq.pick_report(obj, kw.pop("doc_no", "ACME-DWR-CIV-231"), "DWR_No", **kw)
    assert pick(a) == (a, "") and pick([a]) == (a, "")
    # 같은 보고서 중복 -> 첫 번째
    # [TH] รายงานเดียวกันซ้ำ -> ตัวแรก  [EN] Identical duplicates -> the first one
    d, note = pick([a, dict(a)], doc_no="")
    assert d == a and "same report 2 times" in note
    # Doc No 로 선택 (순서/0 채움/대소문자 무관)
    # [TH] เลือกด้วย Doc No (ไม่สนลำดับ/เลข 0 นำหน้า/ตัวพิมพ์)  [EN] Choose by Doc No (order, zero padding and case do not matter)
    d, note = pick([b, a])
    assert d == a and "2 reports" in note and "matches Doc No" in note
    d, note = pick([a, rep_json("acme-dwr-civ-231", Activities="other page")])
    assert d == a and "2 reports match" in note
    # 선택 불가
    # [TH] เลือกไม่ได้  [EN] Cannot choose
    with pytest.raises(mq.MultiReportError) as ei:
        pick([b, rep_json("ACME-DWR-CIV-0233")])
    msg = str(ei.value)
    assert "2 reports" in msg and "#1 DWR_No=ACME-DWR-CIV-0232 Date=2026-07-15" in msg and "#2 DWR_No=ACME-DWR-CIV-0233" in msg
    with pytest.raises(mq.MultiReportError):
        pick([a, b], doc_no="")  # 큐에 Doc No 가 없으면 고를 근거가 없음 / no Doc No -> nothing to match on
    d, note = pick([b, rep_json("ACME-DWR-CIV-0233")], accept_first=True)
    assert d == b and "--accept-first-report" in note
    # 비어 있거나 dict 가 아닌 것
    # [TH] ว่างหรือไม่ใช่ dict  [EN] Empty or not objects
    for bad in ([], [1, "x"], "text", 5):
        with pytest.raises(ValueError):
            pick(bad)
    assert issubclass(mq.MultiReportError, ValueError)


def test_multi_report_picks_matching_doc_no_and_flags_row():
    w = world(["Waiting"])
    w.queue[1][0] = "ACME-DWR-CIV-231"
    other, mine = rep_json("ACME-DWR-CIV-0232", Activities="WRONG"), rep_json("ACME-DWR-CIV-0231", Activities="RIGHT")
    w.gemini_script = [gresp(json.dumps([other, mine]))]
    logs = []
    s = mq.run(ns(workers=1, parse_retries=0), w.clients(), log=logs.append)
    assert (s.done, s.multi_report, s.errors) == (1, 1, {}) and w.gemini_calls == 1
    assert w.queue[1][6] == "Done" and len(w.db) == 2
    row = w.db[1]
    assert row[1] == "ACME-DWR-CIV-0231" and row[12] == "RIGHT"
    assert any("⚠ row 2" in m and "Doc No" in m for m in logs)
    assert any("1 had several reports" in m for m in logs)


def test_multi_report_no_match_retries_with_single_report_hint():
    w = world(["Waiting"])
    w.queue[1][0] = "ACME-DWR-CIV-231"
    wrong = [rep_json("ACME-DWR-CIV-0232"), rep_json("ACME-DWR-CIV-0233")]
    w.gemini_script = [gresp(json.dumps(wrong))]  # 두 번째 응답은 기본값(dict) / the 2nd answer is the default (a plain dict)
    s = mq.run(ns(workers=1, parse_retries=1), w.clients(), log=lambda m: None)
    assert (s.done, s.errors) == (1, {}) and w.gemini_calls == 2
    assert w.gemini_prompts[0] == mq.SPECS["dwr"].prompt  # 첫 요청 = Apps Script 와 같은 프롬프트 / first request = the Apps Script prompt
    assert w.gemini_prompts[1].startswith(mq.SPECS["dwr"].prompt) and "ONE JSON object" in w.gemini_prompts[1]
    assert '"ACME-DWR-CIV-231"' in w.gemini_prompts[1]


def test_json_parse_retry_for_broken_json_keeps_original_prompt():
    w = world(["Waiting"])
    w.gemini_script = [gresp("nope")]
    mq.run(ns(workers=1, parse_retries=1), w.clients(), log=lambda m: None)
    assert w.gemini_prompts == [mq.SPECS["dwr"].prompt] * 2


def test_multi_report_unresolved_error_cell_lists_every_report():
    w = world(["Waiting"])
    w.queue[1][0] = "ACME-DWR-CIV-231"
    w.gemini_script = [gresp(json.dumps([rep_json("ACME-DWR-CIV-0232"), rep_json("ACME-DWR-CIV-0233")]))] * 2
    s = mq.run(ns(workers=1, parse_retries=1), w.clients(), log=lambda m: None)
    assert s.errors == {"JSON Parse": 1} and w.db[1:] == [] and w.gemini_calls == 2
    cell = w.queue[1][6]
    assert cell.startswith("Error: JSON Parse | MultiReportError: AI returned 2 reports")
    assert "#1 DWR_No=ACME-DWR-CIV-0232" in cell and "#2 DWR_No=ACME-DWR-CIV-0233" in cell and "retried 1x" in cell


def test_accept_first_report_option():
    w = world(["Waiting"])
    w.queue[1][0] = "ACME-DWR-CIV-231"
    w.gemini_script = [gresp(json.dumps([rep_json("ACME-DWR-CIV-0232", Activities="FIRST"), rep_json("ACME-DWR-CIV-0233")]))]
    s = mq.run(ns(workers=1, parse_retries=0, accept_first_report=True), w.clients(), log=lambda m: None)
    assert (s.done, s.multi_report) == (1, 1) and w.db[1][12] == "FIRST"
    assert mq.build_parser().parse_args([]).accept_first_report is False


def test_select_jobs_carries_doc_no_from_column_a():
    vals = [["h"] * 7, qrow(1, "Waiting")]
    vals[1][0] = "  ACME-DWR-CIV-0231 "
    assert [j.doc_no for j in mq.select_jobs(vals, retry_errors=True, include_blank=False, only_rows=None)] == ["ACME-DWR-CIV-0231"]
    assert mq.SPECS["dwr"].no_key == "DWR_No" and mq.SPECS["drq"].no_key == "DRQ_No"


# ---------------------------------------------------------------------------
# 시트 ID: 환경 변수만 / Sheet ID: environment variable only / ID ชีต: ตัวแปรสภาพแวดล้อมเท่านั้น
# ---------------------------------------------------------------------------
def test_sheet_id_has_no_default_and_no_cli_option(capsys):
    assert not hasattr(mq, "DEFAULT_SPREADSHEET_ID")
    src = open(mq.__file__, encoding="utf-8").read()
    # no Google-Sheets-ID-like token (44 chars, starts with "1") is hard-coded
    assert not re.search(r"(?<![A-Za-z0-9_-])1[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])", src)
    assert not hasattr(mq.build_parser().parse_args([]), "spreadsheet_id")
    with pytest.raises(SystemExit) as e:
        mq.build_parser().parse_args(["--spreadsheet-id", "ABC"])
    assert e.value.code == 2 and "unrecognized arguments" in capsys.readouterr().err


def test_require_spreadsheet_id_reads_only_the_environment():
    assert mq.require_spreadsheet_id({"PMIS_SPREADSHEET_ID": "  ABC123  "}) == "ABC123"
    for env in ({}, {"PMIS_SPREADSHEET_ID": ""}, {"PMIS_SPREADSHEET_ID": "   "}):
        with pytest.raises(SystemExit) as e:
            mq.require_spreadsheet_id(env)
        msg = str(e.value.code)
        assert "PMIS_SPREADSHEET_ID" in msg and '$env:PMIS_SPREADSHEET_ID = "<ID>"' in msg and "/d/" in msg and "/edit" in msg


def test_main_requires_the_sheet_id_before_touching_credentials(monkeypatch, capsys):
    def boom(*a, **k):
        raise AssertionError("make_clients must not be reached without a sheet ID")

    monkeypatch.setattr(mq, "make_clients", boom)
    monkeypatch.delenv("PMIS_SPREADSHEET_ID", raising=False)
    for argv in ([], ["--dry-run"], ["--check-access"], ["--queue", "drq", "--limit", "5"]):
        with pytest.raises(SystemExit) as e:
            mq.main(argv)
        assert "PMIS_SPREADSHEET_ID" in str(e.value.code)


def test_main_passes_the_env_sheet_id_to_the_run(monkeypatch):
    seen = []

    def spy(args, clients, log=print):
        seen.append(args.spreadsheet_id)
        return mq.Summary()

    monkeypatch.setattr(mq, "spreadsheet_title", lambda sess, sid: ("T", ""))
    monkeypatch.setattr(mq, "make_clients", lambda args, need_gemini: mq.Clients(sheets=None, drive_factory=None, gemini=None, api_key="K", sa_email="sa@x"))
    monkeypatch.setattr(mq, "run", spy)
    monkeypatch.setenv("PMIS_SPREADSHEET_ID", "FROM-ENV")
    assert mq.main(["--queue", "dwr"]) == 0
    assert seen == ["FROM-ENV"]


# ---------------------------------------------------------------------------
# 시트 오류 셀은 영어만 / Sheet error cells are English only / เซลล์ Error ในชีตเป็นภาษาอังกฤษเท่านั้น
# ---------------------------------------------------------------------------
NON_ENGLISH = re.compile(r"[\u0E00-\u0E7F\uAC00-\uD7A3]")  # Thai, Hangul


def _cell_after(w, **over):
    mq.run(ns(workers=1, **over), w.clients(), log=lambda m: None)
    return w.queue[1][6]


def _error_cell_scenarios():
    """(이름, 셀 문구) 목록: 시트에 Error 를 남기는 모든 경로 / every path that leaves an Error in the sheet"""
    out = []
    w = world(["Waiting"]); w.queue[1][3] = ""
    out.append(("No URL", _cell_after(w)))
    w = world(["Waiting"]); del w.drive_files[FID(1)]
    out.append(("Drive Access (hint)", _cell_after(w)))
    w = world(["Waiting"]); w.drive_files[FID(1)]["size"] = 30_000_000
    out.append(("Too Large", _cell_after(w, no_thumbnail=True)))
    w = world(["Waiting"]); w.drive_files[FID(1)]["size"] = 30_000_000
    out.append(("Cover capture failed", _cell_after(w)))
    w = world(["Waiting"]); w.gemini_script = [gresp("x" * 3000, finish="MAX_TOKENS")] * 2
    out.append(("JSON Parse, truncated, retried", _cell_after(w, parse_retries=1)))
    w = world(["Waiting"]); w.queue[1][0] = "ACME-DWR-CIV-231"
    w.gemini_script = [gresp(json.dumps([rep_json("ACME-DWR-CIV-0232"), rep_json("ACME-DWR-CIV-0233")]))] * 2
    out.append(("Multiple reports", _cell_after(w, parse_retries=1)))
    w = world(["Waiting"]); w.gemini_script = [Resp(200, {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}})]
    out.append(("No Result", _cell_after(w)))
    w = world(["Waiting"]); w.gemini_script = [Resp(400, text="bad request")]
    out.append(("API 400", _cell_after(w)))
    w = world(["Waiting"]); w.gemini_script = [Resp(402, text="payment required")]
    out.append(("API 402 (fatal)", _cell_after(w)))
    w = world(["Waiting"]); w.fail_append = True
    out.append(("DB Write", _cell_after(w)))
    return out


def test_every_error_written_to_the_sheet_is_english_only():
    scenarios = _error_cell_scenarios()
    assert len(scenarios) == 10
    for name, cell in scenarios:
        assert cell.startswith("Error: "), (name, cell[:120])
        assert not NON_ENGLISH.search(cell), (name, NON_ENGLISH.search(cell).group(0), cell[:300])


def test_english_hints_in_the_sheet_cells():
    cells = dict(_error_cell_scenarios())
    assert "is not shared with the service account" in cells["Drive Access (hint)"]
    assert "cover capture failed" in cells["Cover capture failed"]
    assert "retried 1x" in cells["JSON Parse, truncated, retried"] and "the answer was cut off at the output token limit" in cells["JSON Parse, truncated, retried"]
    assert "[middle omitted]" in cells["JSON Parse, truncated, retried"]
