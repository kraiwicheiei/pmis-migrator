#!/usr/bin/env bash
# Adds the (already public-safe) output of a run to the job summary.
#
#   PMIS_SPREADSHEET_ID=... bash .github/scripts/job_summary.sh "Sync DB run" "$RUNNER_TEMP/counts.log"
#
# The output comes from pmis.py --public-log (counts only); the spreadsheet ID is masked once more, to be safe.
set -euo pipefail

title="${1:?title}"
log="${2:?log file}"
[[ -f "$log" ]] || exit 0

{
  echo "### $title"
  echo
  echo '```'
  tail -n 40 "$log" | sed "s/${PMIS_SPREADSHEET_ID:-__none__}/***/g"
  echo '```'
} >> "$GITHUB_STEP_SUMMARY"
