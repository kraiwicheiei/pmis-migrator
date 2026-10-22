"""
Gemini API 호출
[TH] การเรียก Gemini API
[EN] Gemini API calls
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Optional

from . import clock
from .config import FATAL_GEMINI_STATUSES, GEMINI_BASE, RETRY_STATUSES
from .models import Clients, RunControl, Usage
from .utils import backoff_seconds, retry_delay_hint


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
                clock.SLEEP(backoff_seconds(attempt))
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
            clock.SLEEP(backoff_seconds(attempt, retry_delay_hint(body_text)))
            continue
        break
    if last_label == "API 429":
        ctl.note_rate_limited()
    return GeminiOutcome("error", label=last_label, detail=last_detail)
