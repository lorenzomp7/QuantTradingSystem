"""
strategy/indicators.py
======================
Indicatori tecnici calcolati solo con dati passati (nessuna finestra centrata).

Formule standard, con gli stessi parametri predefiniti di pandas_ta usati nella
tesi THESIS-fuzzylogic-stocks (alessandroiva): RSI 14 di Wilder, MACD 12/26/9,
stocastico %K 14 lisciato su 3. Codice scritto da zero (la repo della tesi non
ha licenza).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """RSI di Wilder: medie esponenziali con α = 1/window di guadagni e perdite."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    loss = (-delta).clip(lower=0).ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100 - 100 / (1 + gain / loss)
    out[(gain == 0) & (loss == 0)] = 50.0          # prezzo fermo: neutro
    return out.where(gain.notna())


def macd_histogram(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    """Istogramma MACD: (EMA veloce − EMA lenta) − sua EMA di segnale. NaN per le prime slow−1 barre."""
    ema = lambda s, n: s.ewm(span=n, adjust=False).mean()  # noqa: E731
    line = ema(close, fast) - ema(close, slow)
    hist = line - ema(line, signal)
    hist.iloc[: slow - 1] = np.nan
    return hist


def stochastic_k(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14, smooth: int = 3) -> pd.Series:
    """%K lisciato: media su `smooth` barre di 100 × (close − minimo) / (massimo − minimo)."""
    lowest = low.rolling(window, min_periods=window).min()
    highest = high.rolling(window, min_periods=window).max()
    span = highest - lowest
    raw = 100 * (close - lowest) / span.where(span > 0)
    raw = raw.where(span != 0, 50.0).where(span.notna())
    return raw.rolling(smooth, min_periods=smooth).mean()
