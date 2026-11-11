#!/bin/sh
# Lancer avec : sh scripts/run.sh (depuis n'importe quel dossier).
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$PROJECT_DIR"

PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    printf '%s\n' 'Environnement absent : créer .venv et installer requirements.txt selon le README.' >&2
    exit 1
fi

exec "$PYTHON_BIN" -m uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
