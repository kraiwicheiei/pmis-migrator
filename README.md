# migrator - process the MIGRATION_QUEUE with Python

**Language / ภาษา / 언어:** [English](README.md) · [ไทย](README.th.md) · [한국어](README.ko.md)

Replaces `runDwrAiMigrationTurbo` / `runDrqAiMigrationTurbo` in `Code_Migration.js`. It uses the same prompts, the same model
(`gemini-3.8-flash`) and the same DB_DWR / DB_DRQ column order, but runs on your own PC, so the Apps Script UrlFetch quotas no longer apply.
It follows the current Apps Script (`Code_Migration.js`, and `extractDriveFileForAI` in `Code_Core.js` for large PDFs).

## Overview: who does what

| Step | Where it runs |
|---|---|
| Build the `MIGRATION_QUEUE` / `MIGRATION_QUEUE_DRQ` queues from the legacy `<legacy DWR sheet>` / `<legacy DRQ sheet>` sheets | **Apps Script** (`extractLegacyDwrLinks()` / `extractLegacyDrqLinks()`). The Python script **does not create the queue** |
| Download PDF -> Gemini -> write DB_DWR / DB_DRQ -> mark `Done` | **This Python script** |

> **Re-running `extractLegacyDwrLinks()` / `extractLegacyDrqLinks()`:** in the current `Code_Migration.js` both functions `clear()` the queue and rebuild **every** item as `Waiting` - including PDFs that are already in DB_DWR / DB_DRQ - and every `Done` / `Error…` status is wiped.
> The Python script covers for this: a `Waiting` row whose PDF is already in the DB (matched by the Drive file ID in the PDF Link column) is marked `Done` without calling the AI. Always back up column G (and the DB) before re-running either function.

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

## What `--check-access` checks

For every pending row it extracts the file ID from the PDF URL column and asks Drive for the file's metadata (name, type, size, trashed or not) using the service account.
It reports rows with `No URL`, `Drive Access` (404/403: the file does not exist, or it has not been shared with the service account), or a file in the trash.

By default both queues are checked, one after the other (`--queue dwr` or `--queue drq` checks just one).
Every output line starts with the queue sheet name, e.g. `[MIGRATION_QUEUE] Reachable: 2/3 files` or `✗ [MIGRATION_QUEUE_DRQ] row 4: Drive Access | …`. When you check only one queue, the last line gives the command for checking the other one.

It does **not** download the PDF, does **not** call Gemini (so it costs nothing), does **not** write to the sheet, does not verify that the file is a real PDF, and does not test write access to the sheet.
Rows whose PDF is larger than `--max-mb` are listed with an `ℹ` line saying that only the cover image will be sent (see "Good to know").
`--limit` defaults to 30, so pass `--limit 0` to check every row.

## Run on GitHub Actions

`.github/workflows/migrate.yml` runs the same script on a GitHub-hosted runner, so no PC has to stay on. Private repositories on the GitHub Free plan get 2,000 Actions minutes per month.

One-time setup:

1. Create a repository and push this folder to it. **Private** is recommended: the workflow prints only counts, but in a public repository anyone can read the run logs and the run summary.
2. Add three repository secrets (Settings > Secrets and variables > Actions), or with the GitHub CLI in PowerShell:

```powershell
Get-Content C:\path\to\secrets\sa-key.json -Raw | gh secret set SA_KEY_JSON   # the whole key file
gh secret set GEMINI_API_KEY                                                    # prompts for the value
gh secret set PMIS_SPREADSHEET_ID                                               # prompts for the value
```

3. Do the same preparation as for a local run: disable the Apps Script triggers, and share the sheet and the PDFs with the service-account e-mail.

To run: Actions tab > **Migrate queue** > Run workflow, then choose `mode`: `dry-run` first, then `check-access`, then `run` with a small `limit`, then a larger one. The same from the command line: `gh workflow run migrate.yml -f mode=dry-run`.

- The unit tests run first (`test` job); the migration only starts if they pass.
- Two runs never overlap (`concurrency`), so the same `Waiting` rows cannot be processed twice.
- The service-account key is written to a temporary file for the duration of the run only.
- The full output (file IDs, the sheet name, row errors that quote text from the PDFs) goes only to a temporary file on the runner. The log and the run summary show just the counts (queue statuses, Done/Error totals, why a run stopped), with the spreadsheet ID and the service-account e-mail masked. The per-row details are in column G of the queue sheet; run the script locally to see the full log.
- Automatic runs are available but off: remove the `#` in front of the `schedule:` lines in the workflow (a scheduled run uses `mode = run`, both queues, `limit = 30`).

## Good to know

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

## Tests

```powershell
python -m pip install pytest
python -m pytest -q
```
