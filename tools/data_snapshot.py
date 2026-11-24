"""
tools/data_snapshot.py
======================
Snapshot dei prezzi giornalieri per la dashboard su GitHub Pages.

    python -m tools.data_snapshot --out state/data --symbols SPY,QQQ,BTC-USD

Scarica 10 anni di candele giornaliere da Yahoo per ogni simbolo e scrive
`<SIMBOLO>.csv` più `index.json` (righe, prima e ultima data, esito). Se Yahoo
non risponde per un simbolo, il file precedente resta com'è e `index.json`
lo segnala: la dashboard mostra la data dell'ultimo aggiornamento riuscito.
Esce con 1 se almeno un simbolo non si è aggiornato.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from utils import data_fetcher  # noqa: E402
from web.static_api import snapshot_name  # noqa: E402

DEFAULT_SYMBOLS = "SPY,QQQ,AAPL,MSFT,BTC-USD,ETH-USD"
PERIOD = "10y"


def file_name(symbol: str) -> str:
    return snapshot_name(symbol)


def _fresh(index: dict, symbols: list[str], hours: float) -> bool:
    """True se ogni simbolo ha un aggiornamento riuscito più recente di `hours` ore."""
    limit = datetime.now(timezone.utc) - timedelta(hours=hours)
    for symbol in symbols:
        entry = index.get("symbols", {}).get(symbol) or {}
        try:
            if not entry.get("ok") or datetime.fromisoformat(entry["updated_at"]) < limit:
                return False
        except (KeyError, ValueError, TypeError):
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Snapshot dei prezzi giornalieri per la dashboard statica.")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--symbols", default=DEFAULT_SYMBOLS, help="separati da virgola")
    ap.add_argument("--if-older-than", type=float, metavar="ORE",
                    help="non fa nulla se l'ultimo snapshot riuscito per tutti i simboli è più recente")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    index_path = args.out / "index.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        index = {"symbols": {}}
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.if_older_than is not None and _fresh(index, symbols, args.if_older_than):
        print(f"Snapshot aggiornati da meno di {args.if_older_than:g} ore: niente da fare.")
        return 0
    now = datetime.now(timezone.utc).isoformat()
    code = 0
    for symbol in symbols:
        path = args.out / file_name(symbol)
        entry = index["symbols"].get(symbol, {})
        try:
            frame = data_fetcher.fetch_yahoo(symbol, PERIOD, "1d")
            frame.index.name = "date"
            frame.round(6).to_csv(path)
            entry = {"file": path.name, "rows": len(frame), "first": str(frame.index[0].date()),
                     "last": str(frame.index[-1].date()), "updated_at": now, "ok": True}
            print(f"{symbol}: {len(frame)} righe fino al {entry['last']}")
        except Exception as exc:  # rete, Yahoo, simbolo inesistente
            code = 1
            if path.exists() and "rows" not in entry:
                old = pd.read_csv(path, index_col=0, parse_dates=True)
                entry = {"file": path.name, "rows": len(old), "first": str(old.index[0].date()),
                         "last": str(old.index[-1].date())}
            entry.update({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}", "failed_at": now})
            print(f"{symbol}: NON aggiornato ({entry['error']})", file=sys.stderr)
        index["symbols"][symbol] = entry
    index["written_at"] = now
    index_path.write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())
