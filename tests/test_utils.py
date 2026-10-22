"""
작은 헬퍼 테스트
[TH] เทสต์ฟังก์ชันช่วยเล็กๆ
[EN] Tests for the small helpers
"""

from migrator.utils import error_text, extract_file_id

from tests.fakes import FID, URL


def test_extract_file_id():
    assert extract_file_id(URL(1)) == FID(1)
    assert extract_file_id(f"https://drive.google.com/file/d/{FID(2)}/view?usp=sharing") == FID(2)
    assert extract_file_id("") is None and extract_file_id(None) is None
    assert extract_file_id("short") is None


def test_error_text_truncates():
    t = error_text("API 500", "x" * 6000)
    assert t.startswith("Error: API 500 | ") and t.endswith("…[truncated]") and len(t) < 5100
