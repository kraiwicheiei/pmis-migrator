"""
Gemini 의 JSON 답변 해석과 DB 행 만들기
[TH] แปลความคำตอบ JSON ของ Gemini และสร้างแถวสำหรับ DB
[EN] Parsing Gemini's JSON answers and building the DB rows
"""
from __future__ import annotations

import json
import re
from typing import Any

from .specs import QueueSpec


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


def build_db_row(spec: QueueSpec, data: dict, pdf_url: str, folder_url: str, cp: str = "") -> list:
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
        elif kind == "cp":
            row.append(cp)
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
        return items[0], f"AI returned the same report {n} times; used the first one"
    want = norm_doc_no(doc_no)
    if want and no_key:
        hits = [x for x in items if norm_doc_no(x.get(no_key)) == want]
        if hits:
            extra = f" ({len(hits)} reports match; used the first one)" if len(hits) > 1 else ""
            return hits[0], f"AI returned {n} reports for one PDF; picked the one whose {no_key} matches Doc No '{doc_no}'{extra}"
    if accept_first:
        return items[0], f"AI returned {n} reports for one PDF and none matches Doc No '{doc_no}'; used the first one (--accept-first-report)"
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
