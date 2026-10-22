"""
상수: API 주소, 기본 모델, 재시도/중단 상태 코드, 단가표
[TH] ค่าคงที่: ที่อยู่ API, โมเดลเริ่มต้น, รหัสสถานะที่ retry/หยุด, ตารางราคา
[EN] Constants: API URLs, the default model, the retry / stop status codes, the price table
"""
from __future__ import annotations


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
