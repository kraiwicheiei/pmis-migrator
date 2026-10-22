"""
큐 정의 (프롬프트와 DB 열 순서는 Code_Migration.js 에서 그대로 복사)
[TH] นิยามคิว (prompt และลำดับคอลัมน์ DB คัดลอกจาก Code_Migration.js)
[EN] Queue definitions (prompts and DB column order copied from Code_Migration.js)
"""
from __future__ import annotations

from dataclasses import dataclass


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
    # --extract 전용: 옛 시트 탭 이름을 담은 환경 변수 이름 / 스캔할 마지막 열(0부터, 이 값은 포함하지 않음)
    # [TH] เฉพาะ --extract: ชื่อตัวแปรสภาพแวดล้อมที่เก็บชื่อแท็บชีตเก่า / คอลัมน์สุดท้ายที่สแกน (เริ่ม 0 ไม่รวมค่านี้)
    # [EN] --extract only: name of the environment variable holding the legacy tab name / end column to scan (0-based, exclusive)
    legacy_env: str = ""
    legacy_end_col: int = 0


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
        # E열(index 4) ~ BD열(index 55)
        # [TH] คอลัมน์ E (index 4) ถึง BD (index 55)
        # [EN] Column E (index 4) to BD (index 55)
        legacy_env="PMIS_LEGACY_DWR_SHEET",
        legacy_end_col=56,
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
        # E열(index 4) ~ BF열(index 57)
        # [TH] คอลัมน์ E (index 4) ถึง BF (index 57)
        # [EN] Column E (index 4) to BF (index 57)
        legacy_env="PMIS_LEGACY_DRQ_SHEET",
        legacy_end_col=58,
    ),
}
