"""
strategy/optimization.py
========================
Ricerca dei parametri di una strategia: griglia completa o algoritmo genetico.

Attenzione: entrambe le funzioni valutano i parametri su **tutti** i dati che
ricevono, quindi il risultato è "in campione". Per stimare come andrebbero su
dati nuovi usale dentro `strategy.research.walk_forward` (scelta sul training,
verifica fuori campione).

La modalità genetica riprende l'idea della tesi THESIS-fuzzylogic-stocks
(alessandroiva), che ottimizza le gaussiane con PyGAD e un obiettivo
0,7 × guadagno + 0,3 × Sharpe (`objective="thesis"`). Qui è scritta da zero
con numpy: torneo, incrocio uniforme, mutazione gaussiana entro i limiti,
elitismo, seme fisso per risultati ripetibili.
"""

from __future__ import annotations

import itertools
from dataclasses import fields, replace
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from strategy.quant_model import BaseStrategy, _reference_price, _returns_core, check_execution

_KEPT = ("total_return_pct", "cagr_pct", "sharpe_ratio", "max_drawdown_pct", "num_trades")


def _score(metrics: dict, objective: str) -> float:
    if objective == "sharpe":
        return float(metrics["sharpe_ratio"])
    if objective == "total_return":
        return float(metrics["total_return_pct"])
    if objective == "thesis":
        return 0.7 * metrics["total_return_pct"] / 100 + 0.3 * float(metrics["sharpe_ratio"])
    raise ValueError("objective deve essere 'sharpe', 'total_return' o 'thesis'.")


def _evaluate(strategy_cls, base, data, overrides, objective, bars_per_year, start=None) -> dict | None:
    try:
        params = replace(base, **overrides)
    except ValueError:
        return None                                  # combinazione non valida
    try:
        m = strategy_cls(params).backtest(data, bars_per_year=bars_per_year, start=start).metrics
    except ValueError:
        return None                                  # es. dati insufficienti per le finestre
    score = _score(m, objective)
    if not np.isfinite(score):
        return None
    return {"parameters": dict(overrides), "score": round(score, 6), "metrics": {k: m[k] for k in _KEPT}}


def grid_search(
    strategy_cls: type[BaseStrategy],
    data: pd.DataFrame,
    grid: Mapping[str, Sequence[Any]],
    base_params,
    objective: str = "sharpe",
    bars_per_year: int | None = None,
    start: str | pd.Timestamp | None = None,
) -> list[dict]:
    """
    Prova tutte le combinazioni valide di `grid`; restituisce i risultati dal migliore.

    Ogni backtest valuta le barre dopo il proprio riscaldamento: senza un
    `start` comune ogni combinazione è quindi valutata su un periodo diverso
    (una SMA lunga 200 parte 150 barre dopo una lunga 50) e i punteggi non sono
    del tutto confrontabili. Con `start` (una data) tutte partono dalla stessa
    barra, purché sia oltre il riscaldamento più lungo della griglia.
    """
    _score({"sharpe_ratio": 0.0, "total_return_pct": 0.0}, objective)   # valida l'obiettivo
    names = list(grid)
    out = []
    for values in itertools.product(*(grid[n] for n in names)):
        r = _evaluate(strategy_cls, base_params, data, dict(zip(names, values)), objective, bars_per_year, start)
        if r is not None:
            out.append(r)
    return sorted(out, key=lambda r: r["score"], reverse=True)


def genetic_search(
    strategy_cls: type[BaseStrategy],
    data: pd.DataFrame,
    space: Mapping[str, tuple[float, float]],
    base_params,
    population: int = 20,
    generations: int = 15,
    mutation_rate: float = 0.2,
    elite: int = 2,
    tournament: int = 3,
    seed: int = 0,
    objective: str = "sharpe",
    bars_per_year: int | None = None,
) -> dict:
    """
    Algoritmo genetico sui parametri di `space` ({nome: (minimo, massimo)}).

    Un parametro è intero se il campo di `base_params` è intero. Le combinazioni
    non valide hanno punteggio −∞ e vengono scartate dalla selezione. `history`
    è il miglior punteggio dopo ogni generazione (non scende, grazie all'elitismo).
    """
    _score({"sharpe_ratio": 0.0, "total_return_pct": 0.0}, objective)
    if population < 4 or generations < 1 or not 0 <= mutation_rate <= 1 or not 1 <= elite < population:
        raise ValueError("Parametri dell'algoritmo genetico non validi.")
    names = list(space)
    types = {f.name: f.type for f in fields(base_params)}
    is_int = np.array([isinstance(getattr(base_params, n), int) or types.get(n) in (int, "int") for n in names])
    lo = np.array([float(space[n][0]) for n in names])
    hi = np.array([float(space[n][1]) for n in names])
    if np.any(lo > hi):
        raise ValueError("Ogni intervallo deve avere minimo <= massimo.")
    rng = np.random.default_rng(seed)
    cache: dict[tuple, dict | None] = {}

    def decode(genes: np.ndarray) -> dict:
        vals = np.clip(genes, lo, hi)
        return {n: int(round(v)) if i else float(round(v, 6)) for n, v, i in zip(names, vals, is_int)}

    def fitness(genes: np.ndarray) -> tuple[float, dict | None]:
        params = decode(genes)
        key = tuple(params.values())
        if key not in cache:
            cache[key] = _evaluate(strategy_cls, base_params, data, params, objective, bars_per_year)
        r = cache[key]
        return (r["score"] if r else -np.inf), r

    pop = rng.uniform(lo, hi, size=(population, len(names)))
    scores = np.array([fitness(g)[0] for g in pop])
    history = []
    for _ in range(generations):
        order = np.argsort(-scores, kind="stable")
        children = [pop[i].copy() for i in order[:elite]]
        while len(children) < population:
            parents = []
            for _ in range(2):
                pick = rng.choice(population, size=tournament, replace=False)
                parents.append(pop[pick[np.argmax(scores[pick])]])
            mask = rng.random(len(names)) < 0.5
            child = np.where(mask, parents[0], parents[1])
            mutate = rng.random(len(names)) < mutation_rate
            child = child + mutate * rng.normal(0, 0.1 * (hi - lo))
            children.append(np.clip(child, lo, hi))
        pop = np.array(children)
        scores = np.array([fitness(g)[0] for g in pop])
        history.append(float(scores.max()))

    best_score, best = fitness(pop[int(np.argmax(scores))])
    if best is None:
        raise ValueError("Nessuna combinazione valida trovata nello spazio indicato.")
    return {"best": best, "history": history, "evaluations": len(cache), "objective": objective}


def sma_grid_vectorized(
    data: pd.DataFrame,
    shorts: Sequence[int],
    longs: Sequence[int],
    base_params,
    objective: str = "sharpe",
    bars_per_year: int | None = None,
    start: str | pd.Timestamp | None = None,
) -> list[dict]:
    """
    Stessa griglia di `grid_search(MovingAverageCrossover, …)`, calcolata in blocco.

    Le medie mobili si calcolano una volta per finestra (con pandas, come la
    strategia, per avere gli stessi confronti), poi segnali, posizioni, costi,
    equity e metriche di tutte le coppie short < long stanno in matrici numpy
    (coppie × barre). Stesse regole e stessa formula del backtest
    (`quant_model._returns_core`): posizione in t = segnale di t-1, eseguita
    all'open di t o al close di t-1 secondo `base_params.execution`, `fee_bps`
    sulla barra d'esecuzione e `slippage_bps` sul prezzo eseguito; i trade si contano sui cambi della posizione detenuta.

    Come nel backtest ogni coppia è valutata dalla barra `long − 1` (fine del
    riscaldamento): senza un `start` comune ogni coppia ha quindi un periodo
    diverso. Con `start` (una data) si parte dalla più tarda tra `start` e il
    riscaldamento della coppia.
    """
    from strategy.metrics import effective_bars_per_year

    _score({"sharpe_ratio": 0.0, "total_return_pct": 0.0}, objective)
    pairs = [(s, l) for s in shorts for l in longs if 1 <= s < l and l >= 2 and l <= len(data)]
    if not pairs:
        return []
    execution = check_execution(getattr(base_params, "execution", "next_open"))
    if execution == "next_open" and "open" not in data.columns:
        raise ValueError("Con execution='next_open' serve la colonna 'open'; senza open usa execution='close'.")
    close = data["close"].astype(float)
    n = len(close)
    windows = sorted({w for p in pairs for w in p})
    sma = {w: close.rolling(w, min_periods=w).mean().to_numpy() for w in windows}

    s_idx = np.array([p[0] for p in pairs])
    l_idx = np.array([p[1] for p in pairs])
    short_m = np.vstack([sma[w] for w in s_idx])
    long_m = np.vstack([sma[w] for w in l_idx])
    with np.errstate(invalid="ignore"):
        signal = ((short_m > long_m) & ~np.isnan(long_m)).astype(float)

    # Inizio della valutazione di ogni coppia: riscaldamento (long − 1) o `start`, il più tardo.
    first = 0 if start is None else int(close.index.searchsorted(pd.Timestamp(start)))
    if first >= n:
        raise ValueError(f"Nessuna barra da valutare: {n} barre, inizio richiesto {start!r}.")
    t0 = np.maximum(l_idx - 1, first)
    bar = np.arange(n)
    evaluated = bar[None, :] >= t0[:, None]
    bars = n - t0

    held = np.zeros_like(signal)
    held[:, 1:] = signal[:, :-1]
    held[bar[None, :] <= t0[:, None]] = 0.0                # senza posizione fino a t0 compresa
    close_np = close.to_numpy()
    open_np = data["open"].to_numpy(dtype=float) if "open" in data.columns else None
    ref, _ = _reference_price(close_np, open_np, execution)
    ret, turnover, _ = _returns_core(close_np, ref, held, base_params.fee_bps / 10_000,
                                     float(getattr(base_params, "slippage_bps", 0.0)) / 10_000)
    capital = float(base_params.initial_capital)
    equity = capital * np.cumprod(1 + ret, axis=1)         # piatta a `capital` prima di t0

    if bars_per_year:
        bpy = np.full(len(pairs), int(bars_per_year), dtype=float)
    else:                                                  # misurato sulle barre valutate di ogni coppia
        measured = {int(t): effective_bars_per_year(close.index[int(t):]) for t in np.unique(t0)}
        bpy = np.array([measured[int(t)] for t in t0], dtype=float)
    years = np.maximum(bars / bpy, 1e-9)
    final = equity[:, -1]
    total = final / capital - 1
    with np.errstate(divide="ignore", invalid="ignore"):
        cagr = np.where(final > 0, (final / capital) ** (1 / years) - 1, -1.0)
    # Media e deviazione standard solo sulle barre valutate.
    excess = ret - (base_params.risk_free_rate / bpy)[:, None]
    mean = (excess * evaluated).sum(axis=1) / bars
    std = np.sqrt((((excess - mean[:, None]) ** 2) * evaluated).sum(axis=1) / bars)
    with np.errstate(divide="ignore", invalid="ignore"):
        sharpe = np.where(std > 0, mean / std * np.sqrt(bpy), 0.0)
    max_dd = (equity / np.maximum.accumulate(equity, axis=1) - 1).min(axis=1)
    trades = (turnover != 0).sum(axis=1)

    out = []
    for i, (s, l) in enumerate(pairs):
        m = {
            "total_return_pct": round(float(total[i]) * 100, 2),
            "cagr_pct": round(float(cagr[i]) * 100, 2),
            "sharpe_ratio": round(float(sharpe[i]), 3),
            "max_drawdown_pct": round(float(max_dd[i]) * 100, 2),
            "num_trades": int(trades[i]),
        }
        score = _score(m, objective)
        if np.isfinite(score):
            out.append({"parameters": {"short_window": int(s), "long_window": int(l)},
                        "score": round(score, 6), "metrics": m})
    return sorted(out, key=lambda r: r["score"], reverse=True)
