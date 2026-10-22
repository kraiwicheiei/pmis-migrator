"""
한 행 처리: Drive 에서 PDF -> Gemini -> DB 행
[TH] ประมวลผลหนึ่งแถว: PDF จาก Drive -> Gemini -> แถว DB
[EN] Processing a single row: PDF from Drive -> Gemini -> a DB row
"""
from __future__ import annotations

import json
import threading

from .drive import DRIVE_HINT, DriveError, drive_download, drive_metadata, drive_thumbnail
from .gemini import GeminiOutcome, call_gemini
from .models import Clients, Job, RowResult, RunControl, Usage
from .reports import MultiReportError, parse_model_report
from .specs import QueueSpec, SINGLE_REPORT_HINT
from .utils import extract_file_id


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
            ctl.request_stop(f"Gemini answered {outcome.label}: {outcome.detail[:300]}")
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
