"""
strategy/research.py
====================
Studi per validare una strategia senza ingannarsi:

* `cost_sensitivity`: rilancia lo stesso backtest con commissioni moltiplicate
  e slippage aggiuntivo (tabella «costi × slippage» per la dashboard), con le
  coppie di scenari tra cui il rendimento cambia segno (punto 10 del Piano).
* `walk_forward`: sceglie i parametri **solo** sul training, li congela e li
  prova fuori campione; un periodo finale (`holdout_bars`) resta fuori da ogni
  training e da ogni fold (punto 16 del Piano).

Ripreso dal Research Lab di Vibe-Trading (HKUDS), `agent/src/research_lab/
studies.py` (commit 597d902, 23/11/2026): stesse regole (finestre di test che
partono senza posizione e liquidano alla fine, scelta stabile a parità di
punteggio, finale riservato), riscritte sul backtest vettorizzato di QTS.

    Copyright (c) 2026 Vibe-Trading Contributors — MIT License
    https://github.com/HKUDS/Vibe-Trading/blob/main/LICENSE
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from strategy.quant_model import BaseStrategy, StrategyParams, compute_metrics

_OBJECTIVES = {"sharpe": "sharpe_ratio", "total_return": "total_return_pct"}


# ============================================================ Costi
def cost_sensitivity(
    strategy: BaseStrategy,
    data: pd.DataFrame,
    fee_multipliers: Sequence[float] = (1, 2, 3),
    slippage_bps: Sequence[float] = (0, 5),
    bars_per_year: int | None = None,
) -> dict:
    """
    Stesso backtest con costi diversi.

    Il costo per cambio di posizione diventa `fee_bps × moltiplicatore +
    slippage_bps`: nel modello di QTS lo slippage è un costo avverso in punti
    base sul turnover, come la commissione. Le posizioni non cambiano con i
    costi, quindi il numero di trade resta lo stesso in tutti gli scenari.
    """
    base = strategy.params
    rows = []
    for slip in slippage_bps:
        for mult in fee_multipliers:
            if mult < 0 or slip < 0:
                raise ValueError("Moltiplicatori e slippage devono essere >= 0.")
            cost = base.fee_bps * mult + slip
            variant = type(strategy)(replace(base, fee_bps=cost))
            m = variant.backtest(data, bars_per_year=bars_per_year).metrics
            rows.append({
                "name": f"fee×{mult:g} + {slip:g} bps",
                "fee_multiplier": mult,
                "slippage_bps": slip,
                "effective_cost_bps": round(cost, 4),
                **{k: m[k] for k in ("total_return_pct", "cagr_pct", "sharpe_ratio", "max_drawdown_pct",
                                     "num_trades", "buy_and_hold_return_pct", "bars_per_year")},
            })

    # Solo cambi di segno osservati tra scenari vicini, mai un pareggio interpolato.
    brackets = []
    for slip in sorted({r["slippage_bps"] for r in rows}):
        ordered = sorted((r for r in rows if r["slippage_bps"] == slip), key=lambda r: r["fee_multiplier"])
        for left, right in zip(ordered, ordered[1:]):
            a, b = left["total_return_pct"], right["total_return_pct"]
            if a * b <= 0 and a != b:
                brackets.append({"slippage_bps": slip,
                                 "fee_multiplier_interval": [left["fee_multiplier"], right["fee_multiplier"]]})
    return {
        "rows": rows,
        "break_even_brackets": brackets,
        "break_even_method": "observed_sign_change_only",
        "ranking": [r["name"] for r in sorted(rows, key=lambda r: r["total_return_pct"], reverse=True)],
    }


# ======================================================= Finestre
@dataclass
class WindowResult:
    returns: pd.Series        # rendimento della strategia per barra della finestra
    positions: pd.Series      # posizione detenuta per barra (0 alla prima barra)
    turnover: pd.Series       # cambi di posizione, compresa la liquidazione finale
    close: pd.Series
    asset_returns: pd.Series  # rendimento dell'asset per barra (0 alla prima)


def window_returns(strategy: BaseStrategy, data: pd.DataFrame, start: int, stop: int) -> WindowResult:
    """
    Rendimenti della strategia sulle barre `[start, stop)`.

    Gli indicatori usano lo storico dall'inizio dei dati (riscaldamento) ma
    **nessuna** barra da `stop` in poi. La finestra parte senza posizione e
    chiude la posizione sull'ultima barra, pagando i costi di entrambe le
    operazioni (come i fold di test di Vibe-Trading). Resta la regola di QTS:
    posizione in t = segnale di t-1.
    """
    if not 0 <= start < stop <= len(data):
        raise ValueError(f"Finestra non valida: [{start}, {stop}) su {len(data)} barre.")
    sig = strategy.generate_signals(data.iloc[:stop])
    held = sig["signal"].shift(1).fillna(0.0).astype(float).iloc[start:stop].copy()
    held.iloc[0] = 0.0
    close = sig["close"].iloc[start:stop]
    asset_ret = close.pct_change().fillna(0.0)
    turnover = held.diff().abs().fillna(0.0)
    turnover.iloc[-1] += abs(held.iloc[-1])           # liquidazione a fine finestra
    fee = strategy.params.fee_bps / 10_000
    returns = held * asset_ret - turnover * fee
    return WindowResult(returns=returns, positions=held, turnover=turnover, close=close, asset_returns=asset_ret)


def _window_trades(w: WindowResult) -> pd.DataFrame:
    """
    Trade della finestra con le stesse convenzioni del backtest principale.

    La posizione detenuta cambia sulla barra dopo il segnale, quindi il prezzo
    è il close della barra del segnale (barra precedente). La liquidazione di
    fine finestra avviene al close dell'ultima barra.
    """
    change = w.positions.diff().fillna(w.positions)
    price = w.close.shift(1)
    rows = [(ts, "BUY" if c > 0 else "SELL", price.loc[ts]) for ts, c in change[change != 0].items()]
    if w.positions.iloc[-1] > 0:
        rows.append((w.close.index[-1], "SELL", w.close.iloc[-1]))
    idx = pd.DatetimeIndex([r[0] for r in rows], name=w.close.index.name)
    return pd.DataFrame({"side": [r[1] for r in rows], "price": [r[2] for r in rows]}, index=idx)


def _window_metrics(strategy: BaseStrategy, data: pd.DataFrame, start: int, stop: int,
                    bars_per_year: int | None) -> tuple[dict, pd.Series]:
    w = window_returns(strategy, data, start, stop)
    p = strategy.params
    equity = p.initial_capital * (1 + w.returns).cumprod()
    m = compute_metrics(
        strategy_returns=w.returns, asset_returns=w.asset_returns, equity=equity,
        trades=_window_trades(w), initial_capital=p.initial_capital, risk_free_rate=p.risk_free_rate,
        bars_per_year=bars_per_year,
    )
    return m, w


# ===================================================== Walk-forward
def walk_forward(
    strategy_cls: type[BaseStrategy],
    data: pd.DataFrame,
    candidates: Iterable[dict],
    train_bars: int,
    test_bars: int,
    holdout_bars: int = 0,
    max_folds: int = 5,
    objective: str = "sharpe",
    training: str = "rolling",
    base_params: StrategyParams | None = None,
    bars_per_year: int | None = None,
) -> dict:
    """
    Scelta dei parametri sul training, poi test fuori campione con parametri congelati.

    `candidates` è un elenco di modifiche a `base_params` (es. `{"short_window": 10,
    "long_window": 50}`). Per ogni fold vince il candidato con il punteggio migliore
    (`objective`) sul training; a parità vince il primo dell'elenco. Il periodo di
    test non partecipa mai alla scelta. Con `holdout_bars > 0` le ultime barre sono
    il periodo finale: nessun fold le usa, e lo si valuta una sola volta alla fine
    con parametri scelti sulle barre che lo precedono.
    """
    if objective not in _OBJECTIVES:
        raise ValueError(f"objective deve essere uno di {sorted(_OBJECTIVES)}")
    if training not in {"rolling", "expanding"}:
        raise ValueError("training deve essere 'rolling' o 'expanding'")
    if min(train_bars, test_bars) <= 0 or holdout_bars < 0 or max_folds < 1:
        raise ValueError("train_bars e test_bars devono essere > 0, holdout_bars >= 0, max_folds >= 1.")
    candidates = [dict(c) for c in candidates]
    if not candidates:
        raise ValueError("Serve almeno un candidato.")
    base = base_params or StrategyParams()
    for c in candidates:                     # parametri non validi: errore subito
        replace(base, **c)

    n = len(data)
    limit = n - holdout_bars
    if limit < train_bars + test_bars:
        raise ValueError("Troppe poche barre per training, test e periodo finale riservato.")
    boundaries = list(range(train_bars, limit - test_bars + 1, test_bars))[:max_folds]
    key = _OBJECTIVES[objective]
    idx = data.index

    def fit_and_test(boundary: int, end: int) -> tuple[dict, WindowResult]:
        start = 0 if training == "expanding" else boundary - train_bars
        results = []
        for c in candidates:
            m, _ = _window_metrics(strategy_cls(replace(base, **c)), data, start, boundary, bars_per_year)
            score = m[key]
            if score is None or not np.isfinite(score):
                raise ValueError(f"Il candidato {c} non ha un punteggio finito sul training.")
            results.append({"parameters": c, "score": score,
                            "metrics": {k: m[k] for k in ("total_return_pct", "sharpe_ratio",
                                                          "max_drawdown_pct", "num_trades")}})
        chosen = max(results, key=lambda r: r["score"])["parameters"]  # il primo vince a parità
        test_m, test_w = _window_metrics(strategy_cls(replace(base, **chosen)), data, boundary, end, bars_per_year)
        return {
            "training_start": str(idx[start]), "training_end": str(idx[boundary - 1]),
            "test_start": str(idx[boundary]), "test_end": str(idx[end - 1]),
            "training": results, "chosen": chosen,
            "test": {k: test_m[k] for k in ("total_return_pct", "cagr_pct", "sharpe_ratio",
                                            "max_drawdown_pct", "num_trades", "buy_and_hold_return_pct")},
        }, test_w

    folds, oos, oos_asset = [], [], []
    for number, boundary in enumerate(boundaries, 1):
        fold, w = fit_and_test(boundary, boundary + test_bars)
        folds.append({"fold": number, **fold})
        oos.append(w.returns)
        oos_asset.append(w.asset_returns)
    holdout = fit_and_test(limit, n)[0] if holdout_bars else None

    # Buy & Hold sulle stesse finestre di test, concatenate come la strategia.
    combined = pd.concat(oos)
    equity = base.initial_capital * (1 + combined).cumprod()
    oos_m = compute_metrics(combined, pd.concat(oos_asset), equity, pd.DataFrame(columns=["side", "price"]),
                            base.initial_capital, base.risk_free_rate, bars_per_year=bars_per_year)
    return {
        "mode": "locked" if holdout_bars else "strict",
        "objective": objective,
        "training": training,
        "base_params": asdict(base),
        "folds": folds,
        "oos": {k: oos_m[k] for k in ("bars", "total_return_pct", "cagr_pct", "sharpe_ratio", "max_drawdown_pct",
                                      "buy_and_hold_return_pct")},
        "holdout": holdout,
    }
