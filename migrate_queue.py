#!/usr/bin/env python3
"""
migrate_queue.py

MIGRATION_QUEUE / MIGRATION_QUEUE_DRQ 큐를 내 PC 에서 Gemini 로 처리합니다.
(Code_Migration.js 의 runDwrAiMigrationTurbo / runDrqAiMigrationTurbo 를 대체)
[TH] ประมวลผลคิว MIGRATION_QUEUE / MIGRATION_QUEUE_DRQ ด้วย Gemini บนเครื่องของคุณ
     (แทน runDwrAiMigrationTurbo / runDrqAiMigrationTurbo ใน Code_Migration.js)
[EN] Processes the MIGRATION_QUEUE / MIGRATION_QUEUE_DRQ queues with Gemini on your own PC
     (replaces runDwrAiMigrationTurbo / runDrqAiMigrationTurbo in Code_Migration.js).

기존과 동일한 점 / [TH] ทำงานเหมือนของเดิม / [EN] Same as the original:
  큐(G열 = Waiting) -> PDF 다운로드 -> Gemini(기존 프롬프트) -> DB_DWR / DB_DRQ 에 append
  -> G = Done (실패 시 G = "Error: <label> | <detail>")
  [TH] คิว (คอลัมน์ G = Waiting) -> ดาวน์โหลด PDF -> Gemini (prompt เดิม) -> append ลง DB_DWR / DB_DRQ
       -> ตั้ง G = Done (ถ้าพลาด ตั้ง G = "Error: <label> | <detail>")
  [EN] queue (column G = Waiting) -> download PDF -> Gemini (original prompt) -> append to DB_DWR / DB_DRQ
       -> set G = Done (on failure set G = "Error: <label> | <detail>")

기존과 다른 점 / [TH] ต่างจากของเดิม / [EN] Differences from the original:
  * UrlFetchApp 을 쓰지 않으므로 Apps Script 의 업로드 대역폭 쿼터에 걸리지 않음
    [TH] ไม่ผ่าน UrlFetchApp จึงไม่ติดโควตา upload bandwidth ของ Apps Script
    [EN] No UrlFetchApp, so the Apps Script upload-bandwidth quota no longer applies
  * Error 행은 Waiting 으로 되돌리지 않아도 자동으로 재시도됨
    [TH] แถวที่เป็น Error จะถูกลองใหม่ได้เลย (ไม่ต้องรีเซ็ตเป็น Waiting)
    [EN] Error rows are retried automatically (no need to reset them to Waiting)
  * Gemini 가 402(크레딧 소진) / 401 / 403 을 반환하면 즉시 전체 중단 (남은 모든 행이 Error 가 되는 것을 방지)
    [TH] หยุดทันทีเมื่อเจอ 402 (เครดิตหมด) / 401 / 403 แทนที่จะไล่ยิงต่อจนทุกแถวเป็น Error
    [EN] Stops the whole run immediately on 402 (credits exhausted) / 401 / 403 instead of failing every remaining row
  * values.append 사용 (행이 자동 확장됨). DB_DWR/DB_DRQ 는 1000행뿐이라 기존 setValues 는 한계에 걸림
    [TH] ใช้ values.append (ตารางขยายแถวให้เอง) - DB_DWR/DB_DRQ มีแค่ 1000 แถว setValues แบบเดิมจะชนเพดาน
    [EN] Uses values.append (rows are added automatically); DB_DWR/DB_DRQ only have 1,000 rows, so the old setValues hits the limit
  * 중복 방지: PDF 가 이미 DB 에 있으면(file ID 비교) AI 호출 없이 Done 처리
    [TH] กันข้อมูลซ้ำ: ถ้า PDF นั้นอยู่ใน DB แล้ว (เทียบ file ID) จะตั้ง Done โดยไม่เรียก AI
    [EN] Duplicate guard: if the PDF is already in the DB (matched by file ID), it is marked Done without calling the AI
  * 오류 행에 "Error: <label> | <detail>" 로 상세 원인을 기록 (현재 Apps Script 는 "Error: <label>" 만 기록)
    [TH] แถวที่ผิดพลาดจะมีสาเหตุละเอียด "Error: <label> | <detail>" (Apps Script ปัจจุบันเขียนแค่ "Error: <label>")
    [EN] Failed rows get the detailed cause "Error: <label> | <detail>" (the current Apps Script writes only "Error: <label>")

Apps Script(extractDriveFileForAI)와 동일하게 맞춘 점 / [TH] ส่วนที่ทำให้เหมือน Apps Script (extractDriveFileForAI) / [EN] Matched to the Apps Script (extractDriveFileForAI):
  * PDF 가 15MB(15,000,000 바이트)를 넘으면 원본 대신 Drive 썸네일(sz=w2500)의 1페이지 이미지를 Gemini 에 전송
    (끄려면 --no-thumbnail: 이 경우 "Error: Too Large")
    [TH] PDF เกิน 15MB (15,000,000 ไบต์) จะส่งภาพหน้าแรกจาก thumbnail ของ Drive (sz=w2500) ให้ Gemini แทนไฟล์ต้นฉบับ
         (ปิดได้ด้วย --no-thumbnail: จะได้ "Error: Too Large" แทน)
    [EN] A PDF over 15MB (15,000,000 bytes) is sent to Gemini as the page-1 image of the Drive thumbnail (sz=w2500)
         instead of the original file (--no-thumbnail turns this off: the row gets "Error: Too Large" instead)

환경 변수 / [TH] ตัวแปรสภาพแวดล้อม / [EN] Environment variables:
  GEMINI_API_KEY                  Gemini 키 (실제 실행 시 필수)
                                  [TH] คีย์ Gemini (จำเป็นเมื่อรันจริง)
                                  [EN] Gemini key (required for a real run)
  GOOGLE_APPLICATION_CREDENTIALS  서비스 계정 키 파일 경로 (또는 --sa-key 사용)
                                  [TH] path ของ service account key (หรือใช้ --sa-key)
                                  [EN] path to the service account key file (or use --sa-key)
  GEMINI_MODEL                    (선택) 모델 지정 (미설정 시 코드의 DEFAULT_MODEL 사용, --model 이 최우선)
                                  [TH] (ไม่บังคับ) ระบุโมเดล (ถ้าไม่ตั้งจะใช้ DEFAULT_MODEL ในโค้ด; --model สำคัญที่สุด)
                                  [EN] (optional) model name (if unset, DEFAULT_MODEL in the code is used; --model wins over both)
  PMIS_SPREADSHEET_ID             대상 스프레드시트 ID (필수, 항상. 코드에 기본값이 없고 명령줄 옵션도 없음)
                                  [TH] ID ของสเปรดชีตเป้าหมาย (จำเป็นเสมอ ไม่มีค่าเริ่มต้นในโค้ดและไม่มีออปชันบรรทัดคำสั่ง)
                                  [EN] target spreadsheet ID (always required; no default in the code and no command-line option)
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import random
import re
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# 대상 시트 ID 는 코드에 기본값을 두지 않고 이 환경 변수에서만 읽습니다 (아래는 변수 이름)
# [TH] ID ชีตเป้าหมายไม่มีค่าเริ่มต้นในโค้ด อ่านจากตัวแปรสภาพแวดล้อมนี้เท่านั้น (ด้านล่างคือชื่อตัวแปร)
# [EN] The target sheet ID has no default in the code and is read only from this environment variable (below is the variable name)
SPREADSHEET_ID_ENV = "PMIS_SPREADSHEET_ID"
# Code_Config.js 의 CONFIG_GEMINI_MODEL 과 같은 기본 모델. 둘은 서로 독립적이므로(이 스크립트는 그 파일을 읽지 않음) 한쪽을 바꾸면 다른 쪽도 바꾸세요
# [TH] โมเดลเริ่มต้นตัวเดียวกับ CONFIG_GEMINI_MODEL ใน Code_Config.js ทั้งสองฝั่งเป็นอิสระต่อกัน (สคริปต์นี้ไม่อ่านไฟล์นั้น) ถ้าแก้ฝั่งหนึ่งให้แก้อีกฝั่งด้วย
# [EN] The same default model as CONFIG_GEMINI_MODEL in Code_Config.js. The two are independent (this script does not read that file): if you change one, change the other
DEFAULT_MODEL = "gemini-3.8-flash"
# Code_Migration.js 는 'v1beta' 로 호출함
# [TH] Code_Migration.js เรียกด้วย 'v1beta'
# [EN] Code_Migration.js calls the API with 'v1beta'
DEFAULT_API_VERSION = "v1beta"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]
SHEETS_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE_BASE = "https://www.googleapis.com/drive/v3/files"
# Apps Script 의 extractDriveFileForAI 와 동일: 15MB 초과 PDF 는 이 주소(sz=w2500)의 1페이지 이미지로 대체
# [TH] เหมือน extractDriveFileForAI ของ Apps Script: PDF เกิน 15MB ใช้ภาพหน้าแรกจากที่อยู่นี้ (sz=w2500) แทน
# [EN] Same as extractDriveFileForAI in Apps Script: a PDF over 15MB is replaced by the page-1 image from this URL (sz=w2500)
DRIVE_THUMBNAIL_URL = "https://drive.google.com/thumbnail"
THUMBNAIL_SIZE = "w2500"
LARGE_PDF_MB = 15.0
GEMINI_BASE = "https://generativelanguage.googleapis.com"

# 상태 열 = G열 (AI Processed)
# [TH] คอลัมน์สถานะ = คอลัมน์ G (AI Processed)
# [EN] Status column = column G (AI Processed)
STATUS_COL = 7
# 오류 메시지 길이 제한 (셀 하나에 너무 길게 쓰지 않도록)
# [TH] จำกัดความยาวข้อความ error (ไม่ให้ยาวเกินไปในเซลล์เดียว)
# [EN] Length limit for error messages (so a single cell does not get too long)
MAX_ERROR_CHARS = 5000
# 계속 시도해도 모든 행이 실패하므로 전체 실행을 중단하는 상태 코드
# [TH] รหัสที่ถ้าลองต่อก็ล้มทุกแถว -> หยุดทั้งรอบ
# [EN] Status codes where continuing would fail every row -> stop the whole run
FATAL_GEMINI_STATUSES = {401, 402, 403}
RETRY_STATUSES = {429, 500, 502, 503, 504}

# 1M 토큰당 정가 (input, output). 실행 종료 시 비용 추정에만 사용
# [TH] ราคา list price ต่อ 1M tokens (input, output) ใช้ประมาณค่าใช้จ่ายตอนท้ายเท่านั้น
# [EN] List price per 1M tokens (input, output); used only for the end-of-run cost estimate
PRICES_USD_PER_M = {
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.1-pro-preview": (2.00, 12.00),
}

# 테스트에서 교체할 수 있도록 모듈 변수로 둠
# [TH] ให้เทสต์แทนที่ได้
# [EN] Kept as a module variable so tests can replace it
SLEEP: Callable[[float], None] = time.sleep


# ---------------------------------------------------------------------------
# 큐 정의 (프롬프트와 DB 열 순서는 Code_Migration.js 에서 그대로 복사)
# [TH] นิยามคิว (prompt และลำดับคอลัมน์ DB คัดลอกจาก Code_Migration.js)
# [EN] Queue definitions (prompts and DB column order copied from Code_Migration.js)
# ---------------------------------------------------------------------------
DWR_PROMPT = (
    'Extract data into strict JSON: {"DWR_No":"", "Date":"YYYY-MM-DD", "Discipline":"", "Location":"", '
    '"WBS":"", "Weather_Day":"", "Weather_Night":"", "Total_Manpower":0, "Total_Equipment":0, '
    '"Manpower_Details":[], "Equipment_Details":[], "Activities":"", "Issues_Delays":"", '
    '"AI_Risk_Flag":"", "Linked_RITs":""}. Output ONLY raw JSON.'
)
DRQ_PROMPT = (
    'Extract data into strict JSON: {"DRQ_No":"", "Date":"YYYY-MM-DD", "Discipline":"", "Location":"", '
    '"WBS":"", "Time":"", "Shift":"", "Activities":"", "Unit_Quantity":"", "Linked_RITs":"", '
    '"Approved_by":"", "Comments":""}. Output ONLY raw JSON.'
)


# 재시도에서만 프롬프트 뒤에 붙이는 문장 (AI 가 JSON list 로 보고서를 여러 개 돌려줬을 때)
# [TH] ประโยคที่เติมท้าย prompt เฉพาะตอนลองใหม่ (เมื่อ AI ตอบเป็น list หลายรายงาน)
# [EN] Sentence appended to the prompt on a retry only (when the AI answered with a list of several reports)
SINGLE_REPORT_HINT = (
    ' The file may contain several reports. Return exactly ONE JSON object (not an array) for the report whose '
    'document number is "{doc_no}"; ignore the other reports.'
)


@dataclass(frozen=True)
class QueueSpec:
    name: str
    queue_tab: str
    db_tab: str
    prompt: str
    # DB 의 각 열: ("const", 값) | ("field", 키 이름) | ("json", 키 이름) | ("pdf",) | ("folder",)
    # [TH] แต่ละคอลัมน์ของ DB: ("const", ค่า) | ("field", ชื่อคีย์) | ("json", ชื่อคีย์) | ("pdf",) | ("folder",)
    # [EN] Each DB column: ("const", value) | ("field", key name) | ("json", key name) | ("pdf",) | ("folder",)
    columns: tuple
    # DB 에서 PDF Link 열의 인덱스(0부터). 중복 확인에 사용
    # [TH] index (เริ่ม 0) ของคอลัมน์ PDF Link ใน DB ใช้เช็คข้อมูลซ้ำ
    # [EN] 0-based index of the PDF Link column in the DB; used for the duplicate check
    db_pdf_col: int
    # AI JSON 에서 문서 번호가 들어 있는 키 (여러 보고서가 돌아왔을 때 큐의 Doc No 와 맞춰 고르는 데 사용)
    # [TH] คีย์ใน JSON ของ AI ที่เก็บเลขเอกสาร (ใช้จับคู่กับ Doc No ของคิวเมื่อ AI ตอบกลับมาหลายรายงาน)
    # [EN] Key in the AI JSON that holds the document number (used to match the queue's Doc No when the AI returns several reports)
    no_key: str = ""


SPECS = {
    "dwr": QueueSpec(
        name="DWR",
        queue_tab="MIGRATION_QUEUE",
        db_tab="DB_DWR",
        prompt=DWR_PROMPT,
        columns=(
            ("const", ""), ("field", "DWR_No"), ("field", "Date"), ("field", "Discipline"),
            ("field", "Location"), ("field", "WBS"), ("field", "Weather_Day"), ("field", "Weather_Night"),
            ("field", "Total_Manpower"), ("field", "Total_Equipment"),
            ("json", "Manpower_Details"), ("json", "Equipment_Details"),
            ("field", "Activities"), ("field", "Issues_Delays"), ("field", "AI_Risk_Flag"),
            ("field", "Linked_RITs"), ("const", "AI Migrated"), ("pdf",), ("folder",),
        ),
        db_pdf_col=17,
        no_key="DWR_No",
    ),
    "drq": QueueSpec(
        name="DRQ",
        queue_tab="MIGRATION_QUEUE_DRQ",
        db_tab="DB_DRQ",
        prompt=DRQ_PROMPT,
        columns=(
            ("const", ""), ("field", "DRQ_No"), ("field", "Date"), ("field", "Discipline"),
            ("field", "Location"), ("field", "WBS"), ("field", "Time"), ("field", "Shift"),
            ("field", "Activities"), ("field", "Unit_Quantity"), ("field", "Linked_RITs"),
            ("field", "Approved_by"), ("field", "Comments"), ("const", "AI Migrated"),
            ("pdf",), ("folder",), ("const", ""), ("const", ""),
        ),
        db_pdf_col=14,
        no_key="DRQ_No",
    ),
}


# ---------------------------------------------------------------------------
# 헬퍼 함수
# [TH] ฟังก์ชันช่วย
# [EN] Helper functions
# ---------------------------------------------------------------------------
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


def cell_value(v: Any) -> Any:
    """
    셀에 넣을 수 있는 값으로 변환 (list/dict -> JSON, None -> 빈 값, '=' 로 시작하는 문자열은 수식이 되지 않도록 처리)
    [TH] แปลงค่าให้ใส่เซลล์ได้ (list/dict -> JSON, None -> ว่าง, ข้อความที่ขึ้นต้นด้วย = กันกลายเป็นสูตร)
    [EN] Converts a value so it can go into a cell (list/dict -> JSON, None -> empty,
         text starting with '=' is escaped so it cannot become a formula)
    """
    if v is None:
        return ""
    if isinstance(v, bool) or isinstance(v, (int, float)):
        return v
    if isinstance(v, (list, dict)):
        v = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    v = str(v)
    return "'" + v if v.startswith("=") else v


def build_db_row(spec: QueueSpec, data: dict, pdf_url: str, folder_url: str) -> list:
    row = []
    for col in spec.columns:
        kind = col[0]
        if kind == "const":
            row.append(col[1])
        elif kind == "field":
            row.append(cell_value(data.get(col[1])))
        elif kind == "json":
            v = data.get(col[1])
            row.append("" if v is None else cell_value(json.dumps(v, ensure_ascii=False, separators=(",", ":"))))
        elif kind == "pdf":
            row.append(pdf_url)
        elif kind == "folder":
            row.append(folder_url)
    return row


class MultiReportError(ValueError):
    """
    AI 가 보고서를 여러 개(list) 돌려줬는데 어느 것이 이 행의 것인지 정할 수 없을 때
    [TH] AI ตอบกลับมาหลายรายงาน (list) แต่ตัดสินไม่ได้ว่ารายการไหนเป็นของแถวนี้
    [EN] The AI returned several reports (a list) and it is not possible to tell which one belongs to this row
    """


def norm_doc_no(value: Any) -> str:
    """
    문서 번호 비교용: 대문자, 숫자 앞의 0 제거, 영숫자 이외 제거 (ACME-DWR-CIV-0231 == acme dwr civ 231)
    [TH] ใช้เทียบเลขเอกสาร: ตัวพิมพ์ใหญ่, ตัดเลข 0 นำหน้า, ตัดอักขระที่ไม่ใช่ตัวอักษร/ตัวเลข (ACME-DWR-CIV-0231 == acme dwr civ 231)
    [EN] For comparing document numbers: upper-case, drop leading zeros of digit runs, drop non-alphanumerics
         (ACME-DWR-CIV-0231 == acme dwr civ 231)
    """
    text = re.sub(r"(?<!\d)0+(?=\d)", "", str(value or "").upper())
    return re.sub(r"[^0-9A-Z]", "", text)


def describe_reports(items: list, no_key: str) -> str:
    """각 보고서의 번호/날짜 요약 / สรุปเลขที่และวันที่ของแต่ละรายงาน / One-line summary of every report: number and date"""
    out = []
    for i, it in enumerate(items[:12], start=1):
        if isinstance(it, dict):
            out.append(f"#{i} {no_key or 'No'}={it.get(no_key) or '?'} Date={it.get('Date') or '?'} ({len(it)} fields)")
        else:
            out.append(f"#{i} {type(it).__name__}")
    return "; ".join(out) + (f"; … (+{len(items) - 12})" if len(items) > 12 else "")


def pick_report(obj: Any, doc_no: str = "", no_key: str = "", accept_first: bool = False) -> tuple[dict, str]:
    """
    AI 응답(obj)에서 이 행의 보고서 dict 하나를 고른다. 돌려주는 두 번째 값은 "여러 개 중에서 골랐다"는 설명 (그냥 dict 면 빈 문자열)
      - dict                       -> 그대로
      - list 에 dict 1개           -> 그 dict
      - list 에 같은 dict 여러 개  -> 첫 번째
      - list 에 서로 다른 dict     -> no_key 값이 큐의 Doc No 와 같은 것 (여러 개면 첫 번째). 없으면 MultiReportError
                                      (accept_first=True 면 첫 번째를 사용)
    [TH] เลือก dict รายงานเดียวของแถวนี้จากคำตอบของ AI ค่าที่ 2 คือคำอธิบายว่า "เลือกมาจากหลายรายการ" (ถ้าเป็น dict ปกติจะว่าง)
         - dict -> ใช้เลย / list ที่มี dict เดียว -> dict นั้น / list ที่มี dict เหมือนกันหลายตัว -> ตัวแรก
         - list ที่มี dict ต่างกัน -> ตัวที่ค่า no_key ตรงกับ Doc No ของคิว (ถ้าตรงหลายตัวใช้ตัวแรก)
           ถ้าไม่มีตัวที่ตรง -> MultiReportError (ถ้า accept_first=True ใช้ตัวแรก)
    [EN] Pick the single report dict for this row from the AI answer. The second return value explains that a pick was made
         among several (empty for a plain dict).
         - dict -> as is / list with one dict -> that dict / list of identical dicts -> the first
         - list of different dicts -> the one whose no_key value equals the queue's Doc No (first if several match);
           none matches -> MultiReportError (with accept_first=True the first one is used)
    """
    if isinstance(obj, dict):
        return obj, ""
    if not isinstance(obj, list):
        raise ValueError(f"expected a JSON object, got {type(obj).__name__}")
    items = [x for x in obj if isinstance(x, dict)]
    if not items:
        raise ValueError(f"expected a JSON object, got a list with no object in it ({len(obj)} items)")
    if len(items) == 1 and len(obj) == 1:
        return items[0], ""
    n = len(obj)
    if all(x == items[0] for x in items):
        return items[0], f"AI ตอบรายงานเดียวกันซ้ำ {n} รายการ ใช้รายการแรก"
    want = norm_doc_no(doc_no)
    if want and no_key:
        hits = [x for x in items if norm_doc_no(x.get(no_key)) == want]
        if hits:
            extra = f" (มี {len(hits)} รายการที่ตรง ใช้รายการแรก)" if len(hits) > 1 else ""
            return hits[0], f"AI ตอบ {n} รายงานใน PDF เดียว เลือกรายการที่ {no_key} ตรงกับ Doc No '{doc_no}'{extra}"
    if accept_first:
        return items[0], f"AI ตอบ {n} รายงานใน PDF เดียวและไม่มีรายการที่ตรงกับ Doc No '{doc_no}' ใช้รายการแรกตาม --accept-first-report"
    raise MultiReportError(
        f"AI returned {n} reports in one answer and none has {no_key or 'a number'} matching the queue Doc No '{doc_no}': "
        f"{describe_reports(obj, no_key)}")


def load_model_json(raw_text: str) -> Any:
    """
    ```json 코드 펜스를 제거하고 JSON 값(dict 또는 list)으로 변환
    [TH] ลบรั้ว ```json แล้วแปลงเป็นค่า JSON (dict หรือ list)
    [EN] Strips the ```json fences and parses the JSON value (dict or list)
    """
    clean = raw_text.replace("```json", "").replace("```", "").strip()
    try:
        # strict=False: 문자열 안의 줄바꿈/탭 같은 제어문자를 허용 (측량 도면 등에서 AI 가 줄바꿈을 그대로 넣는 경우)
        # [TH] strict=False: ยอมให้มีตัวควบคุมเช่นขึ้นบรรทัดใหม่/แท็บในสตริง (กรณี AI ใส่ขึ้นบรรทัดใหม่ดิบๆ ในข้อความ)
        # [EN] strict=False: allows control characters such as raw newlines/tabs inside strings (when the AI puts line breaks in as-is)
        return json.loads(clean, strict=False)
    except json.JSONDecodeError as first_error:
        # 앞뒤에 설명 문장이 붙은 경우: 첫 '{' 부터 JSON 하나만 읽음. 그래도 안 되면 원래 오류를 그대로 올림
        # [TH] ถ้ามีข้อความอธิบายนำหน้า/ตามหลัง: อ่าน JSON ก้อนเดียวตั้งแต่ '{' แรก ถ้ายังไม่ได้ให้โยน error เดิม
        # [EN] If prose surrounds the JSON: read one JSON value starting at the first '{'; if that fails too, raise the original error
        start = clean.find("{")
        if start < 0:
            raise
        try:
            obj, _ = json.JSONDecoder(strict=False).raw_decode(clean[start:])
        except json.JSONDecodeError:
            raise first_error from None
        return obj


def parse_model_report(raw_text: str, doc_no: str = "", no_key: str = "", accept_first: bool = False) -> tuple[dict, str]:
    return pick_report(load_model_json(raw_text), doc_no, no_key, accept_first)


def parse_model_json(raw_text: str) -> dict:
    """
    기존처럼 ```json 코드 펜스를 제거한 뒤 dict 로 변환 (dict 하나만 든 list 도 허용)
    [TH] ลบรั้ว ```json แบบเดิม แล้วแปลงเป็น dict (ถ้าได้ list ที่มี dict ตัวเดียวก็ยอมรับ)
    [EN] Strips the ```json fences like the original, then parses into a dict
         (a list containing exactly one dict is also accepted)
    """
    return parse_model_report(raw_text)[0]


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


# ---------------------------------------------------------------------------
# 데이터 구조
# [TH] โครงสร้างข้อมูล
# [EN] Data structures
# ---------------------------------------------------------------------------
@dataclass
class Job:
    # 큐 시트의 행 번호 (1부터 시작, 1행 = 헤더)
    # [TH] เลขแถวในชีตคิว (เริ่ม 1, แถว 1 = หัวตาราง)
    # [EN] Row number in the queue sheet (1-based, row 1 = header)
    row: int
    pdf_url: str
    folder_url: str
    prior_status: str
    # 큐 시트 A열 (Doc No)
    # [TH] คอลัมน์ A ของชีตคิว (Doc No)
    # [EN] Column A of the queue sheet (Doc No)
    doc_no: str = ""


@dataclass
class RowResult:
    job: Job
    # "ok" | "error" | "skipped"
    kind: str
    data: Optional[dict] = None
    label: str = ""
    detail: str = ""
    # 성공했지만 알려줄 내용이 있을 때 (예: 큰 PDF 를 표지 이미지로 대체)
    # [TH] ใช้เมื่อสำเร็จแต่มีเรื่องที่ต้องแจ้ง (เช่น PDF ใหญ่ถูกแทนด้วยภาพหน้าปก)
    # [EN] Used when the row succeeded but there is something to report (e.g. a large PDF replaced by its cover image)
    note: str = ""
    # JSON 이 깨져 Gemini 를 다시 호출한 횟수 (성공했을 때만 의미 있음)
    # [TH] จำนวนครั้งที่เรียก Gemini ใหม่เพราะ JSON พัง (มีความหมายเมื่อสำเร็จ)
    # [EN] How many times Gemini was called again because the JSON was broken (meaningful when the row succeeded)
    retried: int = 0
    # AI 가 보고서를 여러 개 돌려줘서 그중 하나를 골랐을 때의 설명 (성공했을 때만 의미 있음)
    # [TH] คำอธิบายเมื่อ AI ตอบกลับมาหลายรายงานแล้วเลือกมาหนึ่งรายการ (มีความหมายเมื่อสำเร็จ)
    # [EN] Explanation when the AI returned several reports and one was picked (meaningful when the row succeeded)
    multi: str = ""


@dataclass
class Usage:
    prompt: int = 0
    # thinking 토큰 포함 (output 으로 과금됨)
    # [TH] รวม thinking tokens (คิดเงินเป็น output)
    # [EN] Includes thinking tokens (billed as output)
    output: int = 0
    calls: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, meta: dict) -> None:
        with self.lock:
            self.prompt += int(meta.get("promptTokenCount") or 0)
            self.output += int(meta.get("candidatesTokenCount") or 0) + int(meta.get("thoughtsTokenCount") or 0)
            self.calls += 1


class RunControl:
    """
    스레드 간 공유 상태: 전체 중단 플래그 + 연속 429 카운터
    [TH] สถานะที่ใช้ร่วมกันระหว่าง thread: ธงหยุดทั้งรอบ + ตัวนับ 429 ติดกัน
    [EN] State shared between threads: the stop-everything flag + a consecutive-429 counter
    """

    def __init__(self) -> None:
        self.stop = threading.Event()
        self.stop_reason = ""
        self._lock = threading.Lock()
        self.consecutive_rate_limited = 0

    def request_stop(self, reason: str) -> None:
        with self._lock:
            if not self.stop.is_set():
                self.stop_reason = reason
                self.stop.set()

    def note_rate_limited(self, limit: int = 4) -> None:
        with self._lock:
            self.consecutive_rate_limited += 1
            hit = self.consecutive_rate_limited >= limit
        if hit:
            self.request_stop(f"Gemini ตอบ 429 ต่อเนื่อง {limit} แถวแม้ retry แล้ว (rate limit / โควตารายวัน) - หยุดเพื่อไม่ให้ทุกแถวกลายเป็น Error")

    def note_success(self) -> None:
        with self._lock:
            self.consecutive_rate_limited = 0


@dataclass
class Clients:
    # 서비스 계정 토큰이 붙은 세션 (메인 스레드에서만 호출)
    # [TH] session ที่แนบ token ของ service account (เรียกจาก main thread เท่านั้น)
    # [EN] Session carrying the service-account token (call from the main thread only)
    sheets: Any
    # Drive 용 세션을 반환 (스레드마다 호출)
    # [TH] คืน session สำหรับ Drive (เรียกต่อ thread)
    # [EN] Returns a session for Drive (called once per thread)
    drive_factory: Callable[[], Any]
    # requests.Session
    gemini: Any
    api_key: str
    model: str = DEFAULT_MODEL
    api_version: str = DEFAULT_API_VERSION
    sa_email: str = ""
    # None = generationConfig 에 maxOutputTokens 를 넣지 않음
    # [TH] None = ไม่ใส่ maxOutputTokens ใน generationConfig
    # [EN] None = maxOutputTokens is not put in generationConfig
    max_output_tokens: Optional[int] = None


# ---------------------------------------------------------------------------
# Sheets
# ---------------------------------------------------------------------------
class SheetsError(RuntimeError):
    pass


def _sheets_call(sess: Any, method: str, url: str, *, retry_5xx: bool = True, max_tries: int = 5, **kw: Any) -> dict:
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
            SLEEP(backoff_seconds(attempt))
    raise SheetsError(last)


def _values_url(sid: str, a1: str) -> str:
    return f"{SHEETS_BASE}/{sid}/values/{urllib.parse.quote(a1, safe='')}"


def read_values(sess: Any, sid: str, a1: str) -> list[list[str]]:
    data = _sheets_call(sess, "GET", _values_url(sid, a1), params={"valueRenderOption": "FORMATTED_VALUE"})
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
        data = _sheets_call(sess, "GET", f"{SHEETS_BASE}/{sid}", max_tries=2, params={"fields": "properties.title"})
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
    _sheets_call(sess, "POST", f"{SHEETS_BASE}/{sid}/values:batchUpdate", json=body)


def append_rows(sess: Any, sid: str, tab: str, rows: list[list]) -> None:
    """
    표 끝에 추가 (행이 자동 확장됨). 5xx/타임아웃 시에는 이미 기록됐을 수 있어 재시도하지 않음 -> 중복 행 방지
    [TH] ต่อท้ายตาราง (ขยายแถวให้เอง) ไม่ retry เมื่อ 5xx/timeout เพราะอาจเขียนไปแล้ว -> กันแถวซ้ำ
    [EN] Appends to the end of the table (rows expand automatically). Does not retry on 5xx/timeout because
         the write may already have happened -> avoids duplicate rows
    """
    url = _values_url(sid, f"'{tab}'!A1") + ":append"
    _sheets_call(
        sess, "POST", url, retry_5xx=False,
        params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
        json={"majorDimension": "ROWS", "values": rows},
    )


# ---------------------------------------------------------------------------
# 처리할 행 선택
# [TH] เลือกแถวที่ต้องทำ
# [EN] Selecting the rows to process
# ---------------------------------------------------------------------------
def select_jobs(
    values: list[list[str]], *, retry_errors: bool, include_blank: bool, only_rows: Optional[set[int]]
) -> list[Job]:
    jobs: list[Job] = []
    # 1행 = 헤더이므로 2행부터
    # [TH] แถว 1 = หัวตาราง จึงเริ่มที่แถว 2
    # [EN] Row 1 is the header, so start at row 2
    for idx, row in enumerate(values[1:], start=2):
        cells = list(row) + [""] * (7 - len(row))
        status = str(cells[6]).strip()
        pdf_url, folder_url = str(cells[3]).strip(), str(cells[4]).strip()
        if only_rows is not None:
            if idx not in only_rows or status == "Done":
                continue
        else:
            pending = status == "Waiting" or (retry_errors and status.startswith("Error"))
            if include_blank and status == "" and (pdf_url or folder_url):
                pending = True
            if not pending:
                continue
        jobs.append(Job(row=idx, pdf_url=pdf_url, folder_url=folder_url, prior_status=status, doc_no=str(cells[0]).strip()))
    return jobs


# ---------------------------------------------------------------------------
# Drive
# ---------------------------------------------------------------------------
class DriveError(RuntimeError):
    pass


def _drive_get(sess: Any, url: str, **kw: Any) -> Any:
    last = ""
    for attempt in range(1, 5):
        try:
            resp = sess.request("GET", url, timeout=(10, 120), **kw)
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        else:
            if resp.status_code == 200:
                return resp
            body = resp.text[:400]
            last = f"HTTP {resp.status_code}: {body}"
            rate_limited = resp.status_code == 403 and "ateLimitExceeded" in body
            if resp.status_code not in RETRY_STATUSES and not rate_limited:
                raise DriveError(last)
        if attempt < 4:
            SLEEP(backoff_seconds(attempt))
    raise DriveError(last)


def drive_metadata(sess: Any, file_id: str) -> dict:
    resp = _drive_get(
        sess, f"{DRIVE_BASE}/{file_id}",
        params={"fields": "id,name,mimeType,size,trashed", "supportsAllDrives": "true"},
    )
    return resp.json()


def drive_download(sess: Any, file_id: str) -> bytes:
    return _drive_get(sess, f"{DRIVE_BASE}/{file_id}", params={"alt": "media", "supportsAllDrives": "true"}).content


def drive_thumbnail(sess: Any, file_id: str) -> tuple[bytes, str]:
    """
    Apps Script 의 extractDriveFileForAI 와 같은 방식으로 1페이지 썸네일(sz=w2500)을 받음. (이미지 바이트, MIME) 반환
    [TH] ดึงภาพ thumbnail หน้าแรก (sz=w2500) แบบเดียวกับ extractDriveFileForAI ของ Apps Script คืน (ไบต์ภาพ, MIME)
    [EN] Fetches the page-1 thumbnail (sz=w2500) the same way extractDriveFileForAI does in Apps Script; returns (image bytes, MIME)
    """
    resp = _drive_get(sess, DRIVE_THUMBNAIL_URL, params={"id": file_id, "sz": THUMBNAIL_SIZE})
    headers = getattr(resp, "headers", None) or {}
    ctype = str(headers.get("Content-Type", "")).split(";")[0].strip().lower()
    if not ctype.startswith("image/"):
        ctype = "image/jpeg"
    return resp.content, ctype


# 시트의 오류 셀(G열)에 기록되는 모든 문구는 영어로 통일 (콘솔 로그만 태국어)
# [TH] ข้อความทุกอย่างที่เขียนลงเซลล์ Error (คอลัมน์ G) เป็นภาษาอังกฤษ (มีแต่ log บนคอนโซลที่เป็นภาษาไทย)
# [EN] Every phrase written into the sheet's Error cell (column G) is in English (only the console log is in Thai)
DRIVE_HINT = "the file does not exist / was deleted, or the file (or its folder) is not shared with the service account"


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------
@dataclass
class GeminiOutcome:
    # "ok" | "error" | "fatal"
    kind: str
    text: str = ""
    label: str = ""
    detail: str = ""
    # candidates[0].finishReason (예: STOP, MAX_TOKENS). 답이 중간에 잘렸는지 알아내는 데 사용
    # [TH] candidates[0].finishReason (เช่น STOP, MAX_TOKENS) ใช้ดูว่าคำตอบถูกตัดกลางคันหรือไม่
    # [EN] candidates[0].finishReason (e.g. STOP, MAX_TOKENS); used to tell whether the answer was cut off
    finish: str = ""


def _gemini_finish(payload: dict) -> str:
    cands = payload.get("candidates") or []
    return str(cands[0].get("finishReason") or "") if cands else ""


def _gemini_text(payload: dict) -> Optional[str]:
    cands = payload.get("candidates") or []
    if not cands:
        return None
    parts = ((cands[0].get("content") or {}).get("parts")) or []
    texts = [p["text"] for p in parts if isinstance(p.get("text"), str) and not p.get("thought")]
    return "".join(texts) if texts else None


def call_gemini(clients: Clients, prompt: str, pdf_bytes: bytes, mime: str, usage: Usage, ctl: RunControl) -> GeminiOutcome:
    url = f"{GEMINI_BASE}/{clients.api_version}/models/{clients.model}:generateContent"
    body = {
        "contents": [{"parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(pdf_bytes).decode("ascii")}},
        ]}],
        "generationConfig": {"response_mime_type": "application/json"},
    }
    # --max-output-tokens 를 줄 때만 추가 (기본값은 Apps Script 와 동일하게 지정하지 않음)
    # [TH] เพิ่มเฉพาะเมื่อใส่ --max-output-tokens (ค่าเริ่มต้นไม่กำหนด เหมือน Apps Script)
    # [EN] Added only when --max-output-tokens is given (by default it is not set, same as the Apps Script)
    if clients.max_output_tokens:
        body["generationConfig"]["maxOutputTokens"] = clients.max_output_tokens
    headers = {"x-goog-api-key": clients.api_key, "Content-Type": "application/json"}
    last_label, last_detail = "API", ""
    for attempt in range(1, 6):
        if ctl.stop.is_set():
            return GeminiOutcome("error", label="Stopped", detail=ctl.stop_reason)
        try:
            resp = clients.gemini.post(url, headers=headers, data=json.dumps(body), timeout=(10, 300))
        except Exception as e:  # noqa: BLE001
            last_label, last_detail = "Network", f"{type(e).__name__}: {e}"
            if attempt < 5:
                SLEEP(backoff_seconds(attempt))
            continue
        code = resp.status_code
        if code == 200:
            try:
                payload = resp.json()
            except ValueError:
                return GeminiOutcome("error", label="No Result", detail="HTTP 200 but body is not JSON: " + resp.text[:500])
            usage.add(payload.get("usageMetadata") or {})
            text = _gemini_text(payload)
            if text is None:
                return GeminiOutcome("error", label="No Result", detail="HTTP 200 but no usable text. Response: " + resp.text[:2000])
            ctl.note_success()
            return GeminiOutcome("ok", text=text, finish=_gemini_finish(payload))
        body_text = resp.text
        last_label, last_detail = f"API {code}", body_text
        if code in FATAL_GEMINI_STATUSES or (code == 400 and "API_KEY_INVALID" in body_text):
            return GeminiOutcome("fatal", label=last_label, detail=body_text)
        if code in RETRY_STATUSES and attempt < 5:
            SLEEP(backoff_seconds(attempt, retry_delay_hint(body_text)))
            continue
        break
    if last_label == "API 429":
        ctl.note_rate_limited()
    return GeminiOutcome("error", label=last_label, detail=last_detail)


# ---------------------------------------------------------------------------
# 한 행 처리
# [TH] ประมวลผลหนึ่งแถว
# [EN] Processing a single row
# ---------------------------------------------------------------------------
def guess_mime(meta_mime: str) -> str:
    return meta_mime if meta_mime.startswith("image/") else "application/pdf"


def json_parse_detail(err: Exception, outcome: GeminiOutcome, retried: int = 0) -> str:
    """
    JSON Parse 오류 셀에 남길 설명: 예외 위치, finishReason, AI 답변의 앞부분과 뒷부분 (잘림은 뒷부분에서 드러남)
    [TH] ข้อความอธิบายที่จะเก็บในเซลล์ JSON Parse: ตำแหน่งที่พัง, finishReason, ส่วนหัวและส่วนท้ายของคำตอบ AI (ถ้าถูกตัดจะเห็นที่ส่วนท้าย)
    [EN] Description stored in the JSON Parse error cell: where it broke, finishReason, and the head and tail of the AI answer
         (a truncated answer shows at the tail)
    """
    text = outcome.text or ""
    shown = text if len(text) <= 1800 else text[:1200] + " …[middle omitted]… " + text[-600:]
    parts = [f"{type(err).__name__}: {err}", f"finishReason={outcome.finish or '?'}"]
    if retried:
        parts.append(f"retried {retried}x")
    if outcome.finish == "MAX_TOKENS":
        parts.append("the answer was cut off at the output token limit -> try --max-output-tokens")
    parts.append(f"AI text ({len(text)} chars): {shown}")
    return " | ".join(parts)


def process_job(job: Job, spec: QueueSpec, clients: Clients, tls: threading.local, usage: Usage,
                ctl: RunControl, max_bytes: int, check_only: bool, allow_thumbnail: bool = True,
                parse_retries: int = 0, accept_first: bool = False) -> RowResult:
    if ctl.stop.is_set():
        return RowResult(job, "skipped")
    file_id = extract_file_id(job.pdf_url)
    if not file_id:
        return RowResult(job, "error", label="No URL", detail=f'PDF URL cell = "{job.pdf_url}" (cannot extract a Drive file ID)')

    if not hasattr(tls, "drive"):
        tls.drive = clients.drive_factory()
    try:
        meta = drive_metadata(tls.drive, file_id)
        if meta.get("trashed"):
            raise DriveError("file is in the trash")
        size = int(meta.get("size") or 0)
        # Apps Script 와 동일: PDF 이고 크기가 한도를 넘을 때만 표지 이미지 사용 (한도와 같으면 원본 전체)
        # [TH] เหมือน Apps Script: ใช้ภาพหน้าปกเฉพาะ PDF ที่ใหญ่เกินเพดาน (เท่าเพดานพอดีใช้ไฟล์เต็ม)
        # [EN] Same as Apps Script: the cover image is used only for a PDF larger than the limit (exactly at the limit = full file)
        is_pdf = meta.get("mimeType", "") == "application/pdf"
        use_cover = size > max_bytes and is_pdf and allow_thumbnail
        # --check-access 모드: 메타데이터만 확인하고 여기서 종료 (다운로드/Gemini 호출 없음)
        # [TH] โหมด --check-access: เช็กแค่ metadata แล้วจบตรงนี้ (ไม่ดาวน์โหลด/ไม่เรียก Gemini)
        # [EN] --check-access mode: only check the metadata and stop here (no download, no Gemini call)
        if check_only:
            return RowResult(job, "ok", data={"_name": meta.get("name"), "_size": size, "_cover": use_cover})
        if size > max_bytes and not use_cover:
            return RowResult(job, "error", label="Too Large",
                             detail=f"fileId={file_id} | {size / 1e6:.1f} MB > {max_bytes / 1e6:.0f} MB inline limit")
    except DriveError as e:
        return RowResult(job, "error", label="Drive Access", detail=f"fileId={file_id} | {e} | {DRIVE_HINT}")
    mime, note = guess_mime(meta.get("mimeType", "")), ""
    if use_cover:
        # 15MB 초과 PDF: 원본 대신 1페이지 썸네일 (Apps Script 의 extractDriveFileForAI 와 동일)
        # [TH] PDF เกิน 15MB: ใช้ thumbnail หน้าแรกแทนไฟล์ต้นฉบับ (เหมือน extractDriveFileForAI ของ Apps Script)
        # [EN] PDF over 15MB: use the page-1 thumbnail instead of the original (same as extractDriveFileForAI in Apps Script)
        try:
            pdf_bytes, mime = drive_thumbnail(tls.drive, file_id)
        except DriveError as e:
            return RowResult(job, "error", label="Drive Access",
                             detail=f"fileId={file_id} | large PDF ({size / 1e6:.1f} MB) cover capture failed: {e}")
        note = f"{size / 1e6:.1f} MB > {max_bytes / 1e6:.0f} MB -> page-1 cover image only"
    else:
        try:
            pdf_bytes = drive_download(tls.drive, file_id)
        except DriveError as e:
            return RowResult(job, "error", label="Drive Access", detail=f"fileId={file_id} | {e} | {DRIVE_HINT}")

    if ctl.stop.is_set():
        return RowResult(job, "skipped")
    # Gemini 호출 -> JSON 해석. JSON 이 깨졌으면 같은 요청을 parse_retries 번까지 다시 보냄 (모델 출력은 매번 조금씩 달라서 재시도로 해결되는 경우가 많음)
    # [TH] เรียก Gemini -> แปลง JSON ถ้า JSON พังจะส่งคำขอเดิมซ้ำได้ถึง parse_retries ครั้ง (ผลลัพธ์ของโมเดลแต่ละครั้งไม่เหมือนกัน การลองใหม่มักช่วยได้)
    # [EN] Call Gemini -> parse the JSON. If the JSON is broken the same request is re-sent up to parse_retries times
    #      (the model's output differs a little every time, so a retry often fixes it)
    retried, prompt, multi = 0, spec.prompt, ""
    while True:
        outcome = call_gemini(clients, prompt, pdf_bytes, mime, usage, ctl)
        if outcome.kind == "fatal":
            ctl.request_stop(f"Gemini ตอบ {outcome.label}: {outcome.detail[:300]}")
            return RowResult(job, "error", label=outcome.label, detail=outcome.detail)
        if outcome.kind == "error":
            if outcome.label == "Stopped":
                return RowResult(job, "skipped")
            return RowResult(job, "error", label=outcome.label, detail=outcome.detail)
        try:
            data, multi = parse_model_report(outcome.text, job.doc_no, spec.no_key, accept_first)
        except (ValueError, json.JSONDecodeError) as e:
            if retried < parse_retries and not ctl.stop.is_set():
                retried += 1
                if isinstance(e, MultiReportError):
                    # 여러 보고서가 돌아와 고를 수 없었을 때만: 다음 요청에 "보고서 하나만, 이 번호로" 라는 지시를 덧붙임 (첫 요청은 Apps Script 와 동일한 프롬프트)
                    # [TH] เฉพาะกรณี AI ตอบหลายรายงานแล้วเลือกไม่ได้: คำขอรอบถัดไปเติมคำสั่ง "ตอบรายงานเดียวตามเลขนี้" (คำขอแรกใช้ prompt เดียวกับ Apps Script)
                    # [EN] Only when the AI returned several reports and none could be picked: the next request gets an extra
                    #      instruction "return just one report, the one with this number" (the first request uses the Apps Script prompt)
                    prompt = spec.prompt + SINGLE_REPORT_HINT.format(doc_no=job.doc_no or "(unknown)")
                continue
            return RowResult(job, "error", label="JSON Parse", detail=json_parse_detail(e, outcome, retried))
        break
    return RowResult(job, "ok", data=data, note=note, retried=retried, multi=multi)


# ---------------------------------------------------------------------------
# 메인 루프
# [TH] วงรอบหลัก
# [EN] Main loop
# ---------------------------------------------------------------------------
@dataclass
class Summary:
    done: int = 0
    reconciled: int = 0
    # 큰 PDF 를 표지 이미지로 대체해 처리한 행 수
    # [TH] จำนวนแถวที่ PDF ใหญ่ถูกแทนด้วยภาพหน้าปก
    # [EN] Number of rows where a large PDF was replaced by its cover image
    cover_only: int = 0
    # AI 가 보고서를 여러 개 돌려줘서 하나를 골라 처리한 행 수
    # [TH] จำนวนแถวที่ AI ตอบกลับมาหลายรายงานแล้วเลือกมาหนึ่งรายการ
    # [EN] Number of rows where the AI returned several reports and one was picked
    multi_report: int = 0
    errors: dict = field(default_factory=dict)
    skipped: int = 0
    stopped_reason: str = ""


def chunked(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def explain_no_jobs(values: list[list[str]], sid: str, spec: QueueSpec, *, retry_errors: bool, include_blank: bool,
                    only_rows: Optional[set[int]]) -> list[str]:
    """
    처리할 행이 0 일 때, 왜 그런지(큐가 비었는지 / 상태값이 맞지 않는지 / 엉뚱한 시트인지) 알려주는 안내문
    [TH] ข้อความอธิบายเมื่อไม่มีแถวให้ทำ ว่าเพราะอะไร (คิวว่าง / ค่าสถานะไม่ตรง / ชี้ผิดชีต)
    [EN] Explains why there are 0 rows to process (empty queue / status values that do not match / wrong sheet)
    """
    lines: list[str] = []
    data = values[1:] if values else []
    if not data:
        lines.append(f"แท็บ {spec.queue_tab} ไม่มีแถวข้อมูล - ถ้าในชีตที่คุณเปิดอยู่มีข้อมูล แปลว่าสคริปต์อ่านคนละสเปรดชีต")
        lines.append(f"ตรวจ ID: {sid} (ดูบรรทัด 'sheet:' ด้านบน; ID มาจากตัวแปร PMIS_SPREADSHEET_ID)")
        return lines
    if only_rows is not None:
        lines.append("ใช้ --only-rows: ทำเฉพาะแถวที่ระบุ และข้ามแถวที่เป็น Done - ตรวจเลขแถวอีกครั้ง")
        return lines
    header_g = (values[0][6].strip() if len(values[0]) > 6 else "")
    if header_g != "AI Processed":
        lines.append(f"หัวคอลัมน์ G คือ {header_g!r} (ปกติคือ 'AI Processed') - สคริปต์อ่านสถานะจากคอลัมน์ G เสมอ ตรวจว่าคอลัมน์สถานะอยู่ที่ G")
    counts: dict[str, int] = {}
    blank_with_url = 0
    for row in data:
        cells = list(row) + [""] * (7 - len(row))
        status = str(cells[6]).strip()
        counts[status] = counts.get(status, 0) + 1
        if status == "" and (str(cells[3]).strip() or str(cells[4]).strip()):
            blank_with_url += 1
    shown = ", ".join(f"{k!r}×{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:8])
    if len(counts) > 8:
        shown += ", …"
    wanted = "'Waiting' หรือขึ้นต้นด้วย 'Error'" if retry_errors else "'Waiting'"
    lines.append(f"สคริปต์ทำเฉพาะแถวที่คอลัมน์ G เป็น {wanted} (ตัวพิมพ์ต้องตรง) แต่ในคิวนี้พบ: {shown}")
    if blank_with_url and not include_blank:
        lines.append(f"มี {blank_with_url} แถวที่คอลัมน์ G ว่างแต่มี URL - ใส่ --include-blank ถ้าต้องการให้ทำแถวพวกนี้ด้วย")
    if set(counts) <= {"Done"}:
        lines.append("ทุกแถวเป็น Done แล้ว - ถ้ายังมีงานค้าง ให้สร้างคิวใหม่ (extractLegacy…Links) หรือแก้สถานะเป็น Waiting")
    return lines


def run(args: argparse.Namespace, clients: Clients, log: Callable[[str], None] = print) -> Summary:
    spec = SPECS[args.queue]
    sid = args.spreadsheet_id
    allow_thumbnail = not getattr(args, "no_thumbnail", False)
    parse_retries = max(0, int(getattr(args, "parse_retries", 0) or 0))
    accept_first = bool(getattr(args, "accept_first_report", False))
    summary = Summary()
    ctl = RunControl()
    usage = Usage()

    values = read_values(clients.sheets, sid, f"'{spec.queue_tab}'!A1:G")
    only_rows = parse_row_spec(args.only_rows) if args.only_rows else None
    jobs = select_jobs(values, retry_errors=not args.no_retry_errors, include_blank=args.include_blank, only_rows=only_rows)

    by_status: dict[str, int] = {}
    for row in values[1:]:
        s = (row[6].strip() if len(row) > 6 else "") or "(blank)"
        key = "Error" if s.startswith("Error") else s
        by_status[key] = by_status.get(key, 0) + 1
    if by_status:
        log(f"คิว {spec.queue_tab}: " + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())))
    else:
        log(f"คิว {spec.queue_tab}: ว่าง (มีแต่หัวตาราง ไม่มีแถวข้อมูล)")
    log(f"พบแถวที่ต้องทำ {len(jobs)} แถว" + (f" (จำกัดรอบนี้ {args.limit})" if args.limit and len(jobs) > args.limit else ""))
    if not jobs:
        for line in explain_no_jobs(values, sid, spec, retry_errors=not args.no_retry_errors, include_blank=args.include_blank,
                                    only_rows=only_rows):
            log("  " + line)
    if args.limit:
        jobs = jobs[: args.limit]

    # --dry-run: 대상 행만 출력하고 종료 (Drive/Gemini 호출 없음, 시트 쓰기 없음)
    # [TH] --dry-run: แสดงแถวที่จะทำแล้วจบ (ไม่เรียก Drive/Gemini ไม่เขียนชีต)
    # [EN] --dry-run: only list the rows that would be processed (no Drive/Gemini calls, no sheet writes)
    if args.dry_run:
        for j in jobs[:50]:
            log(f"  แถว {j.row}: fileId={extract_file_id(j.pdf_url)} | สถานะเดิม={j.prior_status[:60]!r}")
        if len(jobs) > 50:
            log(f"  ... และอีก {len(jobs) - 50} แถว")
        log("(dry-run: ไม่เรียก Drive/Gemini และไม่เขียนชีต)")
        return summary

    tls = threading.local()

    # --check-access: Drive 접근 가능 여부만 확인 (Gemini 호출 없음, 시트 쓰기 없음)
    # [TH] --check-access: เช็กแค่ว่าเข้าถึงไฟล์ใน Drive ได้ไหม (ไม่เรียก Gemini ไม่เขียนชีต)
    # [EN] --check-access: only check whether the Drive files are reachable (no Gemini call, no sheet writes)
    if args.check_access:
        # 모든 출력 줄에 어느 큐 시트(MIGRATION_QUEUE / MIGRATION_QUEUE_DRQ)의 결과인지 표시
        # [TH] ทุกบรรทัดของผลลัพธ์ระบุชื่อชีตคิว (MIGRATION_QUEUE / MIGRATION_QUEUE_DRQ) ว่าผลมาจากชีตไหน
        # [EN] Every output line names the queue sheet (MIGRATION_QUEUE / MIGRATION_QUEUE_DRQ) the result belongs to
        tag = f"[{spec.queue_tab}]"
        log(f"{tag} ตรวจสิทธิ์เข้าถึงไฟล์ PDF ใน Drive ของคิว {spec.name} (--queue {args.queue}; ไม่เรียก Gemini ไม่เขียนชีต) ...")
        bad = 0
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for res in pool.map(lambda j: process_job(j, spec, clients, tls, usage, ctl, args.max_mb * 1_000_000, True, allow_thumbnail), jobs):
                if res.kind == "error":
                    bad += 1
                    log(f"  ✗ {tag} แถว {res.job.row}: {res.label} | {res.detail[:300]}")
                elif (res.data or {}).get("_cover"):
                    log(f"  ℹ {tag} แถว {res.job.row}: PDF ใหญ่ {(res.data or {}).get('_size', 0) / 1e6:.1f} MB (> {args.max_mb:g} MB) -> จะส่งเฉพาะภาพหน้าปก")
        log(f"{tag} เข้าถึงได้ {len(jobs) - bad}/{len(jobs)} ไฟล์" + (f" - แชร์ไฟล์/โฟลเดอร์ให้ {clients.sa_email}" if bad and clients.sa_email else ""))
        # 다른 큐는 이번 실행에서 확인하지 않았으므로, 해당 큐를 확인하는 명령어를 안내
        # [TH] รอบนี้ไม่ได้ตรวจคิวอีกอัน จึงบอกคำสั่งสำหรับตรวจคิวนั้น
        # [EN] The other queue was not checked in this run, so print the command that checks it
        other_key = next((k for k in sorted(SPECS) if k != args.queue), None)
        if other_key and getattr(args, "hint_other_queue", True):
            log(f"(รอบนี้ตรวจเฉพาะ {spec.queue_tab}; คิว {SPECS[other_key].queue_tab}: python migrate_queue.py --check-access --queue {other_key} --limit 0)")
        return summary

    # 중복 방지: 이미 DB 에 있는 PDF -> AI 호출 없이 Done 처리
    # [TH] กันซ้ำ: PDF ที่อยู่ใน DB แล้ว -> ตั้ง Done โดยไม่เรียก AI
    # [EN] Duplicate guard: a PDF already in the DB -> mark Done without calling the AI
    existing_ids: set[str] = set()
    if not args.no_reconcile:
        col = _col_letter(spec.db_pdf_col + 1)
        for r in read_values(clients.sheets, sid, f"'{spec.db_tab}'!{col}2:{col}"):
            fid = extract_file_id(r[0]) if r else None
            if fid:
                existing_ids.add(fid)
    to_run: list[Job] = []
    reconciled_updates: dict[int, str] = {}
    for j in jobs:
        fid = extract_file_id(j.pdf_url)
        if fid and fid in existing_ids:
            reconciled_updates[j.row] = "Done"
        else:
            to_run.append(j)
    if reconciled_updates:
        write_statuses(clients.sheets, sid, spec.queue_tab, reconciled_updates)
        summary.reconciled = len(reconciled_updates)
        log(f"พบ {summary.reconciled} แถวที่อยู่ใน {spec.db_tab} แล้ว -> ตั้ง Done โดยไม่เรียก AI (แถว: {sorted(reconciled_updates)[:20]})")

    max_bytes = args.max_mb * 1_000_000
    interrupted = False
    for chunk in chunked(to_run, args.batch):
        if ctl.stop.is_set():
            break
        results: list[RowResult] = []
        futures: list = []
        pool = ThreadPoolExecutor(max_workers=args.workers)
        try:
            futures = [pool.submit(process_job, j, spec, clients, tls, usage, ctl, max_bytes, False, allow_thumbnail, parse_retries, accept_first) for j in chunk]
            for fut in futures:
                results.append(fut.result())
        except KeyboardInterrupt:
            interrupted = True
            ctl.request_stop("ผู้ใช้กด Ctrl+C")
            # 진행 중인 작업은 끝까지 기다림 (아직 시작 안 한 작업은 건너뜀)
            # [TH] ให้งานที่ค้างอยู่ทำเสร็จ (ตัวที่ยังไม่เริ่มจะถูกข้าม)
            # [EN] Let in-flight jobs finish (jobs that have not started are skipped)
            for fut in futures:
                try:
                    results.append(fut.result())
                except BaseException:  # noqa: BLE001
                    pass
        finally:
            pool.shutdown(wait=True)

        ok = [r for r in results if r.kind == "ok"]
        updates: dict[int, str] = {}
        for r in results:
            if r.kind == "error":
                updates[r.job.row] = error_text(r.label, r.detail)
                summary.errors[r.label] = summary.errors.get(r.label, 0) + 1
                log(f"  ✗ แถว {r.job.row}: {updates[r.job.row][:200]}")
            elif r.kind == "skipped":
                summary.skipped += 1

        # 순서: 먼저 DB 에 쓰고, 성공한 뒤에만 큐에 Done 표시
        # (현재 Apps Script 는 G열 상태를 먼저 쓰고 DB 를 나중에 쓰므로, DB 쓰기가 실패하면 Done 만 남을 수 있음)
        # [TH] ลำดับ: เขียน DB ก่อน แล้วค่อยตั้ง Done ในคิวเมื่อสำเร็จ
        #      (Apps Script ปัจจุบันเขียนสถานะคอลัมน์ G ก่อนแล้วค่อยเขียน DB ถ้าเขียน DB พลาดอาจเหลือ Done โดยไม่มีข้อมูล)
        # [EN] Order: write the DB first, mark Done in the queue only after it succeeds
        #      (the current Apps Script writes the column-G statuses first and the DB afterwards, so a failed DB write
        #      can leave rows marked Done with no data)
        if ok:
            db_rows = [build_db_row(spec, r.data or {}, r.job.pdf_url, r.job.folder_url) for r in ok]
            try:
                append_rows(clients.sheets, sid, spec.db_tab, db_rows)
            except SheetsError as e:
                for r in ok:
                    updates[r.job.row] = error_text("DB Write", f"{e} | rows={len(db_rows)}, cols={len(db_rows[0])}")
                summary.errors["DB Write"] = summary.errors.get("DB Write", 0) + len(ok)
                log(f"  ✗ เขียน {spec.db_tab} ไม่สำเร็จ: {e}")
                ctl.request_stop("เขียน DB ไม่สำเร็จ (รันใหม่ได้ - ระบบเช็คข้อมูลซ้ำให้)")
            else:
                for r in ok:
                    updates[r.job.row] = "Done"
                    if r.note:
                        summary.cover_only += 1
                        log(f"  ℹ แถว {r.job.row}: PDF ใหญ่ ({r.note}) - ข้อมูลมาจากหน้าแรกเท่านั้น ควรตรวจสอบ")
                    if r.retried:
                        log(f"  ℹ แถว {r.job.row}: JSON ที่ AI ตอบพังครั้งแรก ลองเรียกใหม่ {r.retried} ครั้งจึงผ่าน")
                    if r.multi:
                        summary.multi_report += 1
                        log(f"  ⚠ แถว {r.job.row}: {r.multi} - ควรตรวจสอบ")
                summary.done += len(ok)
                log(f"  ✓ {len(ok)} แถว -> {spec.db_tab} (แถวคิว {[r.job.row for r in ok][:10]}{'…' if len(ok) > 10 else ''})")
        try:
            write_statuses(clients.sheets, sid, spec.queue_tab, updates)
        except SheetsError as e:
            log(f"  !! เขียนสถานะลงคิวไม่สำเร็จ: {e}\n     ข้อมูลอยู่ใน {spec.db_tab} แล้ว - รันซ้ำได้ ระบบจะตั้ง Done ให้โดยไม่เรียก AI")
            ctl.request_stop("เขียนสถานะลงคิวไม่สำเร็จ")
        if interrupted:
            break

    summary.stopped_reason = ctl.stop_reason
    log("-" * 60)
    log(f"เสร็จ: Done={summary.done} (+reconciled {summary.reconciled}), Error={sum(summary.errors.values())} {summary.errors or ''}, ข้าม={summary.skipped}")
    if summary.cover_only:
        log(f"ในจำนวน Done มี {summary.cover_only} แถวที่ใช้ภาพหน้าปกแทน PDF ใหญ่ (เหมือน Apps Script) - ข้อมูลจากหน้าแรกเท่านั้น")
    if summary.multi_report:
        log(f"ในจำนวน Done มี {summary.multi_report} แถวที่ AI ตอบหลายรายงานใน PDF เดียวแล้วเลือกมาหนึ่งรายการ - ควรตรวจสอบแถวที่มีเครื่องหมาย ⚠ ด้านบน")
    if ctl.stop_reason:
        log(f"หยุดก่อนครบ: {ctl.stop_reason}")
    if usage.calls:
        pin, pout = PRICES_USD_PER_M.get(clients.model, (None, None))
        cost = f" ≈ ${usage.prompt / 1e6 * pin + usage.output / 1e6 * pout:.3f} (list price)" if pin else ""
        log(f"Gemini {usage.calls} ครั้ง: input {usage.prompt:,} tok, output(+thinking) {usage.output:,} tok{cost}")
    return summary


def _col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ประมวลผลคิว MIGRATION_QUEUE / MIGRATION_QUEUE_DRQ ด้วย Gemini (แทน Apps Script)")
    p.add_argument("--queue", choices=sorted(SPECS), default=None,
                   help="dwr = MIGRATION_QUEUE->DB_DWR, drq = MIGRATION_QUEUE_DRQ->DB_DRQ; ไม่ระบุ = ทำทั้งสองคิว (dwr ก่อน แล้วตามด้วย drq)")
    p.add_argument("--sa-key", default=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"), help="path ของ service account key (.json)")
    p.add_argument("--model", default=os.environ.get("GEMINI_MODEL", DEFAULT_MODEL))
    p.add_argument("--api-version", default=DEFAULT_API_VERSION)
    p.add_argument("--limit", type=int, default=30, help="จำนวนแถวสูงสุดต่อรอบ ต่อหนึ่งคิว (0 = ไม่จำกัด) ค่าเริ่มต้น 30")
    p.add_argument("--batch", type=int, default=30, help="จำนวนแถวต่อก้อน: เขียน DB + สถานะลงชีตทีละก้อน")
    p.add_argument("--workers", type=int, default=6, help="จำนวนงานขนาน (ลดลงถ้าเจอ 429)")
    p.add_argument("--only-rows", help="ทำเฉพาะแถวในชีตที่ระบุ เช่น 83,102,127-128 (ต้องระบุ --queue ด้วย เพราะเลขแถวเป็นของชีตคิวนั้น)")
    p.add_argument("--no-retry-errors", action="store_true", help="ไม่ลองแถวที่เป็น Error ซ้ำ (ทำเฉพาะ Waiting)")
    p.add_argument("--include-blank", action="store_true", help="นับแถวที่คอลัมน์ G ว่าง (แต่มี URL) เป็นงานด้วย")
    p.add_argument("--no-reconcile", action="store_true", help="ไม่เช็คว่า PDF อยู่ใน DB แล้วหรือยัง")
    p.add_argument("--max-mb", type=float, default=LARGE_PDF_MB,
                   help="ขนาด PDF สูงสุดที่ส่งทั้งไฟล์ (MB) ค่าเริ่มต้น 15 = เท่า Apps Script; ใหญ่กว่านี้ใช้ภาพหน้าปก (ดู --no-thumbnail)")
    p.add_argument("--parse-retries", type=int, default=1,
                   help="ถ้า JSON ที่ AI ตอบพัง (Error: JSON Parse) ให้เรียก Gemini ซ้ำกี่ครั้ง ค่าเริ่มต้น 1 (0 = ไม่ลองซ้ำ)")
    p.add_argument("--accept-first-report", action="store_true",
                   help="ถ้า AI ตอบหลายรายงานใน PDF เดียวและไม่มีรายการไหนตรงกับ Doc No ของคิว ให้ใช้รายการแรก (ค่าเริ่มต้น: ขึ้น Error JSON Parse พร้อมรายการเลขที่ที่ AI เจอ)")
    p.add_argument("--max-output-tokens", type=int, default=None,
                   help="กำหนด maxOutputTokens ของ Gemini (ค่าเริ่มต้นไม่กำหนด เหมือน Apps Script) ใช้เมื่อ JSON Parse พังเพราะคำตอบถูกตัด (finishReason=MAX_TOKENS)")
    p.add_argument("--no-thumbnail", action="store_true",
                   help="PDF ที่เกิน --max-mb ไม่ใช้ภาพหน้าปกแทน แต่เขียน Error: Too Large (Apps Script จะใช้ภาพหน้าปกเสมอ)")
    p.add_argument("--dry-run", action="store_true", help="แค่แสดงว่าจะทำแถวไหน (ไม่เรียก Drive/Gemini ไม่เขียนชีต)")
    p.add_argument("--check-access", action="store_true", help="เช็คว่า service account เปิดไฟล์ PDF ได้ไหม (ไม่เรียก Gemini ไม่เขียนชีต)")
    return p


def make_clients(args: argparse.Namespace, need_gemini: bool) -> Clients:
    import requests
    from google.auth.transport.requests import AuthorizedSession, Request
    from google.oauth2 import service_account

    if not args.sa_key or not os.path.isfile(args.sa_key):
        sys.exit("ไม่พบ service account key: ใส่ --sa-key <path> หรือตั้ง GOOGLE_APPLICATION_CREDENTIALS")
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if need_gemini and not api_key:
        sys.exit("ยังไม่ได้ตั้ง GEMINI_API_KEY (PowerShell:  $env:GEMINI_API_KEY = \"...\")")
    creds = service_account.Credentials.from_service_account_file(args.sa_key, scopes=SCOPES)
    creds.refresh(Request())
    return Clients(
        sheets=AuthorizedSession(creds),
        drive_factory=lambda: AuthorizedSession(creds),
        gemini=requests.Session(),
        api_key=api_key,
        model=args.model,
        api_version=args.api_version,
        sa_email=creds.service_account_email,
        max_output_tokens=getattr(args, "max_output_tokens", None),
    )


def queues_to_run(queue_arg: Optional[str]) -> list[str]:
    """
    --queue 를 지정하지 않으면 두 큐 모두 (dwr -> drq 순서), 지정하면 그 큐만
    [TH] ถ้าไม่ระบุ --queue จะทำทั้งสองคิว (dwr แล้วตามด้วย drq) ถ้าระบุจะทำเฉพาะคิวนั้น
    [EN] Without --queue both queues are processed (dwr first, then drq); with --queue only that queue
    """
    return [queue_arg] if queue_arg else list(SPECS)


def run_queues(args: argparse.Namespace, clients: Clients, keys: list[str], log: Callable[[str], None] = print,
               sheet_title: str = "") -> int:
    """
    큐를 차례로 실행하고 종료 코드를 반환 (0 = 정상, 2 = 중단/실패가 있음)
    [TH] รันทีละคิวตามลำดับ แล้วคืนรหัสออก (0 = ปกติ, 2 = มีการหยุดกลางคัน/ล้มเหลว)
    [EN] Runs the queues one after another and returns the exit code (0 = fine, 2 = something stopped or failed)
    """
    multi = len(keys) > 1
    exit_code = 0
    total_done = total_err = 0
    for i, key in enumerate(keys):
        spec = SPECS[key]
        sub = copy.copy(args)
        sub.queue = key
        # 두 큐를 모두 확인할 때는 '다른 큐 확인 명령어' 안내가 필요 없음
        # [TH] ถ้าตรวจทั้งสองคิวอยู่แล้ว ไม่ต้องบอกคำสั่งสำหรับตรวจอีกคิว
        # [EN] When both queues are being checked anyway, the "command for the other queue" hint is not needed
        sub.hint_other_queue = not multi
        # 큐마다 제목 줄을 출력 (스프레드시트 이름 포함)
        # [TH] พิมพ์หัวข้อของแต่ละคิว (พร้อมชื่อสเปรดชีต)
        # [EN] Print a heading for each queue (including the spreadsheet name)
        log(("\n" if multi else "") + f"===== คิว {spec.name}: {spec.queue_tab} -> {spec.db_tab}"
            + (f" | ชีต: {sheet_title}" if sheet_title else "") + " =====")
        try:
            summary = run(sub, clients, log=log)
        except SheetsError as e:
            # 한 큐를 읽지 못해도(예: 시트 탭이 없음) 다음 큐는 계속 진행
            # [TH] อ่านคิวหนึ่งไม่ได้ (เช่นไม่มีแท็บชีต) ก็ยังทำคิวถัดไปต่อ
            # [EN] If one queue cannot be read (e.g. the sheet tab is missing), the next queue is still processed
            log(f"✗ ทำคิว {spec.queue_tab} ไม่ได้: {e}")
            exit_code = 2
            continue
        total_done += summary.done + summary.reconciled
        total_err += sum(summary.errors.values())
        if summary.stopped_reason:
            exit_code = 2
            rest = [SPECS[k].queue_tab for k in keys[i + 1:]]
            if rest:
                # 중단 사유(크레딧 소진, 키 오류 등)는 다음 큐에도 그대로 적용되므로 건너뜀
                # [TH] สาเหตุที่หยุด (เครดิตหมด คีย์ผิด ฯลฯ) จะเกิดกับคิวถัดไปเหมือนกัน จึงข้าม
                # [EN] The reason for stopping (credits exhausted, bad key, ...) would hit the next queue too, so it is skipped
                log(f"หยุดก่อนครบ -> ข้ามคิวที่เหลือ: {', '.join(rest)}")
            break
    if multi and not (args.dry_run or args.check_access):
        log(f"\nรวมทุกคิว: Done={total_done}, Error={total_err}")
    return exit_code


def require_spreadsheet_id(environ: Optional[dict] = None) -> str:
    """
    대상 스프레드시트 ID 를 환경 변수에서만 읽음. 없거나 비어 있으면 안내 메시지와 함께 종료
    [TH] อ่าน ID สเปรดชีตเป้าหมายจากตัวแปรสภาพแวดล้อมเท่านั้น ถ้าไม่มีหรือว่างจะจบพร้อมข้อความแนะนำ
    [EN] Reads the target spreadsheet ID from the environment only; exits with a how-to message if it is missing or empty
    """
    value = str((environ if environ is not None else os.environ).get(SPREADSHEET_ID_ENV, "")).strip()
    if not value:
        sys.exit(
            f"ยังไม่ได้ตั้ง {SPREADSHEET_ID_ENV} (ID ของสเปรดชีตเป้าหมาย - ไม่มีค่าเริ่มต้นในโค้ดและไม่มีออปชัน --spreadsheet-id)\n"
            "  ID คือส่วนใน URL ระหว่าง /d/ กับ /edit  เช่น https://docs.google.com/spreadsheets/d/<ID>/edit\n"
            f'  PowerShell:  $env:{SPREADSHEET_ID_ENV} = "<ID>"')
    return value


def main(argv: Optional[list[str]] = None) -> int:
    # Windows 에서 파일로 redirect 해도 태국어/기호가 깨지지 않도록 UTF-8 로 설정
    # [TH] ภาษาไทย/สัญลักษณ์ไม่พังเมื่อ redirect ไปไฟล์บน Windows
    # [EN] Force UTF-8 so Thai text and symbols do not break when output is redirected to a file on Windows
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    # 가장 먼저 확인 (키/네트워크 작업 전에 설정 누락을 알려 줌)
    # [TH] เช็กเป็นอย่างแรก (แจ้งว่าตั้งค่าขาดก่อนจะแตะคีย์/เครือข่าย)
    # [EN] Checked first (a missing setting is reported before any key or network work)
    args.spreadsheet_id = require_spreadsheet_id()
    keys = queues_to_run(args.queue)
    if args.only_rows and len(keys) > 1:
        sys.exit("--only-rows ใช้ได้กับคิวเดียว: ระบุ --queue dwr หรือ --queue drq ด้วย (เลขแถวเป็นของชีตคิวนั้นๆ)")
    clients = make_clients(args, need_gemini=not (args.dry_run or args.check_access))
    title, title_error = spreadsheet_title(clients.sheets, args.spreadsheet_id)
    if title:
        sheet_label = f"{title} ({args.spreadsheet_id})"
    else:
        sheet_label = f"{args.spreadsheet_id} (อ่านชื่อชีตไม่ได้ - ตรวจ ID และการแชร์สิทธิ์ให้ service account: {title_error})"
    print(f"service account: {clients.sa_email} | sheet: {sheet_label} | model: {clients.model}")
    return run_queues(args, clients, keys, sheet_title=title)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
