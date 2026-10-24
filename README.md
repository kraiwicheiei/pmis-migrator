# pmis-migrator - keep PMIS in sync with the legacy sheets

**Language / ภาษา / 언어:** [English](README.md) · [ไทย](README.th.md) · [한국어](README.ko.md)

Replaces `runDwrAiMigrationTurbo` / `runDrqAiMigrationTurbo` in `Code_Migration.js`. It uses the same prompts, the same model
(`gemini-3.8-flash`) and the same DB_DWR / DB_DRQ column order, but runs on your own PC, so the Apps Script UrlFetch quotas no longer apply.
It follows the current Apps Script (`Code_Migration.js`, and `extractDriveFileForAI` in `Code_Core.js` for large PDFs).
It also replaces `extractLegacyDwrLinks` / `extractLegacyDrqLinks`, which build the queues (see `sync queue`).

One command keeps PMIS in sync with the legacy sheets: **`python pmis.py sync`** = legacy sheet -> queue -> `DB_DWR` / `DB_DRQ`. It has two steps, which can also be run alone (`sync queue`, `sync db`).

## Overview: who does what

| Step | Command | What it does |
|---|---|---|
| `queue` | `python pmis.py sync queue` | Keeps `MIGRATION_QUEUE` / `MIGRATION_QUEUE_DRQ` the same as the legacy `<legacy DWR sheet>` / `<legacy DRQ sheet>` sheets. A **new item or a changed PDF becomes a `Waiting` row**, so that the AI reads it (replaces `extractLegacyDwrLinks()` / `extractLegacyDrqLinks()` in Apps Script) |
| `db` | `python pmis.py sync db` | Makes `DB_DWR` / `DB_DRQ` follow the queue. **Part 1 (no AI):** C/P, Folder Link, the `Legacy sync` tag (`Synced` / `Outdated`), and removing the rows that left the legacy sheet. **Part 2 (the AI):** download the PDF of every `Waiting` row -> Gemini -> write the DB row -> mark `Done` (replaces `runDwrAiMigrationTurbo` / `runDrqAiMigrationTurbo`) |

`python pmis.py sync` runs the two steps in this order. All of it runs in **this Python script**; nothing is left to Apps Script. Each step writes only its own sheets: `queue` writes the queue tab, `db` writes the DB tabs (and the status columns of the queue when the AI is done with a row).

> **Rebuilding a queue:** the Apps Script functions `extractLegacyDwrLinks()` / `extractLegacyDrqLinks()` `clear()` the queue and rebuild **every** item as `Waiting` - including PDFs that are already in DB_DWR / DB_DRQ - and every `Done` / `Error…` status is wiped. `sync queue` does that only with `--overwrite`. Without it, `sync queue` **syncs** a queue that already has rows: every row is recomputed from the legacy sheet and the status of the existing row is kept (see "Step queue").
> After an `--overwrite` the AI part of `sync db` covers for the rebuild: a `Waiting` row whose PDF is already in the DB (matched by the Drive file ID in the PDF Link column) is marked `Done` without calling the AI. Always back up column G (and the DB) before using `--overwrite`.

## Install (once) - PowerShell

```powershell
cd C:\path\to\pmis-migrator
python -m pip install -r requirements.txt
```

`python -m pip` installs the packages for the same Python that runs the script, which avoids `No module named 'requests'`.
If you prefer a virtual environment: `python -m venv .venv`, then `.\.venv\Scripts\Activate.ps1` (you must activate it **in every new window**).
If PowerShell says "running scripts is disabled on this system", run `Set-ExecutionPolicy -Scope Process Bypass` first.

## Before you run

1. **Disable the triggers** for `runDwrAiMigrationTurbo` / `runDrqAiMigrationTurbo` in Apps Script (so the two sides do not write at the same time). The current Apps Script writes the whole status column G back from a snapshot it took when its run started, so a run that overlaps with Python can overwrite the statuses Python has just written.
2. Share the spreadsheet "<your-spreadsheet-name>" with the service-account e-mail as **Editor**.
3. Share the PDF files (or the folder that holds all of them) with the same e-mail as **Viewer**.
4. Set the environment variables. Keep `sa-key.json` outside the repo folder, e.g. `C:\path\to\secrets\`.

```powershell
$env:GOOGLE_APPLICATION_CREDENTIALS = "C:\path\to\secrets\sa-key.json"
$env:GEMINI_API_KEY = "your-key-here"
$env:PMIS_SPREADSHEET_ID = "your-sheet-id"   # required (the part of the sheet URL between /d/ and /edit)
```

A value set with `$env:` lives only in that PowerShell window: set it in the same window where you run `python pmis.py`, and again in every new window.
To make it permanent use `setx GEMINI_API_KEY "..."` (then open a new window); the value is stored unencrypted in the user registry.
`--dry-run`, `check access`, `sync queue` and `sync db --no-ai` do not use `GEMINI_API_KEY`, so you do not need it for them. Only the AI part of `sync db` / `sync` does.

## The commands

```text
python pmis.py sync [queue] [db]   keep PMIS in sync with the legacy sheets; name the steps you want, none = both
python pmis.py sync db --no-ai     the db step without the AI: fix the DB rows only (no Gemini key needed)
python pmis.py check access        can the service account open the PDF files? (nothing is written, no Gemini calls)
python pmis.py <command> --help    the options of a command
```

`sync` is a chain of two steps. **They always run in this order - queue, db - whatever order you type them in** (`sync db queue` = `sync queue db`):

| Step | What it does | Needs |
|---|---|---|
| `queue` | legacy sheet -> queue: recompute every row of `MIGRATION_QUEUE` / `MIGRATION_QUEUE_DRQ` from the legacy sheet, keeping the status of the rows that already exist. A new item or a changed PDF becomes `Waiting` | `PMIS_LEGACY_DWR_SHEET` / `PMIS_LEGACY_DRQ_SHEET`; no Gemini key |
| `db` | queue -> DB, in two parts (below) | part 1: nothing special; part 2: `GEMINI_API_KEY` (not for `--dry-run`) |

The two parts of the `db` step:

| Part | What it does | Left out by |
|---|---|---|
| 1 - update the DB rows (no AI) | the C/P and the Folder Link of the existing rows follow the queue; the `Legacy sync` tag is set (`Synced` / `Outdated`); the rows that came from the legacy sheet and left it are removed (copied to `_REMOVED_...` first) | `--only-rows` |
| 2 - the AI | the PDFs of the `Waiting` (and `Error…`) rows are read with Gemini and written to `DB_DWR` / `DB_DRQ`; a row whose PDF changed replaces its old DB row in place | `--no-ai` |

Part 1 goes first, so its work is done even when Gemini stops (no credits, bad key) or the key is missing. `--no-ai` runs part 1 only (then no Gemini key is needed). `--only-rows` retries those rows with the AI and does nothing else.

Put the steps right after `sync` and the options after them. The options of each command (`python pmis.py sync --help` lists them by step):

| Option | Used by |
|---|---|
| `--queue dwr` / `--queue drq` (default: both), `--dry-run`, `--public-log`, `--sa-key` | every command |
| `--limit`, `--batch`, `--workers`, `--only-rows`, `--no-retry-errors`, `--include-blank`, `--no-reconcile`, `--max-mb`, `--parse-retries`, `--accept-first-report`, `--max-output-tokens`, `--no-thumbnail`, `--model`, `--api-version` | the AI part of `sync db`, and `check access` |
| `--no-ai` | step `db` |
| `--legacy-spreadsheet`, `--overwrite` | step `queue` |
| `--allow-large-removal` | steps `queue` and `db` |

When both steps run (`sync`, or `sync queue db`):

- each step starts with a title line (`▶ Step 1/2 - sync queue: legacy sheet -> queue`) and the last line sums up the result (`Sync steps: queue OK, db OK`);
- a queue whose `queue` step failed, was refused by the safety net or built nothing is **left out of the `db` step** (the log says which); a part that fails does **not** stop the other, but the exit code is 2;
- with `--dry-run` nothing is written and the `db` step shows its plan **on the queue the `queue` step planned** (for example the rows it would add), so `sync --dry-run` is a preview of the whole chain;
- in a real run the `db` step reads the queue sheet again (what the `queue` step wrote, and anything somebody changed in between).

**The old commands.** `migrate_queue.py` was replaced by `pmis.py`. The old file still works: it translates the old options into the new command and prints the new command on stderr. `pmis.py` itself refuses the old options and says what to type instead.

| Old | New |
|---|---|
| `python migrate_queue.py` (it was the AI) | `python pmis.py sync db` (its part 1 fixes the DB rows first, then the AI part reads the PDFs) |
| `python migrate_queue.py --extract` | `python pmis.py sync queue` |
| `python migrate_queue.py --extract --sync-db` | `python pmis.py sync queue db --no-ai` |
| `python migrate_queue.py --sync-db` | `python pmis.py sync db --no-ai` |
| `python migrate_queue.py --check-access` | `python pmis.py check access` |
| `python migrate_queue.py --backfill-cp` | removed: `python pmis.py sync db --no-ai` fills and updates C/P (and does the rest of part 1) |

The other options (`--queue`, `--dry-run`, `--limit`, ...) are unchanged.

## Recommended order

```powershell
python pmis.py sync --dry-run                           # the plan of the whole chain: what changes in the queue and in the DB, which rows the AI would read (nothing is written)
python pmis.py check access --limit 0                   # check that the service account can open every PDF, in both queues
python pmis.py sync queue                               # legacy sheet -> queue (new items and changed PDFs become Waiting)
python pmis.py sync db --no-ai --dry-run                # part 1 only: how many DB rows would change (no Gemini key needed)
python pmis.py sync db --no-ai                          # part 1 for real
python pmis.py sync db --limit 5                        # real test of the AI: up to 5 rows from EACH queue; compare with rows the old script marked Done
python pmis.py sync db --queue dwr --only-rows 83,102,127-128   # retry the rows that failed individually before (needs --queue; only the AI runs)
python pmis.py sync db --limit 200                      # run in rounds; check the result, then raise it (0 = everything)
python pmis.py sync                                     # the whole chain for real
```

**Which queues:** without `--queue` the script processes **both** queues, DWR first (`MIGRATION_QUEUE` -> `DB_DWR`) and then DRQ (`MIGRATION_QUEUE_DRQ` -> `DB_DRQ`); `--limit` applies **per queue**. Use `--queue dwr` or `--queue drq` to process only one.
`--only-rows` refers to row numbers of one particular queue sheet, so it requires `--queue` (and it works with `sync db` and `check access` only).
If the first queue stops early in the AI part (for example Gemini answers 402/401/403, or repeated 429), the second queue is skipped because it would stop for the same reason. If one queue sheet cannot be read (for example the tab is missing), the other queue is still processed. In both cases the exit code is 2.

The target sheet is set **only** through the `PMIS_SPREADSHEET_ID` environment variable. There is no default in the code and no command-line option, and the script stops with a message if the variable is missing or empty (in every mode, including `--dry-run`). The ID is the part of the sheet's URL between `/d/` and `/edit`: `https://docs.google.com/spreadsheets/d/<ID>/edit`.

## Step `queue`: build / sync the queues (`sync queue`)

Replaces `extractLegacyDwrLinks()` / `extractLegacyDrqLinks()`. It reads the legacy sheet through the Sheets API (values, hyperlinks, date formats) and writes `MIGRATION_QUEUE` / `MIGRATION_QUEUE_DRQ` with the same rules as Apps Script:

- The header is on row 3 and the data starts on row 4. A row whose Date (column B) is empty is skipped. The hyperlink on column B becomes `Folder URL`.
- Every non-empty cell from column E to **BD** (DWR) or **BF** (DRQ) is one item. Its column header without `(P)` / `(C)` is the `Discipline`; a `(C)` in the header makes `Status(C/P)` = `C`, otherwise `P`.
- `Doc No` = column A + `-` + Discipline + `-` + the cell value. The hyperlink on that cell is the `PDF URL`.
- The same Date + Discipline + cell value is one item. When a `(C)` cell repeats it, its link (if it has one) replaces the PDF URL and the item becomes `C`.
- Every row starts as `Waiting`.

The tab names of the legacy sheets are **not** in the code. Set them in the environment, exactly as they appear in the spreadsheet (brackets and spaces included):

```powershell
$env:PMIS_LEGACY_DWR_SHEET = "<DWR tab name>"
$env:PMIS_LEGACY_DRQ_SHEET = "<DRQ tab name>"
python pmis.py sync queue --dry-run     # read the legacy sheets, show the counts and sample rows; nothing is written
python pmis.py sync queue               # build both queues
python pmis.py sync queue --queue drq   # only one queue (then only its own tab-name variable is needed)
```

- `sync queue` needs neither `GEMINI_API_KEY` nor any Drive access and ignores `--limit`; it does not take `--only-rows`.
- For each queue the console shows how many unique items were found, how many have no PDF link (those rows become `Error: No URL` when migrated) and how many cells were skipped because their column header is empty.
- **Sync (the default when the queue already has rows):** `sync queue` **recomputes every row of the queue from the legacy sheet** (Doc No, Date, Discipline, PDF URL, Folder URL, C/P; the rows are in the legacy sheet's order) and compares the result with the queue as it is now. Where the same item already has a row, the new row **keeps that row's status** (column G: `Done`, `Error…`, `Waiting`) and every other column follows the legacy sheet. **Exception: a `Done` row whose PDF (the Drive file ID) changed in the legacy sheet goes back to `Waiting`**, and the file ID of the old PDF is written to column H (`Replaces PDF (old file ID)`), so that the AI part of the next `sync db` reads the new file and replaces the DB row that used the old one (see "Keeping PMIS in sync"). A link that only changed its format (same file ID) is not a new PDF. An item without a row becomes a new `Waiting` row. An existing row whose item is no longer in the legacy sheet **is removed**, so the queue always has the same content as the legacy sheet (the console warns how many removed rows were `Done` / `Error…`). Nothing is written when nothing changed, so it is safe to run on a schedule. `--dry-run` shows the plan as a small table (`Kept from the queue`, `New`, `Duplicate rows merged`, `Removed (not in the legacy sheet)`, `Statuses kept`). The writing is done in place over columns A-H only (the sheet is not cleared first, so a failure cannot leave it empty, and the times in `I` / `K` are untouched). **Safety net:** a run that would remove more rows than the limit (the larger of 10% of the rows and 100) is refused, which protects the queue when the legacy sheet is wrongly empty; `--allow-large-removal` lifts it.
- **Finding the existing row of an item:** by the same `(Doc No, Date)`, or - for the case where only the `No.` (column A) of the legacy sheet changed - by the same **Date + Discipline + cell value** (`Doc No` = `No.` + `-` + Discipline + `-` + the cell value, so the part after the `No.` stays the same). The new row then has the new `Doc No`, and the console shows `N renumbered` (`--dry-run` lists `sample (renumbered): row 5: old -> new`). If one item has **several rows** (for example it went in twice, under the old and under the new `Doc No`), they are merged into one that keeps the status of the processed row (`Done` over `Error…` over `Waiting`; on a tie the earlier row). If the **Date** changes as well, the item is a new one.
- **Rebuild:** add `--overwrite` to clear the queue and rebuild every row as `Waiting`, as Apps Script did (every `Done` / `Error…` status is wiped; back up column G first). A missing or empty queue tab is simply built from scratch. If the tab's header row is not `Doc No | Date | Discipline | PDF URL | Folder URL | Status(C/P) | AI Processed`, `sync queue` writes nothing and exits with code 2 until you fix it or add `--overwrite`.
- **Refresh time:** after every real (not `--dry-run`) `sync queue` that wrote the queue, `I1` = `Last extract (refresh time)` and `I2` = the time in Thai time (UTC+7), e.g. `2026-10-22 14:05:33 (UTC+7)`, so the sheet shows when it was last refreshed. Column I is outside the queue data (A-G).
- **Legacy tabs in another file:** by default the legacy tabs are in the same spreadsheet as the queues. To read them from another file, pass `--legacy-spreadsheet <ID or URL>` or set `PMIS_LEGACY_SPREADSHEET_ID`; the queues are still written to `PMIS_SPREADSHEET_ID`. Share that file with the service account (Viewer is enough).
- **`.env`:** all the variables above can be put in a `.env` file next to the script (see `.env.example`); variables already set in the environment win.
- Differences from Apps Script: the queue values are written as plain text (`RAW`), so dates, numbers and leading zeros are never auto-converted; only the values of the queue sheet are cleared (its formatting stays); rows are appended, so the sheet grows by itself.
- The service account needs **Editor** access to the spreadsheet (it reads the legacy tab and writes the queue tab). The PDFs are not touched.

## Keeping PMIS in sync with the legacy sheets

The goal: what is in PMIS (the queues and `DB_DWR` / `DB_DRQ`) always matches the legacy sheets. The chain is **legacy sheet -> (`sync queue`) -> queue -> (`sync db`) -> DB**, and `python pmis.py sync` runs all of it. `sync queue` only decides **which PDFs the AI has to read** (a new item or a changed PDF becomes `Waiting`); everything else that comes from the legacy sheet is kept right by `sync db`:

| A change in the legacy sheet | What happens in PMIS |
|---|---|
| a new item | `sync queue` adds a `Waiting` row; the AI part of the next `sync db` adds the DB row |
| the `No.` (column A) changed | `sync queue` renames the `Doc No` of the same item (no duplicate row) |
| the PDF of an item that is `Done` changed | `sync queue` sets the row back to `Waiting` and writes the old file ID to column H; `sync db` marks the old DB row `Outdated`, then its AI part reads the new file and **replaces the old DB row in place** (see below) and the row is `Synced` again; if that fails the old DB row stays `Outdated` and column H is kept |
| the PDF link of an item was cleared | `sync queue` keeps the row; part 1 of `sync db` removes the DB row of that PDF (copied to `_REMOVED_...` first). When a link comes back the item is read again (a new DB row) |
| the C/P or the folder link (the link on column B) changed | `sync queue` updates the queue row; part 1 of `sync db` updates the C/P / Folder Link of the DB row. No AI is used and the row is not set to `Waiting` |
| an item was removed | `sync queue` removes its queue row; part 1 of `sync db` removes the DB rows that came from the legacy sheet (tag `Synced` / `Outdated`) and whose PDF is not in the queue any more |

- **Who writes what:** `sync queue` writes only the queue tab. `sync db` writes the DB tabs, and the status columns of the queue (G `Done` / `Error…`, H, and the time in `K1:K2`) when the AI finishes a row. After a change in the legacy sheet the DB follows at the next `sync db`; `sync queue` alone never writes a DB tab, so a tag reflects the state at the last `sync db`.
- **Replace in place:** the DB row that used the old file (found by the old file ID) is overwritten with the new data, in the same row. Columns that are not data of the migration are not touched: column A (`CSC Ref. No`) and the empty `Ext1` / `Ext2`. If another queue row still uses the old PDF, or the old file is not in the DB, a new row is appended instead. Column H is cleared once the row is `Done`.
- **Part 1 of `sync db`** (`python pmis.py sync db --no-ai`; `sync` and `sync queue db` run it after the `queue` step - with `--dry-run` on the rows the `queue` step planned): for each DB row it finds the queue row of the **same PDF** (Drive file ID) and
  - updates the **C/P** and the **Folder Link** when the queue has exactly one value and it differs (an empty value in the queue never clears a DB value; a link in another format that points to the same Drive ID is not a change);
  - sets the **`Legacy sync` tag** (below);
  - **removes** the DB rows whose PDF is not in the queue any more, **only rows whose Status is `AI Migrated` and that carry the tag `Synced` or `Outdated`** - that is, rows that came from the legacy sheet and then left it (a row somebody typed in, and a row that never came from the legacy sheet, e.g. a document another system added, is never removed). It keeps the rows whose PDF waits in column H to be replaced. **Before a row is removed it is copied to the tab `_REMOVED_DB_DWR` / `_REMOVED_DB_DRQ`** (the time, the tab, the original row number, the reason and all the original cells; the tab is created when it is missing), so nothing is lost.
  Just before it writes, it reads the PDF column again and stops without changing anything if a row was inserted or removed in the meantime. A second run writes nothing when nothing changed.
- **Safety net:** if the queue has no rows, or more rows would be removed than the limit (the larger of 10% of the DB rows and 100), nothing is written to that DB (`--allow-large-removal` lifts the limit; a `--dry-run` never fails because of it). Rows are deleted from the sheet, so other sheets that refer to DB rows by position may shift: check the plan with `--dry-run` first.
- **In GitHub Actions:** see "Run on GitHub Actions": **Sync all** runs the whole chain, **Sync queue and DB (no AI)** runs `sync queue db --no-ai` (the `db` step is the input `db`, on by default) and **Sync DB (with AI)** runs `sync db`. Run them with `mode = dry-run` first: the log shows the counts only.
- **The `Legacy sync` tag:** the column right after C/P (**`U` in `DB_DWR`, `T` in `DB_DRQ`**, header `Legacy sync`) says whether a DB row matches the legacy sheet:

  | Tag | Meaning |
  |---|---|
  | `Synced` | the row came from the legacy sheet and was **checked**: its PDF is in the queue and its C/P and Folder Link match the queue (the rows the AI part writes get it as they are written) |
  | `Outdated` | the row came from the legacy sheet but does not match it: its PDF changed and it waits for the AI to replace it with the new file (the old PDF is in column H of the queue), or the queue has several different C/P / Folder Link values for the same PDF (the same file is used by several items) and the DB has none of them - nothing is guessed and overwritten |
  | empty | the row never came from the legacy sheet (e.g. a document another system added), was not written by this tool (Status is not `AI Migrated`, so it is not removed either), or has no PDF link |

  The tag is also the memory of which rows came from the legacy sheet: **a row that never was tagged is never removed**, so documents added by another system, or rows that the legacy sheet does not have, stay. The first `sync db` therefore only tags (nothing is removed); rows are removed only after they were tagged and then disappeared from the legacy sheet. Before the first tag is written the header cell is checked like the C/P one: an empty cell gets `Legacy sync`, any other header stops that DB. A removed row keeps its tag in the `_REMOVED_...` copy.
- **What stays as it is:** several DB rows with the same PDF (or the same Doc No) are left alone; only a PDF that came from the legacy sheet and is not in the queue any more is removed. PDFs in columns after `BD` / `BF`, which `sync queue` does not read, are not in the queue; their rows are removed only if they were tagged before.

## C/P and Folder Link in the DB

Every DB row carries the **C/P** value of its queue row (column F, `Status(C/P)`), copied as it is, in **column T of `DB_DWR`** and **column S of `DB_DRQ`** (header `Status (C/P)` in both), followed by the `Legacy sync` tag column (`U` in `DB_DWR`, `T` in `DB_DRQ`; see "Keeping PMIS in sync"). The **Folder Link** (column S of `DB_DWR`, column P of `DB_DRQ`) comes from the queue's `Folder URL`. The existing columns do not move: `DB_DWR` stays A-S (`Folder Link` is S), and in `DB_DRQ` the new column comes right after `Folder Link` (P) and `Ext1` / `Ext2` (Q, R).

- The AI part writes both values when it writes a row. After that part 1 of `sync db` keeps them right: a C/P or a Folder Link that differs from the queue is overwritten with the queue's value. This is also how the rows that were migrated before the C/P column existed get their C/P (what `--backfill-cp` did): run `python pmis.py sync db --no-ai --dry-run`, then `python pmis.py sync db --no-ai`.
- When the same PDF has several different values in the queue (the same file is used by several items) nothing is guessed: if the DB value is one of them the row is fine, if it is none of them the row is `Outdated` and nothing is overwritten.
- Before the first write, the script looks at the header cell (`T1` in `DB_DWR`, `S1` in `DB_DRQ`) of the DB tab: an empty cell is filled with `Status (C/P)`; the same header is kept; **any other header stops that queue** so the value is never written under another column. The same is done for the `Legacy sync` header.

## What `check access` checks

For every pending row it extracts the file ID from the PDF URL column and asks Drive for the file's metadata (name, type, size, trashed or not) using the service account.
It reports rows with `No URL`, `Drive Access` (404/403: the file does not exist, or it has not been shared with the service account), or a file in the trash.

By default both queues are checked, one after the other (`--queue dwr` or `--queue drq` checks just one).
Every output line starts with the queue sheet name, e.g. `[MIGRATION_QUEUE] Reachable: 2/3 files` or `✗ [MIGRATION_QUEUE_DRQ] row 4: Drive Access | …`. When you check only one queue, the last line gives the command for checking the other one.

It does **not** download the PDF, does **not** call Gemini (so it costs nothing), does **not** write to the sheet, does not verify that the file is a real PDF, and does not test write access to the sheet.
Rows whose PDF is larger than `--max-mb` are listed with an `ℹ` line saying that only the cover image will be sent (see "Good to know").
`--limit` defaults to 30, so pass `--limit 0` to check every row.

## Run on GitHub Actions

Three workflows run the same script on a GitHub-hosted runner, so no PC has to stay on:

| Workflow (Actions tab) | File | Runs | Needs |
|---|---|---|---|
| **Sync all** | `.github/workflows/sync.yml` | `python pmis.py sync` (queue -> db, with the AI) | `GEMINI_API_KEY`, the legacy variables |
| **Sync queue and DB (no AI)** | `.github/workflows/sync-no-ai.yml` | `python pmis.py sync queue db --no-ai` (no Gemini call) | the legacy variables |
| **Sync DB (with AI)** | `.github/workflows/sync-db.yml` | `python pmis.py sync db` (part 1, then the AI), or `check access` | `GEMINI_API_KEY` |

Private repositories on the GitHub Free plan get 2,000 Actions minutes per month.

One-time setup:

1. Create a repository and push this folder to it. **Private** is recommended: the workflows print only counts (`--public-log`), but in a public repository anyone can read the run logs and the run summary.
2. Add two repository secrets and one variable (Settings > Secrets and variables > Actions; the spreadsheet ID is a plain setting, so it goes in the **Variables** tab), or with the GitHub CLI in PowerShell:

```powershell
Get-Content C:\path\to\secrets\sa-key.json -Raw | gh secret set SA_KEY_JSON   # the whole key file
gh secret set GEMINI_API_KEY                                                    # prompts for the value
gh variable set PMIS_SPREADSHEET_ID --body "<spreadsheet ID>"                    # a variable, not a secret (a secret with the same name still works as a fallback)
```

3. Do the same preparation as for a local run: disable the Apps Script triggers, and share the sheet and the PDFs with the service-account e-mail.
4. For **Sync queue and DB (no AI)** and **Sync all**, add the legacy settings as repository **variables** (Settings > Secrets and variables > Actions > Variables; they are plain settings, not credentials; **Sync queue and DB (no AI)** does not need `GEMINI_API_KEY`): `PMIS_LEGACY_DWR_SHEET`, `PMIS_LEGACY_DRQ_SHEET` (the exact tab names), and optionally `PMIS_LEGACY_SPREADSHEET_ID` (the file that holds them, if it is not the target spreadsheet; share it with the service account). With the GitHub CLI: `gh variable set PMIS_LEGACY_DWR_SHEET --body "<tab name>"`. A secret with the same name still works as a fallback, and the variable wins over it.

To sync everything: Actions tab > **Sync all** > Run workflow with `mode = dry-run` (shows the plan of both steps and writes nothing), then `mode = sync` with a small `limit`. `queue` picks DWR / DRQ / both; `legacy_spreadsheet` points at another file; `allow_large_removal` lifts the safety limit. The same from the command line: `gh workflow run sync.yml -f mode=dry-run`.

To sync only the queues and fix the DB rows without the AI: Actions tab > **Sync queue and DB (no AI)** > Run workflow with `mode = dry-run` (reads the legacy tabs and writes nothing), then `mode = sync`. `db` (on by default) also runs part 1 of the `db` step, which makes DB_DWR / DB_DRQ match the queue; `overwrite` rebuilds from scratch instead of syncing (leave it off for a scheduled run). The same from the command line: `gh workflow run sync-no-ai.yml -f mode=dry-run`.

To fix the DB rows and let the AI read the Waiting PDFs: Actions tab > **Sync DB (with AI)** > Run workflow, then choose `mode`: `dry-run` first, then `check-access`, then `sync` with a small `limit`, then a larger one. With `only_rows` only the AI runs, for those rows; `allow_large_removal` lifts the safety limit of part 1. The same from the command line: `gh workflow run sync-db.yml -f mode=dry-run`.

- The unit tests (which include a check of the workflows themselves) run first in the `test` job; the sync only starts if they pass.
- Two runs never overlap, whichever workflow they belong to (all three share one `concurrency` group), so the same `Waiting` rows cannot be processed twice.
- The service-account key is written to a temporary file for the duration of the run only.
- The workflows call the script with `--public-log`: the log and the run summary show just the counts (queue statuses, Done/Error totals, why a run stopped), never tab names, sheet names / IDs, Doc Nos, file IDs or error details, and the spreadsheet ID and the service-account e-mail are masked as well. Anything the script writes to stderr goes to a file that is never printed. The per-row details are in column G of the queue sheet; run the script locally (without `--public-log`) to see the full log.
- There is no `schedule:` in the workflows (GitHub's own cron was not starting runs). To run them automatically, call the workflow_dispatch API from an outside scheduler such as cron-job.org: `POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/sync.yml/dispatches` (or `sync-no-ai.yml` / `sync-db.yml`) with the body `{"ref":"main","inputs":{"mode":"sync","limit":"30"}}` (`sync-no-ai.yml`: `{"ref":"main","inputs":{"mode":"sync"}}`, `sync-db.yml`: `{"ref":"main","inputs":{"mode":"sync","limit":"30"}}`) and a fine-grained token with the **Actions: Read and write** permission. In all three workflows `mode = sync` means "for real". The last two were called "Extract queue" and "Migrate queue" (`extract.yml` and `migrate.yml`, with `mode = extract` / `run` and the input `sync_db`) before the sync was reworked: a call that still uses the old file names or values fails (the old files are gone), so update it.
- The shared pieces live in `.github/scripts/` (`with_credentials.sh` writes the key to a temporary file and deletes it afterwards, `job_summary.sh`) and `.github/actions/setup-python-deps/`.

## Good to know

- **Last migration time:** every real run of the AI part (`sync db`, not `--dry-run` / `--no-ai` / `check access`) writes when it finished to the queue sheet it processed: `K1` = `Last migration run (finish time)` and `K2` = the time in Thai time (UTC+7), e.g. `2026-10-22 14:05:33 (UTC+7)`. It is written for each queue that was run (a queue skipped because the run stopped early is not touched) and even when there was nothing to process. It sits next to the `sync queue` refresh time (`I1:I2`), outside the queue data (columns A-G). If the cell cannot be written (for example a tab with fewer than 11 columns) the run is not affected; it only warns.
- Rows whose column G is `Waiting` **or `Error…`** are processed (errors are retried automatically); `Done` rows are skipped. Use `--no-retry-errors` to process only `Waiting`.
- A row that keeps failing for a reason that does not go away (`Error: No URL`, `Error: Drive Access`) is tried again by every run. It fails at once and costs no Gemini call; use `--no-retry-errors` to skip such rows.
- Work is done in chunks (`--batch 30`): one DB write per chunk, and only then are the rows marked `Done`, so a failed DB write never leaves a `Done` row without data.
- A **402 / 401 / 403** from Gemini (credits exhausted / bad key) stops the whole run immediately and leaves the remaining rows untouched; repeated **429** responses also stop the run.
- 429 / 5xx / network drops are retried automatically (backoff, up to 5 attempts); only then is `Error: API <code> | …` written.
- If a PDF is already in the DB (matched by file ID in the PDF Link column) it is marked `Done` without calling the AI, so a run that was cut off can be repeated without creating duplicate rows.
- A PDF larger than `--max-mb` (default **15 MB**, the same cut-off as `extractDriveFileForAI` in the Apps Script) is not sent whole. Like the Apps Script, the script sends the **page-1 cover image** from the Drive thumbnail (`sz=w2500`) instead, so only the first page is analysed. Such rows still end up `Done`, but the run prints an `ℹ` line for each of them (and a total at the end) so you can review them. Use `--no-thumbnail` to get `Error: Too Large` instead, or `--max-mb` to move the cut-off. The image is sent with its real MIME type (the Apps Script labels it `application/pdf`).
- A non-PDF file over the limit still gets `Error: Too Large`, and if Drive refuses the thumbnail request the row gets `Error: Drive Access | … cover capture failed`.
- Failed rows record the cause as `Error: <label> | <detail>`. The current Apps Script writes only `Error: <label>`, and it leaves a row without a Drive file ID as `Waiting`; the Python script reports such a row as `Error: No URL`. Everything the script writes into these Error cells is in English (labels, hints, messages), and so is the console log.
- **`Error: JSON Parse`** means Gemini answered, but the answer is not valid JSON. The usual causes: the answer was **cut off** (`finishReason=MAX_TOKENS`, typical for drawings with very long manpower/equipment lists), a double quote or line break inside a text value that was not escaped, or prose around the JSON. The script now (1) reads the answer more leniently (raw line breaks / tabs inside strings, and text before or after the JSON, are accepted), (2) calls Gemini again when the JSON is still broken (`--parse-retries`, default **1**; the output differs a little each time, so a retry often works; `0` turns it off) and logs `ℹ` when a retry rescued a row, and (3) writes the reason into the error cell: the parser message with the position, `finishReason`, the number of retries, and the **head and tail** of the AI answer (a cut-off answer ends mid-sentence). If the cell shows `finishReason=MAX_TOKENS`, re-run that row with `--max-output-tokens 16000` (not set by default, same as the Apps Script). Retry only that row: `python pmis.py sync db --queue dwr --only-rows 231` (use the sheet row number, not the document number).
- **Several reports in one PDF** (`JSON Parse | ValueError: expected a JSON object, got list`, as seen with PDFs that contain several reports): the prompt asks for one object, but when a PDF holds more than one report Gemini answers with an array `[ {…}, {…} ]`. The Apps Script cannot read an array and would write a `Done` row with empty fields; the script instead picks the report whose `DWR_No` / `DRQ_No` equals the queue row's **Doc No** (column A; upper/lower case, `-`/spaces and leading zeros are ignored, so `…-231` matches `…-0231`). That row is `Done`, but the run prints a `⚠` line for it and a total at the end, because the other reports in the PDF are not migrated: check them. If no report matches, the script re-asks once with an added instruction ("return exactly ONE object, for document number …"); if that fails too, the cell shows `MultiReportError` with the number and date of every report found (`#1 DWR_No=… Date=…`), so you can see whether the Doc No in column A is wrong or the PDF is a different document. `--accept-first-report` uses the first report in that case instead of an error (use it only after checking).
- The default model is `gemini-3.8-flash`, the same as `CONFIG_GEMINI_MODEL` in `Code_Config.js`. The two are independent (the script does not read the Apps Script file): if you change one, change the other. You can override this script's model with `--model` or the `GEMINI_MODEL` environment variable.
- The first output line shows the sheet's name and ID (`sheet: <spreadsheet name> (<id>)`), and every queue heading repeats the name (`===== Queue DWR: MIGRATION_QUEUE -> DB_DWR | sheet: <spreadsheet name> =====`). If the name cannot be read, the reason is printed instead. Check it first if the numbers look wrong, because the ID comes only from the `PMIS_SPREADSHEET_ID` environment variable.
- When a queue yields **0 rows**, the script says why: the tab has no data rows (usually the wrong spreadsheet), or column G holds values other than `Waiting` / `Error…` (the exact values found are listed, e.g. `'Done'×120`; the match is case-sensitive), or column G is blank but the row has a URL (use `--include-blank`).
- The DB is written with `values.append`, so rows are added automatically (DB_DWR / DB_DRQ currently have only 1,000 rows).
- At the end of a run it prints token usage and an estimated cost (list price).
- Code comments are written in three languages (Korean / `[TH]` / `[EN]`); console output (log lines, `--help`) and the text written into the sheet's Error cells are in English.

## Project layout

The code is split by job; `pmis.py` is only the entry point (`python -m migrator` works too). `migrate_queue.py` is the old entry point: it only translates the old options into the new commands.

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

## Tests

```powershell
python -m pip install pytest pyyaml
python -m pytest -q
```
