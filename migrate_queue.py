#!/usr/bin/env python3
"""

migrate_queue.py  -  옛 진입점 / [TH] จุดเริ่มต้นเก่า / [EN] the old entry point

지금의 진입점은 pmis.py 입니다 (python pmis.py --help). 이 파일은 예전 명령이 계속 동작하도록 남겨 둔 것으로,
옛 옵션을 새 명령으로 바꿔 실행하고 새 명령을 stderr 로 알려 줍니다:
[TH] จุดเริ่มต้นปัจจุบันคือ pmis.py (python pmis.py --help) ไฟล์นี้เก็บไว้ให้คำสั่งเก่ายังใช้ได้ โดยแปลออปชันเก่าเป็นคำสั่งใหม่แล้วรัน พร้อมบอกคำสั่งใหม่ทาง stderr:
[EN] The entry point of today is pmis.py (python pmis.py --help). This file is kept so that the old commands keep working: it translates the old options
     into the new command, runs it, and says the new command on stderr:

  python migrate_queue.py                              ->  python pmis.py sync db            (the old default was the AI; the db step also fixes the DB rows first - add --no-ai to skip the AI)
  python migrate_queue.py --extract                    ->  python pmis.py sync queue
  python migrate_queue.py --extract --sync-db          ->  python pmis.py sync queue db --no-ai
  python migrate_queue.py --sync-db                    ->  python pmis.py sync db --no-ai
  python migrate_queue.py --check-access               ->  python pmis.py check access
  python migrate_queue.py --backfill-cp                ->  removed: python pmis.py sync db --no-ai fills and updates C/P

그 밖의 옵션(--queue, --dry-run, --limit, ...)은 그대로 전달됩니다. / [TH] ออปชันอื่น (--queue, --dry-run, --limit, ...) ส่งต่อตามเดิม / [EN] The other options (--queue, --dry-run, --limit, ...) are passed on unchanged.
"""
from migrator.cli import old_entry

if __name__ == "__main__":
    old_entry()
