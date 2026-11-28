"""
strategy/rsi_macd.py
====================
Strategia long-only che combina RSI, istogramma MACD e stocastico con regole fuzzy.

Idea dalla tesi THESIS-fuzzylogic-stocks (alessandroiva): tre livelli Low / Med /
High per ogni indicatore con appartenenze gaussiane, regole con AND = minimo,
aggregazione con il massimo, parametri adatti all'ottimizzazione genetica.
Codice scritto da zero (la repo non ha licenza) e adattato a QTS:

* solo long: le regole Sell chiudono la posizione, non aprono short;
* l'istogramma MACD si normalizza con la deviazione standard delle ultime
  `norm_window` barre (la tesi usa l'intervallo dell'intera serie: look-ahead);
* tolta la regola Buy con la stessa premessa di una regola Sell
  (MACD Low ∧ RSI High ∧ STO High), che nella tesi si annullano.

Regole (forza = minimo delle appartenenze):
    Buy  : MACD High ∧ RSI Low ∧ STO Low     |   MACD High ∧ RSI Med ∧ STO Med
    Sell : MACD Low ∧ RSI Med ∧ STO High     |   RSI High ∧ STO Low
           MACD Low ∧ RSI High ∧ STO High
Si entra quando Buy − Sell > soglia, si esce quando Sell − Buy > soglia,
altrimenti si mantiene lo stato. Il segnale della barra t usa solo dati fino a t.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from strategy.indicators import macd_histogram, rsi, stochastic_k
from strategy.quant_model import BaseStrategy, check_execution

# Ampiezza di base delle gaussiane, moltiplicata per `sigma`.
_RSI_WIDTH = 10.0
_STO_WIDTH = 15.0
_MACD_WIDTH = 0.75
_MACD_CENTERS = (-1.0, 0.0, 1.0)   # istogramma in deviazioni standard


def gauss(x, center: float, width: float):
    """Appartenenza gaussiana: 1 al centro, exp(−½) a una larghezza di distanza."""
    return np.exp(-0.5 * ((np.asarray(x, dtype=float) - center) / width) ** 2)


@dataclass
class RsiMacdParams:
    rsi_window: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    sto_k: int = 14
    sto_smooth: int = 3
    norm_window: int = 50
    rsi_low: float = 30.0
    rsi_med: float = 50.0
    rsi_high: float = 70.0
    sto_low: float = 20.0
    sto_med: float = 50.0
    sto_high: float = 80.0
    sigma: float = 1.0           # moltiplica l'ampiezza di tutte le gaussiane
    threshold: float = 0.1       # margine minimo tra forza Buy e Sell
    initial_capital: float = 10_000.0
    fee_bps: float = 5.0
    risk_free_rate: float = 0.0
    execution: str = "next_open"   # "next_open" (open della barra dopo il segnale) o "close"

    def __post_init__(self) -> None:
        for name in ("rsi_window", "macd_fast", "macd_slow", "macd_signal", "sto_k", "norm_window"):
            if int(getattr(self, name)) < 2:
                raise ValueError(f"{name} deve essere almeno 2.")
        if self.sto_smooth < 1:
            raise ValueError("sto_smooth deve essere almeno 1.")
        if self.macd_fast >= self.macd_slow:
            raise ValueError("macd_fast deve essere minore di macd_slow.")
        for prefix in ("rsi", "sto"):
            lo, med, hi = (getattr(self, f"{prefix}_{k}") for k in ("low", "med", "high"))
            if not 0 <= lo < med < hi <= 100:
                raise ValueError(f"Serve 0 <= {prefix}_low < {prefix}_med < {prefix}_high <= 100.")
        if self.sigma <= 0:
            raise ValueError("sigma deve essere > 0.")
        if not 0 <= self.threshold < 1:
            raise ValueError("threshold deve essere in [0, 1).")
        if self.initial_capital <= 0:
            raise ValueError("initial_capital deve essere > 0.")
        if self.fee_bps < 0:
            raise ValueError("fee_bps non può essere negativo.")
        check_execution(self.execution)


class RsiMacdFuzzy(BaseStrategy):
    """RSI + MACD + stocastico con regole fuzzy, long-only."""

    name = "rsi_macd_fuzzy"

    def __init__(self, params: RsiMacdParams | None = None) -> None:
        super().__init__(params or RsiMacdParams())

    def warmup_bars(self) -> int:
        """
        Barre prima che tutti gli indicatori siano definiti: RSI dalla barra
        `rsi_window`, z-score MACD da `macd_slow − 1 + norm_window − 1`,
        stocastico da `sto_k − 1 + sto_smooth − 1`.
        """
        p = self.params
        return max(p.rsi_window, p.macd_slow + p.norm_window - 2, p.sto_k + p.sto_smooth - 2)

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        missing = [c for c in ("high", "low", "close") if c not in data.columns]
        if missing:
            raise ValueError(f"Il DataFrame deve contenere le colonne {missing}.")
        warm = self.warmup_bars()
        if len(data) < warm + 2:
            raise ValueError(f"Dati insufficienti: servono almeno {warm + 2} barre, ricevute {len(data)}.")
        p = self.params
        df = data.copy()

        df["rsi"] = rsi(df["close"], p.rsi_window)
        hist = macd_histogram(df["close"], p.macd_fast, p.macd_slow, p.macd_signal)
        scale = hist.rolling(p.norm_window, min_periods=p.norm_window).std()
        df["macd_z"] = hist / scale.where(scale > 0)
        df["sto"] = stochastic_k(df["high"], df["low"], df["close"], p.sto_k, p.sto_smooth)

        w = p.sigma
        r = {k: gauss(df["rsi"], c, _RSI_WIDTH * w) for k, c in zip("LMH", (p.rsi_low, p.rsi_med, p.rsi_high))}
        s = {k: gauss(df["sto"], c, _STO_WIDTH * w) for k, c in zip("LMH", (p.sto_low, p.sto_med, p.sto_high))}
        m = {k: gauss(df["macd_z"], c, _MACD_WIDTH * w) for k, c in zip("LMH", _MACD_CENTERS)}

        buy = np.maximum.reduce([
            np.minimum.reduce([m["H"], r["L"], s["L"]]),
            np.minimum.reduce([m["H"], r["M"], s["M"]]),
        ])
        sell = np.maximum.reduce([
            np.minimum.reduce([m["L"], r["M"], s["H"]]),
            np.minimum(r["H"], s["L"]),
            np.minimum.reduce([m["L"], r["H"], s["H"]]),
        ])
        valid = df[["rsi", "macd_z", "sto"]].notna().all(axis=1).to_numpy()
        buy = np.where(valid, buy, 0.0)
        sell = np.where(valid, sell, 0.0)
        df["buy_strength"] = buy
        df["sell_strength"] = sell

        # Stato: entra / esce solo oltre la soglia, altrimenti resta com'era.
        signal = np.zeros(len(df), dtype=int)
        state = 0
        for i in range(len(df)):
            if valid[i]:
                if state == 0 and buy[i] - sell[i] > p.threshold:
                    state = 1
                elif state == 1 and sell[i] - buy[i] > p.threshold:
                    state = 0
            signal[i] = state
        df["signal"] = signal
        df["position"] = df["signal"].diff().fillna(0).astype(int)
        return df
