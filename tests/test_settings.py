"""
환경 변수/.env 설정 테스트
[TH] เทสต์การตั้งค่าจากตัวแปรสภาพแวดล้อม/.env
[EN] Tests for the settings from the environment / .env
"""

import os

import pytest

from migrator.cli import build_parser
from migrator.settings import load_env_file, parse_spreadsheet_id, require_legacy_sheet_names, require_spreadsheet_id, resolve_legacy_spreadsheet_id


def test_require_spreadsheet_id_reads_only_the_environment():
    assert require_spreadsheet_id({"PMIS_SPREADSHEET_ID": "  ABC123  "}) == "ABC123"
    for env in ({}, {"PMIS_SPREADSHEET_ID": ""}, {"PMIS_SPREADSHEET_ID": "   "}):
        with pytest.raises(SystemExit) as e:
            require_spreadsheet_id(env)
        msg = str(e.value.code)
        assert "PMIS_SPREADSHEET_ID" in msg and '$env:PMIS_SPREADSHEET_ID = "<ID>"' in msg and "/d/" in msg and "/edit" in msg


# --- 설정 / CLI ---
def test_legacy_sheet_names_come_only_from_the_environment():
    env = {"PMIS_LEGACY_DWR_SHEET": "Tab (DWR)", "PMIS_LEGACY_DRQ_SHEET": "  "}
    assert require_legacy_sheet_names(["dwr"], env) == {"dwr": "Tab (DWR)"}
    with pytest.raises(SystemExit) as e:
        require_legacy_sheet_names(["dwr", "drq"], env)
    msg = str(e.value.code)
    assert "PMIS_LEGACY_DRQ_SHEET" in msg and "PMIS_LEGACY_DWR_SHEET" not in msg and '$env:PMIS_LEGACY_DRQ_SHEET = "<' in msg
    assert not hasattr(build_parser().parse_args(["sync"]), "legacy_sheet")           # 명령줄 옵션 없음 / [TH] ไม่มีออปชันบรรทัดคำสั่ง / [EN] no command-line option


def test_load_env_file_sets_missing_variables_but_never_overrides(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("PMIS_TEST_NEW=from-file\nPMIS_TEST_KEPT=from-file\n", encoding="utf-8")
    monkeypatch.delenv("PMIS_TEST_NEW", raising=False)
    monkeypatch.setenv("PMIS_TEST_KEPT", "from-environment")
    assert load_env_file(str(env_file)) is True
    assert os.environ["PMIS_TEST_NEW"] == "from-file"
    assert os.environ["PMIS_TEST_KEPT"] == "from-environment"
    monkeypatch.delenv("PMIS_TEST_NEW", raising=False)


def test_load_env_file_does_nothing_without_a_file(tmp_path):
    assert load_env_file(str(tmp_path / "missing.env")) is False


def test_parse_spreadsheet_id_accepts_an_id_or_a_full_url():
    assert parse_spreadsheet_id("  abc_DEF-123 ") == "abc_DEF-123"
    assert parse_spreadsheet_id("https://docs.google.com/spreadsheets/d/abc_DEF-123/edit?gid=0#gid=0") == "abc_DEF-123"
    assert parse_spreadsheet_id("https://docs.google.com/spreadsheets/u/1/d/abc123/edit") == "abc123"
    assert parse_spreadsheet_id(None) == ""


def test_resolve_legacy_spreadsheet_id_priority_and_validation():
    env = {"PMIS_LEGACY_SPREADSHEET_ID": "FROM_ENV"}
    assert resolve_legacy_spreadsheet_id(None, "TARGET", {}) == "TARGET"                      # default: the same file
    assert resolve_legacy_spreadsheet_id("  ", "TARGET", {}) == "TARGET"
    assert resolve_legacy_spreadsheet_id(None, "TARGET", env) == "FROM_ENV"
    assert resolve_legacy_spreadsheet_id("https://docs.google.com/spreadsheets/d/FROM_CLI/edit", "TARGET", env) == "FROM_CLI"
    with pytest.raises(SystemExit) as e:
        resolve_legacy_spreadsheet_id("not a valid id!", "TARGET", {})
    assert "not a valid ID or URL" in str(e.value.code)
