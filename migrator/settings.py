"""
환경 변수/.env 에서 설정을 읽는 함수 (시트 ID, 옛 시트 탭 이름 등)
[TH] ฟังก์ชันอ่านการตั้งค่าจากตัวแปรสภาพแวดล้อม/.env (ID ชีต, ชื่อแท็บชีตเก่า ฯลฯ)
[EN] Functions that read the settings from the environment / .env (sheet IDs, legacy tab names, ...)
"""
from __future__ import annotations

import os
import re
import sys
from typing import Any, Optional

from .specs import SPECS


# 대상 시트 ID 는 코드에 기본값을 두지 않고 이 환경 변수에서만 읽습니다 (아래는 변수 이름)
# [TH] ID ชีตเป้าหมายไม่มีค่าเริ่มต้นในโค้ด อ่านจากตัวแปรสภาพแวดล้อมนี้เท่านั้น (ด้านล่างคือชื่อตัวแปร)
# [EN] The target sheet ID has no default in the code and is read only from this environment variable (below is the variable name)
SPREADSHEET_ID_ENV = "PMIS_SPREADSHEET_ID"

# `sync queue` 가 옛 탭을 읽어 올 스프레드시트 (선택. 비우면 대상 스프레드시트와 같은 파일)
# [TH] สเปรดชีตที่ `sync queue` ใช้อ่านแท็บเก่า (ไม่บังคับ ถ้าว่างจะใช้ไฟล์เดียวกับสเปรดชีตเป้าหมาย)
# [EN] The spreadsheet `sync queue` reads the legacy tabs from (optional; empty = the same file as the target spreadsheet)
LEGACY_SPREADSHEET_ID_ENV = "PMIS_LEGACY_SPREADSHEET_ID"


def require_legacy_sheet_names(keys: list[str], environ: Optional[dict] = None) -> dict[str, str]:
    """
    `sync queue` 에 필요한 옛 시트 탭 이름을 환경 변수에서만 읽음. 비어 있으면 안내 메시지와 함께 종료
    [TH] อ่านชื่อแท็บชีตเก่าที่ `sync queue` ต้องใช้จากตัวแปรสภาพแวดล้อมเท่านั้น ถ้าว่างจะจบพร้อมข้อความแนะนำ
    [EN] Reads the legacy tab names that `sync queue` needs from the environment only; exits with a how-to message if one is empty
    """
    env = environ if environ is not None else os.environ
    names: dict[str, str] = {}
    missing: list[str] = []
    for key in keys:
        value = str(env.get(SPECS[key].legacy_env, ""))
        if value.strip():
            names[key] = value
        else:
            missing.append(key)
    if missing:
        lines = ["The legacy sheet tab name is not set for `sync queue` (there is no default in the code):"]
        for key in missing:
            lines.append(f'  PowerShell:  $env:{SPECS[key].legacy_env} = "<tab name>"   # for the {SPECS[key].name} queue')
        lines.append("  The tab name must match the one shown in the spreadsheet exactly (brackets and spaces included)")
        sys.exit("\n".join(lines))
    return names


def parse_spreadsheet_id(value: Any) -> str:
    """
    스프레드시트 ID 또는 전체 URL(.../d/<ID>/edit...)에서 ID 만 꺼냄
    [TH] ดึงเฉพาะ ID จาก ID หรือ URL เต็ม (.../d/<ID>/edit...)
    [EN] Takes just the ID from a spreadsheet ID or a full URL (.../d/<ID>/edit...)
    """
    text = str(value or "").strip()
    m = re.search(r"/d/([A-Za-z0-9_-]+)", text)
    return m.group(1) if m else text


def resolve_legacy_spreadsheet_id(cli_value: Optional[str], target_id: str, environ: Optional[dict] = None) -> str:
    """
    옛 탭을 읽을 스프레드시트 ID: --legacy-spreadsheet > PMIS_LEGACY_SPREADSHEET_ID > 대상 스프레드시트(같은 파일)
    [TH] ID สเปรดชีตที่อ่านแท็บเก่า: --legacy-spreadsheet > PMIS_LEGACY_SPREADSHEET_ID > สเปรดชีตเป้าหมาย (ไฟล์เดียวกัน)
    [EN] The ID of the spreadsheet the legacy tabs are read from: --legacy-spreadsheet > PMIS_LEGACY_SPREADSHEET_ID > the target spreadsheet (same file)
    """
    env = environ if environ is not None else os.environ
    raw = (cli_value or "").strip() or str(env.get(LEGACY_SPREADSHEET_ID_ENV, "")).strip()
    if not raw:
        return target_id
    value = parse_spreadsheet_id(raw)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        sys.exit(f"The legacy spreadsheet is not a valid ID or URL: {raw!r}\n"
                 "  Use the ID (the part of the URL between /d/ and /edit) or the whole spreadsheet URL")
    return value


def require_spreadsheet_id(environ: Optional[dict] = None) -> str:
    """
    대상 스프레드시트 ID 를 환경 변수에서만 읽음. 없거나 비어 있으면 안내 메시지와 함께 종료
    [TH] อ่าน ID สเปรดชีตเป้าหมายจากตัวแปรสภาพแวดล้อมเท่านั้น ถ้าไม่มีหรือว่างจะจบพร้อมข้อความแนะนำ
    [EN] Reads the target spreadsheet ID from the environment only; exits with a how-to message if it is missing or empty
    """
    value = str((environ if environ is not None else os.environ).get(SPREADSHEET_ID_ENV, "")).strip()
    if not value:
        sys.exit(
            f"{SPREADSHEET_ID_ENV} is not set (the target spreadsheet ID - there is no default in the code and no --spreadsheet-id option)\n"
            "  The ID is the part of the URL between /d/ and /edit, e.g. https://docs.google.com/spreadsheets/d/<ID>/edit\n"
            f'  PowerShell:  $env:{SPREADSHEET_ID_ENV} = "<ID>"')
    return value


def load_env_file(path: Optional[str] = None) -> bool:
    """
    스크립트 옆의 .env 를 읽어 환경 변수로 등록 (이미 설정된 변수는 덮어쓰지 않음). 파일이 없거나 python-dotenv 가 없으면 아무 것도 하지 않음
    [TH] อ่านไฟล์ .env ข้างสคริปต์แล้วตั้งเป็นตัวแปรสภาพแวดล้อม (ตัวแปรที่ตั้งไว้แล้วจะไม่ถูกเขียนทับ) ถ้าไม่มีไฟล์หรือไม่มี python-dotenv จะไม่ทำอะไร
    [EN] Loads the .env next to the script into the environment (variables that are already set are not overwritten);
         does nothing when the file or python-dotenv is missing. Returns True when a file was loaded
    """
    env_path = path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.isfile(env_path):
        return False
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    return bool(load_dotenv(env_path, override=False, encoding="utf-8"))
