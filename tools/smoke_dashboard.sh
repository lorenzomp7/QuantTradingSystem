#!/usr/bin/env bash
# tools/smoke_dashboard.sh — prova della dashboard in un browser vero (agent-browser).
#
#   bash tools/smoke_dashboard.sh                  # avvia uvicorn con dati mock e prova
#   BASE_URL=https://… API_KEY=… bash tools/smoke_dashboard.sh   # prova una dashboard già online
#
# Per due finestre (1280×800 e 390×844): apre la dashboard, aspetta il backtest,
# controlla che il grafico abbia dati e che la pagina non abbia errori, poi prova
# Avvia/Ferma senza chiave (deve dare 401) e con la chiave. Salva gli screenshot.
# Esce con 0 se tutto passa, con 1 al primo controllo fallito.
#
# Richiede agent-browser (npm i -g agent-browser && agent-browser install --with-deps).
# Variabili: PORT (10123), API_KEY (smoke-key), OUT_DIR (/tmp/qts-smoke),
# ALLOW_NO_CHARTJS=1 per non fallire se Chart.js non arriva dal CDN (rete chiusa),
# oppure CHARTJS_FILE=…/chart.umd.js per servirlo da un file locale (npm i chart.js@4.4.1).
# Con BASE_URL lo script non avvia il server e non può fermare il bot di qualcun altro:
# Avvia/Ferma si provano solo se API_KEY è impostata.

set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-10123}"
OUT_DIR="${OUT_DIR:-/tmp/qts-smoke}"
SESSION="qts-smoke-$$"
REMOTE=0
if [[ -n "${BASE_URL:-}" ]]; then
  REMOTE=1
  API_KEY="${API_KEY:-}"
else
  API_KEY="${API_KEY:-smoke-key}"
  BASE_URL="http://127.0.0.1:${PORT}"
fi
BASE_URL="${BASE_URL%/}"
mkdir -p "$OUT_DIR"

command -v agent-browser >/dev/null || { echo "agent-browser non trovato: npm i -g agent-browser" >&2; exit 1; }

SERVER_PID=""
cleanup() {
  agent-browser --session "$SESSION" close >/dev/null 2>&1 || true
  [[ -n "$SERVER_PID" ]] && kill "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT

fail() { echo "✗ $*" >&2; exit 1; }
ok() { echo "✓ $*"; }
ab() { agent-browser --session "$SESSION" "$@"; }
# Valore JS come JSON su stdout.
js() { ab --json eval "$1" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d["data"]["result"]) if d.get("success") else sys.exit("eval fallito: %s" % d.get("error")))'; }
msg_after_click() {  # testo di #msg dopo il clic su un pulsante
  ab eval 'document.getElementById("msg").textContent = ""' >/dev/null
  ab find text "$1" click >/dev/null
  for _ in $(seq 1 20); do
    local m; m="$(ab get text '#msg' 2>/dev/null || true)"
    [[ -n "$m" && "$m" != "…" ]] && { echo "$m"; return; }
    sleep 0.25
  done
  echo ""
}

# ------------------------------------------------------------ Server locale
if [[ "$REMOTE" == 0 ]]; then
  DATA_SOURCE=mock CONTROL_API_KEY="$API_KEY" AUTO_START_BOT=false KEEP_ALIVE=false LOG_LEVEL=WARNING \
    python -m uvicorn main:app --host 127.0.0.1 --port "$PORT" >"$OUT_DIR/uvicorn.log" 2>&1 &
  SERVER_PID=$!
  for _ in $(seq 1 40); do
    curl -fs "$BASE_URL/health" >/dev/null 2>&1 && break
    sleep 0.25
  done
  curl -fs "$BASE_URL/health" >/dev/null || fail "il server non risponde (log: $OUT_DIR/uvicorn.log)"
  ok "server mock su $BASE_URL"
fi

# ------------------------------------------------------------- Pagina e grafico
CHARTJS_URL=""
if [[ -n "${CHARTJS_FILE:-}" ]]; then
  [[ -f "$CHARTJS_FILE" ]] || fail "CHARTJS_FILE non trovato: $CHARTJS_FILE"
  CHART_PORT=$((PORT + 1))
  python -m http.server "$CHART_PORT" --bind 127.0.0.1 --directory "$(dirname "$CHARTJS_FILE")" \
    >"$OUT_DIR/chartjs-server.log" 2>&1 &
  CHART_PID=$!
  trap 'cleanup; kill "$CHART_PID" 2>/dev/null || true' EXIT
  CHARTJS_URL="http://127.0.0.1:${CHART_PORT}/$(basename "$CHARTJS_FILE")"
  for _ in $(seq 1 20); do curl -fs -o /dev/null "$CHARTJS_URL" && break; sleep 0.25; done
  ok "Chart.js servito in locale da $CHARTJS_URL"
fi
for vp in "1280 800" "390 844"; do
  read -r W H <<<"$vp"
  ab set viewport "$W" "$H" >/dev/null
  ab errors --clear >/dev/null 2>&1 || true
  ab console --clear >/dev/null 2>&1 || true
  ab open "$BASE_URL/" >/dev/null
  ab wait --text "Rendimento" >/dev/null || fail "[$W×$H] il backtest non mostra le metriche"
  if [[ -n "$CHARTJS_URL" ]] && [[ "$(js '!!window.Chart')" == false ]]; then
    # CDN non raggiungibile: si carica la copia locale e si ridisegna il grafico.
    js "new Promise((res, rej) => { const s = document.createElement('script');
         s.src = '$CHARTJS_URL'; s.onload = () => res(true); s.onerror = () => rej('load');
         document.head.appendChild(s); })" >/dev/null
    js 'backtest().then(() => true)' >/dev/null
  fi

  metrics="$(js 'document.querySelectorAll("#metrics .metric").length')"
  [[ "$metrics" -ge 8 ]] || fail "[$W×$H] metriche mostrate: $metrics (attese 8)"

  chart="$(js '(() => { const c = window.Chart && Chart.getChart("chart");
    return { lib: !!window.Chart, points: c ? c.data.datasets[0].data.length : 0,
             filled: c ? c.data.datasets[0].data.filter(v => Number.isFinite(v)).length : 0 }; })()')"
  read -r lib points filled < <(python3 -c 'import json,sys; c=json.loads(sys.argv[1]); print(int(c["lib"]), c["points"], c["filled"])' "$chart")
  if [[ "$lib" == 0 ]]; then
    [[ "${ALLOW_NO_CHARTJS:-0}" == 1 ]] || fail "[$W×$H] Chart.js non caricato dal CDN: il grafico resta vuoto (ALLOW_NO_CHARTJS=1 per ignorare)"
    echo "! [$W×$H] Chart.js non disponibile (rete chiusa): grafico non verificato"
  else
    [[ "$points" -gt 0 && "$filled" == "$points" ]] || fail "[$W×$H] grafico vuoto o con valori non numerici ($filled/$points)"
    ok "[$W×$H] grafico con $points punti"
  fi

  errors="$(ab --json errors | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["data"]["errors"]))')"
  [[ "$errors" == 0 ]] || { ab errors; fail "[$W×$H] errori JavaScript nella pagina: $errors"; }
  console_err="$(ab --json console | python3 -c '
import json, sys
msgs = json.load(sys.stdin)["data"]["messages"]
bad = [m for m in msgs if m.get("type") == "error" and "401" not in m.get("text", "")]
print(len(bad))')"
  [[ "$console_err" == 0 ]] || { ab console; fail "[$W×$H] errori in console: $console_err"; }
  ab screenshot "$OUT_DIR/dashboard-${W}x${H}.png" >/dev/null
  ok "[$W×$H] pagina senza errori, screenshot in $OUT_DIR/dashboard-${W}x${H}.png"
done

# ------------------------------------------------------------ Avvia / Ferma
ab fill '#apikey' '' >/dev/null
m="$(msg_after_click "Avvia")"
[[ "$m" == *"401"* ]] || fail "Avvia senza chiave doveva dare 401, ha dato: '$m'"
ok "Avvia senza chiave: $m"

if [[ -n "$API_KEY" ]]; then
  ab fill '#apikey' "$API_KEY" >/dev/null
  m="$(msg_after_click "Avvia")"
  [[ "$m" == *"avviato"* || "$m" == *"già in esecuzione"* ]] || fail "Avvia con chiave: '$m'"
  ok "Avvia con chiave: $m"
  [[ "$(curl -fs "$BASE_URL/health" | python3 -c 'import json,sys; print(json.load(sys.stdin)["bot_running"])')" == True ]] \
    || fail "dopo Avvia /health dice che il bot è fermo"
  m="$(msg_after_click "Ferma")"
  [[ "$m" == *"fermato"* ]] || fail "Ferma con chiave: '$m'"
  ok "Ferma con chiave: $m"
else
  echo "! API_KEY non impostata: Avvia/Ferma con chiave non provati"
fi

echo "Smoke test della dashboard superato."
