#!/usr/bin/env bash
# Runs a command with the Google service-account key available, and removes the key afterwards.
#
#   SA_KEY_JSON=... bash .github/scripts/with_credentials.sh python pmis.py ...
#
# SA_KEY_JSON is the whole contents of the key file. It is written to a temporary file readable only by this user
# (GOOGLE_APPLICATION_CREDENTIALS points to it), the service-account e-mail and project ID are masked in the Actions log,
# and the file is deleted when the command ends, whatever the result.
set -euo pipefail
die() { echo "::error::$*"; exit 1; }

[[ -n "${SA_KEY_JSON:-}" ]] || die "Secret SA_KEY_JSON is missing or empty"
[[ $# -gt 0 ]] || die "with_credentials.sh needs a command to run"

umask 077
key_file="$RUNNER_TEMP/sa-key.json"
trap 'rm -f "$key_file"' EXIT
printf '%s' "$SA_KEY_JSON" > "$key_file"
unset SA_KEY_JSON

python -c 'import json,sys; json.load(open(sys.argv[1]))' "$key_file" 2>/dev/null || die "Secret SA_KEY_JSON is not valid JSON (paste the whole key file)"
# Also hide the service-account e-mail and project ID, should anything print them
for key_field in client_email project_id; do
  value=$(python -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2]) or "")' "$key_file" "$key_field")
  if [[ -n "$value" ]]; then echo "::add-mask::$value"; fi
done

export GOOGLE_APPLICATION_CREDENTIALS="$key_file"
"$@"
