# shellcheck shell=bash
# Funzioni comuni per session_start.sh (hook SessionStart di Claude Code).
# Pensate per le sessioni cloud: ogni sessione parte da una copia nuova della
# repo, quindi le dipendenze vanno installate a ogni avvio. Le installazioni
# sono saltate se già fatte su questa macchina (stato in .claude/hooks/.state/).

ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
STATE="$ROOT/.claude/hooks/.state"
mkdir -p "$STATE"
LOG="$STATE/session-setup.log"
NOTES_FILE="$STATE/notes.txt"
: > "$NOTES_FILE"
echo "=== avvio sessione $(date -u +%FT%TZ)" >> "$LOG"

# Solo nelle sessioni cloud (CLAUDE_CODE_REMOTE=true); CLAUDE_SETUP_FORCE=1 per forzare altrove.
in_cloud() { [ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || [ "${CLAUDE_SETUP_FORCE:-}" = "1" ]; }

note() { echo "- $1" >> "$NOTES_FILE"; echo "[$(date +%T)] $1" >> "$LOG"; }

# Variabile disponibile a tutti i comandi successivi della sessione (CLAUDE_ENV_FILE).
export_env() {
  [ -n "${CLAUDE_ENV_FILE:-}" ] && echo "export $1" >> "$CLAUDE_ENV_FILE"
  eval "export $1"
}

# install_once NOME "FILE..." COMANDO...: esegue COMANDO solo se i FILE sono cambiati
# dall'ultima installazione riuscita su questa macchina.
install_once() {
  local name=$1 files=$2; shift 2
  local stamp="$STATE/$name.sha256"
  if (cd "$ROOT" && sha256sum --check --status "$stamp") 2>/dev/null; then
    note "$name: già pronto"
    return 0
  fi
  rm -f "$stamp"
  if (cd "$ROOT" && "$@") >> "$LOG" 2>&1; then
    # shellcheck disable=SC2086
    (cd "$ROOT" && sha256sum $files > "$stamp") 2>/dev/null || rm -f "$stamp"
    note "$name: installato"
  else
    note "$name: installazione FALLITA, dettagli in .claude/hooks/.state/session-setup.log"
  fi
}

# background NOME SCRIPT [ARG...]: avvia un'installazione lunga senza bloccare la sessione.
# Stato in .claude/hooks/.state/NOME.status (running/ready/failed), log in NOME.log.
background() {
  local name=$1; shift
  local status="$STATE/$name.status" pidfile="$STATE/$name.pid"
  if grep -q '^ready' "$status" 2>/dev/null; then
    note "$name: già pronto"
    return 0
  fi
  if grep -q '^running' "$status" 2>/dev/null && kill -0 "$(cat "$pidfile" 2>/dev/null)" 2>/dev/null; then
    note "$name: installazione ancora in corso in background"
    return 0
  fi
  echo running > "$status"
  STATUS_FILE="$status" setsid nohup bash "$@" > "$STATE/$name.log" 2>&1 < /dev/null &
  echo $! > "$pidfile"
  note "$name: installazione avviata in background (pochi minuti). Prima di usarlo esegui: bash .claude/hooks/wait_setup.sh $name"
}

# finish [reload]: messaggio per Claude e, con "reload", nuova ricerca delle skill.
finish() {
  local reload=${1:-}
  NOTES_FILE="$NOTES_FILE" RELOAD="$reload" python3 - <<'PY' || { cat "$NOTES_FILE"; }
import json, os
notes = open(os.environ["NOTES_FILE"], encoding="utf-8").read().strip()
out = {"hookSpecificOutput": {"hookEventName": "SessionStart",
       "additionalContext": "Preparazione automatica dell'ambiente (.claude/hooks/session_start.sh):\n" + notes}}
if os.environ.get("RELOAD") == "reload":
    out["hookSpecificOutput"]["reloadSkills"] = True
print(json.dumps(out, ensure_ascii=False))
PY
  exit 0
}
