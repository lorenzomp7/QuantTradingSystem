"""
strategy/metrics.py
===================
Fattore di annualizzazione, tabella dei trade e statistiche per trade.

Prima tutte le metriche erano annualizzate con 252 barre l'anno, giusto solo
per le azioni con candele giornaliere. Con la configurazione predefinita del
bot (BTC-USD a 5 minuti, mercato aperto 24/7) CAGR, volatilità, Sharpe e
Sortino risultavano sbagliati. Qui il numero di barre l'anno dipende da
intervallo e mercato, oppure si misura dalle date della serie.

Logica ripresa da Vibe-Trading (HKUDS), `agent/backtest/metrics.py`:
`calc_bars_per_year`, `effective_bars_per_year` e `win_rate_and_stats`
(commit 597d902, 23/11/2026). Codice riscritto per QuantTradingSystem.

    Copyright (c) 2026 Vibe-Trading Contributors — MIT License
    https://github.com/HKUDS/Vibe-Trading/blob/main/LICENSE
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

DEFAULT_BARS_PER_YEAR = 252

# Giorni di contrattazione per anno e minuti di seduta per giorno.
#   crypto: 24/7          -> 365 giorni, 1440 minuti
#   stocks: borsa USA     -> 252 giorni, 6,5 h = 390 minuti
#   forex : 24/5          -> 260 giorni, 1440 minuti
_MARKETS: dict[str, tuple[int, int]] = {
    "crypto": (365, 1440),
    "stocks": (252, 390),
    "forex": (260, 1440),
}

# Durata delle candele intraday in minuti (token di yfinance).
_INTRADAY_MINUTES = {"1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30, "60m": 60, "90m": 90, "1h": 60}

# Barre per giorno in borsa quando la seduta non si divide in parti intere:
# Yahoo restituisce 7 candele orarie (9:30…15:30) e 5 da 90 minuti.
_STOCK_BARS_OVERRIDE = {"60m": 7, "1h": 7, "90m": 5}

# Candele che contano periodi di calendario, qualunque sia il mercato.
_CALENDAR_BARS_PER_YEAR = {"1wk": 52, "1mo": 12, "3mo": 4}

# Suffissi dei ticker crypto di Yahoo (BTC-USD, ETH-USDT, …).
_CRYPTO_QUOTES = {"USD", "USDT", "USDC", "EUR", "GBP", "JPY", "BTC", "ETH"}


def normalize_interval(interval: str) -> str:
    """
    Riporta un intervallo ai token di yfinance (minuscoli).

    `1M` è un mese e va riconosciuto prima di passare alle minuscole, che lo
    leggerebbero come un minuto (stessa regola di Vibe-Trading).
    """
    token = str(interval or "1d").strip()
    if token == "1M":
        return "1mo"
    lower = token.lower()
    return {"1w": "1wk", "1mo": "1mo", "1wk": "1wk"}.get(lower, lower)


def infer_market(symbol: str) -> str:
    """Mercato del simbolo: `crypto` (BTC-USD), `forex` (EURUSD=X) o `stocks`."""
    s = (symbol or "").strip().upper()
    if s.endswith("=X"):
        return "forex"
    m = re.fullmatch(r"[A-Z0-9]+-([A-Z]+)", s)
    if m and m.group(1) in _CRYPTO_QUOTES:
        return "crypto"
    return "stocks"


def bars_per_year(interval: str = "1d", market: str = "stocks") -> int:
    """
    Numero di barre in un anno per l'intervallo e il mercato indicati.

    Esempi: `1d` crypto = 365, `1d` azioni = 252, `5m` crypto = 365 × 288,
    `1h` azioni = 252 × 7, `1wk` = 52, `1mo` (o `1M`) = 12.
    """
    if market not in _MARKETS:
        raise ValueError(f"Mercato sconosciuto: {market!r} (attesi: {sorted(_MARKETS)})")
    key = normalize_interval(interval)
    if key in _CALENDAR_BARS_PER_YEAR:
        return _CALENDAR_BARS_PER_YEAR[key]
    days, session_minutes = _MARKETS[market]
    if key == "1d":
        return days
    if key == "5d":
        return max(days // 5, 1)
    minutes = _INTRADAY_MINUTES.get(key)
    if minutes is None:
        raise ValueError(f"Intervallo non supportato: {interval!r}")
    if market == "stocks" and key in _STOCK_BARS_OVERRIDE:
        return days * _STOCK_BARS_OVERRIDE[key]
    return days * (session_minutes // minutes)


def years_elapsed(index: Any) -> float:
    """
    Anni di calendario coperti dall'indice: secondi totali / (365,25 giorni).

    Si usa `total_seconds()` e non `.days`, che tronca la frazione di giorno:
    72 barre orarie coprono 2 giorni e 23 ore, non 2 giorni.
    """
    if len(index) < 2:
        return 0.0
    try:
        span = index[-1] - index[0]
        seconds = span.total_seconds()
    except (AttributeError, TypeError):
        return 0.0
    return seconds / 86_400.0 / 365.25


def effective_bars_per_year(index: Any, default: int = DEFAULT_BARS_PER_YEAR) -> int:
    """
    Barre per anno misurate sulla serie: barre osservate / anni trascorsi.

    Serve quando intervallo o mercato non sono noti. Senza date misurabili
    restituisce `default`; una serie più corta di un giorno conta come un
    anno, così una curva di poche barre si annualizza al proprio rendimento.
    """
    n = len(index)
    if n == 0:
        return default
    if n >= 2:
        try:
            index[-1] - index[0]
        except TypeError:
            return default
    calendar_days = years_elapsed(index) * 365.25
    years = calendar_days / 365.25 if calendar_days >= 1.0 else 1.0
    return int(n / years) if years > 0 else default


ROUND_TRIP_COLUMNS = ["entry_time", "exit_time", "entry_price", "exit_price", "bars", "gross_pct", "net_pct",
                      "pnl", "fees", "quantity", "mae_pct", "mfe_pct", "status"]


def round_trip_table(
    trades: pd.DataFrame,
    index: pd.Index | None = None,
    fee_bps: float = 0.0,
    initial_capital: float | None = None,
    high: pd.Series | None = None,
    low: pd.Series | None = None,
    close: pd.Series | None = None,
    execution: str = "next_open",
) -> pd.DataFrame:
    """
    Tabella dei trade: una riga per ogni BUY, chiuso dal SELL successivo.

    `trades` ha indice datetime e colonne `side` e `price` (prezzo eseguito,
    slippage compreso). Con c = fee_bps / 10000, pagata all'ingresso e all'uscita:

        gross_pct = P_out / P_in − 1
        net_pct   = (P_out / P_in) · (1 − c)² − 1

    Percentuali in punti percentuali. Con `initial_capital` il capitale è
    tutto investito a ogni ingresso (come nel backtest): `quantity`, `pnl` e
    `fees` in valuta, e la somma dei `pnl` è la variazione dell'equity.
    `bars` (barre tra ingresso e uscita) richiede `index`.

    Un BUY senza SELL è il trade ancora aperto: `status = "open"`, uscita
    all'ultimo `close` con la sola commissione d'ingresso (senza `close` i
    campi d'uscita restano vuoti).

    `mae_pct` / `mfe_pct` sono il peggiore e il migliore scostamento dal prezzo
    d'ingresso toccati durante il trade, da `low` e `high` (vuoti se mancano).
    Contano solo i prezzi tra ingresso e uscita: con `next_open` le barre da
    quella d'ingresso a quella prima dell'uscita, più il prezzo d'uscita (il
    resto della barra d'uscita viene dopo la vendita); con `close` le barre
    dopo quella d'ingresso fino a quella d'uscita compresa.
    """
    c = fee_bps / 10_000
    lo = low.to_numpy(dtype=float) if (low is not None and high is not None and index is not None) else None
    hi = high.to_numpy(dtype=float) if lo is not None else None
    capital = float(initial_capital) if initial_capital is not None else None
    rows: list[dict] = []

    def add(entry_ts, entry_price: float, exit_ts, exit_price: float | None, closed: bool) -> None:
        nonlocal capital
        i_in = int(index.get_loc(entry_ts)) if index is not None else None
        i_out = int(index.get_loc(exit_ts)) if index is not None and exit_ts is not None else None
        gross = net = None
        if exit_price is not None:
            gross = exit_price / entry_price - 1
            net = (1 + gross) * (1 - c) * ((1 - c) if closed else 1.0) - 1
        quantity = pnl = fees = None
        if capital is not None:
            quantity = capital * (1 - c) / entry_price
            fees = capital * c
            if net is not None:
                pnl = capital * net
                if closed:
                    fees += quantity * exit_price * c
                    capital += pnl
        mae = mfe = None
        if lo is not None and exit_price is not None and i_out is not None:
            if execution == "next_open":
                first, stop = i_in, (i_out if closed else i_out + 1)
            else:
                first, stop = i_in + 1, i_out + 1
            worst, best = min(entry_price, exit_price), max(entry_price, exit_price)
            if stop > first:
                worst = min(worst, float(np.nanmin(np.append(lo[first:stop], worst))))
                best = max(best, float(np.nanmax(np.append(hi[first:stop], best))))
            mae, mfe = (worst / entry_price - 1) * 100, (best / entry_price - 1) * 100
        rows.append({
            "entry_time": entry_ts, "exit_time": exit_ts, "entry_price": entry_price, "exit_price": exit_price,
            "bars": (i_out - i_in) if i_out is not None else None,
            "gross_pct": gross * 100 if gross is not None else None,
            "net_pct": net * 100 if net is not None else None,
            "pnl": pnl, "fees": fees, "quantity": quantity, "mae_pct": mae, "mfe_pct": mfe,
            "status": "closed" if closed else "open",
        })

    entry_price, entry_time = None, None
    for ts, row in trades.iterrows():
        if row["side"] == "BUY":
            entry_price, entry_time = float(row["price"]), ts
        elif entry_price is not None:
            add(entry_time, entry_price, ts, float(row["price"]), closed=True)
            entry_price, entry_time = None, None
    if entry_price is not None:                               # trade ancora aperto a fine periodo
        if close is not None and len(close):
            add(entry_time, entry_price, close.index[-1], float(close.iloc[-1]), closed=False)
        else:
            add(entry_time, entry_price, None, None, closed=False)

    table = pd.DataFrame(rows, columns=ROUND_TRIP_COLUMNS)
    numeric = [c_ for c_ in ROUND_TRIP_COLUMNS if c_ not in ("entry_time", "exit_time", "status")]
    table[numeric] = table[numeric].astype(float)             # None -> NaN, uguale con pandas 2 e 3
    return table


def round_trip_stats(trades: pd.DataFrame, index: pd.Index | None = None, fee_bps: float = 0.0) -> dict:
    """
    Statistiche sui round-trip completi (BUY seguito da SELL), al netto delle
    commissioni: ogni trade rende `(P_out / P_in) · (1 − c)² − 1` con
    c = fee_bps / 10000 (con `fee_bps = 0` sono i valori lordi). Si calcolano
    dalla tabella di `round_trip_table`.

    `trades` ha indice datetime e colonne `side` e `price`. Un BUY senza SELL
    successivo (posizione ancora aperta) non entra nelle statistiche: è
    contato in `open_trades`. `index` è l'indice delle barre del backtest e
    serve solo per la durata media in barre.

    Come in Vibe-Trading `win_rate_and_stats`: profit factor e rapporto
    guadagno/perdita sono `None` quando nessun trade ha perso, perché
    dividono per le perdite (0 farebbe sembrare il risultato pessimo).
    """
    table = round_trip_table(trades, index, fee_bps)
    closed = table[table["status"] == "closed"]
    pnl = [float(p) / 100 for p in closed["net_pct"]]
    holding = [int(b) for b in closed["bars"]] if index is not None else []

    n = len(pnl)
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p < 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    gross_loss = abs(sum(losses))

    max_consec = cur = 0
    for p in pnl:
        cur = cur + 1 if p < 0 else 0
        max_consec = max(max_consec, cur)

    return {
        "round_trips": n,
        "open_trades": int(len(table) - n),
        "win_rate_pct": round(len(wins) / n * 100, 2) if n else None,
        "avg_win_pct": round(avg_win * 100, 2) if wins else None,
        "avg_loss_pct": round(avg_loss * 100, 2) if losses else None,
        "avg_trade_pct": round(sum(pnl) / n * 100, 2) if n else None,
        "profit_loss_ratio": round(avg_win / abs(avg_loss), 4) if losses else None,
        "profit_factor": round(sum(wins) / gross_loss, 4) if gross_loss > 1e-12 else None,
        "max_consecutive_losses": max_consec,
        "avg_holding_bars": round(sum(holding) / len(holding), 1) if holding else None,
    }
