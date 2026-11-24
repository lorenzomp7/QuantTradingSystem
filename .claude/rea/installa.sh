#!/usr/bin/env bash
# Installa quello che serve a REA per analizzare gli APK, su Linux e macOS (sessioni cloud di
# Claude Code comprese). Tutto gratuito, niente amministratore:
#   - JADX headless MCP 0.7.1 (JAR, SHA-256 controllato): manifest, classi, decompilazione
#   - Apktool 3.0.2 (JAR, SHA-256 controllato) e il suo lanciatore: risorse e stringhe
#   - REA 6.10.1 nella cache di npx, così il server MCP parte subito
# Cartella: ${REA_TOOLS_DIR:-~/.cache/rea-tools}, condivisa da tutte le repo sulla macchina.
# Serve Java 17+ (JADX e Apktool) e Node ^22.23.4 / ^24.22.1 / >=26.12.1 (REA).
#
#   bash .claude/rea/installa.sh            installa e aspetta (circa 30 s la prima volta)
#   bash .claude/rea/installa.sh --sfondo   avvia in background (lo fa l'hook SessionStart)
#   bash .claude/rea/installa.sh --attendi  aspetta l'installazione in background e dice com'è andata
#   bash .claude/rea/installa.sh --stato    stampa lo stato senza fare nulla
set -u

REA_VERSION=6.10.1
JADX_URL=https://github.com/1013503897/jadx-headless-mcp/releases/download/v0.7.1/jadx-headless-mcp-0.7.1-all.jar
JADX_JAR=jadx-headless-mcp-0.7.1-all.jar
JADX_SHA=6e5eacf500b64292bfb73c49797c1958f6ee44646e43e868039ae7feb573ff75
APKTOOL_URL=https://github.com/iBotPeaches/Apktool/releases/download/v3.0.2/apktool_3.0.2.jar
APKTOOL_SHA=eee4669a704a14e0623407e6701b0b91887e61e1e4049cb7a82833e14ae8b5fd

DIR="${REA_TOOLS_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/rea-tools}"
STATO="$DIR/.installa-stato"
PIDFILE="$DIR/.installa-pid"
LOGFILE="$DIR/installa.log"
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
mkdir -p "$DIR"

sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
stato() { cat "$STATO" 2>/dev/null || echo "assente"; }
in_corso() { grep -q '^in_corso' "$STATO" 2>/dev/null && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; }
pronto() { grep -q '^pronto' "$STATO" 2>/dev/null && [ -f "$DIR/$JADX_JAR" ] && [ -x "$DIR/apktool" ] && [ -x "$DIR/bin/rea" ]; }

case "${1:-}" in
  --stato)
    echo "REA: $(stato) ($DIR)"; exit 0 ;;
  --sfondo)
    if pronto; then echo "REA: già pronto ($DIR)"; exit 0; fi
    if in_corso; then echo "REA: installazione già in corso"; exit 0; fi
    echo "in_corso $(date -u +%FT%TZ)" > "$STATO"
    if command -v setsid >/dev/null 2>&1; then
      setsid nohup bash "$SELF" > "$LOGFILE" 2>&1 < /dev/null &
    else
      nohup bash "$SELF" > "$LOGFILE" 2>&1 < /dev/null &
    fi
    echo $! > "$PIDFILE"
    echo "REA: installazione avviata in background (log: $LOGFILE)"
    exit 0 ;;
  --attendi)
    for _ in $(seq 1 300); do in_corso || break; sleep 1; done
    if pronto; then echo "REA: pronto ($DIR)"; exit 0; fi
    if in_corso; then echo "REA: installazione ancora in corso dopo 5 minuti, log: $LOGFILE"; exit 1; fi
    if grep -q '^fallito' "$STATO" 2>/dev/null; then
      echo "REA: $(stato). Log: $LOGFILE"; tail -n 15 "$LOGFILE" 2>/dev/null; exit 1
    fi
    echo "REA: non installato, lo installo ora"
    exec bash "$SELF" ;;
  "" ) ;;
  *) echo "opzione sconosciuta: $1 (usa --sfondo, --attendi o --stato)"; exit 2 ;;
esac

# Installazione vera (in primo piano, oppure lanciata da --sfondo).
echo "in_corso $(date -u +%FT%TZ)" > "$STATO"
echo $$ > "$PIDFILE"
fallisci() { echo "fallito $(date -u +%FT%TZ): $1" > "$STATO"; echo "ERRORE: $1"; exit 1; }

scarica() { # URL FILE SHA
  local url=$1 dest=$2 want=$3 tmp
  if [ -f "$dest" ] && [ "$(sha "$dest")" = "$want" ]; then echo "  già presente: $(basename "$dest")"; return 0; fi
  tmp="$dest.part"
  curl -fsSL --retry 3 --retry-delay 2 -o "$tmp" "$url" || { rm -f "$tmp"; return 1; }
  local got; got=$(sha "$tmp")
  if [ "$got" != "$want" ]; then rm -f "$tmp"; echo "  SHA-256 diverso per $(basename "$dest"): $got"; return 1; fi
  mv "$tmp" "$dest"
  echo "  scaricato: $(basename "$dest")"
}

echo "== Java"
if command -v java >/dev/null 2>&1; then
  java -version 2>&1 | grep -v '^Picked up' | head -1
else
  echo "  Java non trovato: JADX e Apktool non partiranno. Installa un JDK 17+ (es. Temurin)."
fi

echo "== JADX headless 0.7.1"
scarica "$JADX_URL" "$DIR/$JADX_JAR" "$JADX_SHA" || fallisci "download di JADX non riuscito (serve github.com)"

echo "== Apktool 3.0.2"
scarica "$APKTOOL_URL" "$DIR/apktool.jar" "$APKTOOL_SHA" || fallisci "download di Apktool non riuscito (serve github.com)"
cat > "$DIR/apktool" <<'SH'
#!/bin/sh
exec java -jar "$(dirname "$0")/apktool.jar" "$@"
SH
chmod +x "$DIR/apktool"

# Comando `rea` con versione fissata e strumenti già impostati, per gli script delle repo che
# chiamano `rea` dal PATH (l'hook SessionStart mette $DIR/bin nel PATH della sessione).
mkdir -p "$DIR/bin"
cat > "$DIR/bin/rea" <<SH
#!/bin/sh
D="\$(cd "\$(dirname "\$0")/.." && pwd)"
: "\${REA_JADX_MCP_JAR:=\$D/$JADX_JAR}"
: "\${REA_APKTOOL_COMMAND:=\$D/apktool}"
: "\${REA_MCP_TOOL_FAMILIES:=android,apktool,artifact,adb,flutter}"
export REA_JADX_MCP_JAR REA_APKTOOL_COMMAND REA_MCP_TOOL_FAMILIES
exec npx -y rea-agents@$REA_VERSION "\$@"
SH
chmod +x "$DIR/bin/rea"

echo "== REA $REA_VERSION (cache di npx)"
if command -v npx >/dev/null 2>&1; then
  npx -y "rea-agents@$REA_VERSION" --version || echo "  npx non è riuscito a scaricare REA: riproverà all'avvio del server MCP"
else
  echo "  npx non trovato: serve Node.js"
fi

echo "pronto $(date -u +%FT%TZ)" > "$STATO"
echo "REA: pronto in $DIR"
