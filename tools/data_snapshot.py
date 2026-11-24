"""
tools/data_snapshot.py
======================
Snapshot dei prezzi giornalieri per la dashboard su GitHub Pages.

    python -m tools.data_snapshot --out state/data --symbols SPY,QQQ,BTC-USD

Scarica tutta la storia di candele giornaliere da Yahoo (`max`, come il periodo
"max" di /api/backtest) per ogni simbolo e scrive
`<SIMBOLO>.csv` più `index.json` (righe, prima e ultima data, esito). Se Yahoo
non risponde per un simbolo, il file precedente resta com'è e `index.json`
lo segnala: la dashboard mostra la data dell'ultimo aggiornamento riuscito.
Esce con 1 se almeno un simbolo non si è aggiornato.

Con `--if-older-than ORE` scarica solo i simboli il cui ultimo aggiornamento riuscito
è più vecchio di ORE; un simbolo fallito si riprova dopo RETRY_AFTER_HOURS, per non
interrogare Yahoo a ogni esecuzione (è la stessa fonte del bot).
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
PERIOD = "max"
RETRY_AFTER_HOURS = 3.0


def file_name(symbol: str) -> str:
    return snapshot_name(symbol)


def _due(entry: dict, hours: float, now: datetime) -> bool:
    """True se il simbolo va scaricato: mai riuscito, più vecchio di `hours`, o fallito da tempo."""
    def ts(key: str) -> datetime | None:
        try:
            return datetime.fromisoformat(entry[key])
        except (KeyError, ValueError, TypeError):
            return None
    failed_at = ts("failed_at")
    if not entry.get("ok") and failed_at and now - failed_at < timedelta(hours=RETRY_AFTER_HOURS):
        return False                                    # fallito da poco: si aspetta
    updated_at = ts("updated_at")
    return not entry.get("ok") or updated_at is None or now - updated_at >= timedelta(hours=hours)


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
    now_dt = datetime.now(timezone.utc)
    if args.if_older_than is not None:
        skipped = [x for x in symbols if not _due(index["symbols"].get(x, {}), args.if_older_than, now_dt)]
        if skipped:
            print(f"Non da aggiornare ora: {', '.join(skipped)}")
        symbols = [x for x in symbols if x not in skipped]
    now = now_dt.isoformat()
    code = 0
    for symbol in symbols:
        entry = index["symbols"].get(symbol, {})
        try:
            path = args.out / file_name(symbol)
        except ValueError as exc:              # simbolo non valido: si segnala e si va avanti
            code = 1
            index["symbols"][symbol] = {"ok": False, "error": str(exc), "failed_at": now}
            print(f"{symbol}: {exc}", file=sys.stderr)
            continue
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
