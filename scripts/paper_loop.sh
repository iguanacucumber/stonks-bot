#!/usr/bin/env bash
# Poll quotes and log paper targets. No orders are ever sent.
set -euo pipefail
cd "$(dirname "$0")/.."
INTERVAL="${1:-60}"
ITERATIONS="${2:-100000}"
exec .venv/bin/python -m bot.cli paper --llm --interval "$INTERVAL" --iterations "$ITERATIONS"
