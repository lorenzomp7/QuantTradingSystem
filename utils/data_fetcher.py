"""
utils/data_fetcher.py
=====================
Recupero dei dati di mercato (OHLCV).

Due sorgenti:
    * "yahoo" -> dati storici reali via `yfinance` (nessuna API key richiesta).
    * "mock"  -> serie sintetica generata con un Geometric Brownian Motion
                 (GBM), deterministica grazie a un seed: utile per test,
                 sviluppo offline e come fallback se Yahoo non risponde
                 (capita spesso da IP di datacenter come quelli cloud).

Tutte le funzioni restituiscono un DataFrame con indice `DatetimeIndex`
(UTC-naive, ordinato) e colonne: open, high, low, close, volume.
"""

from __future__ import annotations

import threading
import time
import zlib
from dataclasses import dataclass

import numpy as np
import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]

# Mappa periodo yfinance -> numero approssimativo di barre giornaliere (per il mock).
_PERIOD_TO_DAYS = {
    "1d": 1, "5d": 5, "7d": 7, "1mo": 21, "60d": 60, "3mo": 63, "6mo": 126,
    "1y": 252, "2y": 504, "5y": 1260, "10y": 2520, "ytd": 200, "max": 5000,
}

# Durata in minuti delle candele intraday.
_INTERVAL_MINUTES = {"1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30, "60m": 60, "90m": 90, "1h": 60}

# Limiti di Yahoo Finance sullo storico intraday: (giorni massimi, periodo da usare).
_YAHOO_INTRADAY_LIMIT = {
    "1m": (7, "7d"), "2m": (60, "60d"), "5m": (60, "60d"), "15m": (60, "60d"),
    "30m": (60, "60d"), "90m": (60, "60d"), "60m": (504, "2y"), "1h": (504, "2y"),
}

# Numero massimo di barre generate dal mock.
_MAX_MOCK_BARS = 20_000

# Mappa interval -> frequenza pandas per l'indice temporale del mock.
_INTERVAL_TO_FREQ = {
    "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "1h", "60m": "1h", "1d": "B", "1wk": "W-FRI", "1mo": "ME",
}


class DataFetchError(RuntimeError):
    """Sollevata quando non è possibile ottenere dati validi."""


@dataclass
class _CacheEntry:
    data: pd.DataFrame
    source: str
    expires_at: float


class MarketDataFetcher:
    """
    Fetcher con cache in memoria (TTL) per evitare di martellare l'API di
    Yahoo ad ogni ciclo del bot o ad ogni refresh della dashboard.
    """

    def __init__(self, source: str = "yahoo", cache_ttl_seconds: int = 300) -> None:
        if source not in {"yahoo", "mock"}:
            raise ValueError(f"Sorgente dati non supportata: {source!r}")
        self.source = source
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[tuple, _CacheEntry] = {}
        self._lock = threading.Lock()
        self.last_source_used: str | None = None

    # ------------------------------------------------------------------ API
    def get_ohlcv(
        self,
        symbol: str,
        period: str = "2y",
        interval: str = "1d",
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """Restituisce i dati OHLCV per `symbol`, usando la cache se valida."""
        period = clamp_period(period, interval)
        key = (symbol.upper(), period, interval, self.source)
        now = time.monotonic()

        if use_cache:
            with self._lock:
                entry = self._cache.get(key)
            if entry and entry.expires_at > now:
                self.last_source_used = entry.source
                return entry.data.copy()

        if self.source == "yahoo":
            try:
                df = fetch_yahoo(symbol, period, interval)
                used = "yahoo"
            except Exception as exc:  # rete assente, rate limit, ticker errato...
                logger.warning("Yahoo Finance non disponibile (%s): uso dati mock.", exc)
                df = generate_mock_ohlcv(symbol, period, interval)
                used = "mock"
        else:
            df = generate_mock_ohlcv(symbol, period, interval)
            used = "mock"

        with self._lock:
            self._cache[key] = _CacheEntry(df, used, now + self.cache_ttl_seconds)
        self.last_source_used = used
        logger.info("Caricate %d barre per %s (%s, %s) da '%s'.", len(df), symbol, period, interval, used)
        return df.copy()

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()


# ---------------------------------------------------------------- Sorgenti
def fetch_yahoo(symbol: str, period: str = "2y", interval: str = "1d") -> pd.DataFrame:
    """Scarica dati storici da Yahoo Finance tramite yfinance."""
    import yfinance as yf  # import lazy: non serve se si usa solo il mock

    raw = yf.download(
        tickers=symbol,
        period=period,
        interval=interval,
        auto_adjust=True,   # prezzi già aggiustati per split/dividendi
        progress=False,
        threads=False,
    )
    if raw is None or raw.empty:
        raise DataFetchError(f"Nessun dato restituito da Yahoo per {symbol!r}")
    return normalize_ohlcv(raw)


def generate_mock_ohlcv(
    symbol: str = "MOCK",
    period: str = "2y",
    interval: str = "1d",
    start_price: float = 100.0,
    mu: float = 0.08,
    sigma: float = 0.20,
    seed: int | None = None,
) -> pd.DataFrame:
    """
    Genera una serie OHLCV sintetica con Geometric Brownian Motion:

        S_t = S_{t-1} * exp((mu - sigma^2 / 2) * dt + sigma * sqrt(dt) * Z_t)

    con Z_t ~ N(0, 1) e dt = 1/252 (barre giornaliere). Il seed di default è
    derivato dal simbolo, così lo stesso ticker produce sempre la stessa serie.
    """
    days = _PERIOD_TO_DAYS.get(period, 504)
    freq = _INTERVAL_TO_FREQ.get(interval, "B")
    minutes = _INTERVAL_MINUTES.get(interval)
    # Intraday: barre continue 24/7 (come le crypto); giornaliero: 1 barra al giorno.
    bars_per_day = (24 * 60) // minutes if minutes else 1
    n = min(days * bars_per_day, _MAX_MOCK_BARS)
    if seed is None:
        seed = zlib.crc32(symbol.upper().encode())  # deterministico tra processi
    rng = np.random.default_rng(seed)

    dt = 1 / (252 * bars_per_day)
    shocks = rng.standard_normal(n)
    log_returns = (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * shocks
    close = start_price * np.exp(np.cumsum(log_returns))

    # Open = close precedente con un piccolo gap; high/low coerenti con open/close.
    open_ = np.empty(n)
    open_[0] = start_price
    open_[1:] = close[:-1] * (1 + rng.normal(0, 0.002, n - 1))
    intrabar = np.abs(rng.normal(0, sigma * np.sqrt(dt) / 2, n))
    high = np.maximum(open_, close) * (1 + intrabar)
    low = np.minimum(open_, close) * (1 - intrabar)
    volume = rng.integers(1_000_000, 10_000_000, n).astype(float)

    end = pd.Timestamp.now().floor(freq) if minutes else pd.Timestamp.now().normalize()
    index = pd.date_range(end=end, periods=n, freq=freq, name="date")

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )


# ------------------------------------------------------------- Utilities
def clamp_period(period: str, interval: str) -> str:
    """
    Riduce `period` al massimo consentito da Yahoo per l'intervallo intraday
    richiesto (es. 5m -> 60d): evita errori come DATA_PERIOD=2y con 5m.
    """
    limit = _YAHOO_INTRADAY_LIMIT.get(interval)
    if limit is None:
        return period
    max_days, max_period = limit
    if _PERIOD_TO_DAYS.get(period, max_days + 1) > max_days:
        logger.warning("Periodo %r troppo lungo per l'intervallo %r: uso %r.", period, interval, max_period)
        return max_period
    return period


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """
    Uniforma un DataFrame OHLCV: colonne minuscole, niente MultiIndex
    (yfinance >= 0.2.48 restituisce colonne (Price, Ticker)), indice datetime
    ordinato e senza timezone, righe senza prezzo di chiusura rimosse.
    """
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    out.columns = [str(c).lower().replace(" ", "_") for c in out.columns]
    if "close" not in out.columns and "adj_close" in out.columns:
        out = out.rename(columns={"adj_close": "close"})

    missing = [c for c in OHLCV_COLUMNS if c not in out.columns]
    if missing:
        raise DataFetchError(f"Colonne mancanti nei dati: {missing}")

    out = out[OHLCV_COLUMNS].astype(float)
    out.index = pd.to_datetime(out.index)
    if out.index.tz is not None:
        out.index = out.index.tz_convert("UTC").tz_localize(None)
    out.index.name = "date"
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out.dropna(subset=["close"])
