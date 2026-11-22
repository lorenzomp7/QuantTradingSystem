#!/usr/bin/env bash
# Hook SessionStart: prepara l'ambiente a ogni avvio di una sessione Claude Code cloud.
# Fuori dal cloud non fa nulla (vedi in_cloud in session_lib.sh).
source "${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/session_lib.sh"
in_cloud || exit 0
cd "$ROOT" || exit 0

install_once python-deps "requirements.txt requirements-dev.txt" python3 -m pip install -q -r requirements-dev.txt
note "Verifica standard: python -m pytest -q"
finish
