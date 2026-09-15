#!/bin/sh
set -eu
cd "$(dirname "$0")"
export MARKET_DATA_PROVIDER=yahoo
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python -m uvicorn bracket22.app:app --host 127.0.0.1 --port "${PORT:-8000}"
fi
exec uv run uvicorn bracket22.app:app --host 127.0.0.1 --port "${PORT:-8000}"
