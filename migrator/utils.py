"""
작은 순수 헬퍼 (파일 ID, 오류 문구, 행 범위, backoff 등)
[TH] ฟังก์ชันช่วยเล็กๆ ที่ไม่มีผลข้างเคียง (file ID, ข้อความ error, ช่วงแถว, backoff ฯลฯ)
[EN] Small pure helpers (file IDs, error text, row ranges, backoff, ...)
"""
from __future__ import annotations

import random
import re
from typing import Any, Optional

from .config import MAX_ERROR_CHARS, MAX_REMOVAL_PERCENT, MIN_REMOVAL_LIMIT


_ID_PATTERNS = (
    re.compile(r"/d/([A-Za-z0-9_-]{20,})"),
    re.compile(r"[?&]id=([A-Za-z0-9_-]{20,})"),
    # 마지막 대체 패턴 = 기존 extractFileIdFromUrl
    # [TH] fallback = extractFileIdFromUrl เดิม
    # [EN] Last-resort fallback = the old extractFileIdFromUrl
    re.compile(r"[-\w]{25,}"),
)


def extract_file_id(url: Any) -> Optional[str]:
    if not url:
        return None
    text = str(url)
    for pat in _ID_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(m.lastindex or 0)
    return None


def error_text(label: str, detail: Any = "") -> str:
    text = "Error: " + label
    if detail is not None and str(detail) != "":
        text += " | " + str(detail)
    if len(text) > MAX_ERROR_CHARS:
        text = text[:MAX_ERROR_CHARS] + " …[truncated]"
    return text


def parse_row_spec(spec: str) -> set[int]:
    """
    '83,127-128,500' -> {83,127,128,500}  (시트의 행 번호)
    [TH] '83,127-128,500' -> {83,127,128,500}  (เลขแถวในชีต)
    [EN] '83,127-128,500' -> {83,127,128,500}  (sheet row numbers)
    """
    rows: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            rows.update(range(int(a), int(b) + 1))
        else:
            rows.add(int(part))
    return rows


def backoff_seconds(attempt: int, hint: float = 0.0) -> float:
    return max(hint, min(60.0, 2.0 ** attempt)) + random.uniform(0, 1)


def retry_delay_hint(body_text: str) -> float:
    m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', body_text or "")
    return min(float(m.group(1)), 120.0) if m else 0.0


def chunked(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


COUNT_LEADER = "·"


def count_lines(rows: list[tuple[str, str, str]], indent: str = "    ") -> str:
    """
    "이름 ····· 값  (설명)" 꼴로 값 열을 맞춘 줄들 (한 줄에 개수를 길게 늘어놓는 대신). 공개 로그도 이 줄들을 통과시킴 (개수와 고정 문구만 들어 있음)
    [TH] บรรทัดที่จัดคอลัมน์ค่าให้ตรงกันในรูป "ชื่อ ····· ค่า  (คำอธิบาย)" (แทนการเรียงตัวเลขยาว ๆ ในบรรทัดเดียว) log สาธารณะก็ปล่อยบรรทัดเหล่านี้ผ่าน (มีแต่ตัวเลขและข้อความคงที่)
    [EN] Lines in the form "label ····· value  (note)" with the value column aligned (instead of a long run of counts on one line).
         The public log lets these lines through (they hold only counts and fixed wording)
    """
    width = max(len(label) for label, _, _ in rows) + 4
    return "\n".join(f"{indent}{label} {COUNT_LEADER * (width - len(label) - 1)} {value}" + (f"  {note}" if note else "") for label, value, note in rows)


def removal_limit(total: int) -> int:
    """
    한 번에 지울 수 있는 최대 행 수: 전체 행의 10% 와 100 중 큰 값
    [TH] จำนวนแถวสูงสุดที่ลบได้ในครั้งเดียว: ค่าที่มากกว่าระหว่าง 10% ของแถวทั้งหมดกับ 100
    [EN] The most rows one run may remove: the larger of 10% of all rows and 100
    """
    return max(MIN_REMOVAL_LIMIT, -(-total * MAX_REMOVAL_PERCENT // 100))
