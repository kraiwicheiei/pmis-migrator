"""
실행 중 쓰이는 데이터 구조
[TH] โครงสร้างข้อมูลที่ใช้ระหว่างรัน
[EN] Data structures used during a run
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .config import DEFAULT_API_VERSION, DEFAULT_MODEL


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
    # 큐 시트 F열 (Status(C/P)): DB 의 C/P 열에 그대로 기록
    # [TH] คอลัมน์ F ของชีตคิว (Status(C/P)) เขียนลงคอลัมน์ C/P ของ DB ตามเดิม
    # [EN] Column F of the queue sheet (Status(C/P)): written as it is to the C/P column of the DB
    cp: str = ""


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
            self.request_stop(f"Gemini answered 429 for {limit} rows in a row even after retries (rate limit / daily quota) - stopping so that not every row becomes an Error")

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
