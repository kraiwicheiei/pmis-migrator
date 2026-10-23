"""
--public-log: 공개 저장소의 Actions 로그에 안전한 줄만 출력 (개수와 상태만; 탭 이름, 시트 이름/ID, Doc No, 오류 상세는 제외)
[TH] --public-log: พิมพ์เฉพาะบรรทัดที่ปลอดภัยสำหรับ log ของ Actions ใน repo สาธารณะ (เฉพาะตัวเลขและสถานะ ไม่มีชื่อแท็บ ชื่อ/ID ชีต Doc No หรือรายละเอียด error)
[EN] --public-log: prints only the lines that are safe for the Actions log of a public repository (counts and statuses;
     no tab names, sheet names / IDs, Doc Nos or error details)
"""
from __future__ import annotations

import re
from typing import Callable, Optional

# 줄은 남기되 뒷부분(이름, ID, 오류 상세)을 잘라 내는 규칙. 위에서부터 먼저 맞는 것을 사용
# [TH] กฎที่เก็บบรรทัดไว้แต่ตัดส่วนท้าย (ชื่อ, ID, รายละเอียด error) ออก ใช้อันแรกที่ตรง
# [EN] Rules that keep a line but cut its tail (names, IDs, error detail); the first match wins
_CUT: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(\[[A-Z_]+\] Reachable: \d+/\d+ files)( - share the files/folder with .*)?$"), r"\1"),
    (re.compile(r"^(===== Queue \w+: [A-Z_]+ -> [A-Z_]+)( \| sheet: .*)? =====$"), r"\1 ====="),
    (re.compile(r"^(===== Build queue \w+): .* -> ([A-Z_]+)( \| sheet: .*)? =====$"), r"\1 -> \2 ====="),
    (re.compile(r"^(\[\w+\]) Read tab '.*': (.*)$"), r"\1 Read legacy tab: \2"),
    (re.compile(r"^(✗ Could not (?:process|build) queue [A-Z_]+):.*$"), r"\1 - run the script locally for the reason"),
    (re.compile(r"^(  ⚠ The rows are written, but the refresh time could not be written).*$"), r"\1 (run locally for the reason)"),
    (re.compile(r"^(⚠ The run finished, but the migration time could not be written).*$"), r"\1 (run locally for the reason)"),
    (re.compile(r"^(Stopped early: Gemini answered [^:]+):.*$"), r"\1"),
    (re.compile(r"^(\d+ rows are already in DB_[A-Z]+ -> marked Done without calling the AI) \(rows: .*\)$"), r"\1"),
    (re.compile(r"^(===== Backfill C/P \w+: [A-Z_]+ <- [A-Z_]+)( \| sheet: .*)? =====$"), r"\1 ====="),
    (re.compile(r"^(✗ Could not backfill C/P in DB_[A-Z]+):.*$"), r"\1 - run the script locally for the reason"),
    (re.compile(r"^(✗ Could not sync DB_[A-Z]+):.*$"), r"\1 - run the script locally for the reason"),
    (re.compile(r"^(===== Sync DB \w+: [A-Z_]+ <- [A-Z_]+)( \| sheet: .*)? =====$"), r"\1 ====="),
    (re.compile(r"^(  ✗ Not syncing [A-Z_]+: \d+ rows would be removed, more than the limit of \d+) \(.*$"), r"\1 - check the legacy sheet, then use --allow-large-removal"),
]

# 그대로 출력하는 줄의 시작 부분
# [TH] ส่วนต้นของบรรทัดที่พิมพ์ออกไปตามเดิม
# [EN] The beginnings of the lines that are printed as they are
_KEEP = re.compile(
    r"^(?:"
    r"Queue [A-Z_]+: |Rows to process|Finished:|All queues|Of the Done rows|Stopped early|Gemini calls:"
    r"|Migration time written to|Replaced in place: |\(dry-run|\[[A-Z_]+\] Reachable|\[[A-Z]+\] Backfill C/P in DB_[A-Z]+: |\[[A-Z]+\] Sync DB_[A-Z]+: "
    r"|  (?:✓ Wrote queue|✓ Synced [A-Z_]+: |ℹ \d+ Done rows got a new PDF link|⚠ \d+ removed rows were Done / Error|✓ Refresh time written to [A-Z_]+!I2"
    r"|⚠ \d+ items have no PDF link|⚠ skipped \d+ cells|⚠ --overwrite: |No items found"
    r"|✗ The header row of [A-Z_]+ is not the expected one|\(--dry-run\) nothing was written"
    r"|✓ Filled C/P in DB_[A-Z]+![A-Z]+: |✓ Wrote the header '[A-Za-z /()]+' to DB_[A-Z]+![A-Z]+1"
    r"|✓ Updated C/P in DB_[A-Z]+![A-Z]+: |✓ Legacy sync tag in DB_[A-Z]+![A-Z]+: set on \d+ rows, cleared on \d+ rows"
    r"|✓ Removed \d+ rows from DB_[A-Z]+ \(copied to |✓ DB_[A-Z]+ already matches the queue"
    r"|✗ Not syncing [A-Z_]+: \d+ rows would be removed|✗ Not syncing DB_[A-Z]+: the queue has no rows)"
    r"|    Fix the header"
    r")"
)

MAX_STOP_REASON = 200


def sanitize(line: str) -> Optional[str]:
    """
    로그 한 줄을 공개해도 되는 형태로 바꿈. 공개하면 안 되는 줄은 None
    [TH] แปลงบรรทัด log หนึ่งบรรทัดให้เป็นรูปแบบที่เปิดเผยได้ ถ้าเปิดเผยไม่ได้คืน None
    [EN] Turns one log line into a form that may be shown publicly; None for a line that must not be shown
    """
    for pattern, replacement in _CUT:
        if pattern.match(line):
            return pattern.sub(replacement, line)
    if _KEEP.match(line):
        # 중단 사유는 길이만 제한 (Gemini 오류 본문은 위 규칙에서 이미 잘림)
        # [TH] จำกัดความยาวของเหตุผลที่หยุดเท่านั้น (เนื้อ error ของ Gemini ถูกตัดไปแล้วด้วยกฎด้านบน)
        # [EN] Only the length of the stop reason is limited (the body of a Gemini error is already cut by the rules above)
        return line[:MAX_STOP_REASON] if line.startswith("Stopped early") else line
    return None


def make_log(public: bool) -> Callable[[str], None]:
    """
    log 함수를 만듦: public 이면 줄마다 sanitize 를 거쳐 출력하고, 아니면 print 그대로
    [TH] สร้างฟังก์ชัน log: ถ้า public จะผ่าน sanitize ทีละบรรทัดก่อนพิมพ์ ถ้าไม่ใช่ใช้ print ตามเดิม
    [EN] Builds the log function: when public every line goes through sanitize first, otherwise it is plain print
    """
    if not public:
        return print

    def log(message: str) -> None:
        for line in str(message).split("\n"):
            safe = sanitize(line)
            if safe is not None:
                print(safe, flush=True)

    return log
