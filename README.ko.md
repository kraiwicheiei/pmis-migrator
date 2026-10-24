# pmis-migrator - PMIS 를 옛 시트와 맞추기

**언어 / Language / ภาษา:** [English](README.md) · [ไทย](README.th.md) · [한국어](README.ko.md)

`Code_Migration.js` 의 `runDwrAiMigrationTurbo` / `runDrqAiMigrationTurbo` 를 대체합니다. 프롬프트, 모델(`gemini-3.8-flash`),
DB_DWR / DB_DRQ 열 순서는 기존과 완전히 동일하지만 내 PC 에서 실행되므로 Apps Script 의 UrlFetch 쿼터에 걸리지 않습니다.
현재 Apps Script(`Code_Migration.js`, 대용량 PDF 는 `Code_Core.js` 의 `extractDriveFileForAI`)를 따릅니다.
큐를 만드는 `extractLegacyDwrLinks` / `extractLegacyDrqLinks` 도 대체합니다(`sync queue` 참고).

명령 하나로 PMIS 를 옛 시트와 맞춥니다: **`python pmis.py sync`** = 옛 시트 -> 큐 -> `DB_DWR` / `DB_DRQ`. 두 단계로 되어 있으며 각각 따로도 실행할 수 있습니다(`sync queue`, `sync db`).

## 개요: 누가 무엇을 하는가

| 단계 | 명령 | 하는 일 |
|---|---|---|
| `queue` | `python pmis.py sync queue` | `MIGRATION_QUEUE` / `MIGRATION_QUEUE_DRQ` 큐를 옛 시트 `<legacy DWR sheet>` / `<legacy DRQ sheet>` 와 같게 맞춤. **새 항목이나 PDF 가 바뀐 항목은 `Waiting` 행이 되어** AI 가 읽게 됨 (Apps Script 의 `extractLegacyDwrLinks()` / `extractLegacyDrqLinks()` 를 대체) |
| `db` | `python pmis.py sync db` | `DB_DWR` / `DB_DRQ` 를 큐에 맞춤. **1부 (AI 없음):** C/P, Folder Link, `Legacy sync` 표시(`Synced` / `Outdated`), 옛 시트에서 사라진 행 삭제. **2부 (AI):** `Waiting` 인 모든 행의 PDF 다운로드 -> Gemini -> DB 행 기록 -> `Done` 표시 (`runDwrAiMigrationTurbo` / `runDrqAiMigrationTurbo` 를 대체) |

`python pmis.py sync` 는 두 단계를 이 순서로 실행합니다. 전부 **이 Python 스크립트** 가 하며 Apps Script 에 남는 일은 없습니다. 각 단계는 자기 시트에만 씁니다: `queue` 는 큐 탭에, `db` 는 DB 탭에(그리고 AI 가 행을 끝냈을 때 큐의 상태 열에) 씁니다.

> **큐 다시 만들기:** Apps Script 의 `extractLegacyDwrLinks()` / `extractLegacyDrqLinks()` 는 큐를 `clear()` 한 뒤 **모든** 항목(이미 DB_DWR / DB_DRQ 에 있는 PDF 포함)을 `Waiting` 으로 다시 만들고, 기존 `Done` / `Error…` 상태는 모두 지워집니다. `sync queue` 는 `--overwrite` 를 붙였을 때만 그렇게 하며, 붙이지 않으면 이미 행이 있는 큐를 **맞춥니다(sync)**: 모든 행을 옛 시트에서 새로 계산하되 기존 행의 상태는 유지합니다("queue 단계" 참고).
> `--overwrite` 뒤에는 `sync db` 의 AI 부분이 이를 보완합니다: PDF 가 이미 DB 에 있는 `Waiting` 행(PDF Link 열의 Drive 파일 ID 로 비교)은 AI 호출 없이 `Done` 으로 표시됩니다. `--overwrite` 를 쓰기 전에는 항상 G열(과 DB)을 먼저 백업해 두세요.

## 설치 (최초 1회) - PowerShell

```powershell
cd C:\path\to\pmis-migrator
python -m pip install -r requirements.txt
```

`python -m pip` 는 스크립트를 실행하는 것과 같은 Python 에 패키지를 설치하므로 `No module named 'requests'` 오류를 피할 수 있습니다.
가상환경을 쓰고 싶다면 `python -m venv .venv` 후 `.\.venv\Scripts\Activate.ps1` 을 실행하세요 (새 창을 열 때마다 **매번 activate** 해야 합니다).
"running scripts is disabled on this system" 오류가 나오면 먼저 `Set-ExecutionPolicy -Scope Process Bypass` 를 실행하세요.

## 실행 전 준비

1. Apps Script 에서 `runDwrAiMigrationTurbo` / `runDrqAiMigrationTurbo` 의 **트리거를 끕니다** (양쪽이 동시에 쓰는 것을 방지). 현재 Apps Script 는 실행 시작 시점에 읽어 둔 상태값으로 G열 전체를 다시 쓰므로, Python 과 겹쳐 실행되면 Python 이 방금 기록한 상태를 덮어쓸 수 있습니다.
2. 스프레드시트 "<your-spreadsheet-name>" 를 서비스 계정 이메일에 **편집자(Editor)** 로 공유합니다.
3. PDF 파일(또는 PDF 가 모여 있는 폴더)을 같은 이메일에 **뷰어(Viewer)** 로 공유합니다.
4. 환경 변수를 설정합니다. `sa-key.json` 은 repo 폴더 밖(예: `C:\path\to\secrets\`)에 보관하세요.

```powershell
$env:GOOGLE_APPLICATION_CREDENTIALS = "C:\path\to\secrets\sa-key.json"
$env:GEMINI_API_KEY = "여기에 키 입력"
$env:PMIS_SPREADSHEET_ID = "시트 ID 입력"   # 필수 (시트 URL 에서 /d/ 와 /edit 사이의 값)
```

`$env:` 로 설정한 값은 해당 PowerShell 창에서만 유효합니다. `python pmis.py` 를 실행하는 같은 창에서 설정해야 하고, 새 창을 열 때마다 다시 설정해야 합니다.
영구 설정은 `setx GEMINI_API_KEY "..."` 후 새 창을 여세요 (값이 사용자 레지스트리에 암호화되지 않은 채 저장됩니다).
`--dry-run`, `check access`, `sync queue`, `sync db --no-ai` 는 `GEMINI_API_KEY` 를 쓰지 않으므로 이 명령들에서는 설정하지 않아도 됩니다. `sync db` / `sync` 의 AI 부분만 키를 씁니다.

## 명령

```text
python pmis.py sync [queue] [db]   PMIS 를 옛 시트와 맞춤. 원하는 단계를 적고, 아무것도 안 적으면 두 단계 모두
python pmis.py sync db --no-ai     AI 없이 db 단계만: DB 행만 고침 (Gemini 키 불필요)
python pmis.py check access        서비스 계정이 PDF 파일을 열 수 있는지 확인 (아무것도 쓰지 않음, Gemini 호출 없음)
python pmis.py <command> --help    그 명령의 옵션
```

`sync` 는 두 단계의 연결입니다. **단계는 입력한 순서와 상관없이 항상 queue, db 순서로 실행됩니다** (`sync db queue` = `sync queue db`):

| 단계 | 하는 일 | 필요한 것 |
|---|---|---|
| `queue` | 옛 시트 -> 큐: `MIGRATION_QUEUE` / `MIGRATION_QUEUE_DRQ` 의 모든 행을 옛 시트에서 새로 계산하되 이미 있는 행의 상태는 유지. 새 항목이나 PDF 가 바뀐 항목은 `Waiting` 이 됨 | `PMIS_LEGACY_DWR_SHEET` / `PMIS_LEGACY_DRQ_SHEET`, Gemini 키 불필요 |
| `db` | 큐 -> DB, 두 부분으로 (아래) | 1부: 특별한 것 없음. 2부: `GEMINI_API_KEY` (`--dry-run` 은 불필요) |

`db` 단계의 두 부분:

| 부분 | 하는 일 | 건너뛰는 경우 |
|---|---|---|
| 1 - DB 행 고치기 (AI 없음) | 기존 행의 C/P 와 Folder Link 를 큐에 맞춤, `Legacy sync` 표시(`Synced` / `Outdated`) 설정, 옛 시트에서 왔다가 사라진 행 삭제(먼저 `_REMOVED_...` 에 복사) | `--only-rows` |
| 2 - AI | `Waiting`(과 `Error…`) 행의 PDF 를 Gemini 로 읽어 `DB_DWR` / `DB_DRQ` 에 기록. PDF 가 바뀐 행은 옛 DB 행을 제자리에서 교체 | `--no-ai` |

1부가 먼저 실행되므로 Gemini 가 멈추거나(크레딧 소진, 잘못된 키) 키가 없어도 1부의 일은 이미 끝나 있습니다. `--no-ai` 는 1부만 실행합니다(그러면 Gemini 키 불필요). `--only-rows` 는 그 행만 AI 로 다시 시도하고 다른 일은 하지 않습니다.

단계 이름은 `sync` 바로 뒤에, 옵션은 그 뒤에 적습니다. 명령별 옵션(`python pmis.py sync --help` 가 단계별로 나눠 보여 줍니다):

| 옵션 | 쓰는 곳 |
|---|---|
| `--queue dwr` / `--queue drq` (기본: 둘 다), `--dry-run`, `--public-log`, `--sa-key` | 모든 명령 |
| `--limit`, `--batch`, `--workers`, `--only-rows`, `--no-retry-errors`, `--include-blank`, `--no-reconcile`, `--max-mb`, `--parse-retries`, `--accept-first-report`, `--max-output-tokens`, `--no-thumbnail`, `--model`, `--api-version` | `sync db` 의 AI 부분과 `check access` |
| `--no-ai` | `db` 단계 |
| `--legacy-spreadsheet`, `--overwrite` | `queue` 단계 |
| `--allow-large-removal` | `queue` 와 `db` 단계 |

두 단계가 모두 실행될 때(`sync`, `sync queue db`):

- 각 단계는 제목 줄(`▶ Step 1/2 - sync queue: legacy sheet -> queue`)로 시작하고 마지막 줄이 결과를 요약합니다(`Sync steps: queue OK, db OK`).
- `queue` 단계에서 실패했거나 안전장치에 거부되었거나 아무것도 만들지 못한 큐는 **`db` 단계에서 빠집니다**(로그에 어느 큐인지 표시). 실패한 부분이 다른 부분을 **멈추지는 않지만** 종료 코드는 2 입니다.
- `--dry-run` 이면 아무것도 쓰지 않고, `db` 단계는 **`queue` 단계가 계획한 큐 위에서** 계획을 보여 줍니다(예: 추가될 행). 그래서 `sync --dry-run` 이 전체 흐름의 미리보기가 됩니다.
- 실제 실행에서는 `db` 단계가 큐 시트를 다시 읽습니다(`queue` 단계가 쓴 내용과 그사이 누군가 바꾼 내용).

**옛 명령.** `migrate_queue.py` 는 `pmis.py` 로 바뀌었습니다. 옛 파일도 계속 동작합니다: 옛 옵션을 새 명령으로 바꿔 실행하고 새 명령을 stderr 로 알려 줍니다. `pmis.py` 자체는 옛 옵션을 받지 않고 대신 무엇을 입력할지 알려 줍니다.

| 옛 명령 | 새 명령 |
|---|---|
| `python migrate_queue.py` (AI 였음) | `python pmis.py sync db` (1부가 DB 행을 먼저 고치고, 이어서 AI 부분이 PDF 를 읽음) |
| `python migrate_queue.py --extract` | `python pmis.py sync queue` |
| `python migrate_queue.py --extract --sync-db` | `python pmis.py sync queue db --no-ai` |
| `python migrate_queue.py --sync-db` | `python pmis.py sync db --no-ai` |
| `python migrate_queue.py --check-access` | `python pmis.py check access` |
| `python migrate_queue.py --backfill-cp` | 삭제됨: `python pmis.py sync db --no-ai` 가 C/P 를 채우고 갱신함 (1부의 나머지도 함) |

나머지 옵션(`--queue`, `--dry-run`, `--limit`, ...)은 그대로입니다.

## 권장 순서

```powershell
python pmis.py sync --dry-run                           # 전체 흐름의 계획: 큐와 DB 에서 무엇이 바뀌는지, AI 가 어떤 행을 읽을지 (아무것도 쓰지 않음)
python pmis.py check access --limit 0                   # 서비스 계정이 두 큐의 모든 PDF 를 열 수 있는지 확인
python pmis.py sync queue                               # 옛 시트 -> 큐 (새 항목과 바뀐 PDF 는 Waiting 이 됨)
python pmis.py sync db --no-ai --dry-run                # 1부만: DB 행이 몇 개 바뀔지 (Gemini 키 불필요)
python pmis.py sync db --no-ai                          # 1부를 실제로 실행
python pmis.py sync db --limit 5                        # AI 실제 테스트: 큐마다 최대 5행. 기존 방식으로 Done 된 행과 비교
python pmis.py sync db --queue dwr --only-rows 83,102,127-128   # 이전에 개별 오류가 났던 행만 다시 시도 (--queue 필요, AI 만 실행)
python pmis.py sync db --limit 200                      # 나눠서 실행하고 결과를 확인한 뒤 늘리기 (0 = 전체)
python pmis.py sync                                     # 전체 흐름을 실제로 실행
```

**큐 선택:** `--queue` 를 생략하면 **두 큐 모두** 처리합니다. DWR(`MIGRATION_QUEUE` -> `DB_DWR`)을 먼저, 이어서 DRQ(`MIGRATION_QUEUE_DRQ` -> `DB_DRQ`)를 처리하며 `--limit` 은 **큐마다** 적용됩니다. 한 큐만 처리하려면 `--queue dwr` 또는 `--queue drq` 를 지정하세요.
`--only-rows` 는 특정 큐 시트의 행 번호이므로 `--queue` 와 함께 써야 하며, `sync db` 와 `check access` 에서만 쓸 수 있습니다.
첫 번째 큐가 AI 부분에서 도중에 중단되면(예: Gemini 가 402/401/403 을 반환하거나 429 가 반복됨) 같은 이유로 멈출 것이므로 두 번째 큐는 건너뜁니다. 한 큐 시트를 읽지 못하면(예: 탭이 없음) 다른 큐는 계속 처리합니다. 두 경우 모두 종료 코드는 2 입니다.

대상 시트는 **환경 변수 `PMIS_SPREADSHEET_ID` 로만** 지정합니다. 코드에 기본값이 없고 명령줄 옵션도 없으며, 변수가 없거나 비어 있으면 안내 메시지와 함께 멈춥니다 (`--dry-run` 포함 모든 모드). ID 는 시트 URL 에서 `/d/` 와 `/edit` 사이의 값입니다: `https://docs.google.com/spreadsheets/d/<ID>/edit`.

## `queue` 단계: 큐 만들기/맞추기 (`sync queue`)

`extractLegacyDwrLinks()` / `extractLegacyDrqLinks()` 를 대체합니다. Sheets API 로 옛 시트(값, 하이퍼링크, 날짜 서식)를 읽어 `MIGRATION_QUEUE` / `MIGRATION_QUEUE_DRQ` 에 기록하며, 규칙은 Apps Script 와 같습니다.

- 3행이 헤더, 4행부터 데이터입니다. Date(B열)가 비어 있는 행은 건너뛰고, B열의 하이퍼링크가 `Folder URL` 이 됩니다.
- E열부터 **BD**열(DWR) 또는 **BF**열(DRQ)까지 값이 있는 칸마다 한 항목입니다. 열 헤더에서 `(P)` / `(C)` 를 뗀 것이 `Discipline` 이고, 헤더에 `(C)` 가 있으면 `Status(C/P)` 는 `C`, 아니면 `P` 입니다.
- `Doc No` = A열 + `-` + Discipline + `-` + 칸 값 이며, 그 칸의 하이퍼링크가 `PDF URL` 입니다.
- Date + Discipline + 칸 값이 같으면 하나의 항목입니다. `(C)` 칸이 다시 나오면 그 링크(있을 때)가 PDF URL 을 대체하고 항목은 `C` 가 됩니다.
- 모든 행은 `Waiting` 으로 시작합니다.

옛 시트의 탭 이름은 코드에 **없습니다**. 스프레드시트에 보이는 그대로(괄호와 공백 포함) 환경 변수로 지정하세요.

```powershell
$env:PMIS_LEGACY_DWR_SHEET = "<DWR 탭 이름>"
$env:PMIS_LEGACY_DRQ_SHEET = "<DRQ 탭 이름>"
python pmis.py sync queue --dry-run     # 옛 시트를 읽어 개수와 샘플 행만 표시 (아무것도 쓰지 않음)
python pmis.py sync queue               # 두 큐 모두 생성
python pmis.py sync queue --queue drq   # 한 큐만 (그 큐의 탭 이름 변수만 있으면 됨)
```

- `sync queue` 는 `GEMINI_API_KEY` 도 Drive 접근도 필요 없고, `--limit` 은 무시하며, `--only-rows` 는 받지 않습니다.
- 큐마다 고유 항목 수, PDF 링크가 없는 항목 수(이 행들은 이식할 때 `Error: No URL` 이 됨), 헤더가 비어 건너뛴 칸 수를 콘솔에 보여 줍니다.
- **맞추기(sync, 큐에 이미 행이 있을 때의 기본 동작):** `sync queue` 는 **큐의 모든 행을 옛 시트에서 새로 계산**(Doc No, Date, Discipline, PDF URL, Folder URL, C/P. 행 순서는 옛 시트 순서)한 뒤 지금의 큐와 비교합니다. 같은 항목의 행이 이미 있으면 새 행은 **그 행의 상태를 그대로 유지**(G열: `Done`, `Error…`, `Waiting`)하고 나머지 열은 옛 시트를 따릅니다. **예외: 옛 시트에서 PDF(Drive file ID)가 바뀐 `Done` 행은 `Waiting` 으로 되돌아가고**, 옛 PDF 의 file ID 가 H열(`Replaces PDF (old file ID)`)에 기록되어 다음 `sync db` 의 AI 부분이 새 파일을 읽고 옛 파일을 쓰던 DB 행을 바꿉니다("PMIS 를 옛 시트와 맞추기" 참고). 링크 형식만 바뀐 것(같은 file ID)은 새 PDF 가 아닙니다. 행이 없는 항목은 새 `Waiting` 행이 됩니다. 옛 시트에 더 이상 항목이 없는 기존 행은 **삭제**되어 큐는 항상 옛 시트와 같은 내용이 됩니다(삭제된 행 중 `Done` / `Error…` 였던 행 수를 콘솔이 경고합니다). 바뀐 것이 없으면 아무것도 쓰지 않으므로 예약 실행도 안전합니다. `--dry-run` 은 계획을 작은 표(`Kept from the queue`, `New`, `Duplicate rows merged`, `Removed (not in the legacy sheet)`, `Statuses kept`)로 보여 줍니다. 쓰기는 A~H열만 제자리에서 덮어씁니다(먼저 비우지 않으므로 중간에 실패해도 비지 않으며, `I` / `K` 열의 시각은 건드리지 않음). **안전장치:** 한도(행의 10% 와 100 중 큰 값)보다 많은 행을 지우게 되면 거부합니다(옛 시트가 잘못 비었을 때 큐를 보호). `--allow-large-removal` 로 허용할 수 있습니다.
- **항목의 기존 행 찾기:** 같은 `(Doc No, Date)` 로, 또는(옛 시트의 `No.`(A열)만 바뀐 경우) 같은 **Date + Discipline + 칸의 값** 으로 찾습니다(`Doc No` = `No.` + `-` + Discipline + `-` + 칸의 값 이므로 `No.` 뒤쪽은 그대로입니다). 새 행은 새 `Doc No` 를 갖고 콘솔에 `N renumbered` 가 표시됩니다(`--dry-run` 은 `sample (renumbered): row 5: 이전 -> 새 값`). 한 항목에 **행이 여러 개**(예: 옛 `Doc No` 와 새 `Doc No` 로 두 번 들어간 경우)이면 처리된 행의 상태를 유지하는 하나로 합칩니다(`Done` > `Error…` > `Waiting`, 같으면 앞쪽 행). **Date** 도 바뀌면 새 항목으로 취급합니다.
- **다시 만들기:** `--overwrite` 를 붙이면 큐를 비우고 모든 행을 `Waiting` 으로 다시 만듭니다(Apps Script 와 동일. 모든 `Done` / `Error…` 상태가 지워지니 G열을 먼저 백업하세요). 큐 탭이 없거나 비어 있으면 그냥 새로 만듭니다. 탭의 헤더 행이 `Doc No | Date | Discipline | PDF URL | Folder URL | Status(C/P) | AI Processed` 가 아니면, 고치거나 `--overwrite` 를 붙이기 전까지 `sync queue` 는 아무것도 쓰지 않고 종료 코드 2 로 끝납니다.
- **새로고침 시각:** 실제로 큐를 쓴 `sync queue`(`--dry-run` 제외)마다 `I1` = `Last extract (refresh time)`, `I2` = 태국 시간(UTC+7)의 시각(예: `2026-10-22 14:05:33 (UTC+7)`)이 기록되어 시트에서 마지막 새로고침 시점을 알 수 있습니다. I열은 큐 데이터(A~G열) 밖입니다.
- **옛 탭이 다른 파일에 있을 때:** 기본은 옛 탭이 큐와 같은 스프레드시트에 있는 경우입니다. 다른 파일에서 읽으려면 `--legacy-spreadsheet <ID 또는 URL>` 을 쓰거나 `PMIS_LEGACY_SPREADSHEET_ID` 를 설정하세요. 큐는 계속 `PMIS_SPREADSHEET_ID` 에 기록됩니다. 그 파일을 서비스 계정과 공유하세요(보기 권한이면 충분).
- **`.env`:** 위의 모든 변수는 스크립트 옆의 `.env` 파일에 적을 수 있습니다(`.env.example` 참고). 이미 설정된 환경 변수가 우선합니다.
- Apps Script 와 다른 점: 큐 값을 일반 텍스트(`RAW`)로 기록하므로 날짜·숫자·앞자리 0 이 자동 변환되지 않고, 큐 시트의 값만 지우며(서식은 유지), 행을 append 로 추가하므로 시트가 알아서 늘어납니다.
- 서비스 계정에는 스프레드시트 **편집자(Editor)** 권한이 필요합니다(옛 탭을 읽고 큐 탭에 기록). PDF 는 건드리지 않습니다.

## PMIS 를 옛 시트와 맞추기

목표: PMIS(큐와 `DB_DWR` / `DB_DRQ`)의 내용이 항상 옛 시트와 같아지는 것입니다. 흐름은 **옛 시트 -> (`sync queue`) -> 큐 -> (`sync db`) -> DB** 이고, `python pmis.py sync` 가 이 전부를 실행합니다. `sync queue` 는 **AI 가 읽어야 할 PDF 가 무엇인지**만 정하고(새 항목이나 PDF 가 바뀐 항목은 `Waiting` 이 됨), 옛 시트에서 오는 나머지 정보는 `sync db` 가 맞춥니다:

| 옛 시트의 변경 | PMIS 에서 일어나는 일 |
|---|---|
| 새 항목 | `sync queue` 가 `Waiting` 행을 추가하고, 다음 `sync db` 의 AI 부분이 DB 행을 추가 |
| `No.`(A열) 변경 | `sync queue` 가 같은 항목의 `Doc No` 를 바꿈 (중복 행 없음) |
| `Done` 항목의 PDF 변경 | `sync queue` 가 행을 `Waiting` 으로 되돌리고 옛 file ID 를 H열에 기록. `sync db` 가 옛 DB 행을 `Outdated` 로 표시하고, AI 부분이 새 파일을 읽고 **옛 DB 행을 제자리에서 교체**(아래 참고)한 뒤 행은 다시 `Synced`. 실패하면 옛 DB 행은 `Outdated` 로 남고 H열도 유지 |
| 항목의 PDF 링크를 지움 | `sync queue` 는 행을 유지하고, `sync db` 1부가 그 PDF 의 DB 행을 지움(먼저 `_REMOVED_...` 에 복사). 링크가 다시 생기면 다시 읽음(DB 에 새 행) |
| C/P 나 폴더 링크(B열의 링크) 변경 | `sync queue` 가 큐 행을 갱신하고, `sync db` 1부가 DB 행의 C/P / Folder Link 를 갱신. AI 는 쓰지 않고 행을 `Waiting` 으로 만들지도 않음 |
| 항목 삭제 | `sync queue` 가 큐 행을 지우고, `sync db` 1부가 옛 시트에서 온(`Synced` / `Outdated` 표시) DB 행 중 PDF 가 큐에 더 이상 없는 것을 지움 |

- **누가 무엇을 쓰는가:** `sync queue` 는 큐 탭에만 씁니다. `sync db` 는 DB 탭에 쓰고, AI 가 행을 끝냈을 때 큐의 상태 열(G열 `Done` / `Error…`, H열, `K1:K2` 의 시각)에도 씁니다. 옛 시트가 바뀌면 DB 는 다음 `sync db` 에서 따라옵니다. `sync queue` 만 실행해서는 DB 탭에 쓰지 않으므로 표시는 마지막 `sync db` 시점의 상태를 반영합니다.
- **제자리 교체:** 옛 파일을 쓰던 DB 행(옛 file ID 로 찾음)을 같은 행에서 새 데이터로 덮어씁니다. 마이그레이션 데이터가 아닌 열은 건드리지 않습니다: A열(`CSC Ref. No`)과 비어 있는 `Ext1` / `Ext2`. 다른 큐 행이 아직 옛 PDF 를 쓰고 있거나 옛 파일이 DB 에 없으면 새 행을 추가합니다. 행이 `Done` 이 되면 H열을 비웁니다.
- **`sync db` 1부**(`python pmis.py sync db --no-ai`. `sync` 와 `sync queue db` 는 `queue` 단계 뒤에 실행하며, `--dry-run` 이면 `queue` 단계가 계획한 행 기준): DB 의 각 행에 대해 **같은 PDF**(Drive 파일 ID)의 큐 행을 찾아
  - 큐에 값이 하나뿐이고 DB 값과 다르면 **C/P** 와 **Folder Link** 를 갱신합니다(큐의 값이 비어 있으면 DB 값을 지우지 않고, 형식이 달라도 같은 Drive ID 를 가리키는 링크는 변경으로 보지 않음).
  - **`Legacy sync` 표시**(아래)를 설정합니다.
  - PDF 가 큐에 더 이상 없는 DB 행을 **삭제**합니다. **Status 가 `AI Migrated` 이고 표시가 `Synced` 또는 `Outdated` 인 행만** 지웁니다. 즉 옛 시트에서 왔다가 사라진 행입니다(사람이 직접 넣은 행, 그리고 옛 시트에서 온 적이 없는 행, 예를 들어 다른 시스템이 넣은 문서는 절대 지우지 않음). H열에서 교체를 기다리는 PDF 의 행은 남깁니다. **지우기 전에 행을 `_REMOVED_DB_DWR` / `_REMOVED_DB_DRQ` 탭에 복사**하므로(시각, 탭, 원래 행 번호, 이유, 원래 셀 전부. 탭이 없으면 만듦) 아무것도 잃지 않습니다.
  쓰기 직전에 PDF 열을 다시 읽어, 그사이 행이 끼어들거나 사라졌으면 아무것도 바꾸지 않고 중단합니다. 바뀐 것이 없으면 두 번째 실행은 아무것도 쓰지 않습니다.
- **안전장치:** 큐에 행이 없거나 지울 행이 한도(DB 행의 10% 와 100 중 큰 값)를 넘으면 그 DB 에는 아무것도 쓰지 않습니다(`--allow-large-removal` 로 해제, `--dry-run` 은 이 때문에 실패하지 않음). 행이 실제로 삭제되므로 위치로 DB 행을 참조하는 다른 시트는 밀릴 수 있습니다. 먼저 `--dry-run` 으로 계획을 확인하세요.
- **GitHub Actions 에서:** "GitHub Actions 에서 실행" 참고: **Sync all** 은 전체 흐름을 실행하고, **Sync queue and DB (no AI)** 는 `sync queue db --no-ai` 를 실행하며(`db` 단계는 `db` 입력이며 기본 켜짐) **Sync DB (with AI)** 는 `sync db` 를 실행합니다. 먼저 `mode = dry-run` 으로 실행하세요. 로그에는 집계 숫자만 나옵니다.
- **`Legacy sync` 표시:** C/P 열 바로 뒤의 열(**`DB_DWR` 은 `U`, `DB_DRQ` 는 `T`**, 머리글 `Legacy sync`)이 DB 행이 옛 시트와 맞는지 알려 줍니다:

  | 표시 | 의미 |
  |---|---|
  | `Synced` | 옛 시트에서 온 행이며 **확인됨**: PDF 가 큐에 있고 C/P 와 Folder Link 가 큐와 같음 (AI 부분이 쓰는 행은 쓰는 순간 이 표시가 붙음) |
  | `Outdated` | 옛 시트에서 온 행이지만 맞지 않음: PDF 가 바뀌어 AI 가 새 파일로 교체하기를 기다리는 중(옛 PDF 는 큐의 H열에 있음), 또는 큐에 같은 PDF 의 C/P / Folder Link 값이 서로 다르게 여러 개 있고(같은 파일을 여러 항목이 씀) DB 값이 그중 어느 것과도 같지 않음 - 추측해서 덮어쓰지 않음 |
  | 비어 있음 | 옛 시트에서 온 적이 없는 행(예: 다른 시스템이 넣은 문서), 이 도구가 쓰지 않은 행(Status 가 `AI Migrated` 가 아님, 그래서 지우지도 않음), 또는 PDF 링크가 없는 행 |

  표시는 어느 행이 옛 시트에서 왔는지의 기억이기도 합니다: **표시가 붙은 적 없는 행은 절대 지워지지 않으므로** 다른 시스템이 넣은 문서나 옛 시트에 없는 행은 남습니다. 그래서 처음 실행하는 `sync db` 는 표시만 붙이고(아무것도 지우지 않음), 표시가 붙었다가 옛 시트에서 사라진 행만 지웁니다. 첫 표시를 쓰기 전에 C/P 열처럼 머리글 칸을 확인합니다: 비어 있으면 `Legacy sync` 를 채우고, 다른 머리글이면 그 DB 를 중단합니다. 삭제된 행은 `_REMOVED_...` 복사본에 표시가 그대로 남습니다.
- **그대로 두는 것:** 같은 PDF(또는 같은 Doc No)의 DB 행이 여러 개인 것은 건드리지 않습니다. 옛 시트에서 온 PDF 중 큐에 더 이상 없는 것만 지웁니다. `sync queue` 가 읽지 않는 `BD` / `BF` 이후 열의 PDF 는 큐에 없으며, 그 행은 전에 표시가 붙은 적이 있을 때만 지워집니다.

## DB 의 C/P 와 Folder Link

DB 의 모든 행에는 큐 행의 **C/P** 값(F열 `Status(C/P)`)이 그대로 복사되어 **`DB_DWR` 의 T열**과 **`DB_DRQ` 의 S열**(둘 다 머리글 `Status (C/P)`)에 들어가고, 그 뒤에 `Legacy sync` 표시 열(`DB_DWR` 은 `U`, `DB_DRQ` 는 `T`. "PMIS 를 옛 시트와 맞추기" 참고)이 옵니다. **Folder Link**(`DB_DWR` 의 S열, `DB_DRQ` 의 P열)는 큐의 `Folder URL` 에서 옵니다. 기존 열은 움직이지 않습니다: `DB_DWR` 은 계속 A~S(`Folder Link` 가 S)이고, `DB_DRQ` 의 새 열은 `Folder Link`(P)와 `Ext1` / `Ext2`(Q, R) 바로 뒤에 옵니다.

- AI 부분은 행을 쓸 때 두 값을 모두 씁니다. 그 뒤로는 `sync db` 1부가 값을 바로잡습니다: 큐와 다른 C/P 나 Folder Link 는 큐의 값으로 덮어씁니다. C/P 열이 생기기 전에 이식된 행이 C/P 를 얻는 방법도 이것입니다(예전 `--backfill-cp` 가 하던 일): `python pmis.py sync db --no-ai --dry-run` 을 실행한 뒤 `python pmis.py sync db --no-ai` 를 실행하세요.
- 큐에서 같은 PDF 에 서로 다른 값이 여러 개 있으면(같은 파일을 여러 항목이 씀) 추측하지 않습니다: DB 값이 그중 하나이면 그 행은 문제없고, 어느 것과도 같지 않으면 행은 `Outdated` 가 되며 아무것도 덮어쓰지 않습니다.
- 처음 쓰기 전에 DB 탭의 머리글 칸(`DB_DWR` 은 `T1`, `DB_DRQ` 는 `S1`)을 확인합니다: 비어 있으면 `Status (C/P)` 를 채우고, 같은 머리글이면 그대로 두며, **다른 머리글이면 그 큐를 중단**하여 값이 다른 열 아래에 들어가지 않게 합니다. `Legacy sync` 머리글도 같은 방식으로 확인합니다.

## `check access` 가 확인하는 것

처리 대기 중인 각 행에서 PDF URL 열의 file ID 를 추출하고, 서비스 계정 권한으로 Drive 에 파일 메타데이터(이름, 형식, 크기, 휴지통 여부)를 요청합니다.
`No URL`, `Drive Access`(404/403: 파일이 없거나 서비스 계정에 공유되지 않음), 휴지통에 있는 파일을 보고합니다.

기본값은 두 큐를 차례로 모두 확인합니다 (한 큐만 확인하려면 `--queue dwr` 또는 `--queue drq`).
출력의 모든 줄은 큐 시트 이름으로 시작합니다. 예: `[MIGRATION_QUEUE] Reachable: 2/3 files`, `✗ [MIGRATION_QUEUE_DRQ] row 4: Drive Access | …`. 한 큐만 확인한 경우 마지막 줄에 다른 큐를 확인하는 명령어가 표시됩니다.

PDF 를 **다운로드하지 않고**, Gemini 를 **호출하지 않으며**(비용 없음), 시트에 **쓰지 않습니다**. 실제 PDF 인지, 시트 쓰기 권한이 있는지는 확인하지 않습니다.
PDF 가 `--max-mb` 보다 큰 행은 표지 이미지만 전송한다는 `ℹ` 줄로 표시됩니다 ("알아둘 점" 참고).
`--limit` 기본값이 30 이므로 모든 행을 확인하려면 `--limit 0` 을 지정하세요.

## GitHub Actions 에서 실행

workflow 세 개가 같은 스크립트를 GitHub 호스팅 러너에서 실행하므로 PC 를 켜 둘 필요가 없습니다:

| workflow (Actions 탭) | 파일 | 실행하는 것 | 필요한 것 |
|---|---|---|---|
| **Sync all** | `.github/workflows/sync.yml` | `python pmis.py sync` (queue -> db, AI 포함) | `GEMINI_API_KEY`, 옛 시트 변수 |
| **Sync queue and DB (no AI)** | `.github/workflows/sync-no-ai.yml` | `python pmis.py sync queue db --no-ai` (Gemini 호출 없음) | 옛 시트 변수 |
| **Sync DB (with AI)** | `.github/workflows/sync-db.yml` | `python pmis.py sync db` (1부, 이어서 AI), 또는 `check access` | `GEMINI_API_KEY` |

GitHub Free 플랜의 private 저장소는 매달 Actions 2,000분을 받습니다.

최초 1회 설정:

1. 저장소를 만들고 이 폴더를 push 합니다. **private** 을 권장합니다: workflow 는 집계 숫자만 출력(`--public-log`)하지만, public 저장소에서는 누구나 실행 로그와 실행 요약을 볼 수 있습니다.
2. 저장소 시크릿 2개와 변수 1개를 추가합니다 (Settings > Secrets and variables > Actions. 스프레드시트 ID 는 일반 설정이므로 **Variables** 탭에 넣습니다). PowerShell 에서 GitHub CLI 로도 가능합니다:

```powershell
Get-Content C:\path\to\secrets\sa-key.json -Raw | gh secret set SA_KEY_JSON   # 키 파일 전체
gh secret set GEMINI_API_KEY                                                    # 값을 물어봅니다
gh variable set PMIS_SPREADSHEET_ID --body "<스프레드시트 ID>"                    # 시크릿이 아닌 변수 (같은 이름의 시크릿도 예비로 동작)
```

3. 로컬 실행과 같은 준비를 합니다: Apps Script 트리거를 끄고, 시트와 PDF 를 서비스 계정 이메일에 공유합니다.
4. **Sync queue and DB (no AI)** 와 **Sync all** 에 필요한 옛 시트 설정을 저장소 **변수(variables)** 로 추가합니다(Settings > Secrets and variables > Actions > Variables. 자격 증명이 아닌 일반 설정이며 **Sync queue and DB (no AI)** 에는 `GEMINI_API_KEY` 가 필요 없음): `PMIS_LEGACY_DWR_SHEET`, `PMIS_LEGACY_DRQ_SHEET`(정확한 탭 이름), 그리고 선택으로 `PMIS_LEGACY_SPREADSHEET_ID`(그 탭이 들어 있는 파일이 대상 스프레드시트가 아닐 때, 서비스 계정과 공유). GitHub CLI 로는 `gh variable set PMIS_LEGACY_DWR_SHEET --body "<탭 이름>"`. 같은 이름의 시크릿도 예비로 계속 동작하며 변수가 우선합니다.

전체 맞추기: Actions 탭 > **Sync all** > Run workflow 에서 먼저 `mode = dry-run`(두 단계의 계획을 보여 주고 아무것도 쓰지 않음), 그다음 작은 `limit` 으로 `mode = sync`. `queue` 는 DWR / DRQ / 둘 다, `legacy_spreadsheet` 는 다른 파일 지정, `allow_large_removal` 은 안전 한도를 풉니다. 명령줄에서는 `gh workflow run sync.yml -f mode=dry-run`.

큐를 맞추고 AI 없이 DB 행만 고치기: Actions 탭 > **Sync queue and DB (no AI)** > Run workflow 에서 먼저 `mode = dry-run`(옛 탭을 읽고 아무것도 쓰지 않음), 그다음 `mode = sync`. `db`(기본 켜짐)는 `db` 단계의 1부도 실행해 DB_DWR / DB_DRQ 를 큐에 맞추고, `overwrite` 는 맞추지 않고 처음부터 다시 만듭니다(예약 실행에서는 끄세요). 명령줄에서는 `gh workflow run sync-no-ai.yml -f mode=dry-run`.

DB 행을 고치고 AI 가 대기 중인 PDF 를 읽게 하기: Actions 탭 > **Sync DB (with AI)** > Run workflow 에서 `mode` 를 고릅니다. 먼저 `dry-run`, 다음 `check-access`, 그다음 작은 `limit` 으로 `sync`, 이어서 더 크게. `only_rows` 를 주면 그 행에 대해 AI 만 실행되고, `allow_large_removal` 은 1부의 안전 한도를 풉니다. 명령줄에서는 `gh workflow run sync-db.yml -f mode=dry-run`.

- 단위 테스트(`test` 잡. workflow 자체를 확인하는 테스트 포함)가 먼저 실행되며, 통과해야 sync 가 시작됩니다.
- 어느 workflow 든 두 실행이 겹치지 않으며(셋 모두 같은 `concurrency` 그룹) 같은 `Waiting` 행이 두 번 처리되지 않습니다.
- 서비스 계정 키는 실행 중에만 임시 파일로 존재합니다.
- workflow 는 스크립트를 `--public-log` 로 호출합니다: 로그와 실행 요약에는 집계(큐 상태, Done/Error 합계, 중단 사유)만 나오고 탭 이름, 시트 이름/ID, Doc No, file ID, 오류 상세는 나오지 않으며 스프레드시트 ID 와 서비스 계정 이메일도 한 번 더 가려집니다. 스크립트가 stderr 로 쓰는 내용은 출력되지 않는 파일로 갑니다. 행별 상세는 큐 시트의 G열에 있고, 전체 로그가 필요하면 로컬에서(`--public-log` 없이) 실행하세요.
- workflow 에는 `schedule:` 이 없습니다(GitHub 자체 cron 이 실행을 시작해 주지 않았음). 자동으로 돌리려면 cron-job.org 같은 외부 스케줄러에서 workflow_dispatch API 를 호출하세요: `POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/sync.yml/dispatches`(또는 `sync-no-ai.yml` / `sync-db.yml`), body 는 `{"ref":"main","inputs":{"mode":"sync","limit":"30"}}`(`sync-no-ai.yml`: `{"ref":"main","inputs":{"mode":"sync"}}`, `sync-db.yml`: `{"ref":"main","inputs":{"mode":"sync","limit":"30"}}`), 토큰은 **Actions: Read and write** 권한의 fine-grained 토큰. 세 workflow 모두 `mode = sync` 는 실제 실행을 뜻합니다. 뒤의 둘은 sync 를 고치기 전에는 "Extract queue" / "Migrate queue"(`extract.yml` 과 `migrate.yml`. `mode = extract` / `run` 과 입력 `sync_db`)였습니다: 옛 파일 이름이나 값을 그대로 쓰는 호출은 실패하므로(옛 파일은 삭제됨) 바꿔 주세요.
- 공용 부분은 `.github/scripts/`(`with_credentials.sh` 는 키를 임시 파일에 쓰고 끝나면 삭제, `job_summary.sh`)와 `.github/actions/setup-python-deps/` 에 있습니다.

## 알아둘 점

- **마지막 마이그레이션 시각:** AI 부분의 실제 실행(`sync db`. `--dry-run` / `--no-ai` / `check access` 제외)이 끝날 때마다 처리한 큐 시트에 종료 시각을 기록합니다: `K1` = `Last migration run (finish time)`, `K2` = 태국 시간(UTC+7)의 시각(예: `2026-10-22 14:05:33 (UTC+7)`). 실행된 큐마다 기록되며(실행이 일찍 멈춰 건너뛴 큐는 건드리지 않음) 처리할 행이 없어도 기록합니다. `sync queue` 의 새로고침 시각(`I1:I2`) 옆, 큐 데이터(A~G열) 밖에 있습니다. 셀에 쓰지 못해도(예: 탭의 열이 11개 미만) 실행 결과는 그대로이며 경고만 남깁니다.
- G열이 `Waiting` **또는 `Error…`** 인 행을 처리합니다 (오류 행은 자동 재시도). `Done` 행은 건너뜁니다. `Waiting` 만 처리하려면 `--no-retry-errors` 를 사용하세요.
- 사라지지 않는 이유로 계속 실패하는 행(`Error: No URL`, `Error: Drive Access`)은 실행할 때마다 다시 시도됩니다. 바로 실패하며 Gemini 호출 비용은 들지 않습니다. 이런 행을 건너뛰려면 `--no-retry-errors` 를 사용하세요.
- 묶음(`--batch 30`) 단위로 처리합니다: 묶음마다 DB 에 한 번 쓰고, 성공한 뒤에만 `Done` 을 표시하므로 DB 쓰기가 실패해도 데이터 없는 `Done` 행이 생기지 않습니다.
- Gemini 가 **402 / 401 / 403**(크레딧 소진 / 잘못된 키)을 반환하면 즉시 전체 실행을 중단하고 나머지 행은 그대로 둡니다. **429** 가 연속되어도 중단합니다.
- 429 / 5xx / 네트워크 끊김은 자동 재시도(backoff, 최대 5회)하며, 그래도 실패하면 `Error: API <code> | …` 를 기록합니다.
- PDF 가 이미 DB 에 있으면(PDF Link 열의 file ID 비교) AI 호출 없이 `Done` 으로 표시합니다. 중간에 끊긴 실행을 다시 돌려도 중복 행이 생기지 않습니다.
- `--max-mb`(기본 **15 MB**, Apps Script `extractDriveFileForAI` 의 기준과 동일)보다 큰 PDF 는 전체를 보내지 않습니다. Apps Script 와 마찬가지로 Drive 썸네일(`sz=w2500`)의 **1페이지 표지 이미지**를 대신 전송하므로 첫 페이지만 분석됩니다. 이런 행도 `Done` 이 되지만, 실행 중 행마다 `ℹ` 줄이 출력되고(마지막에 합계도 출력) 검토할 수 있습니다. 대신 `Error: Too Large` 를 원하면 `--no-thumbnail`, 기준을 바꾸려면 `--max-mb` 를 사용하세요. 이미지는 실제 MIME 타입으로 전송됩니다 (Apps Script 는 `application/pdf` 로 표시합니다).
- 한도를 넘는 PDF 가 아닌 파일은 여전히 `Error: Too Large` 이고, Drive 가 썸네일 요청을 거부하면 `Error: Drive Access | … cover capture failed` 가 기록됩니다.
- 실패한 행에는 `Error: <label> | <detail>` 형식으로 원인이 기록됩니다. 현재 Apps Script 는 `Error: <label>` 만 기록하고 Drive 파일 ID 가 없는 행은 `Waiting` 으로 남겨 두지만, Python 스크립트는 그런 행을 `Error: No URL` 로 보고합니다. 이 오류 셀에 기록되는 모든 문구(레이블, 안내, 메시지)는 영어이며, 콘솔 로그도 영어입니다.
- **`Error: JSON Parse`** 는 Gemini 가 응답했지만 JSON 형식이 아니라는 뜻입니다. 흔한 원인: 응답이 **중간에 잘림**(`finishReason=MAX_TOKENS`, 인력/장비 목록이 아주 긴 도면에서 흔함), 텍스트 값 안의 큰따옴표나 줄바꿈이 이스케이프되지 않음, JSON 앞뒤에 설명 문장이 붙음. 이제 스크립트는 (1) 더 너그럽게 해석하고(문자열 안의 줄바꿈/탭, JSON 앞뒤 문장 허용), (2) 그래도 깨지면 Gemini 를 다시 호출하며(`--parse-retries`, 기본 **1**, 출력이 매번 조금씩 달라 재시도로 해결되는 경우가 많음, `0` 이면 끔) 재시도로 살아난 행은 `ℹ` 로 알려 주고, (3) 오류 셀에 이유를 남깁니다: 위치가 포함된 파서 메시지, `finishReason`, 재시도 횟수, AI 응답의 **앞부분과 뒷부분**(잘린 응답은 문장 중간에서 끝남). 셀에 `finishReason=MAX_TOKENS` 가 보이면 `--max-output-tokens 16000` 으로 다시 실행하세요 (기본은 Apps Script 처럼 미지정). 해당 행만 다시: `python pmis.py sync db --queue dwr --only-rows 231` (문서 번호가 아니라 시트의 행 번호).
- **PDF 하나에 보고서가 여러 개** (`JSON Parse | ValueError: expected a JSON object, got list`, 보고서가 여러 개 든 PDF 사례): 프롬프트는 객체 하나를 요구하지만 PDF 에 보고서가 둘 이상이면 Gemini 가 배열 `[ {…}, {…} ]` 로 답합니다. Apps Script 는 배열을 읽지 못해 빈 필드의 `Done` 행을 쓰게 되는데, 이 스크립트는 `DWR_No` / `DRQ_No` 가 큐 행의 **Doc No**(A열)와 같은 보고서를 고릅니다 (대소문자, `-`/공백, 앞의 0 은 무시하므로 `…-231` 은 `…-0231` 과 일치). 이 행은 `Done` 이 되지만 PDF 안의 나머지 보고서는 이관되지 않으므로 실행 중 `⚠` 줄과 마지막 합계로 알려 줍니다. 확인하세요. 일치하는 보고서가 없으면 지시문("번호가 … 인 보고서 하나만 객체로 답하라")을 덧붙여 한 번 다시 요청하고, 그래도 안 되면 셀에 `MultiReportError` 와 발견된 모든 보고서의 번호/날짜(`#1 DWR_No=… Date=…`)가 표시되어 A열 Doc No 가 틀렸는지 PDF 가 다른 문서인지 알 수 있습니다. `--accept-first-report` 를 쓰면 이 경우 오류 대신 첫 번째 보고서를 사용합니다 (확인한 뒤에만 사용).
- 기본 모델은 `gemini-3.8-flash` 로, `Code_Config.js` 의 `CONFIG_GEMINI_MODEL` 과 같은 값입니다. 둘은 서로 독립적이므로(스크립트는 Apps Script 파일을 읽지 않음) 한쪽을 바꾸면 다른 쪽도 바꾸세요. 이 스크립트의 모델은 `--model` 또는 환경 변수 `GEMINI_MODEL` 로 바꿀 수 있습니다.
- 출력 첫 줄에 시트 이름과 ID(`sheet: <spreadsheet name> (<id>)`)가 표시되고, 각 큐 제목 줄에도 이름이 반복됩니다(`===== Queue DWR: MIGRATION_QUEUE -> DB_DWR | sheet: <spreadsheet name> =====`). 이름을 읽지 못하면 그 이유가 대신 표시됩니다. 숫자가 이상하면 먼저 이 줄을 확인하세요. ID 는 환경 변수 `PMIS_SPREADSHEET_ID` 에서만 옵니다.
- 어떤 큐에서 **0행**이 나오면 이유를 알려 줍니다: 탭에 데이터 행이 없음(대개 잘못된 스프레드시트), G열 값이 `Waiting` / `Error…` 가 아님(실제 값을 `'Done'×120` 처럼 표시, 대소문자 구분), 또는 G열은 비었지만 URL 이 있음(`--include-blank` 사용).
- DB 는 `values.append` 로 기록하므로 행이 자동으로 늘어납니다 (DB_DWR / DB_DRQ 는 현재 1,000행뿐).
- 실행이 끝나면 토큰 사용량과 예상 비용(정가 기준)을 출력합니다.
- 코드 주석은 3개 언어(한국어 / `[TH]` / `[EN]`)로 작성되어 있고, 콘솔 출력(로그, `--help`)과 시트의 오류 셀에 기록되는 문구는 영어입니다.

## 프로젝트 구조

코드는 역할별로 나뉘어 있고 `pmis.py` 는 진입점일 뿐입니다(`python -m migrator` 로도 실행할 수 있습니다). `migrate_queue.py` 는 옛 진입점으로, 옛 옵션을 새 명령으로 바꿔 주기만 합니다.

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

## 테스트

```powershell
python -m pip install pytest pyyaml
python -m pytest -q
```
