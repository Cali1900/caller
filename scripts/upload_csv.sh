#!/usr/bin/env bash
# CSV -> the POOL. This NEVER dials.
#   ./scripts/upload_csv.sh leads.csv
# Required columns: company,phone,timezone   Optional: city,state,segment,external_ref
set -euo pipefail
cd "$(dirname "$0")/.."
F="${1:?usage: upload_csv.sh <file.csv>}"
[[ -f "$F" ]] || { echo "no such file: $F" >&2; exit 1; }
set -a; . ./.env; set +a
curl -s -X POST --data-binary @"$F" \
     -H 'Content-Type: text/csv' \
     "http://127.0.0.1:${CALLER_API_PORT}/upload" | python3 -m json.tool
