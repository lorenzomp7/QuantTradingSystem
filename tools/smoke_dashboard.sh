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
# In mezzo prova le sezioni indipendenti (punto 53 del Piano): /api/logs che risponde 500
# (solo i Log in errore, «Riprova» li ricarica), browser offline (avviso in alto, comando
# non inviato, ricarica al ritorno della rete) e bot in ritardo (badge IN RITARDO).
# Esce con 0 se tutto passa, con 1 al primo controllo fallito.
#
# Richiede agent-browser (npm i -g agent-browser && agent-browser install --with-deps).
# Variabili: PORT (10123), API_KEY (smoke-key), OUT_DIR (/tmp/qts-smoke),
# ALLOW_NO_CHARTJS=1 per non fallire se Chart.js non arriva dal CDN (rete chiusa),
# oppure CHARTJS_FILE=…/chart.umd.js per servirlo da un file locale (npm i chart.js@4.4.1).
# Con BASE_URL lo script non avvia il server e non cambia lo stato del bot remoto: con
# API_KEY prova Avvia solo se il bot è già acceso (risposta "già in esecuzione"), mai
# Ferma. ALLOW_CONTROL=1 dà il consenso ad avviarlo e fermarlo (esegue un ciclo vero sul
# registro paper). Senza API_URL l'indirizzo dell'API si legge da BASE_URL/config.js.
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
  [[ "$PAGES" == 1 ]] && echo "! PAGES=1 ignorato: con BASE_URL si prova la pagina indicata"
  if [[ -z "${API_URL:-}" ]]; then
    # Come la dashboard: se config.js indica un'API (GitHub Pages), si prova quella.
    API_URL="$(curl -fs --max-time 15 "$PAGE_URL/config.js" \
      | sed -n 's/.*window\.QTS_API *= *"\([^"]*\)".*/\1/p' | head -1 || true)"
    API_URL="${API_URL:-$PAGE_URL}"
  fi
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
  # Aspetta che i server si chiudano davvero: una prova subito dopo riusa le stesse porte.
  for pid in "$SERVER_PID" "$PAGES_PID" "$CHART_PID"; do
    [[ -n "$pid" ]] && wait "$pid" 2>/dev/null || true
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
  for _ in $(seq 1 120); do   # fino a 30 s: Avvia e Ferma aspettano un ciclo completo
    local m; m="$(ab get text '#msg' 2>/dev/null || true)"
    [[ -n "$m" && "$m" != "…" ]] && { echo "$m"; return; }
    sleep 0.25
  done
  echo ""
}

# ------------------------------------------------------------ Server locale
if [[ "$REMOTE" == 0 ]]; then
  for port in "$PORT" "$((PORT + 1))" "$((PORT + 2))"; do
    ! curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$port/" \
      || fail "la porta $port è già in uso: chiudi l'altro server o usa PORT=…"
  done
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
    curl -fs -o /dev/null "$PAGE_URL/" || fail "il server della pagina non risponde (log: $OUT_DIR/pages.log)"
    ok "pagina come GitHub Pages su $PAGE_URL (API su $API_URL)"
  fi
fi

# --------------------------------------------------------------- CORS
if [[ "$CROSS_ORIGIN" == 1 ]]; then
  header() {  # valore di un header della risposta (minuscole), vuoto se assente
    curl -s --max-time 15 -o /dev/null -D - "${@:2}" | tr -d '\r' | awk -v h="$1" 'tolower($1)==h":" {sub(/^[^:]*: /,""); print}'
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
  ab wait "#costs table" >/dev/null || fail "[$W×$H] la tabella dei costi non compare: $(ab get text '#costs' 2>/dev/null)"
  api="$(js '(window.QTS_API || "").replace(/\/$/, "")')"
  if [[ "$CROSS_ORIGIN" == 1 || "$api" != '""' ]]; then
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

# ------------------------------------- Sezioni indipendenti, offline, ritardo
# Guasti finti dentro la pagina: il server non si può rompere a comando, e così la prova
# vale anche per una dashboard già online. window.__fault sceglie il guasto:
#   "logs500"            /api/logs risponde 500
#   { late, loop }       /api/status vero, ma con il bot acceso, ultimo ciclo `late` secondi fa
#                        e intervallo del ciclo di `loop` secondi
js "$(cat <<'EOF'
(() => {
  window.__realFetch = window.fetch;
  window.fetch = async (u, o) => {
    const url = String(u), f = window.__fault;
    const json = (body, status) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
    if (f === "logs500" && url.includes("/api/logs")) return json({ detail: "guasto di prova" }, 500);
    const r = await window.__realFetch(u, o);
    if (f && f.late && url.includes("/api/status")) {
      const t = new Date(Date.now() - f.late * 1000).toISOString();
      return json({ ...(await r.json()), running: true, started_at: t, last_run_at: t, loop_interval_seconds: f.loop }, 200);
    }
    return r;
  };
  window.__look = () => {
    const state = (id) => document.getElementById(id).closest("section").dataset.state;
    const note = (id) => { const n = document.getElementById(id + "-note"); const b = n.querySelector("button"); const t = n.querySelector("time.ago");
      return { hidden: n.hidden, text: n.textContent, alert: !!n.querySelector("[role=alert]"),
               button: b ? b.tagName + ":" + b.type + ":" + b.textContent : "", ago: t ? t.textContent : "", title: t ? t.title : "" }; };
    const banner = (id) => { const b = document.getElementById(id); return b.hidden ? "" : b.textContent; };
    return { badge: document.getElementById("badge").textContent, online: navigator.onLine,
             state: { status: state("status"), account: state("account"), logs: state("logs") },
             note: { status: note("status"), account: note("account"), logs: note("logs") },
             at: { status: sections.status.at, account: sections.account.at, logs: sections.logs.at },
             offline: banner("offline-banner"), stale: banner("stale-banner"),
             logs: document.getElementById("logs").textContent.length };
  };
  return true;
})()
EOF
)" >/dev/null
expect() {  # expect <cosa> <JSON di __look()> <asserzioni Python su `u`>
  python3 -c 'import json,sys; u=json.loads(sys.argv[1]); exec(sys.argv[2])' "$2" "$3" || fail "$1: $2"
}
all_ok='["status","account","logs"].every(id => document.getElementById(id).closest("section").dataset.state === "ok")'

# 1) /api/logs risponde 500: solo i Log in errore, stato e account aggiornati.
before="$(js '__look()')"
look="$(js '(async () => { window.__fault = "logs500"; await new Promise(r => setTimeout(r, 20)); await refresh(); return __look(); })()')"
expect "/api/logs in errore 500" "$look" '
assert u["state"] == {"status": "ok", "account": "ok", "logs": "error"}, u["state"]
n = u["note"]["logs"]
assert not n["hidden"] and n["alert"] and "errore 500" in n["text"] and "Log" in n["text"], n
assert n["button"] == "BUTTON:button:Riprova", n["button"]
assert n["ago"].startswith("Dati mostrati: aggiornati ") and n["title"], n
assert u["note"]["status"]["hidden"] and u["note"]["account"]["hidden"], u["note"]
'
python3 - "$before" "$look" <<'EOF' || fail "con /api/logs in errore stato e account dovevano aggiornarsi lo stesso: $look"
import json, sys
b, u = json.loads(sys.argv[1]), json.loads(sys.argv[2])
assert u["at"]["status"] > b["at"]["status"] and u["at"]["account"] > b["at"]["account"], (b["at"], u["at"])
assert u["at"]["logs"] == b["at"]["logs"] and u["logs"] == b["logs"], "i log caricati prima devono restare visibili"
EOF
ok "/api/logs 500: solo la sezione Log in errore con «Riprova», stato e account aggiornati"
# Guasto finito e clic su «Riprova» nello stesso passo, prima che arrivi l'aggiornamento periodico.
js 'window.__fault = null; document.querySelector("#logs-note button").click(); true' >/dev/null
ab wait --fn "$all_ok" >/dev/null || fail "«Riprova» non ha ricaricato i log: $(js '__look()')"
look="$(js '__look()')"
expect "dopo «Riprova»" "$look" 'assert u["note"]["logs"]["hidden"] and u["at"]["logs"], u'
ok "«Riprova» ricarica i log e l'avviso sparisce"

# 2) Offline: avviso in alto, Avvia dice che il comando non è partito, poi tutto torna da solo.
ab fill '#apikey' '' >/dev/null     # se l'emulazione non funzionasse, Avvia darebbe solo un 401
ab set offline on >/dev/null
ab wait --fn '!document.getElementById("offline-banner").hidden' >/dev/null \
  || { ab set offline off >/dev/null; fail "offline: l'avviso in alto non compare"; }
m="$(msg_after_click "Avvia")"
look="$(js 'refresh().then(() => __look())')"
# L'aggiornamento periodico chiama la refresh() originale: questo contatore vede solo la
# chiamata fatta dall'evento "online".
js 'window.__onlineRefresh = 0; window.__refresh = window.refresh;
    window.refresh = function () { window.__onlineRefresh++; return window.__refresh(); }; true' >/dev/null
ab set offline off >/dev/null
[[ "$m" == *"Comando non inviato"* ]] || fail "Avvia offline doveva dire che il comando non è partito, ha detto: '$m'"
expect "offline" "$look" '
assert u["online"] is False and "offline" in u["offline"].lower(), u
assert u["state"] == {"status": "error", "account": "error", "logs": "error"}, u["state"]
assert all("sei offline" in u["note"][k]["text"] for k in u["note"]), u["note"]
assert u["badge"] == "—", u["badge"]
'
ok "offline: avviso in alto, sezioni con «Riprova», Avvia: $m"
ab wait --fn "document.getElementById('offline-banner').hidden && $all_ok" >/dev/null \
  || fail "tornata la rete l'avviso doveva sparire e i dati ricaricarsi: $(js '__look()')"
[[ "$(js 'window.refresh = window.__refresh; window.__onlineRefresh')" -ge 1 ]] \
  || fail "tornata la rete la pagina non ha ricaricato subito (ha aspettato l'aggiornamento periodico)"
ok "rete tornata: avviso sparito e sezioni ricaricate subito"

# 3) Bot in ritardo: ultimo ciclo 10 minuti fa con cicli da 60 s (soglia 2 minuti).
look="$(js '(async () => { window.__fault = { late: 600, loop: 60 }; await refresh(); return __look(); })()')"
expect "bot in ritardo" "$look" '
assert u["badge"] == "IN RITARDO" and u["state"]["status"] == "stale", u
assert "2 minuti" in u["stale"], u["stale"]
n = u["note"]["status"]
assert "10 minuti fa" in n["ago"] and n["title"] and not n["alert"], n
assert u["state"]["account"] == "ok" and u["state"]["logs"] == "ok", u["state"]
'
ok "ultimo ciclo 10 minuti fa, cicli da 60 s: badge IN RITARDO ($(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["stale"])' "$look"))"
# Lo stesso ritardo con cicli da un'ora (soglia 2 ore) non è un ritardo.
look="$(js '(async () => { window.__fault = { late: 600, loop: 3600 }; await refresh(); return __look(); })()')"
expect "ciclo da un'ora" "$look" 'assert u["badge"] == "RUNNING" and u["state"]["status"] == "ok" and not u["stale"], u'
js 'window.__fault = null; window.fetch = window.__realFetch; refresh().then(() => true)' >/dev/null
ok "lo stesso ritardo con cicli da un'ora: RUNNING, nessun avviso"
errors="$(ab --json errors | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["data"]["errors"]))')"
[[ "$errors" == 0 ]] || { ab errors; fail "errori JavaScript durante le prove delle sezioni: $errors"; }

# ------------------------------------------------------------ Avvia / Ferma
ab fill '#apikey' '' >/dev/null
m="$(msg_after_click "Avvia")"
[[ "$m" == *"401"* ]] || fail "Avvia senza chiave doveva dare 401, ha dato: '$m'"
ok "Avvia senza chiave: $m"

bot_running() {
  local body
  body="$(curl -fs --max-time 15 "$API_URL/health")" || fail "$API_URL/health non risponde (API_URL giusto?)"
  python3 -c 'import json,sys; print(json.loads(sys.argv[1])["bot_running"])' "$body" 2>/dev/null \
    || fail "$API_URL/health non è la risposta dell'API: ${body:0:80}"
}

if [[ -n "$API_KEY" ]]; then
  was_running="$(bot_running)"
  if [[ "$REMOTE" == 1 && "$was_running" != True && "${ALLOW_CONTROL:-0}" != 1 ]]; then
    echo "! il bot remoto è fermo: Avvia non provato (farebbe un ciclo vero); ALLOW_CONTROL=1 per provarlo"
  else
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
  fi
else
  echo "! API_KEY non impostata: Avvia/Ferma con chiave non provati"
fi

echo "Smoke test della dashboard superato."
