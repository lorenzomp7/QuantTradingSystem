"""Studi di ricerca: costi × slippage e walk-forward (integrazione QTS 4)."""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from strategy.quant_model import MovingAverageCrossover, StrategyParams
from strategy.research import cost_sensitivity, walk_forward, window_returns
from utils.data_fetcher import generate_mock_ohlcv


@pytest.fixture
def data() -> pd.DataFrame:
    return generate_mock_ohlcv("RESEARCH", period="5y")


CANDIDATES = [{"short_window": 5, "long_window": 20}, {"short_window": 10, "long_window": 50},
              {"short_window": 20, "long_window": 100}]


# --------------------------------------------------------- Costi × slippage
def test_cost_sensitivity_rows_and_order(data):
    strat = MovingAverageCrossover(StrategyParams(fee_bps=5))
    out = cost_sensitivity(strat, data, fee_multipliers=(1, 2, 3), slippage_bps=(0, 5), bars_per_year=252)
    rows = out["rows"]
    assert len(rows) == 6
    assert {(r["fee_multiplier"], r["slippage_bps"]) for r in rows} == {(m, s) for m in (1, 2, 3) for s in (0, 5)}
    by = {(r["fee_multiplier"], r["slippage_bps"]): r for r in rows}
    # Più costi, rendimento più basso a parità di trade (le posizioni non cambiano).
    assert by[(1, 0)]["total_return_pct"] > by[(2, 0)]["total_return_pct"] > by[(3, 0)]["total_return_pct"]
    assert by[(1, 0)]["total_return_pct"] > by[(1, 5)]["total_return_pct"]
    assert len({r["num_trades"] for r in rows}) == 1
    # Il caso base coincide con il backtest normale.
    base = strat.backtest(data, bars_per_year=252).metrics
    assert by[(1, 0)]["total_return_pct"] == base["total_return_pct"]
    assert by[(1, 0)]["effective_cost_bps"] == 5 and by[(3, 5)]["effective_cost_bps"] == 20
    assert out["ranking"][0] == "fee×1 + 0 bps"


def test_cost_sensitivity_break_even_bracket():
    # Serie con piccolo guadagno: a costi alti il rendimento cambia segno.
    idx = pd.date_range("2024-01-01", periods=300, freq="B")
    close = pd.Series(100 + np.sin(np.arange(300) / 6) * 3 + np.arange(300) * 0.02, index=idx)
    df = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0})
    strat = MovingAverageCrossover(StrategyParams(short_window=3, long_window=8, fee_bps=5))
    out = cost_sensitivity(strat, df, fee_multipliers=(0, 1, 5, 20, 60), slippage_bps=(0,), bars_per_year=252)
    returns = [r["total_return_pct"] for r in sorted(out["rows"], key=lambda r: r["fee_multiplier"])]
    assert returns[0] > 0 > returns[-1]
    assert out["break_even_brackets"] == [{"slippage_bps": 0, "fee_multiplier_interval": [20, 60]}]


# -------------------------------------------------------------- Finestre
def test_window_starts_flat_and_liquidates(data):
    strat = MovingAverageCrossover(StrategyParams(short_window=5, long_window=20, fee_bps=10))
    r = window_returns(strat, data, 300, 400)
    assert len(r.returns) == 100 and r.returns.index[0] == data.index[300]
    assert r.positions.iloc[0] == 0          # parte senza posizione
    assert r.turnover.iloc[-1] == r.positions.iloc[-1]  # liquida all'ultima barra
    assert (r.turnover >= 0).all()


def test_window_uses_no_data_after_its_end(data):
    strat = MovingAverageCrossover(StrategyParams(short_window=5, long_window=20))
    a = window_returns(strat, data, 300, 400).returns
    changed = data.copy()
    changed.iloc[400:, :] *= 3.0             # dati dopo la finestra: non devono contare
    b = window_returns(strat, changed, 300, 400).returns
    pd.testing.assert_series_equal(a, b)


# ---------------------------------------------------------- Walk-forward
def test_walk_forward_structure(data):
    out = walk_forward(MovingAverageCrossover, data, CANDIDATES, train_bars=400, test_bars=150,
                       holdout_bars=150, max_folds=3, base_params=StrategyParams(fee_bps=5), bars_per_year=252)
    assert len(out["folds"]) == 3
    holdout_start = pd.Timestamp(out["holdout"]["test_start"])
    for fold in out["folds"]:
        assert pd.Timestamp(fold["training_end"]) < pd.Timestamp(fold["test_start"])
        assert pd.Timestamp(fold["test_end"]) < holdout_start   # il finale non entra mai nei fold
        assert fold["chosen"] in [c for c in CANDIDATES]
        assert fold["chosen"] == max(fold["training"], key=lambda t: t["score"])["parameters"]
    assert pd.Timestamp(out["holdout"]["training_end"]) < holdout_start
    assert out["oos"]["bars"] == 3 * 150
    assert out["mode"] == "locked"


def test_walk_forward_selection_ignores_test_data(data):
    kwargs = dict(train_bars=400, test_bars=150, holdout_bars=0, max_folds=1,
                  base_params=StrategyParams(fee_bps=5), bars_per_year=252)
    a = walk_forward(MovingAverageCrossover, data, CANDIDATES, **kwargs)
    changed = data.copy()
    changed.iloc[400:, :] = changed.iloc[400:, :].values[::-1]  # test diverso
    b = walk_forward(MovingAverageCrossover, changed, CANDIDATES, **kwargs)
    assert a["folds"][0]["chosen"] == b["folds"][0]["chosen"]
    assert a["folds"][0]["training"] == b["folds"][0]["training"]
    assert a["folds"][0]["test"] != b["folds"][0]["test"]


def test_walk_forward_rejects_short_data(data):
    with pytest.raises(ValueError):
        walk_forward(MovingAverageCrossover, data.head(300), CANDIDATES, train_bars=200, test_bars=100,
                     holdout_bars=50, base_params=StrategyParams())


def test_walk_forward_expanding_training(data):
    out = walk_forward(MovingAverageCrossover, data, CANDIDATES[:2], train_bars=300, test_bars=200,
                       holdout_bars=0, max_folds=2, training="expanding",
                       base_params=StrategyParams(), bars_per_year=252)
    assert out["folds"][1]["training_start"] == str(data.index[0])


# ----------------------------------------------------------------- API
def test_costs_endpoint():
    import main

    with TestClient(main.app) as client:
        r = client.get("/api/backtest/costs", params={"symbol": "SPY", "short_window": 10, "long_window": 30})
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["rows"]) == 6
        assert body["rows"][0]["bars_per_year"] == 252
