#!/bin/sh
# Avvia uno script Python degli hook di Claude Code con il primo interprete
# Python 3.9+ disponibile: python3, python oppure il launcher "py" di Windows.
# Scarta gli alias finti (es. quello del Microsoft Store su Windows) provandoli.
# Se Python manca, l'hook viene saltato senza bloccare Claude.
script="$1"
shift

for py in python3 python; do
  if command -v "$py" >/dev/null 2>&1 &&
     "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' </dev/null >/dev/null 2>&1; then
    exec "$py" "$script" "$@"
  fi
done

if command -v py >/dev/null 2>&1 &&
   py -3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' </dev/null >/dev/null 2>&1; then
  exec py -3 "$script" "$@"
fi

echo "Hook Claude Code saltato: serve Python 3.9 o successivo (https://www.python.org/downloads/)." >&2
exit 0
