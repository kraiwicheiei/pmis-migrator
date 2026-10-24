"""
GitHub Actions workflow 테스트: 구문, 공통 규칙, 그리고 각 workflow 의 bash 가 만드는 pmis.py 명령이 CLI 에서 유효한지 (자격 증명 스크립트는 인자만 출력하는 가짜로 바꿔 실행)
[TH] เทสต์ workflow ของ GitHub Actions: ไวยากรณ์, กฎร่วม และคำสั่ง pmis.py ที่ bash ของแต่ละ workflow สร้างว่าใช้ได้กับ CLI (รันโดยเปลี่ยนสคริปต์ credentials เป็นตัวปลอมที่พิมพ์แค่อาร์กิวเมนต์)
[EN] Tests for the GitHub Actions workflows: the syntax, the common rules, and that the pmis.py command each workflow's bash builds is valid for the CLI
     (the credentials script is swapped for a fake that only prints its arguments)
"""
import os
import shutil
import subprocess
import sys

import pytest

from migrator.cli import build_parser

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOWS = ("extract.yml", "migrate.yml", "sync.yml")
LEGACY_URL = "https://docs.google.com/spreadsheets/d/LEGACYFILEID123/edit#gid=0"


def load(name):
    return yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", name), encoding="utf-8"))


def find_bash():
    """
    bash 를 찾음. Windows 에서는 WSL 의 bash.exe 가 아니라 Git Bash 일 때만 사용 (경로 방식이 달라 오동작함)
    [TH] หา bash บน Windows ใช้เฉพาะ Git Bash ไม่ใช่ bash.exe ของ WSL (รูปแบบ path ต่างกันจนทำงานผิด)
    [EN] Finds bash; on Windows only Git Bash is used, not the bash.exe of WSL (its paths differ and it misbehaves here)
    """
    path = shutil.which("bash")
    if path and sys.platform == "win32" and not any(part in path.lower() for part in ("git", "usr\\bin", "msys")):
        return None
    return path


BASH = find_bash()


def step_script(workflow):
    """각 workflow 에서 pmis.py 를 부르는 단계의 bash / [TH] bash ของขั้นที่เรียก pmis.py ในแต่ละ workflow / [EN] The bash of the step that calls pmis.py in a workflow"""
    scripts = [step["run"] for job in load(workflow)["jobs"].values() for step in job["steps"] if "with_credentials.sh" in step.get("run", "")]
    assert len(scripts) == 1
    return scripts[0]


def run_step(tmp_path, workflow, env_over=None, **inputs):
    """
    그 단계를 실제 bash 로 실행하고 (종료 코드, python 에 넘어갈 인자, ::error:: 줄) 을 돌려줌
    [TH] รันขั้นนั้นด้วย bash จริงแล้วคืน (รหัสออก, อาร์กิวเมนต์ที่จะส่งให้ python, บรรทัด ::error::)
    [EN] Runs that step in a real bash and returns (exit code, the arguments that would reach python, the ::error:: line)
    """
    scripts = tmp_path / ".github" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "with_credentials.sh").write_text('#!/usr/bin/env bash\nfor a in "$@"; do printf "ARG:%s\\n" "$a"; done\n', newline="\n")
    env = dict(os.environ, PMIS_SPREADSHEET_ID="TARGETSHEETID", PMIS_LEGACY_DWR_SHEET="Tab (DWR)", PMIS_LEGACY_DRQ_SHEET="Tab (DRQ)", PMIS_LEGACY_SPREADSHEET_ID="",
               SA_KEY_JSON="{}", GEMINI_API_KEY="key", RUNNER_TEMP=str(tmp_path))
    env.update({f"IN_{name.upper()}": value for name, value in inputs.items()})
    env.update(env_over or {})
    done = subprocess.run([BASH, "-c", step_script(workflow)], cwd=str(tmp_path), env=env, capture_output=True, text=True, encoding="utf-8")
    args = [line[4:] for line in done.stdout.splitlines() if line.startswith("ARG:")]
    error = next((line for line in done.stdout.splitlines() if line.startswith("::error::")), "")
    return done.returncode, args, error


# --- 구문과 공통 규칙 / syntax and the common rules / ไวยากรณ์และกฎร่วม ---
def test_every_workflow_parses_runs_the_tests_first_and_shares_one_concurrency_group():
    groups = set()
    for name in WORKFLOWS:
        wf = load(name)
        jobs = wf["jobs"]
        assert "test" in jobs and all(job.get("needs") == "test" for key, job in jobs.items() if key != "test"), name
        groups.add(wf["concurrency"]["group"])
        assert wf["concurrency"]["cancel-in-progress"] is False and wf["permissions"] == {"contents": "read"}
        assert "schedule" not in wf[True]                                          # PyYAML reads the key `on` as True
        assert "workflow_dispatch" in wf[True]
    assert len(groups) == 1                                                        # a sync, a queue sync and an AI run never overlap


def test_the_workflows_are_named_after_what_they_run():
    assert [load(n)["name"] for n in WORKFLOWS] == ["Sync queue and DB (no AI)", "Sync DB (with AI)", "Sync all"]


def test_nothing_in_github_calls_the_old_entry_point():
    for folder, _, files in os.walk(os.path.join(ROOT, ".github")):
        for name in files:
            text = open(os.path.join(folder, name), encoding="utf-8").read()
            assert "migrate_queue.py" not in text, os.path.join(folder, name)
            assert "--extract" not in text and "--sync-db" not in text and "--check-access" not in text and "--backfill-cp" not in text, os.path.join(folder, name)


def test_the_inputs_that_existing_calls_send_are_still_accepted():
    # cron-job.org calls the API with these inputs (the file names and the choice values must not change)
    extract = load("extract.yml")[True]["workflow_dispatch"]["inputs"]
    migrate = load("migrate.yml")[True]["workflow_dispatch"]["inputs"]
    assert extract["mode"]["options"] == ["dry-run", "extract"] and migrate["mode"]["options"] == ["dry-run", "check-access", "run"]
    for inputs in (extract, migrate):
        assert inputs["queue"]["options"] == ["both", "dwr", "drq"]
    assert {"legacy_spreadsheet", "overwrite", "sync_db", "allow_large_removal"} <= set(extract)
    assert {"limit", "only_rows", "accept_first_report", "max_output_tokens"} <= set(migrate)
    assert load("sync.yml")[True]["workflow_dispatch"]["inputs"]["mode"]["options"] == ["dry-run", "sync"]


# --- 각 workflow 가 만드는 명령 / the command each workflow builds / คำสั่งที่แต่ละ workflow สร้าง ---
needs_bash = pytest.mark.skipif(BASH is None, reason="needs bash (Git Bash on Windows)")


@needs_bash
@pytest.mark.parametrize("workflow, inputs, expected", [
    ("extract.yml", dict(mode="dry-run", queue="both", sync_db="true", overwrite="false", allow_large_removal="false", legacy_spreadsheet=""),
     ["sync", "queue", "db", "--no-ai", "--public-log", "--dry-run"]),
    ("extract.yml", dict(mode="extract", queue="drq", sync_db="false", overwrite="false", allow_large_removal="false", legacy_spreadsheet=""),
     ["sync", "queue", "--no-ai", "--public-log", "--queue", "drq"]),
    ("extract.yml", dict(mode="extract", queue="both", sync_db="", overwrite="true", allow_large_removal="true", legacy_spreadsheet=LEGACY_URL),
     ["sync", "queue", "db", "--no-ai", "--public-log", "--overwrite", "--allow-large-removal", "--legacy-spreadsheet", LEGACY_URL]),
    ("migrate.yml", dict(mode="dry-run", queue="both", limit="30", only_rows="", accept_first="false", max_tokens=""),
     ["sync", "db", "--public-log", "--dry-run", "--limit", "30"]),
    ("migrate.yml", dict(mode="check-access", queue="dwr", limit="0", only_rows="", accept_first="false", max_tokens=""),
     ["check", "access", "--public-log", "--queue", "dwr", "--limit", "0"]),
    ("migrate.yml", dict(mode="run", queue="dwr", limit="200", only_rows="83,102,127-128", accept_first="true", max_tokens="16000"),
     ["sync", "db", "--public-log", "--queue", "dwr", "--limit", "200", "--only-rows", "83,102,127-128", "--max-output-tokens", "16000", "--accept-first-report"]),
    ("sync.yml", dict(mode="dry-run", queue="both", limit="30", legacy_spreadsheet="", allow_large_removal="false"),
     ["sync", "--public-log", "--dry-run", "--limit", "30"]),
    ("sync.yml", dict(mode="sync", queue="drq", limit="0", legacy_spreadsheet=LEGACY_URL, allow_large_removal="true"),
     ["sync", "--public-log", "--queue", "drq", "--limit", "0", "--allow-large-removal", "--legacy-spreadsheet", LEGACY_URL]),
])
def test_each_workflow_builds_a_command_the_cli_accepts(tmp_path, workflow, inputs, expected):
    status, args, error = run_step(tmp_path, workflow, **inputs)
    assert status == 0 and error == "" and args == ["python", "pmis.py"] + expected
    build_parser().parse_args(args[2:])                                            # the same arguments are valid for the parser


@needs_bash
def test_the_workflows_refuse_what_the_script_would_refuse_before_calling_it(tmp_path):
    # only_rows with both queues, an unknown mode, a missing Gemini key for a real run, a missing legacy tab name
    status, args, error = run_step(tmp_path, "migrate.yml", mode="run", queue="both", limit="30", only_rows="5", accept_first="false", max_tokens="")
    assert status == 1 and args == [] and "only_rows needs queue = dwr or drq" in error
    status, args, error = run_step(tmp_path, "migrate.yml", mode="bogus", queue="both", limit="30", only_rows="", accept_first="false", max_tokens="")
    assert status == 1 and "Unknown mode: bogus" in error
    sync = dict(queue="both", limit="30", legacy_spreadsheet="", allow_large_removal="false")
    status, args, error = run_step(tmp_path, "sync.yml", env_over={"GEMINI_API_KEY": ""}, mode="sync", **sync)
    assert status == 1 and args == [] and "GEMINI_API_KEY is missing or empty (needed for mode = sync)" in error
    assert run_step(tmp_path, "sync.yml", env_over={"GEMINI_API_KEY": ""}, mode="dry-run", **sync)[0] == 0           # a dry run needs no Gemini key
    status, args, error = run_step(tmp_path, "migrate.yml", env_over={"GEMINI_API_KEY": ""}, mode="run", queue="dwr", limit="30", only_rows="", accept_first="false", max_tokens="")
    assert status == 1 and "GEMINI_API_KEY is missing or empty (needed for mode = run)" in error
    status, args, error = run_step(tmp_path, "sync.yml", env_over={"PMIS_LEGACY_DWR_SHEET": ""}, mode="dry-run", **sync)
    assert status == 1 and "PMIS_LEGACY_DWR_SHEET is missing or empty" in error
    assert run_step(tmp_path, "sync.yml", env_over={"PMIS_LEGACY_DWR_SHEET": ""}, mode="dry-run", **dict(sync, queue="drq"))[0] == 0   # only the DRQ tab is needed for the DRQ queue
    status, args, error = run_step(tmp_path, "extract.yml", env_over={"PMIS_SPREADSHEET_ID": ""}, mode="dry-run", queue="both", sync_db="true", overwrite="false",
                                   allow_large_removal="false", legacy_spreadsheet="")
    assert status == 1 and "PMIS_SPREADSHEET_ID is missing or empty" in error
