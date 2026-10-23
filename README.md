# migrator - process the MIGRATION_QUEUE with Python

**Language / ภาษา / 언어:** [English](README.md) · [ไทย](README.th.md) · [한국어](README.ko.md)

Replaces `runDwrAiMigrationTurbo` / `runDrqAiMigrationTurbo` in `Code_Migration.js`. It uses the same prompts, the same model
(`gemini-3.8-flash`) and the same DB_DWR / DB_DRQ column order, but runs on your own PC, so the Apps Script UrlFetch quotas no longer apply.
It follows the current Apps Script (`Code_Migration.js`, and `extractDriveFileForAI` in `Code_Core.js` for large PDFs).
It also replaces `extractLegacyDwrLinks` / `extractLegacyDrqLinks`, which build the queues (see `--extract`).

## Overview: who does what

| Step | Where it runs |
|---|---|
| Build the `MIGRATION_QUEUE` / `MIGRATION_QUEUE_DRQ` queues from the legacy `<legacy DWR sheet>` / `<legacy DRQ sheet>` sheets | **This Python script** with `--extract` (replaces `extractLegacyDwrLinks()` / `extractLegacyDrqLinks()` in Apps Script) |
| Download PDF -> Gemini -> write DB_DWR / DB_DRQ -> mark `Done` | **This Python script** |

> **Rebuilding a queue:** the Apps Script functions `extractLegacyDwrLinks()` / `extractLegacyDrqLinks()` `clear()` the queue and rebuild **every** item as `Waiting` - including PDFs that are already in DB_DWR / DB_DRQ - and every `Done` / `Error…` status is wiped. `--extract` does that only with `--overwrite`. Without it, `--extract` **syncs** a queue that already has rows: every row is recomputed from the legacy sheet and the status of the existing row is kept (see "Build the queues").
> After an `--overwrite` the Python migration covers for the rebuild: a `Waiting` row whose PDF is already in the DB (matched by the Drive file ID in the PDF Link column) is marked `Done` without calling the AI. Always back up column G (and the DB) before using `--overwrite`.

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

A value set with `$env:` lives only in that PowerShell window: set it in the same window where you run `python migrate_queue.py`, and again in every new window.
To make it permanent use `setx GEMINI_API_KEY "..."` (then open a new window); the value is stored unencrypted in the user registry.
`--dry-run` and `--check-access` do not use `GEMINI_API_KEY`, so you do not need it for those two modes.

## Recommended order

```powershell
python migrate_queue.py --dry-run                       # show which rows would be processed, in both queues (no API calls, no sheet writes)
python migrate_queue.py --check-access --limit 0        # check that the service account can open every PDF, in both queues
python migrate_queue.py --limit 5                       # real test: up to 5 rows from EACH queue; compare with rows the old script marked Done
python migrate_queue.py --queue dwr --only-rows 83,102,127-128   # retry the rows that failed individually before (needs --queue)
python migrate_queue.py --limit 200                     # run in rounds; check the result, then raise it (0 = everything)
python migrate_queue.py --queue drq --limit 5           # only the DRQ queue (MIGRATION_QUEUE_DRQ -> DB_DRQ)
```

**Which queues:** without `--queue` the script processes **both** queues, DWR first (`MIGRATION_QUEUE` -> `DB_DWR`) and then DRQ (`MIGRATION_QUEUE_DRQ` -> `DB_DRQ`); `--limit` applies **per queue**. Use `--queue dwr` or `--queue drq` to process only one.
`--only-rows` refers to row numbers of one particular queue sheet, so it requires `--queue`.
If the first queue stops early (for example Gemini answers 402/401/403, or repeated 429), the second queue is skipped because it would stop for the same reason. If one queue sheet cannot be read (for example the tab is missing), the other queue is still processed. In both cases the exit code is 2.

The target sheet is set **only** through the `PMIS_SPREADSHEET_ID` environment variable. There is no default in the code and no command-line option, and the script stops with a message if the variable is missing or empty (in every mode, including `--dry-run`). The ID is the part of the sheet's URL between `/d/` and `/edit`: `https://docs.google.com/spreadsheets/d/<ID>/edit`.

## Build the queues: `--extract`

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
python migrate_queue.py --extract --dry-run     # read the legacy sheets, show the counts and sample rows; nothing is written
python migrate_queue.py --extract               # build both queues
python migrate_queue.py --extract --queue drq   # only one queue (then only its own tab-name variable is needed)
```

- `--extract` needs neither `GEMINI_API_KEY` nor any Drive access, ignores `--limit`, and cannot be combined with `--only-rows` / `--check-access`.
- For each queue the console shows how many unique items were found, how many have no PDF link (those rows become `Error: No URL` when migrated) and how many cells were skipped because their column header is empty.
- **Sync (the default when the queue already has rows):** `--extract` **recomputes every row of the queue from the legacy sheet** (Doc No, Date, Discipline, PDF URL, Folder URL, C/P; the rows are in the legacy sheet's order) and compares the result with the queue as it is now. Where the same item already has a row, the new row **keeps that row's status** (column G: `Done`, `Error…`, `Waiting`) and every other column follows the legacy sheet. **Exception: a `Done` row whose PDF (the Drive file ID) changed in the legacy sheet goes back to `Waiting`**, and the file ID of the old PDF is written to column H (`Replaces PDF (old file ID)`), so that the next migration reads the new file and replaces the DB row that used the old one (see "Keeping PMIS in sync"). A link that only changed its format (same file ID) is not a new PDF. An item without a row becomes a new `Waiting` row. An existing row whose item is no longer in the legacy sheet **is removed**, so the queue always has the same content as the legacy sheet (the console warns how many removed rows were `Done` / `Error…`). Nothing is written when nothing changed, so it is safe to run on a schedule. `--dry-run` shows the plan (`N kept (M changed, R renumbered, S set back to Waiting), +K new, D duplicate rows merged, X rows ... removed; statuses kept: ...`). The writing is done in place over columns A-H only (the sheet is not cleared first, so a failure cannot leave it empty, and the times in `I` / `K` are untouched). **Safety net:** a run that would remove more rows than the limit (the larger of 10% of the rows and 100) is refused, which protects the queue when the legacy sheet is wrongly empty; `--allow-large-removal` lifts it.
- **Finding the existing row of an item:** by the same `(Doc No, Date)`, or - for the case where only the `No.` (column A) of the legacy sheet changed - by the same **Date + Discipline + cell value** (`Doc No` = `No.` + `-` + Discipline + `-` + the cell value, so the part after the `No.` stays the same). The new row then has the new `Doc No`, and the console shows `N renumbered` (`--dry-run` lists `sample (renumbered): row 5: old -> new`). If one item has **several rows** (for example it went in twice, under the old and under the new `Doc No`), they are merged into one that keeps the status of the processed row (`Done` over `Error…` over `Waiting`; on a tie the earlier row). If the **Date** changes as well, the item is a new one.
- **Rebuild:** add `--overwrite` to clear the queue and rebuild every row as `Waiting`, as Apps Script did (every `Done` / `Error…` status is wiped; back up column G first). A missing or empty queue tab is simply built from scratch. If the tab's header row is not `Doc No | Date | Discipline | PDF URL | Folder URL | Status(C/P) | AI Processed`, `--extract` writes nothing and exits with code 2 until you fix it or add `--overwrite`.
- **Refresh time:** after every real (not `--dry-run`) extract that wrote the queue, `I1` = `Last extract (refresh time)` and `I2` = the time in Thai time (UTC+7), e.g. `2026-10-22 14:05:33 (UTC+7)`, so the sheet shows when it was last refreshed. Column I is outside the queue data (A-G).
- **Legacy tabs in another file:** by default the legacy tabs are in the same spreadsheet as the queues. To read them from another file, pass `--legacy-spreadsheet <ID or URL>` or set `PMIS_LEGACY_SPREADSHEET_ID`; the queues are still written to `PMIS_SPREADSHEET_ID`. Share that file with the service account (Viewer is enough).
- **`.env`:** all the variables above can be put in a `.env` file next to the script (see `.env.example`); variables already set in the environment win.
- Differences from Apps Script: the queue values are written as plain text (`RAW`), so dates, numbers and leading zeros are never auto-converted; only the values of the queue sheet are cleared (its formatting stays); rows are appended, so the sheet grows by itself.
- The service account needs **Editor** access to the spreadsheet (it reads the legacy tab and writes the queue tab). The PDFs are not touched.

## Keeping PMIS in sync with the legacy sheets

The goal: what is in PMIS (the queues and `DB_DWR` / `DB_DRQ`) always matches the legacy sheets. The chain is **legacy sheet -> (`--extract`) -> queue -> (migrate) -> DB**, plus `--sync-db`:

| A change in the legacy sheet | What happens in PMIS |
|---|---|
| a new item | `--extract` adds a `Waiting` row; the next migration adds the DB row |
| the `No.` (column A) changed | `--extract` renames the `Doc No` of the same item (no duplicate row) |
| the PDF of an item that is `Done` changed | `--extract` sets the row back to `Waiting` and writes the old file ID to column H; the next migration reads the new file and **replaces the old DB row in place** (see below); if it fails the old DB row stays and column H is kept |
| the PDF link of an item was cleared | `--extract` keeps the row; `--sync-db` removes the DB row of that PDF (copied to `_REMOVED_...` first). When a link comes back the item is migrated again (a new DB row) |
| the C/P changed | `--sync-db` updates the C/P column of the DB (`T` in DB_DWR, `S` in DB_DRQ) |
| an item was removed | `--extract` removes its queue row; `--sync-db` removes the DB rows that were synced with the legacy sheet (tag `Synced`) and whose PDF is not in the queue any more |

- **Replace in place:** the DB row that used the old file (found by the old file ID) is overwritten with the new data, in the same row. Columns that are not data of the migration are not touched: column A (`CSC Ref. No`) and the empty `Ext1` / `Ext2`. If another queue row still uses the old PDF, or the old file is not in the DB, a new row is appended instead. Column H is cleared once the row is `Done`.
- **`--sync-db`** (`python migrate_queue.py --sync-db`, or `--extract --sync-db`, which runs it right after each queue is synced - with `--dry-run` on the planned rows): removes the DB rows whose PDF file ID is not in the queue any more, **only rows whose Status is `AI Migrated` and that carry the `Legacy sync` tag `Synced`** - that is, rows that were synced with the legacy sheet and then left it (a row somebody typed in, and a row that never came from the legacy sheet, e.g. a document another system added, is never removed). It keeps the rows whose PDF waits in column H to be replaced. **Before a row is removed it is copied to the tab `_REMOVED_DB_DWR` / `_REMOVED_DB_DRQ`** (the time, the tab, the original row number, the reason and all the original cells; the tab is created when it is missing), so nothing is lost. It also updates the C/P column to the queue's value (an empty C/P in the queue never clears a DB value; a PDF that has different C/P values in the queue is skipped). Just before it writes, it reads the PDF column again and stops without changing anything if a row was inserted or removed in the meantime.
- **Safety net:** if the queue has no rows, or more rows would be removed than the limit (the larger of 10% of the DB rows and 100), nothing is written to that DB (`--allow-large-removal` lifts the limit; a `--dry-run` never fails because of it). Rows are deleted from the sheet, so other sheets that refer to DB rows by position may shift: check the plan with `--dry-run` first.
- **In GitHub Actions:** the workflow **Extract queue** runs `--extract` and, with the input `sync_db` (on by default), `--sync-db` after each queue. Run it with `mode = dry-run` first: the log shows the counts only.
- **The `Legacy sync` tag:** every DB row whose PDF is in the queue (= the legacy sheet) is tagged `Synced` in the column right after C/P (**`U` in `DB_DWR`, `T` in `DB_DRQ`**, header `Legacy sync`). **An orphan row has no tag**: a row whose PDF is not in the queue any more, a row that was not written by this tool (Status is not `AI Migrated`, so it is not removed either) and a row without a PDF link are left empty, and a tag that is no longer true is cleared. A row that still holds the old version while it waits for its replacement (its PDF is in column H of the queue) keeps whatever it has. The tag is also the memory of which rows came from the legacy sheet: **a row that never was tagged is never removed**, so documents added by another system, or rows that the legacy sheet does not have, stay. The first `--sync-db` therefore only tags (nothing is removed); rows are removed only after they were tagged and then disappeared from the legacy sheet. `--sync-db` keeps the tags right on every run (it writes only the cells that change, so a second run writes nothing); the rows migrate writes (new rows and replaced rows) are tagged `Synced` as they are written. Before the first tag is written the header cell is checked like the C/P one: an empty cell gets `Legacy sync`, any other header stops that DB. A removed row keeps its tag in the `_REMOVED_...` copy.
- **What stays as it is:** several DB rows with the same PDF (or the same Doc No) are left alone; only a synced PDF that is not in the queue any more is removed. PDFs in columns after `BD` / `BF`, which `--extract` does not read, are not in the queue; their rows are removed only if they were tagged before.

## C/P in the DB: column `T` (DWR) / `S` (DRQ), and `--backfill-cp`

Every row the migration writes to `DB_DWR` / `DB_DRQ` now carries the **C/P** value of its queue row (column F, `Status(C/P)`), copied as it is, in **column T of `DB_DWR`** and **column S of `DB_DRQ`** (header `Status (C/P)` in both), followed by the `Legacy sync` tag column (`U` in `DB_DWR`, `T` in `DB_DRQ`; see "Keeping PMIS in sync"). The existing columns do not move: `DB_DWR` stays A-S (`Folder Link` is S), and in `DB_DRQ` the new column comes right after `Folder Link` (P) and `Ext1` / `Ext2` (Q, R).

- Before the first new row is written, the script looks at the header cell (`T1` in `DB_DWR`, `S1` in `DB_DRQ`) of the DB tab: an empty cell is filled with `Status (C/P)`; the same header is kept; **any other header stops that queue** (`✗ Could not process queue ...`) so the value is never written under another column.
- Rows that are already in the DB have no C/P. Fill them with `--backfill-cp` (no Drive / Gemini calls, no `GEMINI_API_KEY` needed):

```powershell
python migrate_queue.py --backfill-cp --dry-run     # the plan: how many cells would be filled (C / P), how many cannot be matched
python migrate_queue.py --backfill-cp               # fill both DB tabs
python migrate_queue.py --backfill-cp --queue dwr   # one tab only
```

  Each DB row is matched with the queue row of the **same PDF** (Drive file ID in the PDF Link column). A cell that already has a C/P is never changed, a row whose PDF is not in the queue is left empty, and a PDF that has **different** C/P values in the queue (for example C in one row and P in another) is skipped, not guessed; the console reports each count. The command can be run again: it only fills the cells that are still empty. It cannot be combined with `--extract`, `--only-rows` or `--check-access`.

## What `--check-access` checks

For every pending row it extracts the file ID from the PDF URL column and asks Drive for the file's metadata (name, type, size, trashed or not) using the service account.
It reports rows with `No URL`, `Drive Access` (404/403: the file does not exist, or it has not been shared with the service account), or a file in the trash.

By default both queues are checked, one after the other (`--queue dwr` or `--queue drq` checks just one).
Every output line starts with the queue sheet name, e.g. `[MIGRATION_QUEUE] Reachable: 2/3 files` or `✗ [MIGRATION_QUEUE_DRQ] row 4: Drive Access | …`. When you check only one queue, the last line gives the command for checking the other one.

It does **not** download the PDF, does **not** call Gemini (so it costs nothing), does **not** write to the sheet, does not verify that the file is a real PDF, and does not test write access to the sheet.
Rows whose PDF is larger than `--max-mb` are listed with an `ℹ` line saying that only the cover image will be sent (see "Good to know").
`--limit` defaults to 30, so pass `--limit 0` to check every row.

## Run on GitHub Actions

Two workflows run the same script on a GitHub-hosted runner, so no PC has to stay on: `.github/workflows/migrate.yml` (**Migrate queue**: PDF -> Gemini -> DB) and `.github/workflows/extract.yml` (**Extract queue**: build / sync the queues from the legacy sheets, `--extract`). Private repositories on the GitHub Free plan get 2,000 Actions minutes per month.

One-time setup:

1. Create a repository and push this folder to it. **Private** is recommended: the workflows print only counts (`--public-log`), but in a public repository anyone can read the run logs and the run summary.
2. Add two repository secrets and one variable (Settings > Secrets and variables > Actions; the spreadsheet ID is a plain setting, so it goes in the **Variables** tab), or with the GitHub CLI in PowerShell:

```powershell
Get-Content C:\path\to\secrets\sa-key.json -Raw | gh secret set SA_KEY_JSON   # the whole key file
gh secret set GEMINI_API_KEY                                                    # prompts for the value
gh variable set PMIS_SPREADSHEET_ID --body "<spreadsheet ID>"                    # a variable, not a secret (a secret with the same name still works as a fallback)
```

3. Do the same preparation as for a local run: disable the Apps Script triggers, and share the sheet and the PDFs with the service-account e-mail.
4. For **Extract queue** only, add the legacy settings as repository **variables** (Settings > Secrets and variables > Actions > Variables; they are plain settings, not credentials; `GEMINI_API_KEY` is not needed there): `PMIS_LEGACY_DWR_SHEET`, `PMIS_LEGACY_DRQ_SHEET` (the exact tab names), and optionally `PMIS_LEGACY_SPREADSHEET_ID` (the file that holds them, if it is not the target spreadsheet; share it with the service account). With the GitHub CLI: `gh variable set PMIS_LEGACY_DWR_SHEET --body "<tab name>"`. A secret with the same name still works as a fallback, and the variable wins over it.

To migrate: Actions tab > **Migrate queue** > Run workflow, then choose `mode`: `dry-run` first, then `check-access`, then `run` with a small `limit`, then a larger one. The same from the command line: `gh workflow run migrate.yml -f mode=dry-run`.

To build the queues: Actions tab > **Extract queue** > Run workflow with `mode = dry-run` (reads the legacy tabs and writes nothing), then `mode = extract`. `queue` picks DWR / DRQ / both; `legacy_spreadsheet` points at another file; `sync_db` (on by default) also makes DB_DWR / DB_DRQ match the queue after each queue; `allow_large_removal` lifts the safety limit; `overwrite` rebuilds from scratch instead of syncing (leave it off for a scheduled run). The same from the command line: `gh workflow run extract.yml -f mode=dry-run`.

- The unit tests run first (`test` job); the migration / extract only starts if they pass.
- Two runs never overlap, not even a migration and an extract (they share one `concurrency` group), so the same `Waiting` rows cannot be processed twice.
- The service-account key is written to a temporary file for the duration of the run only.
- The workflows call the script with `--public-log`: the log and the run summary show just the counts (queue statuses, Done/Error totals, why a run stopped), never tab names, sheet names / IDs, Doc Nos, file IDs or error details, and the spreadsheet ID and the service-account e-mail are masked as well. Anything the script writes to stderr goes to a file that is never printed. The per-row details are in column G of the queue sheet; run the script locally (without `--public-log`) to see the full log.
- There is no `schedule:` in the workflows (GitHub's own cron was not starting runs). To run them automatically, call the workflow_dispatch API from an outside scheduler such as cron-job.org: `POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/migrate.yml/dispatches` (or `extract.yml`) with the body `{"ref":"main","inputs":{"mode":"run","limit":"30"}}` (or `{"ref":"main","inputs":{"mode":"extract"}}`) and a fine-grained token with the **Actions: Read and write** permission.
- The shared pieces live in `.github/scripts/` (`with_credentials.sh` writes the key to a temporary file and deletes it afterwards, `job_summary.sh`) and `.github/actions/setup-python-deps/`.

## Good to know

- **Last migration time:** every real run (not `--dry-run` / `--check-access`) writes when it finished to the queue sheet it processed: `K1` = `Last migration run (finish time)` and `K2` = the time in Thai time (UTC+7), e.g. `2026-10-22 14:05:33 (UTC+7)`. It is written for each queue that was run (a queue skipped because the run stopped early is not touched) and even when there was nothing to process. It sits next to the `--extract` refresh time (`I1:I2`), outside the queue data (columns A-G). If the cell cannot be written (for example a tab with fewer than 11 columns) the run is not affected; it only warns.
- Rows whose column G is `Waiting` **or `Error…`** are processed (errors are retried automatically); `Done` rows are skipped. Use `--no-retry-errors` to process only `Waiting`.
- Work is done in chunks (`--batch 30`): one DB write per chunk, and only then are the rows marked `Done`, so a failed DB write never leaves a `Done` row without data.
- A **402 / 401 / 403** from Gemini (credits exhausted / bad key) stops the whole run immediately and leaves the remaining rows untouched; repeated **429** responses also stop the run.
- 429 / 5xx / network drops are retried automatically (backoff, up to 5 attempts); only then is `Error: API <code> | …` written.
- If a PDF is already in the DB (matched by file ID in the PDF Link column) it is marked `Done` without calling the AI, so a run that was cut off can be repeated without creating duplicate rows.
- A PDF larger than `--max-mb` (default **15 MB**, the same cut-off as `extractDriveFileForAI` in the Apps Script) is not sent whole. Like the Apps Script, the script sends the **page-1 cover image** from the Drive thumbnail (`sz=w2500`) instead, so only the first page is analysed. Such rows still end up `Done`, but the run prints an `ℹ` line for each of them (and a total at the end) so you can review them. Use `--no-thumbnail` to get `Error: Too Large` instead, or `--max-mb` to move the cut-off. The image is sent with its real MIME type (the Apps Script labels it `application/pdf`).
- A non-PDF file over the limit still gets `Error: Too Large`, and if Drive refuses the thumbnail request the row gets `Error: Drive Access | … cover capture failed`.
- Failed rows record the cause as `Error: <label> | <detail>`. The current Apps Script writes only `Error: <label>`, and it leaves a row without a Drive file ID as `Waiting`; the Python script reports such a row as `Error: No URL`. Everything the script writes into these Error cells is in English (labels, hints, messages), and so is the console log.
- **`Error: JSON Parse`** means Gemini answered, but the answer is not valid JSON. The usual causes: the answer was **cut off** (`finishReason=MAX_TOKENS`, typical for drawings with very long manpower/equipment lists), a double quote or line break inside a text value that was not escaped, or prose around the JSON. The script now (1) reads the answer more leniently (raw line breaks / tabs inside strings, and text before or after the JSON, are accepted), (2) calls Gemini again when the JSON is still broken (`--parse-retries`, default **1**; the output differs a little each time, so a retry often works; `0` turns it off) and logs `ℹ` when a retry rescued a row, and (3) writes the reason into the error cell: the parser message with the position, `finishReason`, the number of retries, and the **head and tail** of the AI answer (a cut-off answer ends mid-sentence). If the cell shows `finishReason=MAX_TOKENS`, re-run that row with `--max-output-tokens 16000` (not set by default, same as the Apps Script). Retry only that row: `python migrate_queue.py --queue dwr --only-rows 231` (use the sheet row number, not the document number).
- **Several reports in one PDF** (`JSON Parse | ValueError: expected a JSON object, got list`, as seen with PDFs that contain several reports): the prompt asks for one object, but when a PDF holds more than one report Gemini answers with an array `[ {…}, {…} ]`. The Apps Script cannot read an array and would write a `Done` row with empty fields; the script instead picks the report whose `DWR_No` / `DRQ_No` equals the queue row's **Doc No** (column A; upper/lower case, `-`/spaces and leading zeros are ignored, so `…-231` matches `…-0231`). That row is `Done`, but the run prints a `⚠` line for it and a total at the end, because the other reports in the PDF are not migrated: check them. If no report matches, the script re-asks once with an added instruction ("return exactly ONE object, for document number …"); if that fails too, the cell shows `MultiReportError` with the number and date of every report found (`#1 DWR_No=… Date=…`), so you can see whether the Doc No in column A is wrong or the PDF is a different document. `--accept-first-report` uses the first report in that case instead of an error (use it only after checking).
- The default model is `gemini-3.8-flash`, the same as `CONFIG_GEMINI_MODEL` in `Code_Config.js`. The two are independent (the script does not read the Apps Script file): if you change one, change the other. You can override this script's model with `--model` or the `GEMINI_MODEL` environment variable.
- The first output line shows the sheet's name and ID (`sheet: <spreadsheet name> (<id>)`), and every queue heading repeats the name (`===== Queue DWR: MIGRATION_QUEUE -> DB_DWR | sheet: <spreadsheet name> =====`). If the name cannot be read, the reason is printed instead. Check it first if the numbers look wrong, because the ID comes only from the `PMIS_SPREADSHEET_ID` environment variable.
- When a queue yields **0 rows**, the script says why: the tab has no data rows (usually the wrong spreadsheet), or column G holds values other than `Waiting` / `Error…` (the exact values found are listed, e.g. `'Done'×120`; the match is case-sensitive), or column G is blank but the row has a URL (use `--include-blank`).
- The DB is written with `values.append`, so rows are added automatically (DB_DWR / DB_DRQ currently have only 1,000 rows).
- At the end of a run it prints token usage and an estimated cost (list price).
- Code comments are written in three languages (Korean / `[TH]` / `[EN]`); console output (log lines, `--help`) and the text written into the sheet's Error cells are in English.

## Project layout

The code is split by job; `migrate_queue.py` is only the entry point, so every command in this guide is unchanged. `python -m migrator` works too.

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

## Tests

```powershell
python -m pip install pytest
python -m pytest -q
```
