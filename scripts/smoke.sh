#!/usr/bin/env bash
set -euo pipefail
pids=()
cleanup() { for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done; }
trap cleanup EXIT
for spec in api:18000 mcp:18001 payment:18002; do
  uv run --locked uvicorn "refunddesk.entrypoints.${spec%:*}:app" --port "${spec#*:}" &
  pids+=("$!")
done
for path in 18000/health/ready 18002/health/live; do
  curl --retry 15 --retry-delay 1 --retry-connrefused -fsS "http://127.0.0.1:$path"
done
# Verify routing only; the MCP client contract is exercised in I3.
status=$(curl --retry 10 --retry-connrefused --retry-delay 1 -sS -o /dev/null -w '%{http_code}' -X OPTIONS http://127.0.0.1:18001/mcp)
case "$status" in 200|204|405) ;; *) echo "unexpected MCP route status $status"; exit 1;; esac
uv run --locked python -m refunddesk.entrypoints.worker --check
