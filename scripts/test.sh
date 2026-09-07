#!/usr/bin/env bash
# Run the suite inside the caller-api image, so tests use exactly the
# dependency set the service runs with. The host python is externally
# managed (PEP 668) and is not polluted.
#
# Tests run against caller_test_db, never the database the containers use.
set -euo pipefail
cd "$(dirname "$0")/.."
# api/ is mounted, NOT taken from the baked image. Without this, editing a
# guard on the host would not reach the test run at all - the break pass would
# report a landed edit and a green suite, which is the exact failure the break
# pass exists to catch.
docker compose run --rm --no-deps \
  -v "$(pwd)/api:/app/api" \
  -v "$(pwd)/tests:/app/tests" \
  -v "$(pwd)/migrations:/app/migrations" \
  caller-api python -m pytest tests/ "$@"
