#!/usr/bin/env python3
"""

pmis.py

PMIS 를 옛 시트와 맞춥니다: 옛 시트 -> 큐(MIGRATION_QUEUE / MIGRATION_QUEUE_DRQ) -> DB(DB_DWR / DB_DRQ, PDF 는 Gemini 가 읽음)
(Code_Migration.js 의 extractLegacyDwrLinks / extractLegacyDrqLinks 와 runDwrAiMigrationTurbo / runDrqAiMigrationTurbo 를 내 PC 에서 대체)
[TH] ทำให้ PMIS ตรงกับชีตเก่า: ชีตเก่า -> คิว (MIGRATION_QUEUE / MIGRATION_QUEUE_DRQ) -> DB (DB_DWR / DB_DRQ โดย Gemini อ่าน PDF)
     (แทน extractLegacyDwrLinks / extractLegacyDrqLinks และ runDwrAiMigrationTurbo / runDrqAiMigrationTurbo ใน Code_Migration.js บนเครื่องของคุณ)
[EN] Keeps PMIS in sync with the legacy sheets: legacy sheet -> queue (MIGRATION_QUEUE / MIGRATION_QUEUE_DRQ) -> DB (DB_DWR / DB_DRQ, the PDFs are read by Gemini)
     (replaces extractLegacyDwrLinks / extractLegacyDrqLinks and runDwrAiMigrationTurbo / runDrqAiMigrationTurbo in Code_Migration.js, on your own PC).

명령 / [TH] คำสั่ง / [EN] Commands:
  python pmis.py sync [queue] [db]   두 단계를 이 순서로 (단계를 쓰지 않으면 둘 다) / [TH] ทั้งสองขั้นตามลำดับนี้ (ถ้าไม่ระบุขั้นจะรันทั้งสอง) / [EN] the two steps in this order (none named = both)
      queue   옛 시트 -> 큐: 큐를 새로 계산하되 기존 행의 상태는 유지. 새 항목이나 바뀐 PDF 는 Waiting 이 되어 AI 가 읽음 (--overwrite 면 처음부터 다시)
              [TH] ชีตเก่า -> คิว: คำนวณคิวใหม่แต่คงสถานะของแถวเดิม รายการใหม่หรือ PDF ที่เปลี่ยนจะเป็น Waiting เพื่อให้ AI อ่าน (ถ้า --overwrite จะสร้างใหม่ทั้งหมด)
              [EN] legacy sheet -> queue: recompute the queue and keep the status of the existing rows. A new item or a changed PDF becomes Waiting, so that the AI reads it (--overwrite rebuilds it)
      db      큐 -> DB. 1부(AI 없음): C/P 와 Folder Link 갱신, "Legacy sync" 표시(Synced / Outdated), 옛 시트에서 사라진 행 삭제(보관 후).
              2부: 큐(G열 = Waiting) -> PDF 다운로드 -> Gemini(기존 프롬프트) -> DB_DWR / DB_DRQ -> G = Done (실패 시 G = "Error: <label> | <detail>")
              [TH] คิว -> DB ส่วน 1 (ไม่ใช้ AI): อัปเดต C/P และ Folder Link, ป้าย "Legacy sync" (Synced / Outdated), ลบแถวที่หายไปจากชีตเก่า (เก็บสำรองก่อน)
                   ส่วน 2: คิว (คอลัมน์ G = Waiting) -> ดาวน์โหลด PDF -> Gemini (prompt เดิม) -> DB_DWR / DB_DRQ -> ตั้ง G = Done (ถ้าพลาด ตั้ง G = "Error: <label> | <detail>")
              [EN] queue -> DB. Part 1 (no AI): update C/P and Folder Link, the "Legacy sync" tag (Synced / Outdated), remove the rows that left the legacy sheet (archived first).
                   Part 2: queue (column G = Waiting) -> download the PDF -> Gemini (original prompt) -> DB_DWR / DB_DRQ -> G = Done (on failure G = "Error: <label> | <detail>")
              --no-ai = 1부만 (Gemini 키 불필요) / --only-rows = 그 행의 2부만 / [TH] --no-ai = ส่วน 1 อย่างเดียว (ไม่ต้องมีคีย์ Gemini) / --only-rows = ส่วน 2 ของแถวนั้นอย่างเดียว
              [EN] --no-ai = part 1 only (no Gemini key needed) / --only-rows = part 2 only, for those rows
  python pmis.py check access        서비스 계정이 PDF 를 열 수 있는지 확인 (Gemini 호출 없음, 시트 쓰기 없음)
                                     [TH] เช็กว่า service account เปิดไฟล์ PDF ได้ไหม (ไม่เรียก Gemini ไม่เขียนชีต)
                                     [EN] checks that the service account can open the PDF files (no Gemini calls, no sheet writes)
  python pmis.py <command> --help    옵션 / [TH] ออปชัน / [EN] the options
  (옛 migrate_queue.py 는 옛 옵션 --extract / --sync-db / --check-access 를 새 명령으로 바꿔 실행함. --backfill-cp / `fix cp` 는 없어짐: `sync db --no-ai` 가 C/P 를 채우고 갱신함
   [TH] migrate_queue.py ตัวเก่ายังรับออปชันเก่า --extract / --sync-db / --check-access และแปลงเป็นคำสั่งใหม่ให้ ส่วน --backfill-cp / `fix cp` ถูกลบแล้ว: `sync db --no-ai` เติมและอัปเดต C/P ให้
   [EN] the old migrate_queue.py still accepts the old options --extract / --sync-db / --check-access and translates them into the new commands. --backfill-cp / `fix cp` are gone: `sync db --no-ai` fills and updates C/P)

db 단계의 AI 부분이 기존과 동일한 점 / [TH] ส่วน AI ของขั้น db ทำงานเหมือนของเดิม / [EN] The AI part of the db step is the same as the original:
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
  GEMINI_API_KEY                  Gemini 키 (AI 를 실제로 쓰는 실행 즉 `sync db` 에서 --no-ai / --dry-run 이 없을 때 필수)
                                  [TH] คีย์ Gemini (จำเป็นเมื่อรัน AI จริง คือ `sync db` ที่ไม่มี --no-ai / --dry-run)
                                  [EN] Gemini key (required for a run that really uses the AI: `sync db` without --no-ai / --dry-run)
  GOOGLE_APPLICATION_CREDENTIALS  서비스 계정 키 파일 경로 (또는 --sa-key 사용)
                                  [TH] path ของ service account key (หรือใช้ --sa-key)
                                  [EN] path to the service account key file (or use --sa-key)
  GEMINI_MODEL                    (선택) 모델 지정 (미설정 시 코드의 DEFAULT_MODEL 사용, --model 이 최우선)
                                  [TH] (ไม่บังคับ) ระบุโมเดล (ถ้าไม่ตั้งจะใช้ DEFAULT_MODEL ในโค้ด; --model สำคัญที่สุด)
                                  [EN] (optional) model name (if unset, DEFAULT_MODEL in the code is used; --model wins over both)
  PMIS_SPREADSHEET_ID             대상 스프레드시트 ID (필수, 항상. 코드에 기본값이 없고 명령줄 옵션도 없음)
                                  [TH] ID ของสเปรดชีตเป้าหมาย (จำเป็นเสมอ ไม่มีค่าเริ่มต้นในโค้ดและไม่มีออปชันบรรทัดคำสั่ง)
                                  [EN] target spreadsheet ID (always required; no default in the code and no command-line option)
  PMIS_LEGACY_DWR_SHEET           queue 단계 전용: DWR 큐를 만들 옛 시트 탭 이름 (필수, 코드에 기본값 없음)
                                  [TH] เฉพาะขั้น queue: ชื่อแท็บชีตเก่าที่ใช้สร้างคิว DWR (จำเป็น ไม่มีค่าเริ่มต้นในโค้ด)
                                  [EN] queue step only: name of the legacy sheet tab the DWR queue is built from (required; no default in the code)
  PMIS_LEGACY_DRQ_SHEET           queue 단계 전용: DRQ 큐를 만들 옛 시트 탭 이름 (필수, 코드에 기본값 없음)
                                  [TH] เฉพาะขั้น queue: ชื่อแท็บชีตเก่าที่ใช้สร้างคิว DRQ (จำเป็น ไม่มีค่าเริ่มต้นในโค้ด)
                                  [EN] queue step only: name of the legacy sheet tab the DRQ queue is built from (required; no default in the code)
  PMIS_LEGACY_SPREADSHEET_ID      queue 단계 전용: 옛 시트 탭이 들어 있는 스프레드시트 (ID 또는 URL). 비우면 PMIS_SPREADSHEET_ID 와 같은 파일. --legacy-spreadsheet 가 최우선
                                  [TH] เฉพาะขั้น queue: สเปรดชีต (ID หรือ URL) ที่มีแท็บชีตเก่า ถ้าว่างจะใช้ไฟล์เดียวกับ PMIS_SPREADSHEET_ID; --legacy-spreadsheet สำคัญที่สุด
                                  [EN] queue step only: the spreadsheet (ID or URL) that holds the legacy tabs; empty = the same file as PMIS_SPREADSHEET_ID; --legacy-spreadsheet wins

위 변수는 스크립트 옆의 .env 파일(KEY=value)에 적어 둘 수 있음 (.env.example 참고). 이미 설정된 환경 변수가 우선
[TH] ตัวแปรข้างบนเขียนไว้ในไฟล์ .env ข้างสคริปต์ได้ (KEY=value ดู .env.example) ตัวแปรที่ตั้งไว้ในสภาพแวดล้อมแล้วมีสิทธิ์เหนือกว่า
[EN] The variables above can be put in a .env file next to the script (KEY=value, see .env.example); variables already set in the environment win

코드 구조 / [TH] โครงสร้างโค้ด / [EN] Code layout:
  이 파일은 진입점일 뿐이며 코드는 migrator/ 패키지에 있음 (`python -m migrator` 도 같음. 옛 migrate_queue.py 는 옛 옵션을 번역하는 얇은 진입점)
  [TH] ไฟล์นี้เป็นแค่จุดเริ่มต้น โค้ดอยู่ในแพ็กเกจ migrator/ (`python -m migrator` ก็เหมือนกัน migrate_queue.py ตัวเก่าเป็นจุดเริ่มต้นบาง ๆ ที่แปลออปชันเก่า)
  [EN] This file is only the entry point; the code lives in the migrator/ package (`python -m migrator` is the same; the old migrate_queue.py is a thin entry point that translates the old options):
    cli.py          the commands and options (sync / check), building the clients, main()
    pipeline.py     `sync`: the steps queue -> db in order; the db step = part 1 (update the DB, no AI) then part 2 (the AI)
    public_log.py   --public-log: only the lines that are safe in a public Actions log
    settings.py     settings read from the environment / .env
    runner.py       the AI part of the db step: choosing the rows and the main loop (--dry-run / check access / a real run)
    processing.py   one row: Drive PDF -> Gemini -> DB row
    sheets.py / drive.py / gemini.py   the three APIs
    reports.py      parsing Gemini's JSON answers, building the DB rows
    extract.py      the queue step (legacy_sheet.py reads the legacy sheet, queue_writer.py writes / syncs the queue)
    db_sync.py      part 1 of the db step (C/P, Folder Link, the Legacy sync tag, remove the rows that left the queue) and replacing a DB row in place
    db_columns.py   the C/P and Legacy sync columns of the DB tabs: header checks, writing one column
    stamps.py       the refresh time (I1:I2) and the migration time (K1:K2) written to the queue sheet
    specs.py / config.py / models.py / utils.py / clock.py   queue definitions, constants, data structures, helpers, time
  테스트는 tests/ / [TH] เทสต์อยู่ใน tests/ / [EN] Tests are in tests/:  python -m pytest -q
"""
from migrator.cli import entry

if __name__ == "__main__":
    entry()
