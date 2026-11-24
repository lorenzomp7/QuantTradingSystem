#!/usr/bin/env bash
# tools/analyze_apps.sh — schede delle app di riferimento con REA (integrazione QTS 7).
#
# Da lanciare sul tuo computer, con il telefono collegato via USB (debug USB attivo)
# e le app già installate dal Play Store:
#
#   bash tools/analyze_apps.sh                 # tutte e quattro le app
#   bash tools/analyze_apps.sh tradingview     # una sola
#   APK_DIR=~/apk bash tools/analyze_apps.sh   # cartella per APK e risultati (default ~/qts-apk)
#   SKIP_PULL=1 bash tools/analyze_apps.sh     # APK già in $APK_DIR/<app>/base.apk
#
# Per ogni app: copia l'APK dal telefono (REA pull-adb-package), legge manifest e
# classi (JADX), stringhe (Apktool), firma e motore Flutter, cerca le librerie di
# grafici e di notifiche, poi scrive docs/app-analizzate/<app>.md con
# tools/app_report.py. APK e risultati restano in $APK_DIR, fuori dalla repo:
# sono di terzi. Le schede vanno rilette prima del commit.
#
# Requisiti (gratis): Node.js, REA (npm i -g rea-agents), adb (Android platform-tools),
# un JDK, il JAR di jadx-headless-mcp 0.7.1 in REA_JADX_MCP_JAR e Apktool >= 3.0.2 in
# REA_APKTOOL_COMMAND (o nel PATH). `rea inspect-adb-client` e `rea inspect-apktool-client`
# dicono se sono pronti. Uso consentito: lettura di dichiarazioni e librerie per capire
# funzioni e interoperabilità, niente codice copiato (DISCLAIMER di REA).

set -euo pipefail
cd "$(dirname "$0")/.."

APK_DIR="${APK_DIR:-$HOME/qts-apk}"
DOCS_DIR="docs/app-analizzate"
REA="${REA:-rea}"

# slug|nome|pacchetto (dal foglio «App Play Store» del Piano)
APPS=(
  "tradingview|TradingView|com.tradingview.tradingviewapp"
  "bts-backtest-strategy|BTS – Backtest Strategy|com.backtest"
  "tradetracer|TradeTracer|ai.tradetracer.app"
  "replay-trader|Replay Trader|nl.buqe.chartreplay"
)
# Classi cercate per le librerie (punti 19, 27 e 31).
CLASS_QUERIES=(charting lightweight highcharts anychart vico WebView FirebaseMessagingService onesignal)

fail() { echo "✗ $*" >&2; exit 1; }
command -v "$REA" >/dev/null || fail "REA non trovato: npm i -g rea-agents"
command -v python >/dev/null || fail "python non trovato"
REA_VERSION="$("$REA" --version 2>/dev/null | head -1)"

case "$APK_DIR" in "$PWD"|"$PWD"/*) fail "APK_DIR è dentro la repo: gli APK di terzi devono restare fuori" ;; esac
mkdir -p "$APK_DIR" "$DOCS_DIR"

DEVICE=""
if [[ "${SKIP_PULL:-0}" != 1 ]]; then
  "$REA" inspect-adb-client --json >/dev/null || fail "adb non pronto: installa platform-tools o imposta REA_ADB_PATH"
  DEVICE="$("$REA" list-adb-devices --json | python -c '
import json, sys
def walk(o):
    if isinstance(o, dict):
        if o.get("state") == "device" and o.get("serial"):
            yield o["serial"]
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)
print(next(walk(json.load(sys.stdin)), ""))')"
  [[ -n "$DEVICE" ]] || fail "nessun telefono pronto: collegalo, attiva il debug USB e accetta l'impronta sul telefono"
  echo "Telefono: $DEVICE"
fi

for entry in "${APPS[@]}"; do
  IFS="|" read -r slug name pkg <<<"$entry"
  if [[ $# -gt 0 ]] && [[ " $* " != *" $slug "* ]]; then continue; fi
  out="$APK_DIR/$slug"
  ev="$out/evidence"
  mkdir -p "$ev"
  echo "── $name ($pkg)"

  if [[ "${SKIP_PULL:-0}" != 1 ]]; then
    "$REA" pull-adb-package "$DEVICE" "$pkg" --output-directory "$out" --json >"$ev/pull.json" \
      || { echo "! $name: APK non copiato (app installata?), vedi $ev/pull.json"; continue; }
  fi
  apk="$(find "$out" -name base.apk -print -quit)"
  [[ -n "$apk" ]] || apk="$(find "$out" -name '*.apk' -print -quit)"
  [[ -n "$apk" ]] || { echo "! $name: nessun APK in $out"; continue; }

  # Ogni passo è facoltativo: se uno strumento manca la scheda lo dice, le altre parti restano.
  run() { local file="$1"; shift; "$REA" "$@" --json >"$ev/$file" 2>"$ev/${file%.json}.err" || echo "  ! $1 non riuscito (vedi $ev/${file%.json}.err)"; }
  run package.json   inspect-android-package "$apk" --detail summary --evidence_output "$ev/package-complete.json"
  run resources.json decode-android-resources "$apk" --string-tables-only
  run signature.json inspect-apk-signature "$apk"
  run flutter.json   identify-flutter-build "$apk"
  for q in "${CLASS_QUERIES[@]}"; do
    run "classes-$q.json" search-android-classes "$apk" "$q"
  done

  python tools/app_report.py --app "$name" --package "$pkg" --evidence "$ev" \
    --out "$DOCS_DIR/$slug.md" --rea-version "$REA_VERSION"
done

echo "Fatto. Rileggi le schede in $DOCS_DIR prima del commit; gli APK restano in $APK_DIR."
