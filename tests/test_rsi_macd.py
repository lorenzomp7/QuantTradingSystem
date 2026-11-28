"""Strategia RSI/MACD/stocastico con regole fuzzy e ottimizzazione (integrazione QTS 9)."""

import numpy as np
import pandas as pd
import pytest

from strategy.indicators import macd_histogram, rsi, stochastic_k
from strategy.optimization import genetic_search, grid_search
from strategy.quant_model import MovingAverageCrossover, StrategyParams
from strategy.rsi_macd import RsiMacdFuzzy, RsiMacdParams, gauss
from utils.data_fetcher import generate_mock_ohlcv


@pytest.fixture
def data() -> pd.DataFrame:
    return generate_mock_ohlcv("FUZZY", period="2y")


def _frame(close) -> pd.DataFrame:
    close = pd.Series(close, index=pd.date_range("2024-01-01", periods=len(close), freq="B"), dtype=float)
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1.0})


# ------------------------------------------------------------ Indicatori
def test_rsi_by_hand():
    close = pd.Series([1.0, 2.0, 1.0, 2.0, 3.0])
    # Wilder (α = 1/n), n = 2: guadagni 1,0,1,1; perdite 0,1,0,0.
    r = rsi(close, 2)
    gain = [np.nan, 1.0]
    loss = [np.nan, 0.0]
    for g, l in [(0.0, 1.0), (1.0, 0.0), (1.0, 0.0)]:
        gain.append(gain[-1] * 0.5 + g * 0.5)
        loss.append(loss[-1] * 0.5 + l * 0.5)
    expected = 100 - 100 / (1 + np.array(gain[2:]) / np.array(loss[2:]))
    np.testing.assert_allclose(r.iloc[2:], expected)
    assert rsi(pd.Series(np.arange(1.0, 30.0)), 14).dropna().eq(100).all()   # solo rialzi


def test_macd_histogram_definition():
    close = pd.Series(np.linspace(100, 120, 80) + np.sin(np.arange(80)))
    h = macd_histogram(close, 12, 26, 9)
    ema = lambda s, n: s.ewm(span=n, adjust=False).mean()
    line = ema(close, 12) - ema(close, 26)
    np.testing.assert_allclose(h.dropna(), (line - ema(line, 9)).iloc[25:])


def test_stochastic_k_bounds_and_definition():
    d = _frame(np.r_[np.linspace(10, 20, 20), np.linspace(20, 15, 10)])
    k = stochastic_k(d["high"], d["low"], d["close"], 14, 3)
    assert k.dropna().between(0, 100).all()
    raw = 100 * (d["close"] - d["low"].rolling(14).min()) / (d["high"].rolling(14).max() - d["low"].rolling(14).min())
    np.testing.assert_allclose(k.dropna(), raw.rolling(3).mean().dropna())


def test_gauss_membership():
    assert gauss(50, 50, 10) == pytest.approx(1.0)
    assert gauss(60, 50, 10) == pytest.approx(np.exp(-0.5))


# -------------------------------------------------------------- Strategia
def test_signals_follow_rules_by_construction(data):
    strat = RsiMacdFuzzy(RsiMacdParams())
    sig = strat.generate_signals(data)
    assert {"rsi", "macd_z", "sto", "buy_strength", "sell_strength", "signal", "position"} <= set(sig.columns)
    assert set(sig["signal"].unique()) <= {0, 1}
    assert set(sig["position"].unique()) <= {-1, 0, 1}
    th = strat.params.threshold
    entries = sig.index[sig["position"] == 1]
    exits = sig.index[sig["position"] == -1]
    assert len(entries) > 0 and len(exits) > 0
    # Si entra solo quando Buy supera Sell oltre la soglia, si esce solo nel caso opposto.
    assert ((sig.loc[entries, "buy_strength"] - sig.loc[entries, "sell_strength"]) > th).all()
    assert ((sig.loc[exits, "sell_strength"] - sig.loc[exits, "buy_strength"]) > th).all()
    # Nessun segnale prima che tutti gli indicatori siano definiti.
    warm = strat.warmup_bars()
    assert (sig["signal"].iloc[:warm] == 0).all()


def test_no_lookahead_rising_series():
    close = np.r_[np.linspace(100, 80, 60), np.linspace(80, 140, 120)]
    d = _frame(close)
    res = RsiMacdFuzzy(RsiMacdParams(fee_bps=0)).backtest(d, bars_per_year=252)
    longs = res.signals.index[res.signals["signal"] == 1]
    if len(longs):
        first = longs[0]
        i = res.signals.index.get_loc(first)
        assert res.equity_curve.iloc[i] == pytest.approx(res.equity_curve.iloc[i - 1])


def test_signal_does_not_change_when_future_changes(data):
    strat = RsiMacdFuzzy(RsiMacdParams())
    a = strat.generate_signals(data)["signal"].iloc[:300]
    changed = data.copy()
    changed.iloc[300:, :] *= 0.5
    b = strat.generate_signals(changed)["signal"].iloc[:300]
    pd.testing.assert_series_equal(a, b)


def test_fees_reduce_return(data):
    r0 = RsiMacdFuzzy(RsiMacdParams(fee_bps=0)).backtest(data).metrics["total_return_pct"]
    r50 = RsiMacdFuzzy(RsiMacdParams(fee_bps=50)).backtest(data).metrics["total_return_pct"]
    assert r50 < r0


@pytest.mark.parametrize("bad", [
    dict(rsi_window=1), dict(macd_fast=26, macd_slow=12), dict(threshold=-0.1), dict(threshold=1.0),
    dict(sto_k=1), dict(fee_bps=-1), dict(rsi_low=60, rsi_med=50), dict(sigma=0),
])
def test_invalid_params(bad):
    with pytest.raises(ValueError):
        RsiMacdParams(**bad)


def test_insufficient_data(data):
    with pytest.raises(ValueError):
        RsiMacdFuzzy(RsiMacdParams()).generate_signals(data.head(30))
    with pytest.raises(ValueError):
        RsiMacdFuzzy(RsiMacdParams()).generate_signals(data.drop(columns=["high"]))


def test_latest_signal_and_name(data):
    out = RsiMacdFuzzy().latest_signal(data)
    assert out["action"] in {"BUY", "SELL", "HOLD"}
    assert RsiMacdFuzzy.name != MovingAverageCrossover.name


# ---------------------------------------------------------- Ottimizzazione
def test_grid_search_ranks_by_objective(data):
    out = grid_search(MovingAverageCrossover, data, {"short_window": [5, 10, 20], "long_window": [30, 60]},
                      base_params=StrategyParams(fee_bps=5), bars_per_year=252)
    scores = [r["score"] for r in out]
    assert scores == sorted(scores, reverse=True) and len(out) == 6


def test_grid_search_skips_invalid_combinations(data):
    out = grid_search(MovingAverageCrossover, data, {"short_window": [10, 40], "long_window": [20, 50]},
                      base_params=StrategyParams(), bars_per_year=252)
    assert {(r["parameters"]["short_window"], r["parameters"]["long_window"]) for r in out} == {(10, 20), (10, 50), (40, 50)}


def test_genetic_search_close_to_grid_optimum(data):
    space = {"short_window": (3, 30), "long_window": (31, 120)}
    grid = grid_search(MovingAverageCrossover, data,
                       {"short_window": list(range(3, 31, 3)), "long_window": list(range(31, 121, 10))},
                       base_params=StrategyParams(fee_bps=5), bars_per_year=252)
    ga = genetic_search(MovingAverageCrossover, data, space, base_params=StrategyParams(fee_bps=5),
                        population=24, generations=15, seed=7, bars_per_year=252)
    best_grid = grid[0]["score"]
    # Tolleranza relativa con un minimo assoluto: con uno Sharpe migliore vicino a zero
    # il 15% relativo sarebbe una soglia di pochi millesimi.
    assert ga["best"]["score"] >= best_grid - max(0.15 * abs(best_grid), 0.15)
    s, l = ga["best"]["parameters"]["short_window"], ga["best"]["parameters"]["long_window"]
    assert 3 <= s <= 30 and 31 <= l <= 120 and isinstance(s, int)
    assert len(ga["history"]) == 15
    assert ga["history"] == sorted(ga["history"])            # elitismo: il migliore non peggiora


def test_genetic_search_is_reproducible(data):
    kw = dict(base_params=StrategyParams(), population=10, generations=4, bars_per_year=252)
    space = {"short_window": (3, 20), "long_window": (25, 80)}
    a = genetic_search(MovingAverageCrossover, data, space, seed=1, **kw)
    b = genetic_search(MovingAverageCrossover, data, space, seed=1, **kw)
    assert a["best"] == b["best"]


def test_genetic_search_on_fuzzy_strategy(data):
    space = {"threshold": (0.0, 0.5), "rsi_low": (20.0, 40.0), "sto_low": (10.0, 30.0)}
    out = genetic_search(RsiMacdFuzzy, data, space, base_params=RsiMacdParams(), population=8,
                         generations=3, seed=3, objective="thesis", bars_per_year=252)
    p = out["best"]["parameters"]
    assert 0.0 <= p["threshold"] <= 0.5 and 20 <= p["rsi_low"] <= 40
    assert np.isfinite(out["best"]["score"])


# ----------------------------------------------- Griglia SMA vettorizzata
@pytest.mark.parametrize("bpy", [252, None])
def test_vectorized_sma_grid_matches_loop(data, bpy):
    from strategy.optimization import sma_grid_vectorized

    shorts, longs = list(range(3, 31, 3)), list(range(10, 121, 10))
    base = StrategyParams(fee_bps=7, initial_capital=25_000)
    loop = grid_search(MovingAverageCrossover, data, {"short_window": shorts, "long_window": longs},
                       base_params=base, bars_per_year=bpy)
    vec = sma_grid_vectorized(data, shorts, longs, base_params=base, bars_per_year=bpy)
    key = lambda r: (r["parameters"]["short_window"], r["parameters"]["long_window"])  # noqa: E731
    assert sorted(map(key, loop)) == sorted(map(key, vec))
    by_loop = {key(r): r for r in loop}
    for r in vec:
        expected = by_loop[key(r)]
        assert r["score"] == pytest.approx(expected["score"], abs=1e-3)
        for k, v in expected["metrics"].items():
            assert r["metrics"][k] == pytest.approx(v, abs=0.011), (key(r), k)
    assert key(vec[0]) == key(loop[0])


def test_vectorized_sma_grid_objectives(data):
    from strategy.optimization import sma_grid_vectorized

    out = sma_grid_vectorized(data, [5, 10], [20, 40], base_params=StrategyParams(), objective="total_return",
                              bars_per_year=252)
    assert [r["score"] for r in out] == sorted((r["metrics"]["total_return_pct"] for r in out), reverse=True)
    with pytest.raises(ValueError):
        sma_grid_vectorized(data, [5], [20], base_params=StrategyParams(), objective="nope")
