"""
Google Sheets API 호출
[TH] การเรียก Google Sheets API
[EN] Google Sheets API calls
"""
from __future__ import annotations

import urllib.parse
from typing import Any

from . import clock
from .config import RETRY_STATUSES, SHEETS_BASE
from .utils import backoff_seconds


class SheetsError(RuntimeError):
    pass


def sheets_call(sess: Any, method: str, url: str, *, retry_5xx: bool = True, max_tries: int = 5, **kw: Any) -> dict:
    last = ""
    for attempt in range(1, max_tries + 1):
        try:
            resp = sess.request(method, url, timeout=60, **kw)
        # 네트워크 오류
        # [TH] ข้อผิดพลาดเครือข่าย
        # [EN] Network error
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            if not retry_5xx:
                raise SheetsError(last) from e
        else:
            if resp.status_code == 200:
                return resp.json()
            last = f"HTTP {resp.status_code}: {resp.text[:500]}"
            retryable = resp.status_code == 429 or (retry_5xx and resp.status_code in RETRY_STATUSES)
            if not retryable:
                raise SheetsError(last)
        if attempt < max_tries:
            clock.SLEEP(backoff_seconds(attempt))
    raise SheetsError(last)


def values_url(sid: str, a1: str) -> str:
    return f"{SHEETS_BASE}/{sid}/values/{urllib.parse.quote(a1, safe='')}"


def read_values(sess: Any, sid: str, a1: str) -> list[list[str]]:
    data = sheets_call(sess, "GET", values_url(sid, a1), params={"valueRenderOption": "FORMATTED_VALUE"})
    return data.get("values", [])


def spreadsheet_title(sess: Any, sid: str) -> tuple[str, str]:
    """
    스프레드시트 이름을 가져옴. (이름, 오류) 를 반환하며 실패하면 이름은 빈 문자열이고 오류에 원인이 담김.
    시작 줄과 큐 제목에 표시해 엉뚱한 시트를 읽는 실수를 바로 알아차리게 함
    [TH] ดึงชื่อสเปรดชีต คืน (ชื่อ, ข้อผิดพลาด) ถ้าล้มเหลวชื่อจะว่างและมีสาเหตุในช่องข้อผิดพลาด
         แสดงในบรรทัดแรกและหัวของแต่ละคิว เพื่อให้สังเกตได้ทันทีถ้าชี้ผิดชีต
    [EN] Fetches the spreadsheet's name. Returns (name, error); on failure the name is empty and the error holds the cause.
         Shown on the first line and in each queue header so reading the wrong sheet is obvious
    """
    try:
        data = sheets_call(sess, "GET", f"{SHEETS_BASE}/{sid}", max_tries=2, params={"fields": "properties.title"})
    except Exception as e:  # noqa: BLE001
        return "", f"{type(e).__name__}: {e}"[:300]
    return str((data.get("properties") or {}).get("title", "")), ""


def write_statuses(sess: Any, sid: str, tab: str, updates: dict[int, str]) -> None:
    if not updates:
        return
    body = {
        "valueInputOption": "RAW",
        "data": [{"range": f"'{tab}'!G{row}", "values": [[text]]} for row, text in sorted(updates.items())],
    }
    sheets_call(sess, "POST", f"{SHEETS_BASE}/{sid}/values:batchUpdate", json=body)


def append_rows(sess: Any, sid: str, tab: str, rows: list[list], value_input: str = "USER_ENTERED") -> None:
    """
    표 끝에 추가 (행이 자동 확장됨). 5xx/타임아웃 시에는 이미 기록됐을 수 있어 재시도하지 않음 -> 중복 행 방지
    [TH] ต่อท้ายตาราง (ขยายแถวให้เอง) ไม่ retry เมื่อ 5xx/timeout เพราะอาจเขียนไปแล้ว -> กันแถวซ้ำ
    [EN] Appends to the end of the table (rows expand automatically). Does not retry on 5xx/timeout because
         the write may already have happened -> avoids duplicate rows
    """
    url = values_url(sid, f"'{tab}'!A1") + ":append"
    sheets_call(
        sess, "POST", url, retry_5xx=False,
        params={"valueInputOption": value_input, "insertDataOption": "INSERT_ROWS"},
        json={"majorDimension": "ROWS", "values": rows},
    )


def list_tabs(sess: Any, sid: str) -> dict[str, tuple[int, int]]:
    """
    탭 이름 -> (sheetId, 행 수)
    [TH] ชื่อแท็บ -> (sheetId, จำนวนแถว)
    [EN] Tab name -> (sheetId, row count)
    """
    data = sheets_call(sess, "GET", f"{SHEETS_BASE}/{sid}",
                        params={"fields": "sheets(properties(sheetId,title,gridProperties(rowCount)))"})
    tabs: dict[str, tuple[int, int]] = {}
    for sh in data.get("sheets", []):
        p = sh.get("properties") or {}
        tabs[str(p.get("title", ""))] = (int(p.get("sheetId", 0)), int((p.get("gridProperties") or {}).get("rowCount", 0)))
    return tabs
