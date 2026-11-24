#!/usr/bin/env bash
# tools/smoke_static.sh — prova della dashboard statica (GitHub Pages senza VM) in un browser vero.
#
#   bash tools/smoke_static.sh                                # Pyodide dal CDN jsDelivr
#   PYODIDE_DIR=/tmp/pyodide bash tools/smoke_static.sh       # Pyodide da una cartella locale
#
# Costruisce il sito come .github/workflows/pages.yml in modalità statica: stato del bot
# da due cicli di tools/bot_once.py su dati mock e snapshot SPY (serie mock al posto di
# Yahoo). Lo serve in locale e, per due finestre (1280×800 e 390×844), controlla che:
# stato e log arrivino dai file, i pulsanti di controllo siano sostituiti dal rimando a
# GitHub Actions, il backtest calcolato nel browser dia gli stessi numeri del Python
# locale, il grafico e la tabella dei costi compaiano, un parametro sbagliato mostri
# l'errore e la pagina non abbia errori. Salva gli screenshot. Esce con 1 al primo
# controllo fallito.
#
# PYODIDE_DIR: cartella estratta da pyodide-<versione>.tar.bz2 (release GitHub di Pyodide),
# con numpy e pandas; viene servita dalla stessa origine del sito.
# Altre variabili: PORT (10140), OUT_DIR (/tmp/qts-static), CHARTJS_FILE e ALLOW_NO_CHARTJS
# come in tools/smoke_dashboard.sh.
# Richiede agent-browser (npm i -g agent-browser && agent-browser install --with-deps).

set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-10140}"
OUT_DIR="${OUT_DIR:-/tmp/qts-static}"
SESSION="qts-static-$$"
PAGE_URL="http://127.0.0.1:${PORT}"
SITE="$OUT_DIR/site"
SERVER_PID=""

cleanup() {
  agent-browser --session "$SESSION" close >/dev/null 2>&1 || true
  [[ -n "$SERVER_PID" ]] && { kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true; }
}
trap cleanup EXIT

fail() { echo "✗ $*" >&2; exit 1; }
ok() { echo "✓ $*"; }
ab() { agent-browser --session "$SESSION" "$@"; }
js() { ab --json eval "$1" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d["data"]["result"]) if d.get("success") else sys.exit("eval fallito: %s" % d.get("error")))'; }

command -v agent-browser >/dev/null || fail "agent-browser non trovato: npm i -g agent-browser"
! curl -s -o /dev/null --max-time 2 "$PAGE_URL/" || fail "la porta $PORT è già in uso: usa PORT=…"

# ------------------------------------------------------------------ Sito
rm -rf "$OUT_DIR/state"
mkdir -p "$OUT_DIR/state"
for _ in 1 2; do
  DATA_SOURCE=mock LOG_LEVEL=INFO python -m tools.bot_once --state-dir "$OUT_DIR/state" >/dev/null
done
python - "$OUT_DIR/state/data" <<'EOF'
import sys
from tools import data_snapshot
from utils import data_fetcher
# Niente rete: la serie mock prende il posto di Yahoo, il resto è il percorso vero.
data_fetcher.fetch_yahoo = lambda s, p, i: data_fetcher.generate_mock_ohlcv(s, p, i)
sys.exit(data_snapshot.main(["--out", sys.argv[1], "--symbols", "SPY"]))
EOF
PYODIDE_URL="https://cdn.jsdelivr.net/pyodide/v314.0.8/full/"
[[ -n "${PYODIDE_DIR:-}" ]] && PYODIDE_URL="$PAGE_URL/pyodide/"
python -m tools.build_static_site --out "$SITE" --state "$OUT_DIR/state" --data "$OUT_DIR/state/data" \
  --actions-url "https://github.com/lorenzomp7/QuantTradingSystem/actions/workflows/pages.yml" \
  --pyodide-url "$PYODIDE_URL" >/dev/null
if [[ -n "${PYODIDE_DIR:-}" ]]; then
  [[ -f "$PYODIDE_DIR/pyodide.js" ]] || fail "PYODIDE_DIR senza pyodide.js: $PYODIDE_DIR"
  ln -s "$(cd "$PYODIDE_DIR" && pwd)" "$SITE/pyodide"
fi
if [[ -n "${CHARTJS_FILE:-}" ]]; then
  [[ -f "$CHARTJS_FILE" ]] || fail "CHARTJS_FILE non trovato: $CHARTJS_FILE"
  cp "$CHARTJS_FILE" "$SITE/chart.umd.js"
fi
ok "sito statico in $SITE (Pyodide: $PYODIDE_URL)"

# Numeri attesi: lo stesso modulo eseguito dal Python locale sullo stesso snapshot.
EXPECTED="$(python - "$OUT_DIR/state/data/SPY.csv" <<'EOF'
import json, sys
from pathlib import Path
from web import static_api
p = {"symbol": "SPY", "short_window": "20", "long_window": "50", "period": "2y"}
csv = Path(sys.argv[1]).read_text(encoding="utf-8")
bt, co = static_api.backtest(p, csv), static_api.costs(p, csv)
print(json.dumps({"ret": bt["metrics"]["total_return_pct"], "trades": bt["metrics"]["num_trades"],
                  "points": len(bt["equity_curve"]), "cells": len(co["rows"])}))
EOF
)"

python -m http.server "$PORT" --bind 127.0.0.1 --directory "$SITE" >"$OUT_DIR/http.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 20); do curl -fs -o /dev/null "$PAGE_URL/" && break; sleep 0.25; done
curl -fs -o /dev/null "$PAGE_URL/" || fail "il server del sito non risponde (log: $OUT_DIR/http.log)"

# ------------------------------------------------------------- Browser
for vp in "1280 800" "390 844"; do
  read -r W H <<<"$vp"
  ab set viewport "$W" "$H" >/dev/null
  ab errors --clear >/dev/null 2>&1 || true
  ab console --clear >/dev/null 2>&1 || true
  ab open "$PAGE_URL/" >/dev/null

  ab wait --text "GitHub Actions" >/dev/null || fail "[$W×$H] lo stato del bot non arriva da state/status.json"
  ui="$(js '({ badge: document.getElementById("badge").textContent,
              controls: document.getElementById("controls").offsetParent === null,
              key: document.getElementById("apikey-box").offsetParent === null,
              link: !!document.querySelector("#static-controls a[href*=\"actions\"]"),
              logs: document.getElementById("logs").textContent })')"
  python3 - "$ui" <<'EOF' || fail "[$W×$H] stato o controlli non come atteso: $ui"
import json, sys
u = json.loads(sys.argv[1])
assert u["badge"] == "ATTIVO", u["badge"]
assert u["controls"] and u["key"] and u["link"], u
assert u["logs"] and u["logs"] not in ("Nessun log", "Caricamento…"), u["logs"]
lines = u["logs"].splitlines()
assert lines[0][:19] >= lines[-1][:19], "i log devono mostrare prima i più recenti, come con la VM"
EOF
  ok "[$W×$H] stato e log dai file del bot, controlli sostituiti dal link a GitHub Actions"

  # Il primo caricamento scarica Pyodide, numpy e pandas: si aspetta fino a 3 minuti.
  AGENT_BROWSER_DEFAULT_TIMEOUT=180000 ab wait --fn \
    'document.querySelectorAll("#metrics .metric").length > 0 || /Errore|non riuscito/.test(document.getElementById("metrics").textContent)' >/dev/null \
    || fail "[$W×$H] il backtest nel browser non mostra le metriche: $(ab get text '#metrics' 2>/dev/null)"
  if [[ -n "${CHARTJS_FILE:-}" ]] && [[ "$(js '!!window.Chart')" == false ]]; then
    js "new Promise((res, rej) => { const s = document.createElement('script');
         s.src = 'chart.umd.js'; s.onload = () => res(true); s.onerror = () => rej('load');
         document.head.appendChild(s); })" >/dev/null
    js 'backtest().then(() => true)' >/dev/null
  fi
  AGENT_BROWSER_DEFAULT_TIMEOUT=60000 ab wait "#costs table" >/dev/null || fail "[$W×$H] la tabella dei costi non compare: $(ab get text '#costs' 2>/dev/null)"

  got="$(js '(() => { const m = Object.fromEntries([...document.querySelectorAll("#metrics .metric")]
      .map(e => [e.querySelector("small").textContent, e.querySelector("b").textContent]));
    const c = window.Chart && Chart.getChart("chart");
    return { m, lib: !!window.Chart, points: c ? c.data.datasets[0].data.length : 0,
             filled: c ? c.data.datasets[0].data.filter(v => Number.isFinite(v)).length : 0,
             cells: document.querySelectorAll("#costs table td").length }; })()')"
  python3 - "$got" "$EXPECTED" "${ALLOW_NO_CHARTJS:-0}" <<'EOF' || fail "[$W×$H] risultati del browser diversi dal Python locale: $got (attesi $EXPECTED)"
import json, sys
g, e, allow = json.loads(sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3] == "1"
m = g["m"]
assert len(m) >= 8, m
assert float(m["Rendimento %"]) == e["ret"], (m["Rendimento %"], e["ret"])
assert int(m["Trade"]) == e["trades"], (m["Trade"], e["trades"])
assert m["Dati"].startswith("snapshot al "), m["Dati"]
assert g["cells"] == e["cells"], (g["cells"], e["cells"])
if g["lib"]:
    assert g["points"] == e["points"] == g["filled"], g
elif not allow:
    sys.exit("Chart.js non caricato dal CDN (ALLOW_NO_CHARTJS=1 o CHARTJS_FILE=…)")
EOF
  ok "[$W×$H] backtest e costi nel browser identici al Python locale ($EXPECTED)"

  errors="$(ab --json errors | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["data"]["errors"]))')"
  [[ "$errors" == 0 ]] || { ab errors; fail "[$W×$H] errori JavaScript nella pagina: $errors"; }
  console_err="$(ab --json console | python3 -c '
import json, sys
msgs = json.load(sys.stdin)["data"]["messages"]
print(len([m for m in msgs if m.get("type") == "error"]))')"
  [[ "$console_err" == 0 ]] || { ab console; fail "[$W×$H] errori in console: $console_err"; }
  ab screenshot "$OUT_DIR/static-${W}x${H}.png" >/dev/null
  ok "[$W×$H] pagina senza errori, screenshot in $OUT_DIR/static-${W}x${H}.png"
done

# Stato vecchio (esecuzioni pianificate saltate o sospese): il badge non deve dire ATTIVO.
python3 - "$SITE/state/status.json" <<'EOF'
import json, sys
from datetime import datetime, timedelta, timezone
p = sys.argv[1]; s = json.load(open(p))
s["written_at"] = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
json.dump(s, open(p, "w"))
EOF
js 'refresh().then(() => document.getElementById("badge").textContent)' >"$OUT_DIR/badge.txt"
grep -q "IN RITARDO" "$OUT_DIR/badge.txt" || fail "stato di 5 ore fa: badge $(cat "$OUT_DIR/badge.txt"), atteso IN RITARDO"
ok "stato vecchio di 5 ore: badge IN RITARDO"

# Parametri sbagliati: l'errore di validazione arriva dal Python nel browser, come il 422 dell'API.
ab fill '#bt-short' '60' >/dev/null
ab fill '#bt-long' '50' >/dev/null
js 'backtest().then(() => document.getElementById("metrics").textContent)' >"$OUT_DIR/invalid.txt"
grep -q "Errore" "$OUT_DIR/invalid.txt" || fail "SMA breve ≥ lunga doveva dare un errore: $(cat "$OUT_DIR/invalid.txt")"
ok "parametri sbagliati: $(cat "$OUT_DIR/invalid.txt")"
echo "Prova della dashboard statica superata."
