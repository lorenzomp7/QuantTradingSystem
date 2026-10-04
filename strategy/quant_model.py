"""
strategy/quant_model.py
=======================
Logica matematica e di trading.

Strategia di esempio: **Moving Average Crossover** (long-only).

    SMA_short(t) = media dei close sugli ultimi `short_window` periodi
    SMA_long(t)  = media dei close sugli ultimi `long_window` periodi

    signal(t) = 1  se SMA_short(t) > SMA_long(t)   (trend rialzista -> long)
              = 0  altrimenti                       (flat / in cash)

    Golden cross: signal passa da 0 a 1  -> BUY
    Death cross : signal passa da 1 a 0  -> SELL

Il backtest è vettorizzato con Pandas/NumPy ed evita il *look-ahead bias*:
il segnale calcolato sulla chiusura del giorno t viene eseguito sulla barra
t+1 (posizione = signal.shift(1)). I costi di transazione sono modellati in
basis point su ogni variazione di posizione.

Per aggiungere nuove strategie basta estendere `BaseStrategy` e
implementare `generate_signals()`: backtest e metriche sono riutilizzati.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252


# ============================================================== Parametri
@dataclass
class StrategyParams:
    short_window: int = 20
    long_window: int = 50
    initial_capital: float = 10_000.0
    fee_bps: float = 5.0            # costo per trade in basis point (5 bps = 0.05%)
    risk_free_rate: float = 0.0     # tasso risk-free annuo per lo Sharpe ratio

    def __post_init__(self) -> None:
        if self.short_window < 1 or self.long_window < 2:
            raise ValueError("Le finestre delle medie mobili devono essere positive.")
        if self.short_window >= self.long_window:
            raise ValueError("short_window deve essere strettamente minore di long_window.")
        if self.initial_capital <= 0:
            raise ValueError("initial_capital deve essere > 0.")
        if self.fee_bps < 0:
            raise ValueError("fee_bps non può essere negativo.")


@dataclass
class BacktestResult:
    metrics: dict
    equity_curve: pd.Series = field(repr=False)
    trades: pd.DataFrame = field(repr=False)
    signals: pd.DataFrame = field(repr=False)

    def to_dict(self, tail: int | None = None) -> dict:
        """
        Serializzazione JSON-friendly (per le API).

        Ogni punto della curva include anche il benchmark Buy & Hold e il flag
        `in_market` (posizione detenuta sulla barra): quando è 0 la strategia è
        in liquidità e l'equity resta piatta, cosa che la dashboard evidenzia.
        """
        eq = self.equity_curve if tail is None else self.equity_curve.tail(tail)
        close = self.signals["close"]
        benchmark = self.metrics["initial_capital"] * close / close.iloc[0]
        in_market = self.signals["in_market"]
        return {
            "metrics": self.metrics,
            "equity_curve": [
                {
                    "date": d.strftime("%Y-%m-%d"),
                    "equity": round(float(v), 2),
                    "benchmark": round(float(benchmark.loc[d]), 2),
                    "in_market": int(in_market.loc[d]),
                }
                for d, v in eq.items()
            ],
            "trades": [
                {
                    "date": d.strftime("%Y-%m-%d"),
                    "side": row["side"],
                    "price": round(float(row["price"]), 4),
                }
                for d, row in self.trades.iterrows()
            ],
        }


# ============================================================== Strategie
class BaseStrategy(ABC):
    """Interfaccia comune a tutte le strategie."""

    name: str = "base"

    def __init__(self, params: StrategyParams | None = None) -> None:
        self.params = params or StrategyParams()

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Deve restituire una copia di `data` con almeno le colonne:
            signal   -> posizione desiderata (0 = flat, 1 = long)
            position -> variazione di signal (+1 = BUY, -1 = SELL, 0 = hold)
        """

    # -------------------------------------------------------- Live signal
    def latest_signal(self, data: pd.DataFrame) -> dict:
        """Ultimo segnale disponibile, usato dal bot live a ogni ciclo."""
        sig = self.generate_signals(data)
        last = sig.iloc[-1]
        action = {1: "BUY", -1: "SELL"}.get(int(last["position"]), "HOLD")
        out = {
            "timestamp": sig.index[-1].isoformat(),
            "close": round(float(last["close"]), 4),
            "signal": int(last["signal"]),
            "action": action,
        }
        for col in ("sma_short", "sma_long"):
            if col in sig.columns and pd.notna(last[col]):
                out[col] = round(float(last[col]), 4)
        return out

    # ----------------------------------------------------------- Backtest
    def backtest(self, data: pd.DataFrame) -> BacktestResult:
        p = self.params
        sig = self.generate_signals(data)

        # Rendimenti semplici dell'asset.
        asset_ret = sig["close"].pct_change().fillna(0.0)

        # Posizione effettivamente detenuta: segnale di ieri (no look-ahead).
        held = sig["signal"].shift(1).fillna(0.0)
        sig["in_market"] = held.astype(int)

        # Costi: fee_bps applicati ad ogni cambio di posizione (turnover).
        turnover = held.diff().abs().fillna(held.abs())
        costs = turnover * (p.fee_bps / 10_000)

        strat_ret = held * asset_ret - costs
        equity = p.initial_capital * (1 + strat_ret).cumprod()
        equity.name = "equity"

        trades = sig.loc[sig["position"] != 0, ["close", "position"]].copy()
        trades["side"] = np.where(trades["position"] > 0, "BUY", "SELL")
        trades = trades.rename(columns={"close": "price"})[["side", "price"]]

        metrics = compute_metrics(
            strategy_returns=strat_ret,
            asset_returns=asset_ret,
            equity=equity,
            trades=trades,
            initial_capital=p.initial_capital,
            risk_free_rate=p.risk_free_rate,
        )
        metrics["strategy"] = self.name
        metrics["params"] = asdict(p)
        return BacktestResult(metrics=metrics, equity_curve=equity, trades=trades, signals=sig)


class MovingAverageCrossover(BaseStrategy):
    """Strategia trend-following basata sull'incrocio di due SMA."""

    name = "moving_average_crossover"

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        if "close" not in data.columns:
            raise ValueError("Il DataFrame deve contenere la colonna 'close'.")
        if len(data) < self.params.long_window:
            raise ValueError(
                f"Dati insufficienti: servono almeno {self.params.long_window} barre, "
                f"ricevute {len(data)}."
            )

        df = data.copy()
        df["sma_short"] = df["close"].rolling(self.params.short_window, min_periods=self.params.short_window).mean()
        df["sma_long"] = df["close"].rolling(self.params.long_window, min_periods=self.params.long_window).mean()

        # Segnale valido solo quando entrambe le medie sono definite.
        valid = df["sma_long"].notna()
        df["signal"] = np.where(valid & (df["sma_short"] > df["sma_long"]), 1, 0)
        df["position"] = df["signal"].diff().fillna(0).astype(int)
        return df


# ================================================================ Metriche
def compute_metrics(
    strategy_returns: pd.Series,
    asset_returns: pd.Series,
    equity: pd.Series,
    trades: pd.DataFrame,
    initial_capital: float,
    risk_free_rate: float = 0.0,
) -> dict:
    """Metriche di performance standard (annualizzate su 252 giorni)."""
    n = len(strategy_returns)
    years = max(n / TRADING_DAYS_PER_YEAR, 1e-9)

    final_equity = float(equity.iloc[-1]) if n else initial_capital
    total_return = final_equity / initial_capital - 1
    cagr = (final_equity / initial_capital) ** (1 / years) - 1 if final_equity > 0 else -1.0

    vol = float(strategy_returns.std(ddof=0) * np.sqrt(TRADING_DAYS_PER_YEAR))
    excess = strategy_returns - risk_free_rate / TRADING_DAYS_PER_YEAR
    std = excess.std(ddof=0)
    sharpe = float(excess.mean() / std * np.sqrt(TRADING_DAYS_PER_YEAR)) if std > 0 else 0.0

    downside = excess[excess < 0].std(ddof=0)
    sortino = (
        float(excess.mean() / downside * np.sqrt(TRADING_DAYS_PER_YEAR))
        if downside and downside > 0 else 0.0
    )

    running_max = equity.cummax()
    drawdown = equity / running_max - 1
    max_dd = float(drawdown.min()) if n else 0.0

    buy_hold = float((1 + asset_returns).prod() - 1)

    # Win rate calcolato sui round-trip completi (BUY -> SELL).
    wins, round_trips, entry = 0, 0, None
    for _, row in trades.iterrows():
        if row["side"] == "BUY":
            entry = row["price"]
        elif entry is not None:
            round_trips += 1
            wins += int(row["price"] > entry)
            entry = None

    return {
        "start": strategy_returns.index[0].strftime("%Y-%m-%d") if n else None,
        "end": strategy_returns.index[-1].strftime("%Y-%m-%d") if n else None,
        "bars": n,
        "initial_capital": round(initial_capital, 2),
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "annual_volatility_pct": round(vol * 100, 2),
        "sharpe_ratio": round(sharpe, 3),
        "sortino_ratio": round(sortino, 3),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "buy_and_hold_return_pct": round(buy_hold * 100, 2),
        "num_trades": int(len(trades)),
        "round_trips": round_trips,
        "win_rate_pct": round(wins / round_trips * 100, 2) if round_trips else None,
    }
