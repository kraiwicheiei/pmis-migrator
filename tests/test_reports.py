"""
Gemini JSON 해석과 DB 행 만들기 테스트
[TH] เทสต์การแปลความ JSON ของ Gemini และการสร้างแถว DB
[EN] Tests for parsing Gemini's JSON and building the DB rows
"""

import json

import pytest

from migrator.reports import MultiReportError, build_db_row, norm_doc_no, parse_model_json, pick_report
from migrator.specs import SPECS
from migrator.utils import parse_row_spec

from tests.fakes import DWR_JSON, rep_json


def test_build_db_row_matches_apps_script_layout():
    row = build_db_row(SPECS["dwr"], DWR_JSON, "PDF", "FOLDER")
    assert len(row) == 19
    assert row[0] == "" and row[1] == "ACME-DWR-X" and row[8] == 28 and row[9] == 6
    # 공백 없는 JSON.stringify 형식
    # [TH] JSON.stringify แบบไม่มีช่องว่าง
    # [EN] JSON.stringify style, no spaces
    assert row[10] == '[{"role":"CM","count":10}]'
    assert row[13] == "" and row[16] == "AI Migrated" and row[17] == "PDF" and row[18] == "FOLDER"
    drq = build_db_row(SPECS["drq"], {"DRQ_No": "D1", "Comments": "=SUM(1)"}, "P", "F")
    assert len(drq) == 18 and drq[1] == "D1" and drq[12] == "'=SUM(1)" and drq[13] == "AI Migrated" and drq[14] == "P" and drq[17] == ""


def test_parse_model_json_and_row_spec():
    assert parse_model_json('```json\n{"a":1}\n```') == {"a": 1}
    assert parse_model_json('[{"a":1}]') == {"a": 1}
    with pytest.raises(ValueError):
        parse_model_json('"text"')
    assert parse_row_spec("83, 127-129,5") == {83, 127, 128, 129, 5}


def test_parse_model_json_tolerates_raw_newlines_and_surrounding_prose():
    # 문자열 안의 진짜 줄바꿈/탭 (strict JSON 이면 "Invalid control character")
    # [TH] ขึ้นบรรทัดใหม่/แท็บดิบๆ ในสตริง (JSON แบบ strict จะ error "Invalid control character")
    # [EN] Raw newline/tab inside a string (strict JSON fails with "Invalid control character")
    assert parse_model_json('{"Activities": "line1\nline2\tx"}') == {"Activities": "line1\nline2\tx"}
    # 앞뒤 설명 문장
    # [TH] มีข้อความอธิบายนำหน้า/ตามหลัง
    # [EN] Prose before and after the JSON
    assert parse_model_json('Here is the result:\n{"a": {"b": [1, 2]}}\nHope this helps {not json}') == {"a": {"b": [1, 2]}}
    assert parse_model_json('```json\n{"a":1}\n```\nDone.') == {"a": 1}


def test_parse_model_json_still_rejects_truncated_and_bad_quotes():
    truncated = '{"DWR_No": "X", "Manpower_Details": [{"role": "CM", "count": 10}, {"role": "S'
    with pytest.raises(json.JSONDecodeError):
        parse_model_json(truncated)
    # 따옴표를 이스케이프 하지 않은 경우 (예: 6" pipe)
    # [TH] เครื่องหมายคำพูดที่ไม่ได้ escape (เช่น ท่อ 6")
    # [EN] Unescaped double quote (e.g. a 6" pipe)
    with pytest.raises(json.JSONDecodeError):
        parse_model_json('{"Activities": "install 6" pipe", "Issues_Delays": null}')
    with pytest.raises(ValueError):
        parse_model_json("sorry, I cannot read this file")


def test_norm_doc_no_ignores_case_separators_and_leading_zeros():
    n = norm_doc_no
    assert n("ACME-DWR-CIV-0231") == n("acme dwr civ 231") == n(" ACME_DWR_CIV_231 ") == "ACMEDWRCIV231"
    assert n("ACME-DWR-CIV-0231") != n("ACME-DWR-CIV-0232")
    assert n("DWR-100") == "DWR100" and n("DWR-010") == "DWR10" and n(None) == "" and n("") == ""
    assert n("CIV-0") == "CIV0"  # 숫자 0 하나는 그대로 / a lone 0 stays


def test_pick_report_rules():
    a, b = rep_json("ACME-DWR-CIV-0231"), rep_json("ACME-DWR-CIV-0232", Date="2026-07-15")
    pick = lambda obj, **kw: pick_report(obj, kw.pop("doc_no", "ACME-DWR-CIV-231"), "DWR_No", **kw)
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
    with pytest.raises(MultiReportError) as ei:
        pick([b, rep_json("ACME-DWR-CIV-0233")])
    msg = str(ei.value)
    assert "2 reports" in msg and "#1 DWR_No=ACME-DWR-CIV-0232 Date=2026-07-15" in msg and "#2 DWR_No=ACME-DWR-CIV-0233" in msg
    with pytest.raises(MultiReportError):
        pick([a, b], doc_no="")  # 큐에 Doc No 가 없으면 고를 근거가 없음 / no Doc No -> nothing to match on
    d, note = pick([b, rep_json("ACME-DWR-CIV-0233")], accept_first=True)
    assert d == b and "--accept-first-report" in note
    # 비어 있거나 dict 가 아닌 것
    # [TH] ว่างหรือไม่ใช่ dict  [EN] Empty or not objects
    for bad in ([], [1, "x"], "text", 5):
        with pytest.raises(ValueError):
            pick(bad)
    assert issubclass(MultiReportError, ValueError)
