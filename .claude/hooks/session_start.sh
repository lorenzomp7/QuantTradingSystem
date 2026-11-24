#!/usr/bin/env bash
# Hook SessionStart: prepara l'ambiente a ogni avvio di una sessione Claude Code cloud.
# Fuori dal cloud non fa nulla (vedi in_cloud in session_lib.sh).
source "${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/session_lib.sh"
in_cloud || exit 0
cd "$ROOT" || exit 0

install_once python-deps "requirements.txt requirements-dev.txt" python3 -m pip install -q -r requirements-dev.txt
note "Verifica standard: python -m pytest -q"
# REA (analisi degli APK dalla chat): JADX, Apktool e REA in ~/.cache/rea-tools, in background.
note "$(bash "$ROOT/.claude/rea/installa.sh" --sfondo 2>&1 | tail -n 1). Prima di analizzare APK: bash .claude/rea/installa.sh --attendi (skill rea)"
REA_DIR="${REA_TOOLS_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/rea-tools}"
export_env "PATH=\"$REA_DIR/bin:\$PATH\""
export_env "REA_JADX_MCP_JAR=\"$REA_DIR/jadx-headless-mcp-0.7.1-all.jar\""
export_env "REA_APKTOOL_COMMAND=\"$REA_DIR/apktool\""
finish
