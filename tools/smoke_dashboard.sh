#!/usr/bin/env bash
# tools/smoke_dashboard.sh — prova della dashboard in un browser vero (agent-browser).
#
#   bash tools/smoke_dashboard.sh                  # avvia uvicorn con dati mock e prova
#   PAGES=1 bash tools/smoke_dashboard.sh          # come GitHub Pages: pagina e API su origini diverse
#   BASE_URL=https://… API_KEY=… bash tools/smoke_dashboard.sh   # prova una dashboard già online
#   BASE_URL=https://lorenzomp7.github.io/QuantTradingSystem/ API_URL=https://quant.….sslip.io \
#     API_KEY=… bash tools/smoke_dashboard.sh      # dashboard pubblicata su Pages + API sulla VM
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
# Con BASE_URL lo script non avvia il server; Avvia/Ferma si provano solo se API_KEY è
# impostata e lo stato del bot resta quello iniziale: se era già acceso non lo ferma.
# Quando pagina e API sono su origini diverse (PAGES=1 o API_URL) controlla anche
# config.js, gli header CORS, il preflight di X-API-Key e il rifiuto di un'altra origine.

set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-10123}"
OUT_DIR="${OUT_DIR:-/tmp/qts-smoke}"
SESSION="qts-smoke-$$"
REMOTE=0
PAGES="${PAGES:-0}"
if [[ -n "${BASE_URL:-}" ]]; then
  REMOTE=1
  API_KEY="${API_KEY:-}"
  PAGE_URL="${BASE_URL%/}"
  API_URL="${API_URL:-$PAGE_URL}"
else
  API_KEY="${API_KEY:-smoke-key}"
  API_URL="http://127.0.0.1:${PORT}"
  PAGE_URL="$API_URL"
  [[ "$PAGES" == 1 ]] && PAGE_URL="http://127.0.0.1:$((PORT + 2))"
fi
API_URL="${API_URL%/}"
origin_of() { python3 -c 'import sys,urllib.parse as u; p=u.urlsplit(sys.argv[1]); print(f"{p.scheme}://{p.netloc}")' "$1"; }
PAGE_ORIGIN="$(origin_of "$PAGE_URL")"
CROSS_ORIGIN=0
[[ "$PAGE_ORIGIN" != "$(origin_of "$API_URL")" ]] && CROSS_ORIGIN=1
mkdir -p "$OUT_DIR"

command -v agent-browser >/dev/null || { echo "agent-browser non trovato: npm i -g agent-browser" >&2; exit 1; }

SERVER_PID=""
PAGES_PID=""
CHART_PID=""
cleanup() {
  agent-browser --session "$SESSION" close >/dev/null 2>&1 || true
  for pid in "$SERVER_PID" "$PAGES_PID" "$CHART_PID"; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  done
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
  DATA_SOURCE=mock CONTROL_API_KEY="$API_KEY" AUTO_START_BOT=false LOG_LEVEL=WARNING \
    CORS_ORIGINS="$PAGE_ORIGIN" \
    python -m uvicorn main:app --host 127.0.0.1 --port "$PORT" >"$OUT_DIR/uvicorn.log" 2>&1 &
  SERVER_PID=$!
  for _ in $(seq 1 40); do
    curl -fs "$API_URL/health" >/dev/null 2>&1 && break
    sleep 0.25
  done
  curl -fs "$API_URL/health" >/dev/null || fail "il server non risponde (log: $OUT_DIR/uvicorn.log)"
  ok "server mock su $API_URL"
  if [[ "$PAGES" == 1 ]]; then
    # Stesso sito che costruisce .github/workflows/pages.yml: index.html + config.js.
    SITE="$OUT_DIR/site"
    mkdir -p "$SITE"
    cp templates/dashboard.html "$SITE/index.html"
    printf 'window.QTS_API = "%s";\n' "$API_URL" > "$SITE/config.js"
    python -m http.server "$((PORT + 2))" --bind 127.0.0.1 --directory "$SITE" >"$OUT_DIR/pages.log" 2>&1 &
    PAGES_PID=$!
    for _ in $(seq 1 20); do curl -fs -o /dev/null "$PAGE_URL/" && break; sleep 0.25; done
    ok "pagina come GitHub Pages su $PAGE_URL (API su $API_URL)"
  fi
fi

# --------------------------------------------------------------- CORS
if [[ "$CROSS_ORIGIN" == 1 ]]; then
  header() {  # valore di un header della risposta (minuscole), vuoto se assente
    curl -s -o /dev/null -D - "${@:2}" | tr -d '\r' | awk -v h="$1" 'tolower($1)==h":" {sub(/^[^:]*: /,""); print}'
  }
  got="$(header access-control-allow-origin -H "Origin: $PAGE_ORIGIN" "$API_URL/api/status")"
  [[ "$got" == "$PAGE_ORIGIN" ]] || fail "CORS: /api/status non autorizza $PAGE_ORIGIN (ricevuto: '$got')"
  got="$(header access-control-allow-headers -X OPTIONS -H "Origin: $PAGE_ORIGIN" \
    -H "Access-Control-Request-Method: POST" -H "Access-Control-Request-Headers: x-api-key" "$API_URL/api/bot/start")"
  [[ "${got,,}" == *x-api-key* ]] || fail "CORS: il preflight di POST con X-API-Key non è autorizzato ('$got')"
  got="$(header access-control-allow-origin -H "Origin: https://example.com" "$API_URL/api/status")"
  [[ -z "$got" ]] || fail "CORS: anche un'origine estranea è autorizzata ('$got')"
  ok "CORS: $PAGE_ORIGIN autorizzata (anche il preflight di X-API-Key), https://example.com no"
fi

# ------------------------------------------------------------- Pagina e grafico
CHARTJS_URL=""
if [[ -n "${CHARTJS_FILE:-}" ]]; then
  [[ -f "$CHARTJS_FILE" ]] || fail "CHARTJS_FILE non trovato: $CHARTJS_FILE"
  CHART_PORT=$((PORT + 1))
  python -m http.server "$CHART_PORT" --bind 127.0.0.1 --directory "$(dirname "$CHARTJS_FILE")" \
    >"$OUT_DIR/chartjs-server.log" 2>&1 &
  CHART_PID=$!
  CHARTJS_URL="http://127.0.0.1:${CHART_PORT}/$(basename "$CHARTJS_FILE")"
  for _ in $(seq 1 20); do curl -fs -o /dev/null "$CHARTJS_URL" && break; sleep 0.25; done
  ok "Chart.js servito in locale da $CHARTJS_URL"
fi
for vp in "1280 800" "390 844"; do
  read -r W H <<<"$vp"
  ab set viewport "$W" "$H" >/dev/null
  ab errors --clear >/dev/null 2>&1 || true
  ab console --clear >/dev/null 2>&1 || true
  ab open "$PAGE_URL/" >/dev/null
  ab wait --text "Rendimento" >/dev/null || fail "[$W×$H] il backtest non mostra le metriche"
  if [[ -n "$CHARTJS_URL" ]] && [[ "$(js '!!window.Chart')" == false ]]; then
    # CDN non raggiungibile: si carica la copia locale e si ridisegna il grafico.
    js "new Promise((res, rej) => { const s = document.createElement('script');
         s.src = '$CHARTJS_URL'; s.onload = () => res(true); s.onerror = () => rej('load');
         document.head.appendChild(s); })" >/dev/null
    js 'backtest().then(() => true)' >/dev/null
  fi

  metrics="$(js 'document.querySelectorAll("#metrics .metric").length')"
  if [[ "$CROSS_ORIGIN" == 1 ]]; then
    api="$(js '(window.QTS_API || "").replace(/\/$/, "")')"
    [[ "$api" == "\"$API_URL\"" ]] || fail "[$W×$H] config.js: window.QTS_API = $api, atteso \"$API_URL\""
  fi
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

bot_running() { curl -fs "$API_URL/health" | python3 -c 'import json,sys; print(json.load(sys.stdin)["bot_running"])'; }

if [[ -n "$API_KEY" ]]; then
  was_running="$(bot_running)"
  ab fill '#apikey' "$API_KEY" >/dev/null
  m="$(msg_after_click "Avvia")"
  [[ "$m" == *"avviato"* || "$m" == *"già in esecuzione"* ]] || fail "Avvia con chiave: '$m'"
  ok "Avvia con chiave: $m"
  [[ "$(bot_running)" == True ]] || fail "dopo Avvia /health dice che il bot è fermo"
  if [[ "$was_running" == True ]]; then
    echo "! il bot era già acceso: Ferma non provato, per non fermarlo"
  else
    m="$(msg_after_click "Ferma")"
    [[ "$m" == *"fermato"* ]] || fail "Ferma con chiave: '$m'"
    ok "Ferma con chiave: $m (bot di nuovo fermo come all'inizio)"
  fi
else
  echo "! API_KEY non impostata: Avvia/Ferma con chiave non provati"
fi

echo "Smoke test della dashboard superato."
