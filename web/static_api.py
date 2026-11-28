"""
web/static_api.py
=================
`/api/backtest` e `/api/backtest/costs` senza server: la dashboard su GitHub
Pages esegue questo modulo nel browser con Pyodide, con lo stesso codice di
strategia, backtest e metriche del server FastAPI.

I prezzi arrivano dallo snapshot giornaliero pubblicato con il sito
(`data/<SIMBOLO>.csv`, aggiornato da GitHub Actions); per un simbolo senza
snapshot si usa la serie mock, come fa il server quando Yahoo non risponde.
Stessi controlli sui parametri degli endpoint (ValueError -> messaggio di errore).
"""

from __future__ import annotations

import io
import re

import pandas as pd

from strategy.metrics import bars_per_year, infer_market
from strategy.quant_model import EXECUTIONS, MovingAverageCrossover, StrategyParams
from utils.data_fetcher import clamp_period, generate_mock_ohlcv, normalize_ohlcv

INITIAL_CAPITAL = 10_000.0
DEFAULT_FEE_BPS = 5.0
PERIODS = {"1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"}
_OFFSETS = {"1mo": pd.DateOffset(months=1), "3mo": pd.DateOffset(months=3), "6mo": pd.DateOffset(months=6),
            "1y": pd.DateOffset(years=1), "2y": pd.DateOffset(years=2), "5y": pd.DateOffset(years=5),
            "10y": pd.DateOffset(years=10)}


def _int(params: dict, name: str, default: int, lo: int, hi: int) -> int:
    raw = params.get(name, default)
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"{name} deve essere un intero, ricevuto {raw!r}") from exc
    if not lo <= value <= hi:
        raise ValueError(f"{name} deve essere tra {lo} e {hi}, ricevuto {value}")
    return value


def parse_params(params: dict) -> tuple[str, str, StrategyParams]:
    """Stessi limiti dei Query di main.py."""
    symbol = str(params.get("symbol", "SPY")).strip()
    if not 1 <= len(symbol) <= 20:
        raise ValueError("Il simbolo deve avere da 1 a 20 caratteri.")
    period = str(params.get("period", "2y")).strip()
    if period not in PERIODS:
        raise ValueError(f"Periodo non valido: {period!r} (validi: {', '.join(sorted(PERIODS))})")
    try:
        fee = float(params.get("fee_bps", DEFAULT_FEE_BPS))
    except ValueError as exc:
        raise ValueError("fee_bps deve essere un numero") from exc
    if not 0 <= fee <= 500:
        raise ValueError("fee_bps deve essere tra 0 e 500")
    execution = str(params.get("execution", "next_open")).strip()
    if execution not in EXECUTIONS:
        raise ValueError(f"execution deve essere uno di {list(EXECUTIONS)}, ricevuto {execution!r}")
    strat = StrategyParams(
        short_window=_int(params, "short_window", 20, 1, 400),
        long_window=_int(params, "long_window", 50, 2, 500),
        initial_capital=INITIAL_CAPITAL,
        fee_bps=fee,
        execution=execution,
    )
    return symbol, period, strat


def slice_period(data: pd.DataFrame, period: str) -> pd.DataFrame:
    """Le ultime barre del periodo, come `period` di Yahoo Finance."""
    if period == "max" or data.empty:
        return data
    last = data.index[-1]
    start = pd.Timestamp(year=last.year, month=1, day=1) if period == "ytd" else last - _OFFSETS[period]
    return data[data.index >= start]


def load_data(symbol: str, period: str, csv_text: str | None) -> tuple[pd.DataFrame, str]:
    """Snapshot pubblicato se c'è, altrimenti serie mock (come il server senza Yahoo)."""
    if csv_text:
        frame = pd.read_csv(io.StringIO(csv_text), index_col=0, parse_dates=True)
        return slice_period(normalize_ohlcv(frame), period), "snapshot"
    return generate_mock_ohlcv(symbol, clamp_period(period, "1d"), "1d"), "mock"


def backtest(params: dict, csv_text: str | None) -> dict:
    """Come GET /api/backtest."""
    symbol, period, strat = parse_params(params)
    data, source = load_data(symbol, period, csv_text)
    bpy = bars_per_year("1d", infer_market(symbol))
    out = MovingAverageCrossover(strat).backtest(data, bars_per_year=bpy).to_dict()
    out["symbol"] = symbol.upper()
    out["data_source"] = source
    return out


def costs(params: dict, csv_text: str | None) -> dict:
    """Come GET /api/backtest/costs."""
    from strategy.research import cost_sensitivity

    symbol, period, strat = parse_params(params)
    data, source = load_data(symbol, period, csv_text)
    out = cost_sensitivity(MovingAverageCrossover(strat), data,
                           bars_per_year=bars_per_year("1d", infer_market(symbol)))
    out["symbol"] = symbol.upper()
    out["data_source"] = source
    return out


def snapshot_name(symbol: str) -> str:
    """Nome del file di snapshot per un simbolo (uguale a tools/data_snapshot.py)."""
    s = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9^=.\-]{1,20}", s) or ".." in s:
        raise ValueError(f"Simbolo non valido: {symbol!r}")
    return s.replace("^", "_") + ".csv"
