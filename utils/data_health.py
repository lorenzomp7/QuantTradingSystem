"""
utils/data_health.py
====================
Controllo della fonte dati con simboli "canarino".

    python -m utils.data_health            # esce con 1 se una fonte non è sana
    python -m utils.data_health --output report.json

Per ogni canarino scarica i dati da Yahoo **senza** ripiego sulla serie mock
e ne controlla schema, valori e freschezza. Ogni controllo gira in un
processo separato con un tempo massimo: una fonte che non risponde fa
fallire il comando, non viene mai saltata in silenzio.

Ripreso da Vibe-Trading (HKUDS), `agent/backtest/loader_health.py`
(commit 597d902, 23/11/2026), ridotto alla sola fonte Yahoo di QTS.

    Copyright (c) 2026 Vibe-Trading Contributors — MIT License
    https://github.com/HKUDS/Vibe-Trading/blob/main/LICENSE
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Simbolo canarino -> (periodo, intervallo). BTC è aperto 24/7, quindi la
# candela oraria deve essere recente; SPY copre la borsa USA.
CANARY_SYMBOLS: dict[str, tuple[str, str]] = {
    "BTC-USD": ("5d", "1h"),
    "SPY": ("1mo", "1d"),
}
PROBE_TIMEOUT_SECONDS = 60
OHLCV = ["open", "high", "low", "close", "volume"]


def check_frame(frame: pd.DataFrame, interval: str, now: pd.Timestamp | None = None) -> dict:
    """
    Verifica un DataFrame OHLCV normalizzato e restituisce stato e motivo.

    Stati: `healthy`, `stale` (ultima candela troppo vecchia) o `invalid`
    con il motivo (`empty_frame`, `schema`, `nonfinite_values`, …).
    """
    from utils.data_fetcher import bar_age, interval_timedelta, max_bar_age, utc_now

    def bad(reason: str) -> dict:
        return {"status": "invalid", "reason": reason}

    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return bad("empty_frame")
    if not frame.columns.is_unique or not set(OHLCV) <= set(frame.columns):
        return bad("schema")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.hasnans:
        return bad("datetime_index")
    if not frame.index.is_monotonic_increasing or not frame.index.is_unique:
        return bad("index_order")
    values = frame[OHLCV].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        return bad("nonfinite_values")
    if (values[:, :4] <= 0).any() or (values[:, 4] < 0).any():
        return bad("invalid_prices_or_volume")
    if (frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any() or (
        frame["low"] > frame[["open", "close"]].min(axis=1)
    ).any():
        return bad("ohlc_order")

    now = now if now is not None else utc_now()
    last = frame.index[-1]
    if last > now + interval_timedelta(interval) + pd.Timedelta(days=1):
        return bad("future_bar")
    age = bar_age(last, now)
    out = {"rows": len(frame), "last_bar": last.isoformat(), "age_seconds": round(age.total_seconds())}
    if age > max_bar_age(interval):
        return {"status": "stale", "reason": "last_bar_too_old", **out}
    return {"status": "healthy", **out}


def probe(symbol: str, period: str, interval: str) -> dict:
    """Scarica da Yahoo senza ripiego e controlla il risultato."""
    from utils import data_fetcher

    row = {"symbol": symbol, "period": period, "interval": interval, "source": "yahoo"}
    try:
        frame = data_fetcher.fetch_yahoo(symbol, period, interval)
    except Exception as exc:  # rete, rate limit, simbolo, dipendenza mancante
        return {**row, "status": "error", "reason": type(exc).__name__, "detail": str(exc)[:200]}
    return {**row, **check_frame(frame, interval)}


def _probe_isolated(symbol: str, period: str, interval: str, timeout: int) -> dict:
    """Esegue `probe` in un processo figlio con un tempo massimo."""
    row = {"symbol": symbol, "period": period, "interval": interval, "source": "yahoo"}
    cmd = [sys.executable, "-m", "utils.data_health", "--probe", symbol, period, interval]
    root = Path(__file__).resolve().parent.parent
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=root)
    except subprocess.TimeoutExpired:
        return {**row, "status": "error", "reason": "timeout", "detail": f"nessuna risposta in {timeout} s"}
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {**row, "status": "error", "reason": "probe_crashed", "detail": proc.stderr[-200:]}


def run(inline: bool = False, timeout: int = PROBE_TIMEOUT_SECONDS) -> dict:
    """Controlla tutti i canarini e restituisce il rapporto."""
    rows = [
        probe(s, p, i) if inline else _probe_isolated(s, p, i, timeout)
        for s, (p, i) in CANARY_SYMBOLS.items()
    ]
    return {
        "ok": all(r["status"] == "healthy" for r in rows),
        "checked_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "sources": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Controllo della fonte dati (Yahoo) con simboli canarino.")
    parser.add_argument("--output", type=Path, help="scrive il rapporto JSON anche in questo file")
    parser.add_argument("--inline", action="store_true", help="niente processi separati (per i test)")
    parser.add_argument("--timeout", type=int, default=PROBE_TIMEOUT_SECONDS)
    parser.add_argument("--probe", nargs=3, metavar=("SYMBOL", "PERIOD", "INTERVAL"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.probe:  # processo figlio: una riga JSON su stdout
        print(json.dumps(probe(*args.probe)))
        return 0

    report = run(inline=args.inline, timeout=args.timeout)
    text = json.dumps(report, indent=2)
    print(text)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
