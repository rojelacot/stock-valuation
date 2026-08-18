#!/usr/bin/env bash
# Start the Long-Term Value Screener.
set -e
cd "$(dirname "$0")"
exec .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --app-dir backend "$@"
