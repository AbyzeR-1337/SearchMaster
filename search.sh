#!/usr/bin/env bash
# SearchMaster for Linux / macOS / Android (Termux).
#   ./search.sh "Hollow Knight"            # normal search
#   ./search.sh "Hollow Knight" --mode deep  # extra options go after the query
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if [ ! -x .venv/bin/python ]; then
  echo "Первый запуск: устанавливаю зависимости…"
  "$PY" -m venv .venv
  .venv/bin/python -m pip install -q -r requirements.txt
fi

QUERY="${1:-}"
if [ -z "$QUERY" ]; then
  read -r -p "Что искать: " QUERY
fi
[ -z "$QUERY" ] && exit 0
shift || true

OPEN=()
# open the HTML report automatically when a desktop/browser is available
if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] || [ "$(uname)" = "Darwin" ] || command -v termux-open >/dev/null 2>&1; then
  OPEN=(--open)
fi
.venv/bin/python -m searchmaster "${OPEN[@]}" "$@" -- "$QUERY"
