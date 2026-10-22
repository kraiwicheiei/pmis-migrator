"""
Google Drive API 호출 (PDF 메타데이터/다운로드/썸네일)
[TH] การเรียก Google Drive API (metadata/ดาวน์โหลด PDF/thumbnail)
[EN] Google Drive API calls (PDF metadata / download / thumbnail)
"""
from __future__ import annotations

from typing import Any

from . import clock
from .config import DRIVE_BASE, DRIVE_THUMBNAIL_URL, RETRY_STATUSES, THUMBNAIL_SIZE
from .utils import backoff_seconds


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
            clock.SLEEP(backoff_seconds(attempt))
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


# 시트의 오류 셀(G열)에 기록되는 모든 문구와 콘솔 로그는 영어
# [TH] ข้อความทุกอย่างที่เขียนลงเซลล์ Error (คอลัมน์ G) และ log บนคอนโซลเป็นภาษาอังกฤษ
# [EN] Every phrase written into the sheet's Error cell (column G), and the console log, is in English
DRIVE_HINT = "the file does not exist / was deleted, or the file (or its folder) is not shared with the service account"
