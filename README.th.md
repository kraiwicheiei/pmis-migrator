# pmis-migrator - ทำให้ PMIS sync กับชีตเก่า

**ภาษา / Language / 언어:** [English](README.md) · [ไทย](README.th.md) · [한국어](README.ko.md)

แทน `runDwrAiMigrationTurbo` / `runDrqAiMigrationTurbo` ใน `Code_Migration.js` โดยใช้ prompt, โมเดล (`gemini-3.8-flash`)
และลำดับคอลัมน์ DB_DWR / DB_DRQ ชุดเดิมทุกอย่าง แต่รันบนเครื่องคุณ จึงไม่ติดโควตา UrlFetch ของ Apps Script
ทำตามโค้ด Apps Script ฉบับปัจจุบัน (`Code_Migration.js` และ `extractDriveFileForAI` ใน `Code_Core.js` สำหรับ PDF ขนาดใหญ่)
และยังแทน `extractLegacyDwrLinks` / `extractLegacyDrqLinks` ที่สร้างคิว (ดู `sync queue`)

คำสั่งเดียวทำให้ PMIS sync กับชีตเก่า: **`python pmis.py sync`** = ชีตเก่า -> คิว -> `DB_DWR` / `DB_DRQ` มีสองขั้น และแยกรันได้ด้วย (`sync queue`, `sync db`)

## ภาพรวม: ใครทำอะไร

| ขั้น | คำสั่ง | ทำอะไร |
|---|---|---|
| `queue` | `python pmis.py sync queue` | ทำให้ `MIGRATION_QUEUE` / `MIGRATION_QUEUE_DRQ` ตรงกับชีตเก่า `<legacy DWR sheet>` / `<legacy DRQ sheet>` **รายการใหม่หรือ PDF ที่เปลี่ยนจะกลายเป็นแถว `Waiting`** เพื่อให้ AI อ่าน (แทน `extractLegacyDwrLinks()` / `extractLegacyDrqLinks()` ของ Apps Script) |
| `db` | `python pmis.py sync db` | ทำให้ `DB_DWR` / `DB_DRQ` ตามคิว **ส่วน 1 (ไม่ใช้ AI):** C/P, Folder Link, ป้าย `Legacy sync` (`Synced` / `Outdated`) และลบแถวที่หายไปจากชีตเก่า **ส่วน 2 (AI):** โหลด PDF ของทุกแถว `Waiting` -> Gemini -> เขียนแถว DB -> ตั้ง `Done` (แทน `runDwrAiMigrationTurbo` / `runDrqAiMigrationTurbo`) |

`python pmis.py sync` รันสองขั้นตามลำดับนี้ ทุกอย่างทำที่ **สคริปต์ Python นี้** ไม่เหลืองานให้ Apps Script แต่ละขั้นเขียนเฉพาะชีตของตัวเอง: `queue` เขียนแท็บคิว ส่วน `db` เขียนแท็บ DB (และคอลัมน์สถานะของคิวเมื่อ AI ทำแถวนั้นเสร็จ)

> **การสร้างคิวใหม่:** ฟังก์ชัน `extractLegacyDwrLinks()` / `extractLegacyDrqLinks()` ของ Apps Script สั่ง `clear()` แล้วสร้างคิวใหม่ทั้งหมดเป็น `Waiting` **ทุกรายการ** - รวมถึง PDF ที่อยู่ใน DB_DWR / DB_DRQ แล้ว - และล้างสถานะ `Done` / `Error…` เดิมทั้งหมด `sync queue` ทำแบบนั้นเฉพาะเมื่อใส่ `--overwrite` ถ้าไม่ใส่ จะ **sync** คิวที่มีแถวอยู่แล้ว: คำนวณทุกแถวใหม่จากชีตเก่าและคงสถานะของแถวเดิมไว้ (ดู "ขั้น queue")
> หลัง `--overwrite` ส่วน AI ของ `sync db` รับมือเรื่องนี้ได้: แถว `Waiting` ที่ PDF อยู่ใน DB แล้ว (เทียบด้วย Drive file ID ในคอลัมน์ PDF Link) จะถูกตั้งเป็น `Done` โดยไม่เรียก AI ควรสำรองคอลัมน์ G (และ DB) ไว้ก่อนใช้ `--overwrite` เสมอ

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

ค่าที่ตั้งด้วย `$env:` อยู่แค่ในหน้าต่าง PowerShell นั้น ต้องตั้งในหน้าต่างเดียวกับที่รัน `python pmis.py` และตั้งใหม่ทุกครั้งที่เปิดหน้าต่างใหม่
ถ้าอยากตั้งถาวรใช้ `setx GEMINI_API_KEY "..."` (แล้วเปิดหน้าต่างใหม่) - ค่านี้เก็บใน registry แบบไม่เข้ารหัส
`--dry-run`, `check access`, `sync queue` และ `sync db --no-ai` ไม่ใช้ `GEMINI_API_KEY` จึงไม่ต้องตั้งก่อนคำสั่งเหล่านี้ มีแค่ส่วน AI ของ `sync db` / `sync` ที่ใช้

## คำสั่ง

```text
python pmis.py sync [queue] [db]   ทำให้ PMIS sync กับชีตเก่า ระบุขั้นที่ต้องการ ถ้าไม่ระบุ = ทั้งสองขั้น
python pmis.py sync db --no-ai     ขั้น db แบบไม่ใช้ AI: แก้แถวใน DB อย่างเดียว (ไม่ต้องมีคีย์ Gemini)
python pmis.py check access        service account เปิดไฟล์ PDF ได้ไหม (ไม่เขียนอะไร ไม่เรียก Gemini)
python pmis.py <command> --help    ออปชันของคำสั่งนั้น
```

`sync` คือสายงานสองขั้น **ขั้นต่างๆ รันตามลำดับ queue, db เสมอ ไม่ว่าจะพิมพ์ลำดับไหน** (`sync db queue` = `sync queue db`):

| ขั้น | ทำอะไร | ต้องใช้ |
|---|---|---|
| `queue` | ชีตเก่า -> คิว: คำนวณทุกแถวของ `MIGRATION_QUEUE` / `MIGRATION_QUEUE_DRQ` ใหม่จากชีตเก่า โดยคงสถานะของแถวที่มีอยู่แล้ว รายการใหม่หรือ PDF ที่เปลี่ยนจะเป็น `Waiting` | `PMIS_LEGACY_DWR_SHEET` / `PMIS_LEGACY_DRQ_SHEET` ไม่ต้องใช้คีย์ Gemini |
| `db` | คิว -> DB แบ่งเป็นสองส่วน (ด้านล่าง) | ส่วน 1: ไม่ต้องใช้อะไรพิเศษ ส่วน 2: `GEMINI_API_KEY` (ยกเว้น `--dry-run`) |

สองส่วนของขั้น `db`:

| ส่วน | ทำอะไร | ข้ามเมื่อ |
|---|---|---|
| 1 - แก้แถวใน DB (ไม่ใช้ AI) | C/P และ Folder Link ของแถวที่มีอยู่ตามคิว, ติดป้าย `Legacy sync` (`Synced` / `Outdated`), ลบแถวที่มาจากชีตเก่าแล้วหายไป (คัดลอกไป `_REMOVED_...` ก่อน) | `--only-rows` |
| 2 - AI | อ่าน PDF ของแถว `Waiting` (และ `Error…`) ด้วย Gemini แล้วเขียนลง `DB_DWR` / `DB_DRQ` แถวที่ PDF เปลี่ยนจะแทนที่แถวเก่าใน DB ในที่เดิม | `--no-ai` |

ส่วน 1 ทำก่อน แม้ Gemini หยุด (เครดิตหมด คีย์ผิด) หรือไม่มีคีย์ งานของส่วน 1 ก็เสร็จไปแล้ว `--no-ai` ทำแค่ส่วน 1 (ไม่ต้องมีคีย์ Gemini) ส่วน `--only-rows` คือลองแถวนั้นกับ AI ใหม่ และไม่ทำอย่างอื่น

ใส่ชื่อขั้นไว้ตามหลัง `sync` ทันที แล้วจึงตามด้วยออปชัน ออปชันของแต่ละคำสั่ง (`python pmis.py sync --help` แยกให้ตามขั้น):

| ออปชัน | ใช้กับ |
|---|---|
| `--queue dwr` / `--queue drq` (ค่าเริ่มต้น: ทั้งสองคิว), `--dry-run`, `--public-log`, `--sa-key` | ทุกคำสั่ง |
| `--limit`, `--batch`, `--workers`, `--only-rows`, `--no-retry-errors`, `--include-blank`, `--no-reconcile`, `--max-mb`, `--parse-retries`, `--accept-first-report`, `--max-output-tokens`, `--no-thumbnail`, `--model`, `--api-version` | ส่วน AI ของ `sync db` และ `check access` |
| `--no-ai` | ขั้น `db` |
| `--legacy-spreadsheet`, `--overwrite` | ขั้น `queue` |
| `--allow-large-removal` | ขั้น `queue` และ `db` |

เมื่อรันทั้งสองขั้น (`sync` หรือ `sync queue db`):

- แต่ละขั้นขึ้นต้นด้วยบรรทัดหัวข้อ (`▶ Step 1/2 - sync queue: legacy sheet -> queue`) และบรรทัดสุดท้ายสรุปผล (`Sync steps: queue OK, db OK`)
- คิวที่ขั้น `queue` ล้มเหลว ถูกตัวกันพลาดปฏิเสธ หรือไม่ได้สร้างอะไร จะ**ถูกตัดออกจากขั้น `db`** (log บอกว่าคิวไหน) ส่วนที่ล้มเหลว**ไม่ทำให้อีกส่วนหยุด** แต่รหัสออกเป็น 2
- ถ้า `--dry-run` จะไม่เขียนอะไร และขั้น `db` แสดงแผน**บนคิวที่ขั้น `queue` วางแผนไว้** (เช่นแถวที่จะเพิ่ม) `sync --dry-run` จึงเป็นตัวอย่างของทั้งสายงาน
- ถ้ารันจริง ขั้น `db` จะอ่านชีตคิวใหม่ (อ่านสิ่งที่ขั้น `queue` เขียน และสิ่งที่มีคนแก้ระหว่างนั้น)

**คำสั่งเก่า** `migrate_queue.py` ถูกแทนที่ด้วย `pmis.py` ไฟล์เก่ายังใช้ได้: แปลงออปชันเก่าเป็นคำสั่งใหม่แล้วรัน พร้อมบอกคำสั่งใหม่ทาง stderr ส่วน `pmis.py` เองไม่รับออปชันเก่า และจะบอกว่าให้พิมพ์อะไรแทน

| เก่า | ใหม่ |
|---|---|
| `python migrate_queue.py` (เดิมคือ AI) | `python pmis.py sync db` (ส่วน 1 แก้แถวใน DB ก่อน แล้วส่วน AI อ่าน PDF) |
| `python migrate_queue.py --extract` | `python pmis.py sync queue` |
| `python migrate_queue.py --extract --sync-db` | `python pmis.py sync queue db --no-ai` |
| `python migrate_queue.py --sync-db` | `python pmis.py sync db --no-ai` |
| `python migrate_queue.py --check-access` | `python pmis.py check access` |
| `python migrate_queue.py --backfill-cp` | ถูกลบแล้ว: `python pmis.py sync db --no-ai` เติมและอัปเดต C/P ให้ (และทำส่วน 1 ที่เหลือด้วย) |

ออปชันอื่น (`--queue`, `--dry-run`, `--limit`, ...) ไม่เปลี่ยน

## ลำดับที่แนะนำ

```powershell
python pmis.py sync --dry-run                           # แผนของทั้งสายงาน: คิวและ DB จะเปลี่ยนอะไร AI จะอ่านแถวไหน (ไม่เขียนอะไร)
python pmis.py check access --limit 0                   # เช็คว่า service account เปิด PDF ได้ทุกไฟล์ไหม ทั้งสองคิว
python pmis.py sync queue                               # ชีตเก่า -> คิว (รายการใหม่และ PDF ที่เปลี่ยนจะเป็น Waiting)
python pmis.py sync db --no-ai --dry-run                # ส่วน 1 อย่างเดียว: แถวใน DB จะเปลี่ยนกี่แถว (ไม่ต้องมีคีย์ Gemini)
python pmis.py sync db --no-ai                          # ส่วน 1 จริง
python pmis.py sync db --limit 5                        # ทดสอบ AI จริง คิวละ 5 แถว แล้วเทียบกับแถวที่ Done จากของเดิม
python pmis.py sync db --queue dwr --only-rows 83,102,127-128   # ลองแถวที่เคย error เดี่ยวๆ (ต้องระบุ --queue ทำเฉพาะ AI)
python pmis.py sync db --limit 200                      # รันเป็นรอบ ตรวจผลแล้วค่อยเพิ่ม (0 = ทั้งหมด)
python pmis.py sync                                     # รันทั้งสายงานจริง
```

**เลือกคิว:** ถ้าไม่ใส่ `--queue` สคริปต์จะทำ **ทั้งสองคิว** โดยทำ DWR ก่อน (`MIGRATION_QUEUE` -> `DB_DWR`) แล้วตามด้วย DRQ (`MIGRATION_QUEUE_DRQ` -> `DB_DRQ`); `--limit` นับ **ต่อคิว** ถ้าอยากทำคิวเดียวใช้ `--queue dwr` หรือ `--queue drq`
`--only-rows` อ้างเลขแถวของชีตคิวใดคิวหนึ่ง จึงต้องระบุ `--queue` คู่กันเสมอ (และใช้ได้กับ `sync db` / `check access` เท่านั้น)
ถ้าคิวแรกหยุดกลางคันในส่วน AI (เช่น Gemini ตอบ 402/401/403 หรือติด 429 ต่อเนื่อง) จะข้ามคิวที่สองเพราะจะหยุดด้วยสาเหตุเดียวกัน แต่ถ้าอ่านชีตคิวใดคิวหนึ่งไม่ได้ (เช่นไม่มีแท็บนั้น) จะยังทำอีกคิวต่อ ทั้งสองกรณีรหัสออกของโปรแกรมเป็น 2

ชีตเป้าหมายกำหนด**ผ่านตัวแปรสภาพแวดล้อม `PMIS_SPREADSHEET_ID` เท่านั้น** ไม่มีค่าเริ่มต้นในโค้ดและไม่มีออปชันบรรทัดคำสั่ง ถ้าไม่ตั้งหรือเป็นค่าว่างสคริปต์จะหยุดพร้อมข้อความแนะนำ (ทุกโหมด รวมถึง `--dry-run`) ID คือส่วนใน URL ของชีตระหว่าง `/d/` กับ `/edit`: `https://docs.google.com/spreadsheets/d/<ID>/edit`

## ขั้น `queue`: สร้าง/sync คิว (`sync queue`)

แทน `extractLegacyDwrLinks()` / `extractLegacyDrqLinks()` อ่านชีตเก่าผ่าน Sheets API (ค่า ไฮเปอร์ลิงก์ รูปแบบวันที่) แล้วเขียน `MIGRATION_QUEUE` / `MIGRATION_QUEUE_DRQ` ด้วยกฎเดียวกับ Apps Script:

- หัวตารางอยู่แถว 3 ข้อมูลเริ่มแถว 4 ข้ามแถวที่ Date (คอลัมน์ B) ว่าง ไฮเปอร์ลิงก์ของคอลัมน์ B กลายเป็น `Folder URL`
- ทุกช่องที่มีค่าตั้งแต่คอลัมน์ E ถึง **BD** (DWR) หรือ **BF** (DRQ) คือ 1 รายการ `Discipline` = หัวคอลัมน์ที่ตัด `(P)` / `(C)` ออก ถ้าหัวคอลัมน์มี `(C)` ค่า `Status(C/P)` เป็น `C` ไม่งั้นเป็น `P`
- `Doc No` = คอลัมน์ A + `-` + Discipline + `-` + ค่าในช่อง ส่วนไฮเปอร์ลิงก์ของช่องนั้นคือ `PDF URL`
- Date + Discipline + ค่าในช่องเดียวกันนับเป็นรายการเดียว ถ้าช่อง `(C)` มาซ้ำ ลิงก์ของมัน (ถ้ามี) จะแทน PDF URL และรายการกลายเป็น `C`
- ทุกแถวเริ่มที่ `Waiting`

ชื่อแท็บของชีตเก่า**ไม่ได้อยู่ในโค้ด** ให้ตั้งใน environment ตามที่เห็นในสเปรดชีตเป๊ะๆ (รวมวงเล็บและช่องว่าง):

```powershell
$env:PMIS_LEGACY_DWR_SHEET = "<ชื่อแท็บ DWR>"
$env:PMIS_LEGACY_DRQ_SHEET = "<ชื่อแท็บ DRQ>"
python pmis.py sync queue --dry-run     # อ่านชีตเก่า แสดงจำนวนและแถวตัวอย่าง ไม่เขียนอะไร
python pmis.py sync queue               # สร้างทั้งสองคิว
python pmis.py sync queue --queue drq   # เฉพาะคิวเดียว (ตั้งแค่ตัวแปรชื่อแท็บของคิวนั้นก็พอ)
```

- `sync queue` ไม่ต้องใช้ `GEMINI_API_KEY` และไม่แตะ Drive ไม่สนใจ `--limit` และไม่รับ `--only-rows`
- คอนโซลแสดงต่อคิวว่าพบกี่รายการไม่ซ้ำ กี่รายการไม่มีลิงก์ PDF (แถวเหล่านั้นจะเป็น `Error: No URL` ตอนย้าย) และกี่ช่องที่ข้ามเพราะหัวคอลัมน์ว่าง
- **Sync (ค่าเริ่มต้นเมื่อคิวมีแถวอยู่แล้ว):** `sync queue` **คำนวณทุกแถวของคิวใหม่จากชีตเก่า** (Doc No, Date, Discipline, PDF URL, Folder URL, C/P เรียงตามลำดับของชีตเก่า) แล้วเทียบกับคิวตอนนี้ ถ้ารายการเดียวกันมีแถวอยู่แล้ว แถวใหม่จะ**คงสถานะของแถวเดิม** (คอลัมน์ G: `Done`, `Error…`, `Waiting`) ส่วนคอลัมน์อื่นเป็นไปตามชีตเก่า **ข้อยกเว้น: แถว `Done` ที่ PDF (Drive file ID) ในชีตเก่าเปลี่ยน จะกลับเป็น `Waiting`** และจด file ID ของ PDF เก่าไว้ในคอลัมน์ H (`Replaces PDF (old file ID)`) เพื่อให้ส่วน AI ของ `sync db` รอบถัดไปอ่านไฟล์ใหม่และแทนที่แถวใน DB ที่ใช้ไฟล์เก่า (ดู "ทำให้ PMIS sync กับชีตเก่า") ลิงก์ที่แค่เปลี่ยนรูปแบบ (file ID เดียวกัน) ไม่นับเป็น PDF ใหม่ รายการที่ยังไม่มีแถวจะเป็นแถว `Waiting` ใหม่ แถวเดิมที่รายการของมันไม่อยู่ในชีตเก่าแล้ว**จะถูกลบออก** คิวจึงมีเนื้อหาเท่ากับชีตเก่าเสมอ (คอนโซลเตือนว่าแถวที่ถูกลบเคยเป็น `Done` / `Error…` กี่แถว) ถ้าไม่มีอะไรเปลี่ยนจะไม่เขียนอะไร จึงรันตามเวลาได้อย่างปลอดภัย `--dry-run` แสดงแผนเป็นตารางสั้นๆ (`Kept from the queue`, `New`, `Duplicate rows merged`, `Removed (not in the legacy sheet)`, `Statuses kept`) การเขียนทำทับในที่เดิมเฉพาะคอลัมน์ A-H (ไม่ล้างชีตก่อน ถ้าพลาดกลางทางจึงไม่ว่าง และเวลาในคอลัมน์ `I` / `K` ไม่ถูกแตะ) **ตัวกันพลาด:** ถ้าจะลบแถวมากกว่าเพดาน (ค่าที่มากกว่าระหว่าง 10% ของแถวทั้งหมดกับ 100) จะปฏิเสธ เพื่อกันกรณีชีตเก่าว่างผิดปกติ ใช้ `--allow-large-removal` ถ้าต้องการให้ผ่าน
- **การหาแถวเดิมของรายการ:** ด้วย `(Doc No, Date)` เดียวกัน หรือ (กรณีที่แค่ `No.` (คอลัมน์ A) ของชีตเก่าเปลี่ยน) ด้วย **Date + Discipline + ค่าในช่อง** เดียวกัน (`Doc No` = `No.` + `-` + Discipline + `-` + ค่าในช่อง ส่วนหลัง `No.` จึงไม่เปลี่ยน) แถวใหม่จะมี `Doc No` ใหม่ และคอนโซลแสดง `N renumbered` (`--dry-run` แสดง `sample (renumbered): row 5: เดิม -> ใหม่`) ถ้ารายการเดียวมี**หลายแถว** (เช่นเข้าคิวสองครั้งด้วย `Doc No` เก่าและใหม่) จะรวมเป็นแถวเดียวที่คงสถานะของแถวที่ประมวลผลแล้ว (`Done` ก่อน `Error…` ก่อน `Waiting` ถ้าเท่ากันเอาแถวที่อยู่ก่อน) ถ้า **Date** เปลี่ยนด้วย จะถือเป็นรายการใหม่
- **สร้างใหม่:** ใส่ `--overwrite` เพื่อล้างคิวแล้วสร้างทุกแถวเป็น `Waiting` เหมือน Apps Script (ล้างสถานะ `Done` / `Error…` ทั้งหมด สำรองคอลัมน์ G ก่อน) แท็บคิวที่ยังไม่มีหรือว่างจะสร้างใหม่เลย ถ้าหัวตารางของแท็บไม่ใช่ `Doc No | Date | Discipline | PDF URL | Folder URL | Status(C/P) | AI Processed` `sync queue` จะไม่เขียนอะไรและจบด้วยรหัส 2 จนกว่าจะแก้หรือใส่ `--overwrite`
- **เวลา refresh:** ทุกครั้งที่ `sync queue` จริง (ไม่ใช่ `--dry-run`) และเขียนคิวแล้ว `I1` = `Last extract (refresh time)` และ `I2` = เวลาเป็นเวลาไทย (UTC+7) เช่น `2026-10-22 14:05:33 (UTC+7)` ชีตจึงบอกได้ว่า refresh ล่าสุดเมื่อไหร่ คอลัมน์ I อยู่นอกข้อมูลคิว (A-G)
- **แท็บเก่าอยู่คนละไฟล์:** ค่าเริ่มต้นคือแท็บเก่าอยู่ในสเปรดชีตเดียวกับคิว ถ้าจะอ่านจากไฟล์อื่น ใส่ `--legacy-spreadsheet <ID หรือ URL>` หรือตั้ง `PMIS_LEGACY_SPREADSHEET_ID` คิวยังเขียนที่ `PMIS_SPREADSHEET_ID` เหมือนเดิม แชร์ไฟล์นั้นให้ service account (แค่ Viewer ก็พอ)
- **`.env`:** ตัวแปรทั้งหมดข้างบนเขียนไว้ในไฟล์ `.env` ข้างสคริปต์ได้ (ดู `.env.example`) ตัวแปรที่ตั้งไว้ในสภาพแวดล้อมแล้วมีสิทธิ์เหนือกว่า
- ต่างจาก Apps Script: ค่าในคิวเขียนเป็นข้อความล้วน (`RAW`) วันที่ ตัวเลข และเลข 0 นำหน้าจึงไม่ถูกแปลง ล้างเฉพาะค่าในชีตคิว (รูปแบบเดิมยังอยู่) และเพิ่มแถวด้วย append ชีตจึงขยายเอง
- service account ต้องมีสิทธิ์ **Editor** ในสเปรดชีต (อ่านแท็บเก่า เขียนแท็บคิว) ไฟล์ PDF ไม่ถูกแตะ

## ทำให้ PMIS sync กับชีตเก่า

เป้าหมาย: ข้อมูลใน PMIS (คิวและ `DB_DWR` / `DB_DRQ`) ตรงกับชีตเก่าเสมอ สายงานคือ **ชีตเก่า -> (`sync queue`) -> คิว -> (`sync db`) -> DB** และ `python pmis.py sync` รันทั้งหมดนี้ `sync queue` ตัดสินแค่ว่า **PDF ไหนที่ AI ต้องอ่าน** (รายการใหม่หรือ PDF ที่เปลี่ยนจะเป็น `Waiting`) ส่วนข้อมูลอื่นที่มาจากชีตเก่า `sync db` ดูแลให้ตรง:

| การเปลี่ยนในชีตเก่า | สิ่งที่เกิดใน PMIS |
|---|---|
| มีรายการใหม่ | `sync queue` เพิ่มแถว `Waiting` ส่วน AI ของ `sync db` รอบถัดไปเพิ่มแถวใน DB |
| `No.` (คอลัมน์ A) เปลี่ยน | `sync queue` เปลี่ยน `Doc No` ของรายการเดิม (ไม่เกิดแถวซ้ำ) |
| PDF ของรายการที่เป็น `Done` เปลี่ยน | `sync queue` ตั้งแถวกลับเป็น `Waiting` และจด file ID เก่าไว้ที่คอลัมน์ H `sync db` ตั้งแถวเก่าใน DB เป็น `Outdated` แล้วส่วน AI อ่านไฟล์ใหม่และ**แทนที่แถวเก่าใน DB ในที่เดิม** (ดูด้านล่าง) แถวกลับเป็น `Synced` ถ้าพลาด แถวเก่าใน DB ยังเป็น `Outdated` และคอลัมน์ H ยังเก็บไว้ |
| ลิงก์ PDF ของรายการถูกลบออก | `sync queue` เก็บแถวไว้ ส่วน 1 ของ `sync db` ลบแถวใน DB ของ PDF นั้น (คัดลอกไป `_REMOVED_...` ก่อน) ถ้ามีลิงก์กลับมา จะอ่านใหม่ (แถวใหม่ใน DB) |
| C/P หรือลิงก์โฟลเดอร์ (ลิงก์ที่คอลัมน์ B) เปลี่ยน | `sync queue` อัปเดตแถวในคิว ส่วน 1 ของ `sync db` อัปเดต C/P / Folder Link ของแถวใน DB ไม่ใช้ AI และไม่ตั้งแถวเป็น `Waiting` |
| รายการถูกลบ | `sync queue` ลบแถวในคิว และส่วน 1 ของ `sync db` ลบแถวใน DB ที่มาจากชีตเก่า (ป้าย `Synced` / `Outdated`) และ PDF ไม่อยู่ในคิวแล้ว |

- **ใครเขียนอะไร:** `sync queue` เขียนแท็บคิวอย่างเดียว `sync db` เขียนแท็บ DB และคอลัมน์สถานะของคิว (G `Done` / `Error…`, H และเวลาใน `K1:K2`) เมื่อ AI ทำแถวนั้นเสร็จ เมื่อชีตเก่าเปลี่ยน DB จะตามในรอบ `sync db` ถัดไป `sync queue` อย่างเดียวไม่เขียนแท็บ DB เลย ป้ายจึงสะท้อนสถานะ ณ `sync db` ครั้งล่าสุด
- **แทนที่ในที่เดิม:** แถวใน DB ที่ใช้ไฟล์เก่า (หาด้วย file ID เก่า) ถูกเขียนทับด้วยข้อมูลใหม่ในแถวเดิม คอลัมน์ที่ไม่ใช่ข้อมูลของ migration จะไม่ถูกแตะ: คอลัมน์ A (`CSC Ref. No`) และ `Ext1` / `Ext2` ที่ว่าง ถ้ามีแถวคิวอื่นที่ยังใช้ PDF เก่าอยู่ หรือไฟล์เก่าไม่อยู่ใน DB จะต่อท้ายเป็นแถวใหม่แทน คอลัมน์ H จะถูกล้างเมื่อแถวเป็น `Done`
- **ส่วน 1 ของ `sync db`** (`python pmis.py sync db --no-ai`; `sync` และ `sync queue db` รันต่อจากขั้น `queue` ถ้ามี `--dry-run` จะใช้แถวที่ขั้น `queue` วางแผนไว้): แต่ละแถวใน DB จะหาแถวในคิวที่ใช้ **PDF เดียวกัน** (Drive file ID) แล้ว
  - อัปเดต **C/P** และ **Folder Link** เมื่อในคิวมีค่าเดียวและต่างจาก DB (ถ้าค่าในคิวว่างจะไม่ล้างค่าใน DB ลิงก์ที่รูปแบบต่างแต่ชี้ Drive ID เดียวกันไม่นับว่าเปลี่ยน)
  - ติด **ป้าย `Legacy sync`** (ด้านล่าง)
  - **ลบ** แถวใน DB ที่ PDF ไม่อยู่ในคิวแล้ว **เฉพาะแถวที่ Status เป็น `AI Synced` (หรือชื่อเก่า `AI Migrated`) และมีป้าย `Synced` หรือ `Outdated`** คือแถวที่มาจากชีตเก่าแล้วหายไปจากชีตเก่า (แถวที่คนพิมพ์เอง และแถวที่ไม่เคยมาจากชีตเก่า เช่นเอกสารที่ระบบอื่นเพิ่มเข้ามา จะไม่ถูกลบเด็ดขาด) และเก็บแถวที่ PDF รอการแทนที่ในคอลัมน์ H ไว้ **ก่อนลบจะคัดลอกแถวไปที่แท็บ `_REMOVED_DB_DWR` / `_REMOVED_DB_DRQ`** (เวลา, แท็บ, เลขแถวเดิม, เหตุผล และเซลล์เดิมทั้งหมด ถ้าไม่มีแท็บจะสร้างให้) จึงไม่มีอะไรหาย
  ก่อนเขียนจะอ่านคอลัมน์ PDF อีกครั้ง ถ้ามีแถวถูกแทรกหรือลบระหว่างนั้นจะหยุดโดยไม่แก้อะไร รันซ้ำแล้วไม่มีอะไรเปลี่ยนก็ไม่เขียนอะไร
- **ตัวกันพลาด:** ถ้าคิวไม่มีแถว หรือจะลบแถวมากกว่าเพดาน (ค่าที่มากกว่าระหว่าง 10% ของแถวใน DB กับ 100) จะไม่เขียนอะไรลง DB นั้น (`--allow-large-removal` ปลดเพดาน และ `--dry-run` ไม่ล้มเพราะเรื่องนี้) แถวถูกลบออกจากชีตจริง ชีตอื่นที่อ้างแถวของ DB ด้วยตำแหน่งอาจเลื่อน ควรดูแผนด้วย `--dry-run` ก่อน
- **บน GitHub Actions:** ดู "รันบน GitHub Actions": **Sync all** รันทั้งสายงาน **Sync queue and DB (no AI)** รัน `sync queue db --no-ai` (ขั้น `db` คือ input `db` ที่เปิดเป็นค่าเริ่มต้น) และ **Sync DB (with AI)** รัน `sync db` ให้รันด้วย `mode = dry-run` ก่อน log แสดงแค่ตัวเลข
- **ป้าย `Legacy sync`:** คอลัมน์ถัดจาก C/P (**`U` ใน `DB_DWR`, `T` ใน `DB_DRQ`** หัวตาราง `Legacy sync`) บอกว่าแถวใน DB ตรงกับชีตเก่าหรือไม่:

  | ป้าย | ความหมาย |
  |---|---|
  | `Synced` | แถวมาจากชีตเก่าและ**ตรวจแล้ว**: PDF อยู่ในคิว และ C/P กับ Folder Link ตรงกับคิว (แถวที่ส่วน AI เขียนจะได้ป้ายนี้ทันทีที่เขียน) |
  | `Outdated` | แถวมาจากชีตเก่าแต่ยังไม่ตรง: PDF เปลี่ยนและรอ AI อ่านไฟล์ใหม่มาแทนที่ (PDF เก่าอยู่ในคอลัมน์ H ของคิว) หรือในคิว PDF เดียวกันมี C/P / Folder Link หลายค่าที่ต่างกัน (ไฟล์เดียวถูกใช้ในหลายรายการ) และใน DB ไม่ตรงกับค่าไหนเลย - ไม่เดาแล้วเขียนทับ |
  | ว่าง | แถวไม่เคยมาจากชีตเก่า (เช่นเอกสารที่ระบบอื่นเพิ่ม), ไม่ได้เขียนโดยเครื่องมือนี้ (Status ไม่ใช่ `AI Synced` / `AI Migrated` จึงไม่ถูกลบด้วย) หรือไม่มีลิงก์ PDF |

  ป้ายยังเป็นความจำว่าแถวไหนมาจากชีตเก่า: **แถวที่ไม่เคยติดป้ายจะไม่ถูกลบ** เอกสารที่ระบบอื่นเพิ่ม หรือแถวที่ชีตเก่าไม่มี จึงอยู่ต่อ `sync db` รอบแรกจึงแค่ติดป้าย (ไม่ลบอะไร) และจะลบเฉพาะแถวที่เคยติดป้ายแล้วหายไปจากชีตเก่า ก่อนเขียนป้ายครั้งแรกจะเช็กหัวตารางเหมือนคอลัมน์ C/P: ถ้าว่างจะเติม `Legacy sync` ถ้าเป็นหัวอื่นจะหยุด DB นั้น แถวที่ถูกลบจะเก็บป้ายไว้ในสำเนาที่ `_REMOVED_...`
- **ค่า Status:** แถวที่เครื่องมือนี้เขียนจะมี `AI Synced` ในคอลัมน์ Status (เดิมเป็น `AI Migrated` ตั้งแต่ตอนที่เครื่องมือนี้ยัง migrate อย่างเดียว) แถวที่ยังใช้ชื่อเก่าถือว่าเป็นแถวที่เครื่องมือนี้เขียนต่อไป และ `sync db` ส่วนที่ 1 จะเปลี่ยนเป็น `AI Synced` ในที่เดิม (log แสดง `Renamed Status ...`, `--dry-run` แสดง `Status to rename`) ส่วนอื่นของแถวไม่เปลี่ยน และแถวที่ Status เป็นค่าอื่นจะไม่ถูกแตะ
- **สิ่งที่คงไว้:** แถวใน DB ที่ PDF เดียวกัน (หรือ Doc No เดียวกัน) มีหลายแถวจะไม่ถูกแตะ ลบเฉพาะ PDF ที่เคยมาจากชีตเก่าแล้วไม่อยู่ในคิวแล้ว PDF ในคอลัมน์หลัง `BD` / `BF` ที่ `sync queue` ไม่อ่านจึงไม่อยู่ในคิว แถวของมันจะถูกลบก็ต่อเมื่อเคยติดป้ายมาก่อน

## C/P และ Folder Link ใน DB

ทุกแถวใน DB มีค่า **C/P** ของแถวในคิว (คอลัมน์ F `Status(C/P)`) คัดลอกตามเดิม ที่ **คอลัมน์ T ของ `DB_DWR`** และ **คอลัมน์ S ของ `DB_DRQ`** (หัวตาราง `Status (C/P)` ทั้งคู่) ตามด้วยคอลัมน์ป้าย `Legacy sync` (`U` ใน `DB_DWR`, `T` ใน `DB_DRQ` ดู "ทำให้ PMIS sync กับชีตเก่า") ส่วน **Folder Link** (คอลัมน์ S ของ `DB_DWR`, คอลัมน์ P ของ `DB_DRQ`) มาจาก `Folder URL` ของคิว คอลัมน์เดิมไม่ขยับ: `DB_DWR` ยังเป็น A-S (`Folder Link` คือ S) ส่วน `DB_DRQ` คอลัมน์ใหม่อยู่ต่อจาก `Folder Link` (P) และ `Ext1` / `Ext2` (Q, R)

- ส่วน AI เขียนทั้งสองค่าตอนเขียนแถว หลังจากนั้นส่วน 1 ของ `sync db` ดูแลให้ตรง: C/P หรือ Folder Link ที่ต่างจากคิวจะถูกเขียนทับด้วยค่าในคิว แถวที่ migrate ไปก่อนมีคอลัมน์ C/P ก็ได้ C/P ทางนี้ (สิ่งที่ `--backfill-cp` เคยทำ): รัน `python pmis.py sync db --no-ai --dry-run` แล้วค่อย `python pmis.py sync db --no-ai`
- ถ้า PDF เดียวกันมีหลายค่าที่ต่างกันในคิว (ไฟล์เดียวถูกใช้ในหลายรายการ) จะไม่เดา: ถ้าค่าใน DB เป็นหนึ่งในนั้นก็ถือว่าตรง ถ้าไม่ตรงกับค่าไหนเลยแถวจะเป็น `Outdated` และไม่เขียนทับ
- ก่อนเขียนครั้งแรก สคริปต์จะดูเซลล์หัวตาราง (`T1` ใน `DB_DWR`, `S1` ใน `DB_DRQ`) ของแท็บ DB: ถ้าว่างจะเติม `Status (C/P)` ให้ ถ้าเป็นหัวเดียวกันก็คงไว้ **ถ้าเป็นหัวอื่นจะหยุดคิวนั้น** เพื่อไม่ให้ค่าไปลงใต้คอลัมน์อื่น หัว `Legacy sync` ก็เช็กแบบเดียวกัน

## `check access` เช็คอะไร

สำหรับแต่ละแถวที่รอทำ จะดึง file ID จากคอลัมน์ PDF URL แล้วขอ metadata ของไฟล์จาก Drive (ชื่อ, ชนิด, ขนาด, อยู่ในถังขยะหรือไม่) ด้วยสิทธิ์ของ service account
รายงานแถวที่ `No URL`, `Drive Access` (404/403: ไม่มีไฟล์ หรือยังไม่ได้แชร์ให้ service account) หรือไฟล์อยู่ในถังขยะ

ค่าเริ่มต้นตรวจทั้งสองคิวต่อกัน (ใส่ `--queue dwr` หรือ `--queue drq` ถ้าอยากตรวจแค่คิวเดียว)
ทุกบรรทัดของผลลัพธ์ขึ้นต้นด้วยชื่อชีตคิว เช่น `[MIGRATION_QUEUE] Reachable: 2/3 files` หรือ `✗ [MIGRATION_QUEUE_DRQ] row 4: Drive Access | …` และถ้าตรวจคิวเดียว บรรทัดสุดท้ายจะบอกคำสั่งสำหรับตรวจคิวอีกอัน

**ไม่** ดาวน์โหลด PDF, **ไม่** เรียก Gemini (ไม่เสียเงิน), **ไม่** เขียนชีต, ไม่เช็กว่าเป็น PDF จริง และไม่ทดสอบสิทธิ์เขียนชีต
แถวที่ PDF ใหญ่กว่า `--max-mb` จะแสดงบรรทัด `ℹ` ว่าจะส่งเฉพาะภาพหน้าปก (ดู "พฤติกรรมที่ควรรู้")
ค่าเริ่มต้น `--limit` คือ 30 จึงต้องใส่ `--limit 0` ถ้าอยากเช็กทุกแถว

## รันบน GitHub Actions

workflow สามตัวรันสคริปต์ตัวเดียวกันบน runner ของ GitHub จึงไม่ต้องเปิดคอมค้างไว้:

| workflow (แท็บ Actions) | ไฟล์ | รัน | ต้องใช้ |
|---|---|---|---|
| **Sync all** | `.github/workflows/sync.yml` | `python pmis.py sync` (queue -> db พร้อม AI) | `GEMINI_API_KEY` และ variables ของชีตเก่า |
| **Sync queue and DB (no AI)** | `.github/workflows/sync-no-ai.yml` | `python pmis.py sync queue db --no-ai` (ไม่เรียก Gemini) | variables ของชีตเก่า |
| **Sync DB (with AI)** | `.github/workflows/sync-db.yml` | `python pmis.py sync db` (ส่วน 1 แล้วตามด้วย AI) หรือ `check access` | `GEMINI_API_KEY` |

repo แบบ private บนแพ็กเกจ GitHub Free ได้ Actions 2,000 นาทีต่อเดือน

ตั้งค่าครั้งเดียว:

1. สร้าง repo แล้ว push โฟลเดอร์นี้ขึ้นไป แนะนำแบบ **private**: workflow แสดงแค่ตัวเลขสรุป (`--public-log`) แต่ถ้า repo เป็น public ใครก็เปิดดู log และสรุปผลการรันได้
2. เพิ่ม repository secrets 2 ตัวและ variable 1 ตัว (Settings > Secrets and variables > Actions ID สเปรดชีตเป็นแค่ค่าตั้ง จึงใส่ในแท็บ **Variables**) หรือใช้ GitHub CLI ใน PowerShell:

```powershell
Get-Content C:\path\to\secrets\sa-key.json -Raw | gh secret set SA_KEY_JSON   # เนื้อหาไฟล์คีย์ทั้งไฟล์
gh secret set GEMINI_API_KEY                                                    # จะถามค่า
gh variable set PMIS_SPREADSHEET_ID --body "<ID สเปรดชีต>"                        # เป็น variable ไม่ใช่ secret (secret ชื่อเดียวกันยังใช้เป็นตัวสำรองได้)
```

3. เตรียมเหมือนรันในเครื่อง: ปิด trigger ของ Apps Script และแชร์ชีตกับ PDF ให้อีเมล service account
4. เฉพาะ **Sync queue and DB (no AI)** และ **Sync all** ให้เพิ่มค่าตั้งของชีตเก่าเป็น **variables** ของ repo (Settings > Secrets and variables > Actions > Variables เป็นแค่ค่าตั้ง ไม่ใช่ความลับ และ **Sync queue and DB (no AI)** ไม่ต้องใช้ `GEMINI_API_KEY`): `PMIS_LEGACY_DWR_SHEET`, `PMIS_LEGACY_DRQ_SHEET` (ชื่อแท็บเป๊ะๆ) และถ้าต้องการ `PMIS_LEGACY_SPREADSHEET_ID` (ไฟล์ที่เก็บแท็บเหล่านั้น ถ้าไม่ใช่สเปรดชีตเป้าหมาย แชร์ให้ service account ด้วย) ถ้าใช้ GitHub CLI: `gh variable set PMIS_LEGACY_DWR_SHEET --body "<ชื่อแท็บ>"` secret ชื่อเดียวกันยังใช้เป็นตัวสำรองได้ และ variable มาก่อน

วิธี sync ทั้งหมด: แท็บ Actions > **Sync all** > Run workflow ด้วย `mode = dry-run` (แสดงแผนของทั้งสองขั้น ไม่เขียนอะไร) แล้วค่อย `mode = sync` ด้วย `limit` น้อยๆ ช่อง `queue` เลือก DWR / DRQ / ทั้งสอง, `legacy_spreadsheet` ชี้ไปไฟล์อื่น, `allow_large_removal` ปลดเพดานตัวกันพลาด ถ้าใช้บรรทัดคำสั่ง: `gh workflow run sync.yml -f mode=dry-run`

วิธี sync เฉพาะคิวและแก้แถวใน DB โดยไม่ใช้ AI: แท็บ Actions > **Sync queue and DB (no AI)** > Run workflow ด้วย `mode = dry-run` (อ่านแท็บชีตเก่า ไม่เขียนอะไร) แล้วค่อย `mode = sync` `db` (เปิดเป็นค่าเริ่มต้น) รันส่วน 1 ของขั้น `db` ต่อ ทำให้ DB_DWR / DB_DRQ ตรงกับคิว, `overwrite` สร้างใหม่ทั้งคิวแทนการ sync (รันตามเวลาอย่าเปิดตัวนี้) ถ้าใช้บรรทัดคำสั่ง: `gh workflow run sync-no-ai.yml -f mode=dry-run`

วิธีแก้แถวใน DB และให้ AI อ่าน PDF ที่รออยู่: แท็บ Actions > **Sync DB (with AI)** > Run workflow แล้วเลือก `mode` เริ่มจาก `dry-run` ตามด้วย `check-access` แล้ว `sync` ด้วย `limit` น้อยๆ ก่อนค่อยเพิ่ม ถ้าใส่ `only_rows` จะทำเฉพาะ AI ของแถวนั้น `allow_large_removal` ปลดเพดานตัวกันพลาดของส่วน 1 ถ้าใช้บรรทัดคำสั่ง: `gh workflow run sync-db.yml -f mode=dry-run`

- unit test (job `test` รวมถึงตัวเช็ก workflow เอง) จะรันก่อน ผ่านแล้ว sync ถึงจะเริ่ม
- ไม่มีสองรอบรันซ้อนกัน ไม่ว่าจะเป็น workflow ไหน (ทั้งสามตัวใช้ `concurrency` กลุ่มเดียวกัน) จึงไม่ทำแถว `Waiting` เดียวกันซ้ำ
- คีย์ service account ถูกเขียนเป็นไฟล์ชั่วคราวเฉพาะช่วงที่รัน
- workflow เรียกสคริปต์ด้วย `--public-log`: log และสรุปผลการรันแสดงแค่ตัวเลข (สถานะคิว, ยอด Done/Error, สาเหตุที่หยุด) ไม่มีชื่อแท็บ ชื่อ/ID ชีต Doc No file ID หรือรายละเอียด error และซ่อน ID สเปรดชีตกับอีเมล service account อีกชั้น สิ่งที่สคริปต์เขียนลง stderr ไปอยู่ในไฟล์ที่ไม่เคยถูกพิมพ์ รายละเอียดรายแถวดูได้ที่คอลัมน์ G ของชีตคิว ถ้าต้องการ log เต็มให้รันในเครื่อง (ไม่ใส่ `--public-log`)
- workflow ไม่มี `schedule:` (cron ของ GitHub เองไม่เริ่มรันให้) ถ้าจะรันอัตโนมัติ ให้เรียก workflow_dispatch API จากตัวตั้งเวลาภายนอก เช่น cron-job.org: `POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/sync.yml/dispatches` (หรือ `sync-no-ai.yml` / `sync-db.yml`) ด้วย body `{"ref":"main","inputs":{"mode":"sync","limit":"30"}}` (`sync-no-ai.yml`: `{"ref":"main","inputs":{"mode":"sync"}}`, `sync-db.yml`: `{"ref":"main","inputs":{"mode":"sync","limit":"30"}}`) และ token แบบ fine-grained สิทธิ์ **Actions: Read and write** ในทั้งสามตัว `mode = sync` แปลว่ารันจริง สองตัวหลังเคยชื่อ "Extract queue" และ "Migrate queue" (`extract.yml` และ `migrate.yml` ที่ใช้ `mode = extract` / `run` และ input `sync_db`) ก่อนปรับระบบ sync: การเรียกที่ยังใช้ชื่อไฟล์หรือค่าเก่าจะล้มเหลว (ไฟล์เก่าถูกลบแล้ว) จึงต้องแก้ตามนี้
- ส่วนที่ใช้ร่วมกันอยู่ใน `.github/scripts/` (`with_credentials.sh` เขียนคีย์เป็นไฟล์ชั่วคราวแล้วลบทิ้ง, `job_summary.sh`) และ `.github/actions/setup-python-deps/`

## พฤติกรรมที่ควรรู้

- **เวลา migration ล่าสุด:** ทุกครั้งที่รันส่วน AI จริง (`sync db` ที่ไม่ใช่ `--dry-run` / `--no-ai` / `check access`) จะเขียนเวลาที่จบลงชีตคิวที่ประมวลผล: `K1` = `Last migration run (finish time)` และ `K2` = เวลาเป็นเวลาไทย (UTC+7) เช่น `2026-10-22 14:05:33 (UTC+7)` เขียนให้ทุกคิวที่ถูกรัน (คิวที่ถูกข้ามเพราะรอบหยุดก่อนจะไม่ถูกแตะ) และเขียนแม้รอบนั้นไม่มีแถวให้ทำ อยู่ข้างเวลา refresh ของ `sync queue` (`I1:I2`) นอกข้อมูลคิว (คอลัมน์ A-G) ถ้าเขียนเซลล์ไม่ได้ (เช่นแท็บมีไม่ถึง 11 คอลัมน์) ผลการรันไม่เปลี่ยน แค่เตือน
- เลือกแถวที่คอลัมน์ G เป็น `Waiting` **และ `Error…`** (ลองใหม่ให้เอง) ข้ามแถว `Done`; ใช้ `--no-retry-errors` ถ้าอยากทำเฉพาะ `Waiting`
- แถวที่พลาดซ้ำด้วยสาเหตุที่ไม่หายไป (`Error: No URL`, `Error: Drive Access`) จะถูกลองใหม่ทุกรอบ แต่พลาดทันทีและไม่เสียการเรียก Gemini ใช้ `--no-retry-errors` ถ้าอยากข้ามแถวพวกนี้
- รันเป็นก้อน (`--batch 30`): เขียน DB ก้อนเดียว แล้วค่อยตั้ง `Done` จึงไม่มีแถว `Done` ที่ข้อมูลไม่เข้า DB
- เจอ **402 / 401 / 403** จาก Gemini (เครดิตหมด/คีย์ผิด) = หยุดทั้งรอบทันที แถวที่เหลือยังเป็นสถานะเดิม; ติด **429 ต่อเนื่อง** ก็หยุดเช่นกัน
- 429 / 5xx / เครือข่ายหลุด = retry อัตโนมัติ (backoff สูงสุด 5 ครั้ง) ถ้ายังไม่ผ่านจึงเขียน `Error: API <code> | …`
- ถ้า PDF นั้นอยู่ใน DB แล้ว (เทียบ file ID ในคอลัมน์ PDF Link) จะตั้ง `Done` ให้เลยโดยไม่เรียก AI - รันซ้ำหลังโดนตัดกลางทางได้โดยไม่เกิดแถวซ้ำ
- PDF ใหญ่กว่า `--max-mb` (ค่าเริ่มต้น **15 MB** เท่าเกณฑ์ของ `extractDriveFileForAI` ใน Apps Script) จะไม่ส่งทั้งไฟล์ แต่ทำเหมือน Apps Script คือส่ง **ภาพหน้าแรก (หน้าปก)** จาก thumbnail ของ Drive (`sz=w2500`) แทน จึงวิเคราะห์ได้แค่หน้าแรก แถวพวกนี้ยังเป็น `Done` แต่ระหว่างรันจะแสดงบรรทัด `ℹ` ทีละแถว (และสรุปจำนวนตอนท้าย) ให้ตรวจสอบ ถ้าอยากได้ `Error: Too Large` แทนให้ใช้ `--no-thumbnail` หรือปรับเพดานด้วย `--max-mb` ภาพจะถูกส่งด้วย MIME ที่ถูกต้อง (Apps Script ติดป้ายเป็น `application/pdf`)
- ไฟล์ที่ไม่ใช่ PDF และใหญ่เกินเพดานยังได้ `Error: Too Large` และถ้า Drive ปฏิเสธคำขอ thumbnail แถวนั้นจะได้ `Error: Drive Access | … cover capture failed`
- แถวที่พลาดจะบันทึกสาเหตุเป็น `Error: <label> | <detail>` ส่วน Apps Script ปัจจุบันเขียนแค่ `Error: <label>` และปล่อยแถวที่ไม่มี Drive file ID ไว้เป็น `Waiting`; สคริปต์ Python จะรายงานแถวแบบนั้นเป็น `Error: No URL` ข้อความทุกอย่างที่สคริปต์เขียนลงเซลล์ Error เหล่านี้ (label, คำแนะนำ, ข้อความ) เป็นภาษาอังกฤษ รวมถึง log บนคอนโซลด้วย
- **`Error: JSON Parse`** แปลว่า Gemini ตอบกลับมาแล้วแต่คำตอบไม่ใช่ JSON ที่ถูกต้อง สาเหตุที่พบบ่อย: คำตอบ**ถูกตัดกลางคัน** (`finishReason=MAX_TOKENS` เจอบ่อยกับแบบที่มีรายการแรงงาน/เครื่องจักรยาวมาก), มีเครื่องหมายคำพูดหรือการขึ้นบรรทัดใหม่ในค่าข้อความโดยไม่ได้ escape, หรือมีข้อความอธิบายครอบ JSON ตอนนี้สคริปต์ (1) อ่านคำตอบแบบผ่อนปรนขึ้น (ยอมให้มีขึ้นบรรทัดใหม่/แท็บในสตริง และข้อความนำหน้า/ตามหลัง JSON), (2) ถ้ายังพังจะเรียก Gemini ซ้ำ (`--parse-retries` ค่าเริ่มต้น **1** ผลลัพธ์ของโมเดลต่างกันเล็กน้อยทุกครั้ง การลองใหม่จึงมักช่วยได้ ใส่ `0` เพื่อปิด) และแสดงบรรทัด `ℹ` เมื่อแถวที่รอดเพราะลองใหม่ และ (3) เขียนเหตุผลลงเซลล์ error: ข้อความจาก parser พร้อมตำแหน่ง, `finishReason`, จำนวนครั้งที่ลองใหม่ และ**ส่วนหัวกับส่วนท้าย**ของคำตอบ AI (คำตอบที่ถูกตัดจะจบกลางประโยค) ถ้าในเซลล์เห็น `finishReason=MAX_TOKENS` ให้รันแถวนั้นใหม่ด้วย `--max-output-tokens 16000` (ค่าเริ่มต้นไม่กำหนด เหมือน Apps Script) รันเฉพาะแถวนั้น: `python pmis.py sync db --queue dwr --only-rows 231` (ใช้เลขแถวในชีต ไม่ใช่เลขที่เอกสาร)
- **PDF ไฟล์เดียวมีหลายรายงาน** (`JSON Parse | ValueError: expected a JSON object, got list` อย่างที่เจอกับ PDF ที่มีหลายรายงาน): prompt ขอ object เดียว แต่ถ้า PDF มีมากกว่าหนึ่งรายงาน Gemini จะตอบเป็นอาร์เรย์ `[ {…}, {…} ]` ซึ่ง Apps Script อ่านอาร์เรย์ไม่ได้และจะเขียนแถว `Done` ที่ช่องว่างหมดลง DB สคริปต์นี้จึงเลือกรายงานที่ `DWR_No` / `DRQ_No` ตรงกับ **Doc No** ของแถวในคิว (คอลัมน์ A ไม่สนตัวพิมพ์เล็ก/ใหญ่ `-`/ช่องว่าง และเลข 0 นำหน้า เช่น `…-231` ตรงกับ `…-0231`) แถวนั้นเป็น `Done` แต่จะแสดงบรรทัด `⚠` และสรุปตอนท้าย เพราะรายงานอื่นใน PDF ไม่ได้ถูกย้าย ต้องตรวจสอบ ถ้าไม่มีรายงานไหนตรง สคริปต์จะถามซ้ำหนึ่งครั้งพร้อมคำสั่งเพิ่ม ("ตอบ object เดียวสำหรับเอกสารเลขที่ …") ถ้ายังไม่ได้ เซลล์จะแสดง `MultiReportError` พร้อมเลขที่และวันที่ของทุกรายงานที่เจอ (`#1 DWR_No=… Date=…`) ให้ดูว่า Doc No ในคอลัมน์ A ผิดหรือ PDF เป็นเอกสารอื่น `--accept-first-report` จะใช้รายงานแรกแทนการขึ้น error ในกรณีนี้ (ใช้หลังตรวจสอบแล้วเท่านั้น)
- โมเดลเริ่มต้นคือ `gemini-3.8-flash` ตัวเดียวกับ `CONFIG_GEMINI_MODEL` ใน `Code_Config.js` สองฝั่งเป็นอิสระต่อกัน (สคริปต์ไม่อ่านไฟล์ของ Apps Script) ถ้าแก้ฝั่งหนึ่งให้แก้อีกฝั่งด้วย และเปลี่ยนโมเดลของสคริปต์นี้ได้ด้วย `--model` หรือตัวแปร `GEMINI_MODEL`
- บรรทัดแรกของผลลัพธ์แสดงชื่อและ ID ของชีต (`sheet: <spreadsheet name> (<id>)`) และหัวของแต่ละคิวก็แสดงชื่อชีตซ้ำ (`===== Queue DWR: MIGRATION_QUEUE -> DB_DWR | sheet: <spreadsheet name> =====`) ถ้าอ่านชื่อไม่ได้จะแสดงสาเหตุแทน ถ้าตัวเลขดูผิดปกติให้ดูตรงนี้ก่อน เพราะ ID มาจากตัวแปรสภาพแวดล้อม `PMIS_SPREADSHEET_ID` เท่านั้น
- ถ้าคิวไหนได้ **0 แถว** สคริปต์จะบอกสาเหตุ: แท็บไม่มีแถวข้อมูล (มักเป็นเพราะอ่านผิดสเปรดชีต) หรือคอลัมน์ G มีค่าอื่นที่ไม่ใช่ `Waiting` / `Error…` (แสดงค่าที่พบจริง เช่น `'Done'×120` และตัวพิมพ์ต้องตรง) หรือคอลัมน์ G ว่างแต่มี URL (ใช้ `--include-blank`)
- ใช้ `values.append` เขียน DB จึงขยายแถวให้เอง (ตาราง DB_DWR/DB_DRQ ตอนนี้มีแค่ 1,000 แถว)
- จบรอบจะสรุปจำนวน token และค่าใช้จ่ายโดยประมาณ (ราคา list price)
- คอมเมนต์ในโค้ดมี 3 ภาษา (เกาหลี / `[TH]` / `[EN]`) ส่วนข้อความที่แสดงตอนรัน (log, `--help`) และข้อความที่เขียนลงเซลล์ Error ในชีตเป็นภาษาอังกฤษ

## โครงสร้างโปรเจกต์

โค้ดแยกตามหน้าที่ `pmis.py` เป็นแค่จุดเริ่มต้น (รันด้วย `python -m migrator` ได้ด้วย) ส่วน `migrate_queue.py` เป็นจุดเริ่มต้นเก่า ทำหน้าที่แค่แปลออปชันเก่าเป็นคำสั่งใหม่

```text
pmis.py                 entry point (python pmis.py sync | check ...)
migrate_queue.py        old entry point: translates --extract / --sync-db / ... into the new commands
migrator/
  cli.py                the commands and options (sync / check), building the clients, main()
  pipeline.py           `sync`: the steps queue -> db, run in order; the db step = part 1 (update the DB, no AI) then part 2 (the AI)
  public_log.py         --public-log: only the lines that are safe in a public log
  settings.py           settings from the environment / .env
  runner.py             the AI part of the db step: choosing the rows + the main loop
  processing.py         one row: Drive PDF -> Gemini -> DB row
  sheets.py drive.py gemini.py     the three APIs
  reports.py            parsing Gemini's JSON, building the DB rows
  extract.py            the queue step (legacy_sheet.py reads, queue_writer.py writes / syncs)
  db_sync.py            part 1 of the db step (C/P, Folder Link, the Legacy sync tag, remove the rows that left the queue) and replacing a DB row in place
  db_columns.py         the C/P and Legacy sync columns of the DB tabs: header checks, writing one column
  stamps.py             the refresh time (I1:I2) and the migration time (K1:K2) in the queue sheet
  specs.py config.py models.py utils.py clock.py
tests/                  one test file per module + fakes.py / legacy_fakes.py; test_workflows.py checks the workflows
```

## เทสต์

```powershell
python -m pip install pytest pyyaml
python -m pytest -q
```
