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

from strategy.quant_model import BaseStrategy

_KEPT = ("total_return_pct", "cagr_pct", "sharpe_ratio", "max_drawdown_pct", "num_trades")


def _score(metrics: dict, objective: str) -> float:
    if objective == "sharpe":
        return float(metrics["sharpe_ratio"])
    if objective == "total_return":
        return float(metrics["total_return_pct"])
    if objective == "thesis":
        return 0.7 * metrics["total_return_pct"] / 100 + 0.3 * float(metrics["sharpe_ratio"])
    raise ValueError("objective deve essere 'sharpe', 'total_return' o 'thesis'.")


def _evaluate(strategy_cls, base, data, overrides, objective, bars_per_year) -> dict | None:
    try:
        params = replace(base, **overrides)
    except ValueError:
        return None                                  # combinazione non valida
    try:
        m = strategy_cls(params).backtest(data, bars_per_year=bars_per_year).metrics
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
) -> list[dict]:
    """Prova tutte le combinazioni valide di `grid`; restituisce i risultati dal migliore."""
    _score({"sharpe_ratio": 0.0, "total_return_pct": 0.0}, objective)   # valida l'obiettivo
    names = list(grid)
    out = []
    for values in itertools.product(*(grid[n] for n in names)):
        r = _evaluate(strategy_cls, base_params, data, dict(zip(names, values)), objective, bars_per_year)
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
    if population < 4 or generations < 1 or not 0 <= mutation_rate <= 1 or not 0 <= elite < population:
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
