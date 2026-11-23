"""
strategy/metrics.py
===================
Fattore di annualizzazione e statistiche per trade.

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


def round_trip_stats(trades: pd.DataFrame, index: pd.Index | None = None) -> dict:
    """
    Statistiche sui round-trip completi (BUY seguito da SELL), al lordo dei costi.

    `trades` ha indice datetime e colonne `side` e `price`. Un BUY senza SELL
    successivo (posizione ancora aperta) non conta. `index` è l'indice delle
    barre del backtest e serve solo per la durata media in barre.

    Come in Vibe-Trading `win_rate_and_stats`: profit factor e rapporto
    guadagno/perdita sono `None` quando nessun trade ha perso, perché
    dividono per le perdite (0 farebbe sembrare il risultato pessimo).
    """
    pnl: list[float] = []
    holding: list[int] = []
    entry_price, entry_time = None, None
    for ts, row in trades.iterrows():
        if row["side"] == "BUY":
            entry_price, entry_time = float(row["price"]), ts
        elif entry_price is not None:
            pnl.append(float(row["price"]) / entry_price - 1)
            if index is not None:
                holding.append(int(index.get_loc(ts)) - int(index.get_loc(entry_time)))
            entry_price, entry_time = None, None

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
        "win_rate_pct": round(len(wins) / n * 100, 2) if n else None,
        "avg_win_pct": round(avg_win * 100, 2) if wins else None,
        "avg_loss_pct": round(avg_loss * 100, 2) if losses else None,
        "profit_loss_ratio": round(avg_win / abs(avg_loss), 4) if losses else None,
        "profit_factor": round(sum(wins) / gross_loss, 4) if gross_loss > 1e-12 else None,
        "max_consecutive_losses": max_consec,
        "avg_holding_bars": round(sum(holding) / len(holding), 1) if holding else None,
    }
