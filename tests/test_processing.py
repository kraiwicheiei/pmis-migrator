"""
한 행 처리 테스트: 큰 PDF, JSON 재시도, 여러 보고서, 시트에 쓰는 영어 문구
[TH] เทสต์การประมวลผลหนึ่งแถว: PDF ใหญ่, retry JSON, หลายรายงาน, ข้อความภาษาอังกฤษที่เขียนลงชีต
[EN] Tests for processing one row: big PDFs, JSON retries, several reports, the English text written to the sheet
"""

import json

from migrator.cli import build_parser
from migrator.config import MAX_ERROR_CHARS
from migrator.runner import run
from migrator.specs import SPECS

from tests.fakes import FID, NON_ENGLISH, Resp, error_cell_scenarios, gresp, ns, rep_json, world


def test_too_large_pdf_not_sent_when_thumbnail_disabled():
    w = world(["Waiting"])
    w.drive_files[FID(1)]["size"] = 30_000_000
    s = run(ns(no_thumbnail=True), w.clients(), log=lambda m: None)
    assert s.errors == {"Too Large": 1} and w.gemini_calls == 0 and w.thumbnail_calls == []


def test_large_pdf_sent_as_cover_thumbnail_like_apps_script():
    w = world(["Waiting"])
    w.drive_files[FID(1)].update(size=30_000_000, thumb=b"\x89PNG-cover", thumb_type="image/png")
    logs = []
    s = run(ns(), w.clients(), log=logs.append)
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
    s = run(ns(), w.clients(), log=lambda m: None)
    assert s.done == 2 and s.cover_only == 1
    assert w.downloads == [FID(1)] and w.thumbnail_calls == [FID(2)]
    assert sorted(w.gemini_mimes) == ["application/pdf", "image/png"]


def test_cover_thumbnail_without_content_type_defaults_to_jpeg():
    w = world(["Waiting"])
    w.drive_files[FID(1)].update(size=30_000_000, thumb=b"jpgbytes", thumb_type="text/html")
    s = run(ns(), w.clients(), log=lambda m: None)
    assert s.done == 1 and w.gemini_mimes == ["image/jpeg"]


def test_cover_thumbnail_failure_is_a_drive_access_error():
    w = world(["Waiting", "Waiting"])
    w.drive_files[FID(1)].update(size=30_000_000, thumb=None, thumb_status=403)
    s = run(ns(), w.clients(), log=lambda m: None)
    assert s.errors == {"Drive Access": 1} and s.done == 1
    assert w.queue[1][6].startswith("Error: Drive Access | fileId=") and "cover capture failed" in w.queue[1][6] and "HTTP 403" in w.queue[1][6]
    assert w.queue[2][6] == "Done"


def test_large_non_pdf_is_still_too_large():
    w = world(["Waiting"])
    w.drive_files[FID(1)].update(size=30_000_000, mime="image/png", thumb=b"x")
    s = run(ns(), w.clients(), log=lambda m: None)
    assert s.errors == {"Too Large": 1} and w.thumbnail_calls == [] and w.gemini_calls == 0


def test_check_access_reports_rows_that_will_use_the_cover():
    w = world(["Waiting", "Waiting"])
    w.drive_files[FID(2)]["size"] = 30_000_000
    logs = []
    run(ns(check_access=True), w.clients(), log=logs.append)
    assert w.gemini_calls == 0 and w.downloads == [] and w.thumbnail_calls == []
    info = [m for m in logs if "cover image" in m]
    assert len(info) == 1 and "row 3" in info[0] and "[MIGRATION_QUEUE]" in info[0]
    assert any("Reachable: 2/2" in m for m in logs)


def test_json_parse_error_cell_has_reason_finish_reason_and_tail():
    w = world(["Waiting"])
    long_broken = '{"DWR_No": "X", "Activities": "' + "a" * 3000 + "ZZTAIL"
    w.gemini_script = [gresp(long_broken, finish="MAX_TOKENS")]
    s = run(ns(workers=1), w.clients(), log=lambda m: None)
    assert s.errors == {"JSON Parse": 1} and w.db[1:] == []
    cell = w.queue[1][6]
    assert cell.startswith("Error: JSON Parse | ")
    assert "JSONDecodeError" in cell and "finishReason=MAX_TOKENS" in cell and "--max-output-tokens" in cell
    # 뒷부분이 보여야 어디서 잘렸는지 알 수 있음 / the tail shows where the answer was cut
    # [TH] ต้องเห็นส่วนท้ายถึงจะรู้ว่าถูกตัดตรงไหน
    assert f"AI text ({len(long_broken)} chars)" in cell and "ZZTAIL" in cell
    assert len(cell) <= MAX_ERROR_CHARS + 50


def test_parse_retries_recovers_when_second_answer_is_valid():
    w = world(["Waiting"])
    w.gemini_script = [gresp('{"Activities": "install 6" pipe"}')]  # 첫 답은 깨짐, 두 번째는 기본 응답(정상) / first is broken, second is the valid default
    logs = []
    s = run(ns(workers=1, parse_retries=1), w.clients(), log=logs.append)
    assert (s.done, s.errors) == (1, {}) and w.gemini_calls == 2
    assert w.queue[1][6] == "Done" and len(w.db) == 2
    assert any("after retrying 1x" in m for m in logs)


def test_parse_retries_gives_up_and_reports_count():
    w = world(["Waiting"])
    w.gemini_script = [gresp("nope")] * 3
    s = run(ns(workers=1, parse_retries=2), w.clients(), log=lambda m: None)
    assert s.errors == {"JSON Parse": 1} and w.gemini_calls == 3
    assert "retried 2x" in w.queue[1][6]


def test_parse_retries_zero_means_single_call_and_cli_default_is_one():
    w = world(["Waiting"])
    w.gemini_script = [gresp("nope")]
    s = run(ns(workers=1, parse_retries=0), w.clients(), log=lambda m: None)
    assert s.errors == {"JSON Parse": 1} and w.gemini_calls == 1
    p = build_parser()
    assert p.parse_args(["sync"]).parse_retries == 1 and p.parse_args(["sync"]).max_output_tokens is None


def test_parse_retry_does_not_apply_to_other_errors():
    w = world(["Waiting"])
    w.gemini_script = [Resp(200, {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}})]
    s = run(ns(workers=1, parse_retries=3), w.clients(), log=lambda m: None)
    assert s.errors == {"No Result": 1} and w.gemini_calls == 1


def test_max_output_tokens_only_sent_when_requested():
    w = world(["Waiting"])
    run(ns(workers=1), w.clients(), log=lambda m: None)
    assert "maxOutputTokens" not in w.gemini_configs[0]
    w2 = world(["Waiting"])
    run(ns(workers=1), w2.clients(max_output_tokens=16000), log=lambda m: None)
    assert w2.gemini_configs[0]["maxOutputTokens"] == 16000 and w2.gemini_configs[0]["response_mime_type"] == "application/json"


def test_multi_report_picks_matching_doc_no_and_flags_row():
    w = world(["Waiting"])
    w.queue[1][0] = "ACME-DWR-CIV-231"
    other, mine = rep_json("ACME-DWR-CIV-0232", Activities="WRONG"), rep_json("ACME-DWR-CIV-0231", Activities="RIGHT")
    w.gemini_script = [gresp(json.dumps([other, mine]))]
    logs = []
    s = run(ns(workers=1, parse_retries=0), w.clients(), log=logs.append)
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
    s = run(ns(workers=1, parse_retries=1), w.clients(), log=lambda m: None)
    assert (s.done, s.errors) == (1, {}) and w.gemini_calls == 2
    assert w.gemini_prompts[0] == SPECS["dwr"].prompt  # 첫 요청 = Apps Script 와 같은 프롬프트 / first request = the Apps Script prompt
    assert w.gemini_prompts[1].startswith(SPECS["dwr"].prompt) and "ONE JSON object" in w.gemini_prompts[1]
    assert '"ACME-DWR-CIV-231"' in w.gemini_prompts[1]


def test_json_parse_retry_for_broken_json_keeps_original_prompt():
    w = world(["Waiting"])
    w.gemini_script = [gresp("nope")]
    run(ns(workers=1, parse_retries=1), w.clients(), log=lambda m: None)
    assert w.gemini_prompts == [SPECS["dwr"].prompt] * 2


def test_multi_report_unresolved_error_cell_lists_every_report():
    w = world(["Waiting"])
    w.queue[1][0] = "ACME-DWR-CIV-231"
    w.gemini_script = [gresp(json.dumps([rep_json("ACME-DWR-CIV-0232"), rep_json("ACME-DWR-CIV-0233")]))] * 2
    s = run(ns(workers=1, parse_retries=1), w.clients(), log=lambda m: None)
    assert s.errors == {"JSON Parse": 1} and w.db[1:] == [] and w.gemini_calls == 2
    cell = w.queue[1][6]
    assert cell.startswith("Error: JSON Parse | MultiReportError: AI returned 2 reports")
    assert "#1 DWR_No=ACME-DWR-CIV-0232" in cell and "#2 DWR_No=ACME-DWR-CIV-0233" in cell and "retried 1x" in cell


def test_accept_first_report_option():
    w = world(["Waiting"])
    w.queue[1][0] = "ACME-DWR-CIV-231"
    w.gemini_script = [gresp(json.dumps([rep_json("ACME-DWR-CIV-0232", Activities="FIRST"), rep_json("ACME-DWR-CIV-0233")]))]
    s = run(ns(workers=1, parse_retries=0, accept_first_report=True), w.clients(), log=lambda m: None)
    assert (s.done, s.multi_report) == (1, 1) and w.db[1][12] == "FIRST"
    assert build_parser().parse_args(["sync"]).accept_first_report is False


def test_every_error_written_to_the_sheet_is_english_only():
    scenarios = error_cell_scenarios()
    assert len(scenarios) == 10
    for name, cell in scenarios:
        assert cell.startswith("Error: "), (name, cell[:120])
        assert not NON_ENGLISH.search(cell), (name, NON_ENGLISH.search(cell).group(0), cell[:300])


def test_english_hints_in_the_sheet_cells():
    cells = dict(error_cell_scenarios())
    assert "is not shared with the service account" in cells["Drive Access (hint)"]
    assert "cover capture failed" in cells["Cover capture failed"]
    assert "retried 1x" in cells["JSON Parse, truncated, retried"] and "the answer was cut off at the output token limit" in cells["JSON Parse, truncated, retried"]
    assert "[middle omitted]" in cells["JSON Parse, truncated, retried"]
