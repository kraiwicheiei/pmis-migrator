# migrator - Python 으로 MIGRATION_QUEUE 처리하기

**언어 / Language / ภาษา:** [English](README.md) · [ไทย](README.th.md) · [한국어](README.ko.md)

`Code_Migration.js` 의 `runDwrAiMigrationTurbo` / `runDrqAiMigrationTurbo` 를 대체합니다. 프롬프트, 모델(`gemini-3.8-flash`),
DB_DWR / DB_DRQ 열 순서는 기존과 완전히 동일하지만 내 PC 에서 실행되므로 Apps Script 의 UrlFetch 쿼터에 걸리지 않습니다.
현재 Apps Script(`Code_Migration.js`, 대용량 PDF 는 `Code_Core.js` 의 `extractDriveFileForAI`)를 따릅니다.
큐를 만드는 `extractLegacyDwrLinks` / `extractLegacyDrqLinks` 도 대체합니다(`--extract` 참고).

## 개요: 누가 무엇을 하는가

| 단계 | 실행 위치 |
|---|---|
| 기존 시트 `<legacy DWR sheet>` / `<legacy DRQ sheet>` 에서 `MIGRATION_QUEUE` / `MIGRATION_QUEUE_DRQ` 큐 생성 | **이 Python 스크립트** 의 `--extract` (Apps Script 의 `extractLegacyDwrLinks()` / `extractLegacyDrqLinks()` 를 대체) |
| PDF 다운로드 -> Gemini -> DB_DWR / DB_DRQ 기록 -> `Done` 표시 | **이 Python 스크립트** |

> **큐 다시 만들기:** Apps Script 의 `extractLegacyDwrLinks()` / `extractLegacyDrqLinks()` 는 큐를 `clear()` 한 뒤 **모든** 항목(이미 DB_DWR / DB_DRQ 에 있는 PDF 포함)을 `Waiting` 으로 다시 만들고, 기존 `Done` / `Error…` 상태는 모두 지워집니다.
> Python 스크립트가 이를 보완합니다: PDF 가 이미 DB 에 있는 `Waiting` 행(PDF Link 열의 Drive 파일 ID 로 비교)은 AI 호출 없이 `Done` 으로 표시됩니다. `--extract` 는 `--overwrite` 를 붙였을 때만 같은 동작을 하며, 붙이지 않으면 이미 행이 있는 큐에 **합칩니다(merge)**: 모든 상태는 그대로 두고 새 항목만 추가합니다("큐 만들기" 참고). `--overwrite` 를 쓰기 전에는 항상 G열(과 DB)을 먼저 백업해 두세요.

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

`$env:` 로 설정한 값은 해당 PowerShell 창에서만 유효합니다. `python migrate_queue.py` 를 실행하는 같은 창에서 설정해야 하고, 새 창을 열 때마다 다시 설정해야 합니다.
영구 설정은 `setx GEMINI_API_KEY "..."` 후 새 창을 여세요 (값이 사용자 레지스트리에 암호화되지 않은 채 저장됩니다).
`--dry-run` 과 `--check-access` 는 `GEMINI_API_KEY` 를 쓰지 않으므로 이 두 모드에서는 설정하지 않아도 됩니다.

## 권장 순서

```powershell
python migrate_queue.py --dry-run                       # 두 큐에서 처리할 행만 표시 (API 호출 없음, 시트 쓰기 없음)
python migrate_queue.py --check-access --limit 0        # 서비스 계정이 두 큐의 모든 PDF 를 열 수 있는지 확인
python migrate_queue.py --limit 5                       # 실제로 큐마다 5행씩 테스트 후 기존 방식으로 Done 된 행과 비교
python migrate_queue.py --queue dwr --only-rows 83,102,127-128   # 이전에 개별 오류가 났던 행만 다시 시도 (--queue 필요)
python migrate_queue.py --limit 200                     # 나눠서 실행하고 결과를 확인한 뒤 늘리기 (0 = 전체)
python migrate_queue.py --queue drq --limit 5           # DRQ 큐만 (MIGRATION_QUEUE_DRQ -> DB_DRQ)
```

**큐 선택:** `--queue` 를 생략하면 **두 큐 모두** 처리합니다. DWR(`MIGRATION_QUEUE` -> `DB_DWR`)을 먼저, 이어서 DRQ(`MIGRATION_QUEUE_DRQ` -> `DB_DRQ`)를 처리하며 `--limit` 은 **큐마다** 적용됩니다. 한 큐만 처리하려면 `--queue dwr` 또는 `--queue drq` 를 지정하세요.
`--only-rows` 는 특정 큐 시트의 행 번호이므로 `--queue` 와 함께 써야 합니다.
첫 번째 큐가 도중에 중단되면(예: Gemini 가 402/401/403 을 반환하거나 429 가 반복됨) 같은 이유로 멈출 것이므로 두 번째 큐는 건너뜁니다. 한 큐 시트를 읽지 못하면(예: 탭이 없음) 다른 큐는 계속 처리합니다. 두 경우 모두 종료 코드는 2 입니다.

대상 시트는 **환경 변수 `PMIS_SPREADSHEET_ID` 로만** 지정합니다. 코드에 기본값이 없고 명령줄 옵션도 없으며, 변수가 없거나 비어 있으면 안내 메시지와 함께 멈춥니다 (`--dry-run` 포함 모든 모드). ID 는 시트 URL 에서 `/d/` 와 `/edit` 사이의 값입니다: `https://docs.google.com/spreadsheets/d/<ID>/edit`.

## 큐 만들기: `--extract`

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
python migrate_queue.py --extract --dry-run     # 옛 시트를 읽어 개수와 샘플 행만 표시 (아무것도 쓰지 않음)
python migrate_queue.py --extract               # 두 큐 모두 생성
python migrate_queue.py --extract --queue drq   # 한 큐만 (그 큐의 탭 이름 변수만 있으면 됨)
```

- `--extract` 는 `GEMINI_API_KEY` 도 Drive 접근도 필요 없고, `--limit` 은 무시하며, `--only-rows` / `--check-access` 와 함께 쓸 수 없습니다.
- 큐마다 고유 항목 수, PDF 링크가 없는 항목 수(이 행들은 이식할 때 `Error: No URL` 이 됨), 헤더가 비어 건너뛴 칸 수를 콘솔에 보여 줍니다.
- **합치기(merge, 큐에 이미 행이 있을 때의 기본 동작):** `--extract` 는 각 항목을 **Doc No + Date** 로 큐와 맞춰 보며 큐를 비우지 않습니다. 새 항목은 `Waiting` 으로 맨 끝에 추가됩니다. `Done` 행은 건드리지 않으며(옛 시트의 PDF 링크가 달라도 그대로 두고, 콘솔에 개수를 알려 줍니다), 그 밖의 행(`Waiting`, `Error…`)은 G열 상태는 유지하되 Date, Discipline, PDF URL, Folder URL, C/P(B~F열)를 옛 시트 값으로 갱신합니다. 큐에만 있는 행은 건드리지 않습니다. 옛 시트가 바뀌지 않았다면 다시 실행해도 아무것도 바뀌지 않으므로 예약 실행도 안전합니다. `--dry-run` 은 계획(`+N new, M updated, K unchanged, D Done kept as is`)을 보여 줍니다.
- **다시 만들기:** `--overwrite` 를 붙이면 큐를 비우고 모든 행을 `Waiting` 으로 다시 만듭니다(Apps Script 와 동일. 모든 `Done` / `Error…` 상태가 지워지니 G열을 먼저 백업하세요). 큐 탭이 없거나 비어 있으면 그냥 새로 만듭니다. 탭의 헤더 행이 `Doc No | Date | Discipline | PDF URL | Folder URL | Status(C/P) | AI Processed` 가 아니면, 고치거나 `--overwrite` 를 붙이기 전까지 `--extract` 는 아무것도 쓰지 않고 종료 코드 2 로 끝납니다.
- **새로고침 시각:** 실제로 큐를 쓴 extract(`--dry-run` 제외)마다 `I1` = `Last extract (refresh time)`, `I2` = 태국 시간(UTC+7)의 시각(예: `2026-10-22 14:05:33 (UTC+7)`)이 기록되어 시트에서 마지막 새로고침 시점을 알 수 있습니다. I열은 큐 데이터(A~G열) 밖입니다.
- **옛 탭이 다른 파일에 있을 때:** 기본은 옛 탭이 큐와 같은 스프레드시트에 있는 경우입니다. 다른 파일에서 읽으려면 `--legacy-spreadsheet <ID 또는 URL>` 을 쓰거나 `PMIS_LEGACY_SPREADSHEET_ID` 를 설정하세요. 큐는 계속 `PMIS_SPREADSHEET_ID` 에 기록됩니다. 그 파일을 서비스 계정과 공유하세요(보기 권한이면 충분).
- **`.env`:** 위의 모든 변수는 스크립트 옆의 `.env` 파일에 적을 수 있습니다(`.env.example` 참고). 이미 설정된 환경 변수가 우선합니다.
- Apps Script 와 다른 점: 큐 값을 일반 텍스트(`RAW`)로 기록하므로 날짜·숫자·앞자리 0 이 자동 변환되지 않고, 큐 시트의 값만 지우며(서식은 유지), 행을 append 로 추가하므로 시트가 알아서 늘어납니다.
- 서비스 계정에는 스프레드시트 **편집자(Editor)** 권한이 필요합니다(옛 탭을 읽고 큐 탭에 기록). PDF 는 건드리지 않습니다.

## `--check-access` 가 확인하는 것

처리 대기 중인 각 행에서 PDF URL 열의 file ID 를 추출하고, 서비스 계정 권한으로 Drive 에 파일 메타데이터(이름, 형식, 크기, 휴지통 여부)를 요청합니다.
`No URL`, `Drive Access`(404/403: 파일이 없거나 서비스 계정에 공유되지 않음), 휴지통에 있는 파일을 보고합니다.

기본값은 두 큐를 차례로 모두 확인합니다 (한 큐만 확인하려면 `--queue dwr` 또는 `--queue drq`).
출력의 모든 줄은 큐 시트 이름으로 시작합니다. 예: `[MIGRATION_QUEUE] Reachable: 2/3 files`, `✗ [MIGRATION_QUEUE_DRQ] row 4: Drive Access | …`. 한 큐만 확인한 경우 마지막 줄에 다른 큐를 확인하는 명령어가 표시됩니다.

PDF 를 **다운로드하지 않고**, Gemini 를 **호출하지 않으며**(비용 없음), 시트에 **쓰지 않습니다**. 실제 PDF 인지, 시트 쓰기 권한이 있는지는 확인하지 않습니다.
PDF 가 `--max-mb` 보다 큰 행은 표지 이미지만 전송한다는 `ℹ` 줄로 표시됩니다 ("알아둘 점" 참고).
`--limit` 기본값이 30 이므로 모든 행을 확인하려면 `--limit 0` 을 지정하세요.

## GitHub Actions 에서 실행

`.github/workflows/migrate.yml` 은 같은 스크립트를 GitHub 호스팅 러너에서 실행하므로 PC 를 켜 둘 필요가 없습니다. GitHub Free 플랜의 private 저장소는 매달 Actions 2,000분을 받습니다.

최초 1회 설정:

1. 저장소를 만들고 이 폴더를 push 합니다. **private** 을 권장합니다: workflow 는 집계 숫자만 출력하지만, public 저장소에서는 누구나 실행 로그와 실행 요약을 볼 수 있습니다.
2. 저장소 시크릿 3개를 추가합니다 (Settings > Secrets and variables > Actions). PowerShell 에서 GitHub CLI 로도 가능합니다:

```powershell
Get-Content C:\path\to\secrets\sa-key.json -Raw | gh secret set SA_KEY_JSON   # 키 파일 전체
gh secret set GEMINI_API_KEY                                                    # 값을 물어봅니다
gh secret set PMIS_SPREADSHEET_ID                                               # 값을 물어봅니다
```

3. 로컬 실행과 같은 준비를 합니다: Apps Script 트리거를 끄고, 시트와 PDF 를 서비스 계정 이메일에 공유합니다.

실행: Actions 탭 > **Migrate queue** > Run workflow 에서 `mode` 를 고릅니다. 먼저 `dry-run`, 다음 `check-access`, 그다음 작은 `limit` 으로 `run`, 이어서 더 크게. 명령줄에서는 `gh workflow run migrate.yml -f mode=dry-run`.

- 단위 테스트(`test` 잡)가 먼저 실행되며, 통과해야 마이그레이션이 시작됩니다.
- 두 실행이 겹치지 않으므로(`concurrency`) 같은 `Waiting` 행이 두 번 처리되지 않습니다.
- 서비스 계정 키는 실행 중에만 임시 파일로 존재합니다.
- 전체 출력(file ID, 시트 이름, PDF 내용이 인용된 행별 오류)은 러너의 임시 파일에만 기록되고, 로그와 실행 요약에는 집계(큐 상태, Done/Error 합계, 중단 사유)만 나오며 스프레드시트 ID 와 서비스 계정 이메일은 가려집니다. 행별 상세는 큐 시트의 G열에 있고, 전체 로그가 필요하면 로컬에서 실행하세요.
- 자동 실행은 가능하지만 꺼져 있습니다: workflow 의 `schedule:` 줄 앞의 `#` 을 지우세요 (예약 실행은 `mode = run`, 두 큐 모두, `limit = 30`).

## 알아둘 점

- G열이 `Waiting` **또는 `Error…`** 인 행을 처리합니다 (오류 행은 자동 재시도). `Done` 행은 건너뜁니다. `Waiting` 만 처리하려면 `--no-retry-errors` 를 사용하세요.
- 묶음(`--batch 30`) 단위로 처리합니다: 묶음마다 DB 에 한 번 쓰고, 성공한 뒤에만 `Done` 을 표시하므로 DB 쓰기가 실패해도 데이터 없는 `Done` 행이 생기지 않습니다.
- Gemini 가 **402 / 401 / 403**(크레딧 소진 / 잘못된 키)을 반환하면 즉시 전체 실행을 중단하고 나머지 행은 그대로 둡니다. **429** 가 연속되어도 중단합니다.
- 429 / 5xx / 네트워크 끊김은 자동 재시도(backoff, 최대 5회)하며, 그래도 실패하면 `Error: API <code> | …` 를 기록합니다.
- PDF 가 이미 DB 에 있으면(PDF Link 열의 file ID 비교) AI 호출 없이 `Done` 으로 표시합니다. 중간에 끊긴 실행을 다시 돌려도 중복 행이 생기지 않습니다.
- `--max-mb`(기본 **15 MB**, Apps Script `extractDriveFileForAI` 의 기준과 동일)보다 큰 PDF 는 전체를 보내지 않습니다. Apps Script 와 마찬가지로 Drive 썸네일(`sz=w2500`)의 **1페이지 표지 이미지**를 대신 전송하므로 첫 페이지만 분석됩니다. 이런 행도 `Done` 이 되지만, 실행 중 행마다 `ℹ` 줄이 출력되고(마지막에 합계도 출력) 검토할 수 있습니다. 대신 `Error: Too Large` 를 원하면 `--no-thumbnail`, 기준을 바꾸려면 `--max-mb` 를 사용하세요. 이미지는 실제 MIME 타입으로 전송됩니다 (Apps Script 는 `application/pdf` 로 표시합니다).
- 한도를 넘는 PDF 가 아닌 파일은 여전히 `Error: Too Large` 이고, Drive 가 썸네일 요청을 거부하면 `Error: Drive Access | … cover capture failed` 가 기록됩니다.
- 실패한 행에는 `Error: <label> | <detail>` 형식으로 원인이 기록됩니다. 현재 Apps Script 는 `Error: <label>` 만 기록하고 Drive 파일 ID 가 없는 행은 `Waiting` 으로 남겨 두지만, Python 스크립트는 그런 행을 `Error: No URL` 로 보고합니다. 이 오류 셀에 기록되는 모든 문구(레이블, 안내, 메시지)는 영어이며, 콘솔 로그도 영어입니다.
- **`Error: JSON Parse`** 는 Gemini 가 응답했지만 JSON 형식이 아니라는 뜻입니다. 흔한 원인: 응답이 **중간에 잘림**(`finishReason=MAX_TOKENS`, 인력/장비 목록이 아주 긴 도면에서 흔함), 텍스트 값 안의 큰따옴표나 줄바꿈이 이스케이프되지 않음, JSON 앞뒤에 설명 문장이 붙음. 이제 스크립트는 (1) 더 너그럽게 해석하고(문자열 안의 줄바꿈/탭, JSON 앞뒤 문장 허용), (2) 그래도 깨지면 Gemini 를 다시 호출하며(`--parse-retries`, 기본 **1**, 출력이 매번 조금씩 달라 재시도로 해결되는 경우가 많음, `0` 이면 끔) 재시도로 살아난 행은 `ℹ` 로 알려 주고, (3) 오류 셀에 이유를 남깁니다: 위치가 포함된 파서 메시지, `finishReason`, 재시도 횟수, AI 응답의 **앞부분과 뒷부분**(잘린 응답은 문장 중간에서 끝남). 셀에 `finishReason=MAX_TOKENS` 가 보이면 `--max-output-tokens 16000` 으로 다시 실행하세요 (기본은 Apps Script 처럼 미지정). 해당 행만 다시: `python migrate_queue.py --queue dwr --only-rows 231` (문서 번호가 아니라 시트의 행 번호).
- **PDF 하나에 보고서가 여러 개** (`JSON Parse | ValueError: expected a JSON object, got list`, 보고서가 여러 개 든 PDF 사례): 프롬프트는 객체 하나를 요구하지만 PDF 에 보고서가 둘 이상이면 Gemini 가 배열 `[ {…}, {…} ]` 로 답합니다. Apps Script 는 배열을 읽지 못해 빈 필드의 `Done` 행을 쓰게 되는데, 이 스크립트는 `DWR_No` / `DRQ_No` 가 큐 행의 **Doc No**(A열)와 같은 보고서를 고릅니다 (대소문자, `-`/공백, 앞의 0 은 무시하므로 `…-231` 은 `…-0231` 과 일치). 이 행은 `Done` 이 되지만 PDF 안의 나머지 보고서는 이관되지 않으므로 실행 중 `⚠` 줄과 마지막 합계로 알려 줍니다. 확인하세요. 일치하는 보고서가 없으면 지시문("번호가 … 인 보고서 하나만 객체로 답하라")을 덧붙여 한 번 다시 요청하고, 그래도 안 되면 셀에 `MultiReportError` 와 발견된 모든 보고서의 번호/날짜(`#1 DWR_No=… Date=…`)가 표시되어 A열 Doc No 가 틀렸는지 PDF 가 다른 문서인지 알 수 있습니다. `--accept-first-report` 를 쓰면 이 경우 오류 대신 첫 번째 보고서를 사용합니다 (확인한 뒤에만 사용).
- 기본 모델은 `gemini-3.8-flash` 로, `Code_Config.js` 의 `CONFIG_GEMINI_MODEL` 과 같은 값입니다. 둘은 서로 독립적이므로(스크립트는 Apps Script 파일을 읽지 않음) 한쪽을 바꾸면 다른 쪽도 바꾸세요. 이 스크립트의 모델은 `--model` 또는 환경 변수 `GEMINI_MODEL` 로 바꿀 수 있습니다.
- 출력 첫 줄에 시트 이름과 ID(`sheet: <spreadsheet name> (<id>)`)가 표시되고, 각 큐 제목 줄에도 이름이 반복됩니다(`===== Queue DWR: MIGRATION_QUEUE -> DB_DWR | sheet: <spreadsheet name> =====`). 이름을 읽지 못하면 그 이유가 대신 표시됩니다. 숫자가 이상하면 먼저 이 줄을 확인하세요. ID 는 환경 변수 `PMIS_SPREADSHEET_ID` 에서만 옵니다.
- 어떤 큐에서 **0행**이 나오면 이유를 알려 줍니다: 탭에 데이터 행이 없음(대개 잘못된 스프레드시트), G열 값이 `Waiting` / `Error…` 가 아님(실제 값을 `'Done'×120` 처럼 표시, 대소문자 구분), 또는 G열은 비었지만 URL 이 있음(`--include-blank` 사용).
- DB 는 `values.append` 로 기록하므로 행이 자동으로 늘어납니다 (DB_DWR / DB_DRQ 는 현재 1,000행뿐).
- 실행이 끝나면 토큰 사용량과 예상 비용(정가 기준)을 출력합니다.
- 코드 주석은 3개 언어(한국어 / `[TH]` / `[EN]`)로 작성되어 있고, 콘솔 출력(로그, `--help`)과 시트의 오류 셀에 기록되는 문구는 영어입니다.

## 프로젝트 구조

코드는 역할별로 나뉘어 있고 `migrate_queue.py` 는 진입점일 뿐이라 이 안내서의 모든 명령은 그대로입니다. `python -m migrator` 로도 실행할 수 있습니다.

```text
migrate_queue.py        entry point (python migrate_queue.py ...)
migrator/
  cli.py                options, building the clients, main()
  settings.py           settings from the environment / .env
  runner.py             choosing the rows + the main loop
  processing.py         one row: Drive PDF -> Gemini -> DB row
  sheets.py drive.py gemini.py     the three APIs
  reports.py            parsing Gemini's JSON, building the DB rows
  extract.py            --extract (legacy_sheet.py reads, queue_writer.py writes / merges)
  specs.py config.py models.py utils.py clock.py
tests/                  one test file per module + fakes.py / legacy_fakes.py
```

## 테스트

```powershell
python -m pip install pytest
python -m pytest -q
```
