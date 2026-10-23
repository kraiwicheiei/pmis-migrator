# migrator - ประมวลผลคิว MIGRATION_QUEUE ด้วย Python

**ภาษา / Language / 언어:** [English](README.md) · [ไทย](README.th.md) · [한국어](README.ko.md)

แทน `runDwrAiMigrationTurbo` / `runDrqAiMigrationTurbo` ใน `Code_Migration.js` โดยใช้ prompt, โมเดล (`gemini-3.8-flash`)
และลำดับคอลัมน์ DB_DWR / DB_DRQ ชุดเดิมทุกอย่าง แต่รันบนเครื่องคุณ จึงไม่ติดโควตา UrlFetch ของ Apps Script
ทำตามโค้ด Apps Script ฉบับปัจจุบัน (`Code_Migration.js` และ `extractDriveFileForAI` ใน `Code_Core.js` สำหรับ PDF ขนาดใหญ่)
และยังแทน `extractLegacyDwrLinks` / `extractLegacyDrqLinks` ที่สร้างคิว (ดู `--extract`)

## ภาพรวม: ใครทำอะไร

| ขั้นตอน | ทำที่ไหน |
|---|---|
| สร้างคิว `MIGRATION_QUEUE` / `MIGRATION_QUEUE_DRQ` จากชีตเก่า `<legacy DWR sheet>` / `<legacy DRQ sheet>` | **สคริปต์ Python นี้** ด้วย `--extract` (แทน `extractLegacyDwrLinks()` / `extractLegacyDrqLinks()` ของ Apps Script) |
| โหลด PDF -> Gemini -> เขียน DB_DWR / DB_DRQ -> ตั้ง `Done` | **สคริปต์ Python นี้** |

> **การสร้างคิวใหม่:** ฟังก์ชัน `extractLegacyDwrLinks()` / `extractLegacyDrqLinks()` ของ Apps Script สั่ง `clear()` แล้วสร้างคิวใหม่ทั้งหมดเป็น `Waiting` **ทุกรายการ** - รวมถึง PDF ที่อยู่ใน DB_DWR / DB_DRQ แล้ว - และล้างสถานะ `Done` / `Error…` เดิมทั้งหมด
> สคริปต์ Python รับมือเรื่องนี้ได้: แถว `Waiting` ที่ PDF อยู่ใน DB แล้ว (เทียบด้วย Drive file ID ในคอลัมน์ PDF Link) จะถูกตั้งเป็น `Done` โดยไม่เรียก AI ส่วน `--extract` ทำแบบเดียวกันเฉพาะเมื่อใส่ `--overwrite` ถ้าไม่ใส่ จะ **sync** คิวที่มีแถวอยู่แล้ว: คำนวณทุกแถวใหม่จากชีตเก่าและคงสถานะของแถวเดิมไว้ (ดู "สร้างคิว") ควรสำรองคอลัมน์ G (และ DB) ไว้ก่อนใช้ `--overwrite` เสมอ

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

## สร้างคิว: `--extract`

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
python migrate_queue.py --extract --dry-run     # อ่านชีตเก่า แสดงจำนวนและแถวตัวอย่าง ไม่เขียนอะไร
python migrate_queue.py --extract               # สร้างทั้งสองคิว
python migrate_queue.py --extract --queue drq   # เฉพาะคิวเดียว (ตั้งแค่ตัวแปรชื่อแท็บของคิวนั้นก็พอ)
```

- `--extract` ไม่ต้องใช้ `GEMINI_API_KEY` และไม่แตะ Drive ไม่สนใจ `--limit` และใช้ร่วมกับ `--only-rows` / `--check-access` ไม่ได้
- คอนโซลแสดงต่อคิวว่าพบกี่รายการไม่ซ้ำ กี่รายการไม่มีลิงก์ PDF (แถวเหล่านั้นจะเป็น `Error: No URL` ตอนย้าย) และกี่ช่องที่ข้ามเพราะหัวคอลัมน์ว่าง
- **Sync (ค่าเริ่มต้นเมื่อคิวมีแถวอยู่แล้ว):** `--extract` **คำนวณทุกแถวของคิวใหม่จากชีตเก่า** (Doc No, Date, Discipline, PDF URL, Folder URL, C/P เรียงตามลำดับของชีตเก่า) แล้วเทียบกับคิวตอนนี้ ถ้ารายการเดียวกันมีแถวอยู่แล้ว แถวใหม่จะ**คงสถานะของแถวเดิม** (คอลัมน์ G: `Done`, `Error…`, `Waiting`) ส่วนคอลัมน์อื่นเป็นไปตามชีตเก่า **ข้อยกเว้น: แถว `Done` ที่ PDF (Drive file ID) ในชีตเก่าเปลี่ยน จะกลับเป็น `Waiting`** และจด file ID ของ PDF เก่าไว้ในคอลัมน์ H (`Replaces PDF (old file ID)`) เพื่อให้ migrate รอบถัดไปอ่านไฟล์ใหม่และแทนที่แถวใน DB ที่ใช้ไฟล์เก่า (ดู "ทำให้ PMIS sync กับชีตเก่า") ลิงก์ที่แค่เปลี่ยนรูปแบบ (file ID เดียวกัน) ไม่นับเป็น PDF ใหม่ รายการที่ยังไม่มีแถวจะเป็นแถว `Waiting` ใหม่ แถวเดิมที่รายการของมันไม่อยู่ในชีตเก่าแล้ว**จะถูกลบออก** คิวจึงมีเนื้อหาเท่ากับชีตเก่าเสมอ (คอนโซลเตือนว่าแถวที่ถูกลบเคยเป็น `Done` / `Error…` กี่แถว) ถ้าไม่มีอะไรเปลี่ยนจะไม่เขียนอะไร จึงรันตามเวลาได้อย่างปลอดภัย `--dry-run` แสดงแผน (`N kept (M changed, R renumbered, S set back to Waiting), +K new, D duplicate rows merged, X rows ... removed; statuses kept: ...`) การเขียนทำทับในที่เดิมเฉพาะคอลัมน์ A-H (ไม่ล้างชีตก่อน ถ้าพลาดกลางทางจึงไม่ว่าง และเวลาในคอลัมน์ `I` / `K` ไม่ถูกแตะ) **ตัวกันพลาด:** ถ้าจะลบแถวมากกว่าเพดาน (ค่าที่มากกว่าระหว่าง 10% ของแถวทั้งหมดกับ 100) จะปฏิเสธ เพื่อกันกรณีชีตเก่าว่างผิดปกติ ใช้ `--allow-large-removal` ถ้าต้องการให้ผ่าน
- **การหาแถวเดิมของรายการ:** ด้วย `(Doc No, Date)` เดียวกัน หรือ (กรณีที่แค่ `No.` (คอลัมน์ A) ของชีตเก่าเปลี่ยน) ด้วย **Date + Discipline + ค่าในช่อง** เดียวกัน (`Doc No` = `No.` + `-` + Discipline + `-` + ค่าในช่อง ส่วนหลัง `No.` จึงไม่เปลี่ยน) แถวใหม่จะมี `Doc No` ใหม่ และคอนโซลแสดง `N renumbered` (`--dry-run` แสดง `sample (renumbered): row 5: เดิม -> ใหม่`) ถ้ารายการเดียวมี**หลายแถว** (เช่นเข้าคิวสองครั้งด้วย `Doc No` เก่าและใหม่) จะรวมเป็นแถวเดียวที่คงสถานะของแถวที่ประมวลผลแล้ว (`Done` ก่อน `Error…` ก่อน `Waiting` ถ้าเท่ากันเอาแถวที่อยู่ก่อน) ถ้า **Date** เปลี่ยนด้วย จะถือเป็นรายการใหม่
- **สร้างใหม่:** ใส่ `--overwrite` เพื่อล้างคิวแล้วสร้างทุกแถวเป็น `Waiting` เหมือน Apps Script (ล้างสถานะ `Done` / `Error…` ทั้งหมด สำรองคอลัมน์ G ก่อน) แท็บคิวที่ยังไม่มีหรือว่างจะสร้างใหม่เลย ถ้าหัวตารางของแท็บไม่ใช่ `Doc No | Date | Discipline | PDF URL | Folder URL | Status(C/P) | AI Processed` `--extract` จะไม่เขียนอะไรและจบด้วยรหัส 2 จนกว่าจะแก้หรือใส่ `--overwrite`
- **เวลา refresh:** ทุกครั้งที่ extract จริง (ไม่ใช่ `--dry-run`) และเขียนคิวแล้ว `I1` = `Last extract (refresh time)` และ `I2` = เวลาเป็นเวลาไทย (UTC+7) เช่น `2026-10-22 14:05:33 (UTC+7)` ชีตจึงบอกได้ว่า refresh ล่าสุดเมื่อไหร่ คอลัมน์ I อยู่นอกข้อมูลคิว (A-G)
- **แท็บเก่าอยู่คนละไฟล์:** ค่าเริ่มต้นคือแท็บเก่าอยู่ในสเปรดชีตเดียวกับคิว ถ้าจะอ่านจากไฟล์อื่น ใส่ `--legacy-spreadsheet <ID หรือ URL>` หรือตั้ง `PMIS_LEGACY_SPREADSHEET_ID` คิวยังเขียนที่ `PMIS_SPREADSHEET_ID` เหมือนเดิม แชร์ไฟล์นั้นให้ service account (แค่ Viewer ก็พอ)
- **`.env`:** ตัวแปรทั้งหมดข้างบนเขียนไว้ในไฟล์ `.env` ข้างสคริปต์ได้ (ดู `.env.example`) ตัวแปรที่ตั้งไว้ในสภาพแวดล้อมแล้วมีสิทธิ์เหนือกว่า
- ต่างจาก Apps Script: ค่าในคิวเขียนเป็นข้อความล้วน (`RAW`) วันที่ ตัวเลข และเลข 0 นำหน้าจึงไม่ถูกแปลง ล้างเฉพาะค่าในชีตคิว (รูปแบบเดิมยังอยู่) และเพิ่มแถวด้วย append ชีตจึงขยายเอง
- service account ต้องมีสิทธิ์ **Editor** ในสเปรดชีต (อ่านแท็บเก่า เขียนแท็บคิว) ไฟล์ PDF ไม่ถูกแตะ

## ทำให้ PMIS sync กับชีตเก่า

เป้าหมาย: ข้อมูลใน PMIS (คิวและ `DB_DWR` / `DB_DRQ`) ตรงกับชีตเก่าเสมอ สายงานคือ **ชีตเก่า -> (`--extract`) -> คิว -> (migrate) -> DB** และ `--sync-db`:

| การเปลี่ยนในชีตเก่า | สิ่งที่เกิดใน PMIS |
|---|---|
| มีรายการใหม่ | `--extract` เพิ่มแถว `Waiting` migrate รอบถัดไปเพิ่มแถวใน DB |
| `No.` (คอลัมน์ A) เปลี่ยน | `--extract` เปลี่ยน `Doc No` ของรายการเดิม (ไม่เกิดแถวซ้ำ) |
| PDF ของรายการที่เป็น `Done` เปลี่ยน | `--extract` ตั้งแถวกลับเป็น `Waiting` และจด file ID เก่าไว้ที่คอลัมน์ H migrate รอบถัดไปอ่านไฟล์ใหม่และ**แทนที่แถวเก่าใน DB ในที่เดิม** (ดูด้านล่าง) ถ้าพลาด แถวเก่าใน DB ยังอยู่และคอลัมน์ H ยังเก็บไว้ |
| ลิงก์ PDF ของรายการถูกลบออก | `--extract` เก็บแถวไว้ `--sync-db` ลบแถวใน DB ของ PDF นั้น (คัดลอกไป `_REMOVED_...` ก่อน) ถ้ามีลิงก์กลับมา จะ migrate ใหม่ (แถวใหม่ใน DB) |
| C/P เปลี่ยน | `--sync-db` อัปเดตคอลัมน์ C/P ของ DB (`T` ใน DB_DWR, `S` ใน DB_DRQ) |
| รายการถูกลบ | `--extract` ลบแถวในคิว และ `--sync-db` ลบแถวใน DB ที่เคย sync กับชีตเก่า (ป้าย `Synced`) และ PDF ไม่อยู่ในคิวแล้ว |

- **แทนที่ในที่เดิม:** แถวใน DB ที่ใช้ไฟล์เก่า (หาด้วย file ID เก่า) ถูกเขียนทับด้วยข้อมูลใหม่ในแถวเดิม คอลัมน์ที่ไม่ใช่ข้อมูลของ migration จะไม่ถูกแตะ: คอลัมน์ A (`CSC Ref. No`) และ `Ext1` / `Ext2` ที่ว่าง ถ้ามีแถวคิวอื่นที่ยังใช้ PDF เก่าอยู่ หรือไฟล์เก่าไม่อยู่ใน DB จะต่อท้ายเป็นแถวใหม่แทน คอลัมน์ H จะถูกล้างเมื่อแถวเป็น `Done`
- **`--sync-db`** (`python migrate_queue.py --sync-db` หรือ `--extract --sync-db` ที่รันทันทีหลังซิงก์แต่ละคิว ถ้ามี `--dry-run` จะใช้แถวตามแผน): ลบแถวใน DB ที่ PDF file ID ไม่อยู่ในคิวแล้ว **เฉพาะแถวที่ Status เป็น `AI Migrated` และมีป้าย `Legacy sync` เป็น `Synced`** คือแถวที่เคย sync กับชีตเก่าแล้วหายไปจากชีตเก่า (แถวที่คนพิมพ์เอง และแถวที่ไม่เคยมาจากชีตเก่า เช่นเอกสารที่ระบบอื่นเพิ่มเข้ามา จะไม่ถูกลบเด็ดขาด) และเก็บแถวที่ PDF รอการแทนที่ในคอลัมน์ H ไว้ **ก่อนลบจะคัดลอกแถวไปที่แท็บ `_REMOVED_DB_DWR` / `_REMOVED_DB_DRQ`** (เวลา, แท็บ, เลขแถวเดิม, เหตุผล และเซลล์เดิมทั้งหมด ถ้าไม่มีแท็บจะสร้างให้) จึงไม่มีอะไรหาย และอัปเดตคอลัมน์ C/P ให้ตรงกับคิวด้วย (ถ้า C/P ในคิวว่างจะไม่ล้างค่าใน DB ถ้า PDF เดียวกันมี C/P ต่างกันในคิวจะข้าม) ก่อนเขียนจะอ่านคอลัมน์ PDF อีกครั้ง ถ้ามีแถวถูกแทรกหรือลบระหว่างนั้นจะหยุดโดยไม่แก้อะไร
- **ตัวกันพลาด:** ถ้าคิวไม่มีแถว หรือจะลบแถวมากกว่าเพดาน (ค่าที่มากกว่าระหว่าง 10% ของแถวใน DB กับ 100) จะไม่เขียนอะไรลง DB นั้น (`--allow-large-removal` ปลดเพดาน และ `--dry-run` ไม่ล้มเพราะเรื่องนี้) แถวถูกลบออกจากชีตจริง ชีตอื่นที่อ้างแถวของ DB ด้วยตำแหน่งอาจเลื่อน ควรดูแผนด้วย `--dry-run` ก่อน
- **บน GitHub Actions:** workflow **Extract queue** รัน `--extract` และถ้า input `sync_db` เปิดอยู่ (ค่าเริ่มต้น) จะรัน `--sync-db` ต่อหลังแต่ละคิว ให้รันด้วย `mode = dry-run` ก่อน log แสดงแค่ตัวเลข
- **ป้าย `Legacy sync`:** แถวใน DB ที่ PDF อยู่ในคิว (= ชีตเก่า) ทุกแถวจะติดป้าย `Synced` ในคอลัมน์ถัดจาก C/P (**`U` ใน `DB_DWR`, `T` ใน `DB_DRQ`** หัวตาราง `Legacy sync`) **แถวกำพร้าไม่มีป้าย**: แถวที่ PDF ไม่อยู่ในคิวแล้ว, แถวที่ไม่ได้เขียนโดยเครื่องมือนี้ (Status ไม่ใช่ `AI Migrated` จึงไม่ถูกลบด้วย) และแถวที่ไม่มีลิงก์ PDF จะว่าง และป้ายที่ไม่เป็นจริงแล้วจะถูกล้าง ส่วนแถวที่ยังเป็นฉบับเก่ารอการแทนที่ (PDF ของมันอยู่ในคอลัมน์ H ของคิว) จะคงค่าที่มีอยู่ ป้ายยังเป็นความจำว่าแถวไหนมาจากชีตเก่า: **แถวที่ไม่เคยติดป้ายจะไม่ถูกลบ** เอกสารที่ระบบอื่นเพิ่ม หรือแถวที่ชีตเก่าไม่มี จึงอยู่ต่อ `--sync-db` รอบแรกจึงแค่ติดป้าย (ไม่ลบอะไร) และจะลบเฉพาะแถวที่เคยติดป้ายแล้วหายไปจากชีตเก่า `--sync-db` ทำให้ป้ายถูกต้องทุกรอบ (เขียนเฉพาะเซลล์ที่เปลี่ยน รันรอบสองจึงไม่เขียนอะไร) ส่วนแถวที่ migrate เขียน (แถวใหม่และแถวที่แทนที่) จะติด `Synced` ทันทีที่เขียน ก่อนเขียนป้ายครั้งแรกจะเช็กหัวตารางเหมือนคอลัมน์ C/P: ถ้าว่างจะเติม `Legacy sync` ถ้าเป็นหัวอื่นจะหยุด DB นั้น แถวที่ถูกลบจะเก็บป้ายไว้ในสำเนาที่ `_REMOVED_...`
- **สิ่งที่คงไว้:** แถวใน DB ที่ PDF เดียวกัน (หรือ Doc No เดียวกัน) มีหลายแถวจะไม่ถูกแตะ ลบเฉพาะ PDF ที่เคย sync แล้วไม่อยู่ในคิวแล้ว PDF ในคอลัมน์หลัง `BD` / `BF` ที่ `--extract` ไม่อ่านจึงไม่อยู่ในคิว แถวของมันจะถูกลบก็ต่อเมื่อเคยติดป้ายมาก่อน

## C/P ใน DB: คอลัมน์ `T` (DWR) / `S` (DRQ) และ `--backfill-cp`

ทุกแถวที่ migration เขียนลง `DB_DWR` / `DB_DRQ` จะมีค่า **C/P** ของแถวในคิว (คอลัมน์ F `Status(C/P)`) ติดไปด้วย คัดลอกตามเดิม ลงที่ **คอลัมน์ T ของ `DB_DWR`** และ **คอลัมน์ S ของ `DB_DRQ`** (หัวตาราง `Status (C/P)` ทั้งคู่) ตามด้วยคอลัมน์ป้าย `Legacy sync` (`U` ใน `DB_DWR`, `T` ใน `DB_DRQ` ดู "ทำให้ PMIS sync กับชีตเก่า") คอลัมน์เดิมไม่ขยับ: `DB_DWR` ยังเป็น A-S (`Folder Link` คือ S) ส่วน `DB_DRQ` คอลัมน์ใหม่อยู่ต่อจาก `Folder Link` (P) และ `Ext1` / `Ext2` (Q, R)

- ก่อนเขียนแถวใหม่แถวแรก สคริปต์จะดูเซลล์หัวตาราง (`T1` ใน `DB_DWR`, `S1` ใน `DB_DRQ`) ของแท็บ DB: ถ้าว่างจะเติม `Status (C/P)` ให้ ถ้าเป็นหัวเดียวกันก็คงไว้ **ถ้าเป็นหัวอื่นจะหยุดคิวนั้น** (`✗ Could not process queue ...`) เพื่อไม่ให้ค่าไปลงใต้คอลัมน์อื่น
- แถวที่อยู่ใน DB แล้วยังไม่มี C/P เติมได้ด้วย `--backfill-cp` (ไม่เรียก Drive / Gemini ไม่ต้องใช้ `GEMINI_API_KEY`):

```powershell
python migrate_queue.py --backfill-cp --dry-run     # ดูแผน: จะเติมกี่เซลล์ (C / P) และจับคู่ไม่ได้กี่แถว
python migrate_queue.py --backfill-cp               # เติมทั้งสองแท็บ DB
python migrate_queue.py --backfill-cp --queue dwr   # เฉพาะแท็บเดียว
```

  แต่ละแถวใน DB จะจับคู่กับแถวในคิวที่ใช้ **PDF เดียวกัน** (Drive file ID ในคอลัมน์ PDF Link) เซลล์ที่มี C/P อยู่แล้วจะไม่ถูกแก้ แถวที่ PDF ไม่อยู่ในคิวจะปล่อยว่าง และ PDF ที่ในคิวมี C/P **ต่างกัน** (เช่นแถวหนึ่งเป็น C อีกแถวเป็น P) จะข้ามไม่เดา คอนโซลรายงานจำนวนของแต่ละกรณี สั่งซ้ำได้: จะเติมเฉพาะเซลล์ที่ยังว่าง ใช้ร่วมกับ `--extract`, `--only-rows` หรือ `--check-access` ไม่ได้

## `--check-access` เช็คอะไร

สำหรับแต่ละแถวที่รอทำ จะดึง file ID จากคอลัมน์ PDF URL แล้วขอ metadata ของไฟล์จาก Drive (ชื่อ, ชนิด, ขนาด, อยู่ในถังขยะหรือไม่) ด้วยสิทธิ์ของ service account
รายงานแถวที่ `No URL`, `Drive Access` (404/403: ไม่มีไฟล์ หรือยังไม่ได้แชร์ให้ service account) หรือไฟล์อยู่ในถังขยะ

ค่าเริ่มต้นตรวจทั้งสองคิวต่อกัน (ใส่ `--queue dwr` หรือ `--queue drq` ถ้าอยากตรวจแค่คิวเดียว)
ทุกบรรทัดของผลลัพธ์ขึ้นต้นด้วยชื่อชีตคิว เช่น `[MIGRATION_QUEUE] Reachable: 2/3 files` หรือ `✗ [MIGRATION_QUEUE_DRQ] row 4: Drive Access | …` และถ้าตรวจคิวเดียว บรรทัดสุดท้ายจะบอกคำสั่งสำหรับตรวจคิวอีกอัน

**ไม่** ดาวน์โหลด PDF, **ไม่** เรียก Gemini (ไม่เสียเงิน), **ไม่** เขียนชีต, ไม่เช็กว่าเป็น PDF จริง และไม่ทดสอบสิทธิ์เขียนชีต
แถวที่ PDF ใหญ่กว่า `--max-mb` จะแสดงบรรทัด `ℹ` ว่าจะส่งเฉพาะภาพหน้าปก (ดู "พฤติกรรมที่ควรรู้")
ค่าเริ่มต้น `--limit` คือ 30 จึงต้องใส่ `--limit 0` ถ้าอยากเช็กทุกแถว

## รันบน GitHub Actions

workflow สองตัวรันสคริปต์ตัวเดียวกันบน runner ของ GitHub จึงไม่ต้องเปิดคอมค้างไว้: `.github/workflows/migrate.yml` (**Migrate queue**: PDF -> Gemini -> DB) และ `.github/workflows/extract.yml` (**Extract queue**: สร้าง/sync คิวจากชีตเก่า คือ `--extract`) repo แบบ private บนแพ็กเกจ GitHub Free ได้ Actions 2,000 นาทีต่อเดือน

ตั้งค่าครั้งเดียว:

1. สร้าง repo แล้ว push โฟลเดอร์นี้ขึ้นไป แนะนำแบบ **private**: workflow แสดงแค่ตัวเลขสรุป (`--public-log`) แต่ถ้า repo เป็น public ใครก็เปิดดู log และสรุปผลการรันได้
2. เพิ่ม repository secrets 2 ตัวและ variable 1 ตัว (Settings > Secrets and variables > Actions ID สเปรดชีตเป็นแค่ค่าตั้ง จึงใส่ในแท็บ **Variables**) หรือใช้ GitHub CLI ใน PowerShell:

```powershell
Get-Content C:\path\to\secrets\sa-key.json -Raw | gh secret set SA_KEY_JSON   # เนื้อหาไฟล์คีย์ทั้งไฟล์
gh secret set GEMINI_API_KEY                                                    # จะถามค่า
gh variable set PMIS_SPREADSHEET_ID --body "<ID สเปรดชีต>"                        # เป็น variable ไม่ใช่ secret (secret ชื่อเดียวกันยังใช้เป็นตัวสำรองได้)
```

3. เตรียมเหมือนรันในเครื่อง: ปิด trigger ของ Apps Script และแชร์ชีตกับ PDF ให้อีเมล service account
4. เฉพาะ **Extract queue** ให้เพิ่มค่าตั้งของชีตเก่าเป็น **variables** ของ repo (Settings > Secrets and variables > Actions > Variables เป็นแค่ค่าตั้ง ไม่ใช่ความลับ และไม่ต้องใช้ `GEMINI_API_KEY`): `PMIS_LEGACY_DWR_SHEET`, `PMIS_LEGACY_DRQ_SHEET` (ชื่อแท็บเป๊ะๆ) และถ้าต้องการ `PMIS_LEGACY_SPREADSHEET_ID` (ไฟล์ที่เก็บแท็บเหล่านั้น ถ้าไม่ใช่สเปรดชีตเป้าหมาย แชร์ให้ service account ด้วย) ถ้าใช้ GitHub CLI: `gh variable set PMIS_LEGACY_DWR_SHEET --body "<ชื่อแท็บ>"` secret ชื่อเดียวกันยังใช้เป็นตัวสำรองได้ และ variable มาก่อน

วิธี migrate: แท็บ Actions > **Migrate queue** > Run workflow แล้วเลือก `mode` เริ่มจาก `dry-run` ตามด้วย `check-access` แล้ว `run` ด้วย `limit` น้อยๆ ก่อนค่อยเพิ่ม ถ้าใช้บรรทัดคำสั่ง: `gh workflow run migrate.yml -f mode=dry-run`

วิธีสร้างคิว: แท็บ Actions > **Extract queue** > Run workflow ด้วย `mode = dry-run` (อ่านแท็บชีตเก่า ไม่เขียนอะไร) แล้วค่อย `mode = extract` ช่อง `queue` เลือก DWR / DRQ / ทั้งสอง, `legacy_spreadsheet` ชี้ไปไฟล์อื่น, `sync_db` (เปิดเป็นค่าเริ่มต้น) ทำให้ DB_DWR / DB_DRQ ตรงกับคิวหลังแต่ละคิวด้วย, `allow_large_removal` ปลดเพดานตัวกันพลาด, `overwrite` สร้างใหม่ทั้งคิวแทนการ sync (รันตามเวลาอย่าเปิดตัวนี้) ถ้าใช้บรรทัดคำสั่ง: `gh workflow run extract.yml -f mode=dry-run`

- unit test (job `test`) จะรันก่อน ผ่านแล้ว migrate / extract ถึงจะเริ่ม
- ไม่มีสองรอบรันซ้อนกัน แม้แต่ migrate กับ extract (ใช้ `concurrency` กลุ่มเดียวกัน) จึงไม่ทำแถว `Waiting` เดียวกันซ้ำ
- คีย์ service account ถูกเขียนเป็นไฟล์ชั่วคราวเฉพาะช่วงที่รัน
- workflow เรียกสคริปต์ด้วย `--public-log`: log และสรุปผลการรันแสดงแค่ตัวเลข (สถานะคิว, ยอด Done/Error, สาเหตุที่หยุด) ไม่มีชื่อแท็บ ชื่อ/ID ชีต Doc No file ID หรือรายละเอียด error และซ่อน ID สเปรดชีตกับอีเมล service account อีกชั้น สิ่งที่สคริปต์เขียนลง stderr ไปอยู่ในไฟล์ที่ไม่เคยถูกพิมพ์ รายละเอียดรายแถวดูได้ที่คอลัมน์ G ของชีตคิว ถ้าต้องการ log เต็มให้รันในเครื่อง (ไม่ใส่ `--public-log`)
- workflow ไม่มี `schedule:` (cron ของ GitHub เองไม่เริ่มรันให้) ถ้าจะรันอัตโนมัติ ให้เรียก workflow_dispatch API จากตัวตั้งเวลาภายนอก เช่น cron-job.org: `POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/migrate.yml/dispatches` (หรือ `extract.yml`) ด้วย body `{"ref":"main","inputs":{"mode":"run","limit":"30"}}` (หรือ `{"ref":"main","inputs":{"mode":"extract"}}`) และ token แบบ fine-grained สิทธิ์ **Actions: Read and write**
- ส่วนที่ใช้ร่วมกันอยู่ใน `.github/scripts/` (`with_credentials.sh` เขียนคีย์เป็นไฟล์ชั่วคราวแล้วลบทิ้ง, `job_summary.sh`) และ `.github/actions/setup-python-deps/`

## พฤติกรรมที่ควรรู้

- **เวลา migration ล่าสุด:** ทุกครั้งที่รันจริง (ไม่ใช่ `--dry-run` / `--check-access`) จะเขียนเวลาที่จบลงชีตคิวที่ประมวลผล: `K1` = `Last migration run (finish time)` และ `K2` = เวลาเป็นเวลาไทย (UTC+7) เช่น `2026-10-22 14:05:33 (UTC+7)` เขียนให้ทุกคิวที่ถูกรัน (คิวที่ถูกข้ามเพราะรอบหยุดก่อนจะไม่ถูกแตะ) และเขียนแม้รอบนั้นไม่มีแถวให้ทำ อยู่ข้างเวลา refresh ของ `--extract` (`I1:I2`) นอกข้อมูลคิว (คอลัมน์ A-G) ถ้าเขียนเซลล์ไม่ได้ (เช่นแท็บมีไม่ถึง 11 คอลัมน์) ผลการรันไม่เปลี่ยน แค่เตือน
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

## โครงสร้างโปรเจกต์

โค้ดแยกตามหน้าที่ `migrate_queue.py` เป็นแค่จุดเริ่มต้น คำสั่งทุกอันในคู่มือนี้จึงใช้เหมือนเดิม และรันด้วย `python -m migrator` ได้ด้วย

```text
migrate_queue.py        entry point (python migrate_queue.py ...)
migrator/
  cli.py                options, building the clients, main()
  public_log.py         --public-log: only the lines that are safe in a public log
  settings.py           settings from the environment / .env
  runner.py             choosing the rows + the main loop
  processing.py         one row: Drive PDF -> Gemini -> DB row
  sheets.py drive.py gemini.py     the three APIs
  reports.py            parsing Gemini's JSON, building the DB rows
  extract.py            --extract (legacy_sheet.py reads, queue_writer.py writes / syncs)
  db_sync.py            --sync-db (remove DB rows not in the queue, update C/P) and replacing a DB row in place
  cp_column.py          the C/P column of the DB tabs (T in DB_DWR, S in DB_DRQ): header check, --backfill-cp
  stamps.py             the refresh time (I1:I2) and the migration time (K1:K2) in the queue sheet
  specs.py config.py models.py utils.py clock.py
tests/                  one test file per module + fakes.py / legacy_fakes.py
```

## เทสต์

```powershell
python -m pip install pytest
python -m pytest -q
```
