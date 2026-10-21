# migrator - ประมวลผลคิว MIGRATION_QUEUE ด้วย Python

**ภาษา / Language / 언어:** [English](README.md) · [ไทย](README.th.md) · [한국어](README.ko.md)

แทน `runDwrAiMigrationTurbo` / `runDrqAiMigrationTurbo` ใน `Code_Migration.js` โดยใช้ prompt, โมเดล (`gemini-3.8-flash`)
และลำดับคอลัมน์ DB_DWR / DB_DRQ ชุดเดิมทุกอย่าง แต่รันบนเครื่องคุณ จึงไม่ติดโควตา UrlFetch ของ Apps Script
ทำตามโค้ด Apps Script ฉบับปัจจุบัน (`Code_Migration.js` และ `extractDriveFileForAI` ใน `Code_Core.js` สำหรับ PDF ขนาดใหญ่)

## ภาพรวม: ใครทำอะไร

| ขั้นตอน | ทำที่ไหน |
|---|---|
| สร้างคิว `MIGRATION_QUEUE` / `MIGRATION_QUEUE_DRQ` จากชีตเก่า `<legacy DWR sheet>` / `<legacy DRQ sheet>` | **Apps Script** (`extractLegacyDwrLinks()` / `extractLegacyDrqLinks()`) - สคริปต์ Python **ไม่สร้างคิว** |
| โหลด PDF -> Gemini -> เขียน DB_DWR / DB_DRQ -> ตั้ง `Done` | **สคริปต์ Python นี้** |

> **การรัน `extractLegacyDwrLinks()` / `extractLegacyDrqLinks()` ซ้ำ:** ใน `Code_Migration.js` ปัจจุบัน สองฟังก์ชันนี้สั่ง `clear()` แล้วสร้างคิวใหม่ทั้งหมดเป็น `Waiting` **ทุกรายการ** - รวมถึง PDF ที่อยู่ใน DB_DWR / DB_DRQ แล้ว - และล้างสถานะ `Done` / `Error…` เดิมทั้งหมด
> สคริปต์ Python รับมือเรื่องนี้ได้: แถว `Waiting` ที่ PDF อยู่ใน DB แล้ว (เทียบด้วย Drive file ID ในคอลัมน์ PDF Link) จะถูกตั้งเป็น `Done` โดยไม่เรียก AI ควรสำรองคอลัมน์ G (และ DB) ไว้ก่อนรันสองฟังก์ชันนี้ซ้ำเสมอ

## ติดตั้ง (ครั้งเดียว) - PowerShell

```powershell
cd C:\path\to\pmis-migrator
python -m pip install -r requirements.txt
```

`python -m pip` จะลงแพ็กเกจให้ Python ตัวเดียวกับที่รันสคริปต์ จึงเลี่ยง error `No module named 'requests'` ได้
ถ้าอยากใช้ virtual environment: `python -m venv .venv` แล้ว `.\.venv\Scripts\Activate.ps1` (ต้อง activate **ทุกครั้งที่เปิดหน้าต่างใหม่**)
ถ้าเจอ "running scripts is disabled on this system" ให้รัน `Set-ExecutionPolicy -Scope Process Bypass` ก่อน

## ก่อนรัน

1. **ปิด trigger** ของ `runDwrAiMigrationTurbo` / `runDrqAiMigrationTurbo` ใน Apps Script (กันสองฝั่งแย่งกันเขียน) Apps Script ปัจจุบันเขียนคอลัมน์สถานะ G ทั้งคอลัมน์กลับจากข้อมูลที่อ่านไว้ตอนเริ่มรัน ถ้ารันซ้อนกับ Python อาจทับสถานะที่ Python เพิ่งเขียน
2. แชร์สเปรดชีต "<your-spreadsheet-name>" ให้อีเมล service account เป็น **Editor**
3. แชร์ไฟล์ PDF (หรือโฟลเดอร์ที่เก็บ PDF ทั้งหมด) ให้อีเมลเดียวกันเป็น **Viewer**
4. ตั้งตัวแปรสภาพแวดล้อม - เก็บ `sa-key.json` ไว้นอกโฟลเดอร์ repo เช่น `C:\path\to\secrets\`

```powershell
$env:GOOGLE_APPLICATION_CREDENTIALS = "C:\path\to\secrets\sa-key.json"
$env:GEMINI_API_KEY = "ใส่คีย์ที่นี่"
$env:PMIS_SPREADSHEET_ID = "ใส่ ID ของชีต"   # จำเป็น (ส่วนใน URL ของชีตระหว่าง /d/ กับ /edit)
```

ค่าที่ตั้งด้วย `$env:` อยู่แค่ในหน้าต่าง PowerShell นั้น ต้องตั้งในหน้าต่างเดียวกับที่รัน `python migrate_queue.py` และตั้งใหม่ทุกครั้งที่เปิดหน้าต่างใหม่
ถ้าอยากตั้งถาวรใช้ `setx GEMINI_API_KEY "..."` (แล้วเปิดหน้าต่างใหม่) - ค่านี้เก็บใน registry แบบไม่เข้ารหัส
`--dry-run` และ `--check-access` ไม่ใช้ `GEMINI_API_KEY` จึงไม่ต้องตั้งก่อนสองโหมดนี้

## ลำดับที่แนะนำ

```powershell
python migrate_queue.py --dry-run                       # ดูว่าจะทำแถวไหนบ้าง ทั้งสองคิว (ไม่เรียก API/ไม่เขียนชีต)
python migrate_queue.py --check-access --limit 0        # เช็คว่า service account เปิด PDF ได้ทุกไฟล์ไหม ทั้งสองคิว
python migrate_queue.py --limit 5                       # ทดสอบจริง คิวละ 5 แถว แล้วเทียบกับแถวที่ Done จากของเดิม
python migrate_queue.py --queue dwr --only-rows 83,102,127-128   # ลองแถวที่เคย error เดี่ยวๆ (ต้องระบุ --queue)
python migrate_queue.py --limit 200                     # รันเป็นรอบ ตรวจผลแล้วค่อยเพิ่ม (0 = ทั้งหมด)
python migrate_queue.py --queue drq --limit 5           # เฉพาะคิว DRQ (MIGRATION_QUEUE_DRQ -> DB_DRQ)
```

**เลือกคิว:** ถ้าไม่ใส่ `--queue` สคริปต์จะทำ **ทั้งสองคิว** โดยทำ DWR ก่อน (`MIGRATION_QUEUE` -> `DB_DWR`) แล้วตามด้วย DRQ (`MIGRATION_QUEUE_DRQ` -> `DB_DRQ`); `--limit` นับ **ต่อคิว** ถ้าอยากทำคิวเดียวใช้ `--queue dwr` หรือ `--queue drq`
`--only-rows` อ้างเลขแถวของชีตคิวใดคิวหนึ่ง จึงต้องระบุ `--queue` คู่กันเสมอ
ถ้าคิวแรกหยุดกลางคัน (เช่น Gemini ตอบ 402/401/403 หรือติด 429 ต่อเนื่อง) จะข้ามคิวที่สองเพราะจะหยุดด้วยสาเหตุเดียวกัน แต่ถ้าอ่านชีตคิวใดคิวหนึ่งไม่ได้ (เช่นไม่มีแท็บนั้น) จะยังทำอีกคิวต่อ ทั้งสองกรณีรหัสออกของโปรแกรมเป็น 2

ชีตเป้าหมายกำหนด**ผ่านตัวแปรสภาพแวดล้อม `PMIS_SPREADSHEET_ID` เท่านั้น** ไม่มีค่าเริ่มต้นในโค้ดและไม่มีออปชันบรรทัดคำสั่ง ถ้าไม่ตั้งหรือเป็นค่าว่างสคริปต์จะหยุดพร้อมข้อความแนะนำ (ทุกโหมด รวมถึง `--dry-run`) ID คือส่วนใน URL ของชีตระหว่าง `/d/` กับ `/edit`: `https://docs.google.com/spreadsheets/d/<ID>/edit`

## `--check-access` เช็คอะไร

สำหรับแต่ละแถวที่รอทำ จะดึง file ID จากคอลัมน์ PDF URL แล้วขอ metadata ของไฟล์จาก Drive (ชื่อ, ชนิด, ขนาด, อยู่ในถังขยะหรือไม่) ด้วยสิทธิ์ของ service account
รายงานแถวที่ `No URL`, `Drive Access` (404/403: ไม่มีไฟล์ หรือยังไม่ได้แชร์ให้ service account) หรือไฟล์อยู่ในถังขยะ

ค่าเริ่มต้นตรวจทั้งสองคิวต่อกัน (ใส่ `--queue dwr` หรือ `--queue drq` ถ้าอยากตรวจแค่คิวเดียว)
ทุกบรรทัดของผลลัพธ์ขึ้นต้นด้วยชื่อชีตคิว เช่น `[MIGRATION_QUEUE] Reachable: 2/3 files` หรือ `✗ [MIGRATION_QUEUE_DRQ] row 4: Drive Access | …` และถ้าตรวจคิวเดียว บรรทัดสุดท้ายจะบอกคำสั่งสำหรับตรวจคิวอีกอัน

**ไม่** ดาวน์โหลด PDF, **ไม่** เรียก Gemini (ไม่เสียเงิน), **ไม่** เขียนชีต, ไม่เช็กว่าเป็น PDF จริง และไม่ทดสอบสิทธิ์เขียนชีต
แถวที่ PDF ใหญ่กว่า `--max-mb` จะแสดงบรรทัด `ℹ` ว่าจะส่งเฉพาะภาพหน้าปก (ดู "พฤติกรรมที่ควรรู้")
ค่าเริ่มต้น `--limit` คือ 30 จึงต้องใส่ `--limit 0` ถ้าอยากเช็กทุกแถว

## รันบน GitHub Actions

`.github/workflows/migrate.yml` รันสคริปต์ตัวเดียวกันบน runner ของ GitHub จึงไม่ต้องเปิดคอมค้างไว้ repo แบบ private บนแพ็กเกจ GitHub Free ได้ Actions 2,000 นาทีต่อเดือน

ตั้งค่าครั้งเดียว:

1. สร้าง repo แล้ว push โฟลเดอร์นี้ขึ้นไป แนะนำแบบ **private**: workflow แสดงแค่ตัวเลขสรุป แต่ถ้า repo เป็น public ใครก็เปิดดู log และสรุปผลการรันได้
2. เพิ่ม repository secrets 3 ตัว (Settings > Secrets and variables > Actions) หรือใช้ GitHub CLI ใน PowerShell:

```powershell
Get-Content C:\path\to\secrets\sa-key.json -Raw | gh secret set SA_KEY_JSON   # เนื้อหาไฟล์คีย์ทั้งไฟล์
gh secret set GEMINI_API_KEY                                                    # จะถามค่า
gh secret set PMIS_SPREADSHEET_ID                                               # จะถามค่า
```

3. เตรียมเหมือนรันในเครื่อง: ปิด trigger ของ Apps Script และแชร์ชีตกับ PDF ให้อีเมล service account

วิธีรัน: แท็บ Actions > **Migrate queue** > Run workflow แล้วเลือก `mode` เริ่มจาก `dry-run` ตามด้วย `check-access` แล้ว `run` ด้วย `limit` น้อยๆ ก่อนค่อยเพิ่ม ถ้าใช้บรรทัดคำสั่ง: `gh workflow run migrate.yml -f mode=dry-run`

- unit test (job `test`) จะรันก่อน ผ่านแล้วการ migrate ถึงจะเริ่ม
- ไม่มีสองรอบรันซ้อนกัน (`concurrency`) จึงไม่ทำแถว `Waiting` เดียวกันซ้ำ
- คีย์ service account ถูกเขียนเป็นไฟล์ชั่วคราวเฉพาะช่วงที่รัน
- ผลลัพธ์เต็ม (fileId, ชื่อชีต, error รายแถวที่มีข้อความจาก PDF) เขียนลงไฟล์ชั่วคราวบน runner เท่านั้น ส่วน log และสรุปผลการรันแสดงแค่ตัวเลข (สถานะคิว, ยอด Done/Error, สาเหตุที่หยุด) โดยซ่อน ID สเปรดชีตและอีเมล service account รายละเอียดรายแถวดูได้ที่คอลัมน์ G ของชีตคิว ถ้าต้องการ log เต็มให้รันในเครื่อง
- มีโหมดรันอัตโนมัติแต่ปิดไว้: ลบ `#` หน้าบรรทัด `schedule:` ใน workflow (รอบตั้งเวลาใช้ `mode = run`, ทั้งสองคิว, `limit = 30`)

## พฤติกรรมที่ควรรู้

- เลือกแถวที่คอลัมน์ G เป็น `Waiting` **และ `Error…`** (ลองใหม่ให้เอง) ข้ามแถว `Done`; ใช้ `--no-retry-errors` ถ้าอยากทำเฉพาะ `Waiting`
- รันเป็นก้อน (`--batch 30`): เขียน DB ก้อนเดียว แล้วค่อยตั้ง `Done` จึงไม่มีแถว `Done` ที่ข้อมูลไม่เข้า DB
- เจอ **402 / 401 / 403** จาก Gemini (เครดิตหมด/คีย์ผิด) = หยุดทั้งรอบทันที แถวที่เหลือยังเป็นสถานะเดิม; ติด **429 ต่อเนื่อง** ก็หยุดเช่นกัน
- 429 / 5xx / เครือข่ายหลุด = retry อัตโนมัติ (backoff สูงสุด 5 ครั้ง) ถ้ายังไม่ผ่านจึงเขียน `Error: API <code> | …`
- ถ้า PDF นั้นอยู่ใน DB แล้ว (เทียบ file ID ในคอลัมน์ PDF Link) จะตั้ง `Done` ให้เลยโดยไม่เรียก AI - รันซ้ำหลังโดนตัดกลางทางได้โดยไม่เกิดแถวซ้ำ
- PDF ใหญ่กว่า `--max-mb` (ค่าเริ่มต้น **15 MB** เท่าเกณฑ์ของ `extractDriveFileForAI` ใน Apps Script) จะไม่ส่งทั้งไฟล์ แต่ทำเหมือน Apps Script คือส่ง **ภาพหน้าแรก (หน้าปก)** จาก thumbnail ของ Drive (`sz=w2500`) แทน จึงวิเคราะห์ได้แค่หน้าแรก แถวพวกนี้ยังเป็น `Done` แต่ระหว่างรันจะแสดงบรรทัด `ℹ` ทีละแถว (และสรุปจำนวนตอนท้าย) ให้ตรวจสอบ ถ้าอยากได้ `Error: Too Large` แทนให้ใช้ `--no-thumbnail` หรือปรับเพดานด้วย `--max-mb` ภาพจะถูกส่งด้วย MIME ที่ถูกต้อง (Apps Script ติดป้ายเป็น `application/pdf`)
- ไฟล์ที่ไม่ใช่ PDF และใหญ่เกินเพดานยังได้ `Error: Too Large` และถ้า Drive ปฏิเสธคำขอ thumbnail แถวนั้นจะได้ `Error: Drive Access | … cover capture failed`
- แถวที่พลาดจะบันทึกสาเหตุเป็น `Error: <label> | <detail>` ส่วน Apps Script ปัจจุบันเขียนแค่ `Error: <label>` และปล่อยแถวที่ไม่มี Drive file ID ไว้เป็น `Waiting`; สคริปต์ Python จะรายงานแถวแบบนั้นเป็น `Error: No URL` ข้อความทุกอย่างที่สคริปต์เขียนลงเซลล์ Error เหล่านี้ (label, คำแนะนำ, ข้อความ) เป็นภาษาอังกฤษ รวมถึง log บนคอนโซลด้วย
- **`Error: JSON Parse`** แปลว่า Gemini ตอบกลับมาแล้วแต่คำตอบไม่ใช่ JSON ที่ถูกต้อง สาเหตุที่พบบ่อย: คำตอบ**ถูกตัดกลางคัน** (`finishReason=MAX_TOKENS` เจอบ่อยกับแบบที่มีรายการแรงงาน/เครื่องจักรยาวมาก), มีเครื่องหมายคำพูดหรือการขึ้นบรรทัดใหม่ในค่าข้อความโดยไม่ได้ escape, หรือมีข้อความอธิบายครอบ JSON ตอนนี้สคริปต์ (1) อ่านคำตอบแบบผ่อนปรนขึ้น (ยอมให้มีขึ้นบรรทัดใหม่/แท็บในสตริง และข้อความนำหน้า/ตามหลัง JSON), (2) ถ้ายังพังจะเรียก Gemini ซ้ำ (`--parse-retries` ค่าเริ่มต้น **1** ผลลัพธ์ของโมเดลต่างกันเล็กน้อยทุกครั้ง การลองใหม่จึงมักช่วยได้ ใส่ `0` เพื่อปิด) และแสดงบรรทัด `ℹ` เมื่อแถวที่รอดเพราะลองใหม่ และ (3) เขียนเหตุผลลงเซลล์ error: ข้อความจาก parser พร้อมตำแหน่ง, `finishReason`, จำนวนครั้งที่ลองใหม่ และ**ส่วนหัวกับส่วนท้าย**ของคำตอบ AI (คำตอบที่ถูกตัดจะจบกลางประโยค) ถ้าในเซลล์เห็น `finishReason=MAX_TOKENS` ให้รันแถวนั้นใหม่ด้วย `--max-output-tokens 16000` (ค่าเริ่มต้นไม่กำหนด เหมือน Apps Script) รันเฉพาะแถวนั้น: `python migrate_queue.py --queue dwr --only-rows 231` (ใช้เลขแถวในชีต ไม่ใช่เลขที่เอกสาร)
- **PDF ไฟล์เดียวมีหลายรายงาน** (`JSON Parse | ValueError: expected a JSON object, got list` อย่างที่เจอกับ PDF ที่มีหลายรายงาน): prompt ขอ object เดียว แต่ถ้า PDF มีมากกว่าหนึ่งรายงาน Gemini จะตอบเป็นอาร์เรย์ `[ {…}, {…} ]` ซึ่ง Apps Script อ่านอาร์เรย์ไม่ได้และจะเขียนแถว `Done` ที่ช่องว่างหมดลง DB สคริปต์นี้จึงเลือกรายงานที่ `DWR_No` / `DRQ_No` ตรงกับ **Doc No** ของแถวในคิว (คอลัมน์ A ไม่สนตัวพิมพ์เล็ก/ใหญ่ `-`/ช่องว่าง และเลข 0 นำหน้า เช่น `…-231` ตรงกับ `…-0231`) แถวนั้นเป็น `Done` แต่จะแสดงบรรทัด `⚠` และสรุปตอนท้าย เพราะรายงานอื่นใน PDF ไม่ได้ถูกย้าย ต้องตรวจสอบ ถ้าไม่มีรายงานไหนตรง สคริปต์จะถามซ้ำหนึ่งครั้งพร้อมคำสั่งเพิ่ม ("ตอบ object เดียวสำหรับเอกสารเลขที่ …") ถ้ายังไม่ได้ เซลล์จะแสดง `MultiReportError` พร้อมเลขที่และวันที่ของทุกรายงานที่เจอ (`#1 DWR_No=… Date=…`) ให้ดูว่า Doc No ในคอลัมน์ A ผิดหรือ PDF เป็นเอกสารอื่น `--accept-first-report` จะใช้รายงานแรกแทนการขึ้น error ในกรณีนี้ (ใช้หลังตรวจสอบแล้วเท่านั้น)
- โมเดลเริ่มต้นคือ `gemini-3.8-flash` ตัวเดียวกับ `CONFIG_GEMINI_MODEL` ใน `Code_Config.js` สองฝั่งเป็นอิสระต่อกัน (สคริปต์ไม่อ่านไฟล์ของ Apps Script) ถ้าแก้ฝั่งหนึ่งให้แก้อีกฝั่งด้วย และเปลี่ยนโมเดลของสคริปต์นี้ได้ด้วย `--model` หรือตัวแปร `GEMINI_MODEL`
- บรรทัดแรกของผลลัพธ์แสดงชื่อและ ID ของชีต (`sheet: <spreadsheet name> (<id>)`) และหัวของแต่ละคิวก็แสดงชื่อชีตซ้ำ (`===== Queue DWR: MIGRATION_QUEUE -> DB_DWR | sheet: <spreadsheet name> =====`) ถ้าอ่านชื่อไม่ได้จะแสดงสาเหตุแทน ถ้าตัวเลขดูผิดปกติให้ดูตรงนี้ก่อน เพราะ ID มาจากตัวแปรสภาพแวดล้อม `PMIS_SPREADSHEET_ID` เท่านั้น
- ถ้าคิวไหนได้ **0 แถว** สคริปต์จะบอกสาเหตุ: แท็บไม่มีแถวข้อมูล (มักเป็นเพราะอ่านผิดสเปรดชีต) หรือคอลัมน์ G มีค่าอื่นที่ไม่ใช่ `Waiting` / `Error…` (แสดงค่าที่พบจริง เช่น `'Done'×120` และตัวพิมพ์ต้องตรง) หรือคอลัมน์ G ว่างแต่มี URL (ใช้ `--include-blank`)
- ใช้ `values.append` เขียน DB จึงขยายแถวให้เอง (ตาราง DB_DWR/DB_DRQ ตอนนี้มีแค่ 1,000 แถว)
- จบรอบจะสรุปจำนวน token และค่าใช้จ่ายโดยประมาณ (ราคา list price)
- คอมเมนต์ในโค้ดมี 3 ภาษา (เกาหลี / `[TH]` / `[EN]`) ส่วนข้อความที่แสดงตอนรัน (log, `--help`) และข้อความที่เขียนลงเซลล์ Error ในชีตเป็นภาษาอังกฤษ

## เทสต์

```powershell
python -m pip install pytest
python -m pytest -q
```
