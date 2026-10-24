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


# DB 탭의 C/P 열 머리글 (DB_DWR 은 T열, DB_DRQ 는 S열)
# [TH] หัวตารางของคอลัมน์ C/P ในแท็บ DB (DB_DWR คอลัมน์ T, DB_DRQ คอลัมน์ S)
# [EN] The header of the C/P column of the DB tabs (column T in DB_DWR, column S in DB_DRQ)
DB_CP_HEADER = "Status (C/P)"
# DB 행이 옛 시트와 맞는지 알려 주는 표시 열의 머리글과 값 (C/P 열 바로 뒤: DB_DWR 은 U열, DB_DRQ 는 T열).
# "Synced" = 옛 시트에서 온 행이고 PDF, C/P, Folder Link 가 큐(= 옛 시트와 같은 내용)와 확인되어 맞음.
# "Outdated" = 옛 시트에서 온 행이지만 맞지 않음 (PDF 가 바뀌어 AI 가 새 파일로 교체하기를 기다리는 행, 또는 C/P / Folder Link 를 큐 안에서 정할 수 없는 행).
# 비어 있음 = 옛 시트에서 온 적이 없는 행 (다른 시스템이 넣은 문서 등), 이 도구가 쓴 행이 아닌 행, PDF 링크가 없는 행
# [TH] หัวตารางและค่าของคอลัมน์ป้ายที่บอกว่าแถวใน DB ตรงกับชีตเก่าหรือไม่ (ต่อจากคอลัมน์ C/P ทันที: DB_DWR คอลัมน์ U, DB_DRQ คอลัมน์ T)
#      "Synced" = แถวที่มาจากชีตเก่าและตรวจแล้วว่า PDF, C/P, Folder Link ตรงกับคิว (= เนื้อหาเดียวกับชีตเก่า)
#      "Outdated" = แถวที่มาจากชีตเก่าแต่ยังไม่ตรง (PDF เปลี่ยนและรอ AI อ่านไฟล์ใหม่มาแทนที่ หรือ C/P / Folder Link ในคิวขัดกันจนตัดสินไม่ได้)
#      ว่าง = แถวที่ไม่เคยมาจากชีตเก่า (เช่นเอกสารที่ระบบอื่นเพิ่ม), แถวที่ไม่ใช่แถวที่เครื่องมือนี้เขียน, แถวที่ไม่มีลิงก์ PDF
# [EN] The header and values of the tag column that says whether a DB row matches the legacy sheet (right after the C/P column: column U in DB_DWR, column T in DB_DRQ).
#      "Synced" = the row came from the legacy sheet and its PDF, C/P and Folder Link were checked against the queue (= the same content as the legacy sheet).
#      "Outdated" = the row came from the legacy sheet but does not match (its PDF changed and it waits for the AI to replace it with the new file,
#      or its C/P / Folder Link cannot be decided because the queue contradicts itself).
#      Empty = a row that never came from the legacy sheet (e.g. a document another system added), a row this tool did not write, a row without a PDF link
DB_SYNC_HEADER = "Legacy sync"
DB_SYNC_VALUE = "Synced"
DB_OUTDATED_VALUE = "Outdated"


@dataclass(frozen=True)
class QueueSpec:
    name: str
    queue_tab: str
    db_tab: str
    prompt: str
    # DB 의 각 열: ("const", 값) | ("field", 키 이름) | ("json", 키 이름) | ("pdf",) | ("folder",) | ("cp",) | ("sync",)
    # [TH] แต่ละคอลัมน์ของ DB: ("const", ค่า) | ("field", ชื่อคีย์) | ("json", ชื่อคีย์) | ("pdf",) | ("folder",) | ("cp",) | ("sync",)
    # [EN] Each DB column: ("const", value) | ("field", key name) | ("json", key name) | ("pdf",) | ("folder",) | ("cp",) | ("sync",)
    # ("sync",) = "Synced": migrate 가 쓰는 행은 방금 큐의 PDF 로 만든 것이므로 옛 시트와 맞음 / [TH] แถวที่ migrate เขียนสร้างจาก PDF ของคิวเมื่อครู่ จึงตรงกับชีตเก่า / [EN] a row migrate writes was just made from the PDF of the queue, so it matches the legacy sheet
    # ("cp",) = 큐 시트 F열(Status(C/P))의 값 / [TH] ค่าจากคอลัมน์ F (Status(C/P)) ของชีตคิว / [EN] the value of column F (Status(C/P)) of the queue sheet
    columns: tuple
    # DB 에서 PDF Link 열의 인덱스(0부터). 중복 확인에 사용
    # [TH] index (เริ่ม 0) ของคอลัมน์ PDF Link ใน DB ใช้เช็คข้อมูลซ้ำ
    # [EN] 0-based index of the PDF Link column in the DB; used for the duplicate check
    db_pdf_col: int
    # DB 에서 Folder Link 열의 인덱스(0부터): DB_DWR 은 S열(index 18), DB_DRQ 는 P열(index 15). 큐의 Folder URL(E열)과 맞춤
    # [TH] index (เริ่ม 0) ของคอลัมน์ Folder Link ใน DB: DB_DWR คือคอลัมน์ S (index 18), DB_DRQ คือคอลัมน์ P (index 15) ใช้เทียบกับ Folder URL (คอลัมน์ E) ของคิว
    # [EN] 0-based index of the Folder Link column in the DB: column S (index 18) in DB_DWR, column P (index 15) in DB_DRQ; matched with the queue's Folder URL (column E)
    db_folder_col: int
    # DB 에서 C/P 열의 인덱스(0부터). 기존 열 바로 뒤: DB_DWR 은 T열(index 19), DB_DRQ 는 S열(index 18)
    # [TH] index (เริ่ม 0) ของคอลัมน์ C/P ใน DB ต่อจากคอลัมน์เดิมทันที: DB_DWR คือคอลัมน์ T (index 19), DB_DRQ คือคอลัมน์ S (index 18)
    # [EN] 0-based index of the C/P column in the DB, right after the existing columns: column T (index 19) in DB_DWR, column S (index 18) in DB_DRQ
    db_cp_col: int
    # DB 에서 "Legacy sync" 표시 열의 인덱스(0부터). C/P 열 바로 뒤: DB_DWR 은 U열(index 20), DB_DRQ 는 T열(index 19)
    # [TH] index (เริ่ม 0) ของคอลัมน์ป้าย "Legacy sync" ใน DB ต่อจากคอลัมน์ C/P ทันที: DB_DWR คือคอลัมน์ U (index 20), DB_DRQ คือคอลัมน์ T (index 19)
    # [EN] 0-based index of the "Legacy sync" tag column in the DB, right after the C/P column: column U (index 20) in DB_DWR, column T (index 19) in DB_DRQ
    db_sync_col: int
    # AI JSON 에서 문서 번호가 들어 있는 키 (여러 보고서가 돌아왔을 때 큐의 Doc No 와 맞춰 고르는 데 사용)
    # [TH] คีย์ใน JSON ของ AI ที่เก็บเลขเอกสาร (ใช้จับคู่กับ Doc No ของคิวเมื่อ AI ตอบกลับมาหลายรายงาน)
    # [EN] Key in the AI JSON that holds the document number (used to match the queue's Doc No when the AI returns several reports)
    no_key: str = ""
    # `sync queue` 전용: 옛 시트 탭 이름을 담은 환경 변수 이름 / 스캔할 마지막 열(0부터, 이 값은 포함하지 않음)
    # [TH] เฉพาะ `sync queue`: ชื่อตัวแปรสภาพแวดล้อมที่เก็บชื่อแท็บชีตเก่า / คอลัมน์สุดท้ายที่สแกน (เริ่ม 0 ไม่รวมค่านี้)
    # [EN] `sync queue` only: name of the environment variable holding the legacy tab name / end column to scan (0-based, exclusive)
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
            ("field", "Linked_RITs"), ("const", "AI Migrated"), ("pdf",), ("folder",), ("cp",), ("sync",),
        ),
        db_pdf_col=17,
        db_folder_col=18,
        db_cp_col=19,
        db_sync_col=20,
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
            ("pdf",), ("folder",), ("const", ""), ("const", ""), ("cp",), ("sync",),
        ),
        db_pdf_col=14,
        db_folder_col=15,
        db_cp_col=18,
        db_sync_col=19,
        no_key="DRQ_No",
        # E열(index 4) ~ BF열(index 57)
        # [TH] คอลัมน์ E (index 4) ถึง BF (index 57)
        # [EN] Column E (index 4) to BF (index 57)
        legacy_env="PMIS_LEGACY_DRQ_SHEET",
        legacy_end_col=58,
    ),
}
