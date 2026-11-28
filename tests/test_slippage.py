"""Slippage nel backtest: prezzo d'esecuzione spostato a sfavore (punto 10 del Piano, parte backtest)."""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from strategy.optimization import grid_search, sma_grid_vectorized
from strategy.quant_model import MovingAverageCrossover, StrategyParams
from strategy.research import _window_trades, cost_sensitivity, window_returns
from strategy.rsi_macd import RsiMacdFuzzy, RsiMacdParams
from utils.data_fetcher import generate_mock_ohlcv
from web import static_api


@pytest.fixture
def data() -> pd.DataFrame:
    return generate_mock_ohlcv("SLIP", period="2y")


def _sma(**kw) -> MovingAverageCrossover:
    return MovingAverageCrossover(StrategyParams(short_window=5, long_window=20, **kw))


def test_slippage_lowers_return(data):
    base = _sma(fee_bps=5).backtest(data, bars_per_year=252)
    slip = _sma(fee_bps=5, slippage_bps=5).backtest(data, bars_per_year=252)
    more = _sma(fee_bps=5, slippage_bps=20).backtest(data, bars_per_year=252)
    assert base.metrics["num_trades"] == slip.metrics["num_trades"] > 0
    assert base.metrics["total_return_pct"] > slip.metrics["total_return_pct"] > more.metrics["total_return_pct"]
    # Anche il Buy & Hold compra con lo stesso slippage.
    assert slip.benchmark_curve.iloc[-1] == pytest.approx(base.benchmark_curve.iloc[-1] / 1.0005)
    f0 = RsiMacdFuzzy(RsiMacdParams()).backtest(data, bars_per_year=252).metrics["total_return_pct"]
    f1 = RsiMacdFuzzy(RsiMacdParams(slippage_bps=10)).backtest(data, bars_per_year=252).metrics["total_return_pct"]
    assert f1 < f0


@pytest.mark.parametrize("execution", ["next_open", "close"])
def test_buy_fill_above_market_sell_below(data, execution):
    res = _sma(fee_bps=0, slippage_bps=10, execution=execution).backtest(data, bars_per_year=252)
    market = data["open"] if execution == "next_open" else data["close"]
    buys = res.trades[res.trades["side"] == "BUY"]
    sells = res.trades[res.trades["side"] == "SELL"]
    assert len(buys) > 0 and len(sells) > 0
    np.testing.assert_allclose(buys["price"], market.loc[buys.index] * 1.001)
    np.testing.assert_allclose(sells["price"], market.loc[sells.index] * 0.999)
    # Sulla barra d'ingresso il rendimento parte dal prezzo eseguito, più alto di quello di mercato.
    if execution == "next_open":
        t = buys.index[0]
        i = res.equity_curve.index.get_loc(t)
        got = res.equity_curve.iloc[i] / res.equity_curve.iloc[i - 1]
        assert got == pytest.approx(data.loc[t, "close"] / (data.loc[t, "open"] * 1.001))


def test_zero_slippage_unchanged(data):
    assert StrategyParams().slippage_bps == 0.0 and RsiMacdParams().slippage_bps == 0.0
    a = _sma(fee_bps=5).backtest(data, bars_per_year=252)
    b = _sma(fee_bps=5, slippage_bps=0.0).backtest(data, bars_per_year=252)
    pd.testing.assert_series_equal(a.equity_curve, b.equity_curve)
    pd.testing.assert_frame_equal(a.trades, b.trades)
    assert a.metrics["params"]["slippage_bps"] == 0.0
    # Senza slippage il prezzo dei trade è esattamente l'open della barra d'esecuzione.
    assert (a.trades["price"] == data["open"].loc[a.trades.index]).all()


def test_negative_slippage_raises():
    with pytest.raises(ValueError):
        StrategyParams(slippage_bps=-1)
    with pytest.raises(ValueError):
        RsiMacdParams(slippage_bps=-0.5)


def test_cost_sensitivity_uses_price_slippage(data):
    strat = _sma(fee_bps=5, slippage_bps=2)
    out = cost_sensitivity(strat, data, fee_multipliers=(1, 3), slippage_bps=(0, 5), bars_per_year=252)
    by = {(r["fee_multiplier"], r["slippage_bps"]): r for r in out["rows"]}
    # Lo slippage dello scenario si somma a quello di base e sposta il prezzo, non la commissione.
    direct = _sma(fee_bps=15, slippage_bps=7).backtest(data, bars_per_year=252).metrics
    assert by[(3, 5)]["total_return_pct"] == direct["total_return_pct"]
    assert by[(3, 5)]["effective_cost_bps"] == 22 and by[(1, 0)]["effective_cost_bps"] == 7
    assert by[(1, 0)]["total_return_pct"] == strat.backtest(data, bars_per_year=252).metrics["total_return_pct"]
    assert by[(1, 0)]["total_return_pct"] > by[(1, 5)]["total_return_pct"]
    with pytest.raises(ValueError):
        cost_sensitivity(strat, data, slippage_bps=(-1,))


def test_window_and_grid_apply_slippage(data):
    strat = _sma(fee_bps=5, slippage_bps=10)
    plain = _sma(fee_bps=5)
    w, w0 = window_returns(strat, data, 100, 300), window_returns(plain, data, 100, 300)
    assert (1 + w.returns).prod() < (1 + w0.returns).prod()
    trades, trades0 = _window_trades(w), _window_trades(w0)
    sign = np.where(trades["side"] == "BUY", 1.001, 0.999)
    np.testing.assert_allclose(trades["price"], trades0["price"] * sign)   # liquidazione finale compresa
    base = StrategyParams(fee_bps=5, slippage_bps=10)
    loop = grid_search(MovingAverageCrossover, data, {"short_window": [5, 10], "long_window": [20, 40]},
                       base_params=base, bars_per_year=252)
    vec = sma_grid_vectorized(data, [5, 10], [20, 40], base_params=base, bars_per_year=252)
    key = lambda r: (r["parameters"]["short_window"], r["parameters"]["long_window"])  # noqa: E731
    by_loop = {key(r): r for r in loop}
    for r in vec:
        for k, v in by_loop[key(r)]["metrics"].items():
            assert r["metrics"][k] == pytest.approx(v, abs=0.011), (key(r), k)
    no_slip = sma_grid_vectorized(data, [5], [20], base_params=StrategyParams(fee_bps=5), bars_per_year=252)
    with_slip = [r for r in vec if key(r) == (5, 20)]
    assert with_slip[0]["metrics"]["total_return_pct"] < no_slip[0]["metrics"]["total_return_pct"]


def test_static_and_api_slippage_param_equal():
    import main

    q = {"symbol": "SPY", "short_window": "10", "long_window": "30", "period": "2y", "slippage_bps": "5"}
    with TestClient(main.app) as client:
        api = client.get("/api/backtest", params=q).json()
        api_costs = client.get("/api/backtest/costs", params=q).json()
        assert client.get("/api/backtest", params={**q, "slippage_bps": "-1"}).status_code == 422
        assert client.get("/api/backtest", params={**q, "slippage_bps": "501"}).status_code == 422
        plain = client.get("/api/backtest", params={k: v for k, v in q.items() if k != "slippage_bps"}).json()
    mine = static_api.backtest(q, None)
    assert mine["metrics"] == api["metrics"] and mine["metrics"]["params"]["slippage_bps"] == 5.0
    assert mine["equity_curve"] == api["equity_curve"] and mine["trades"] == api["trades"]
    assert static_api.costs(q, None)["rows"] == api_costs["rows"]
    assert api["metrics"]["total_return_pct"] < plain["metrics"]["total_return_pct"]
    assert plain["metrics"]["params"]["slippage_bps"] == 0.0
    for bad in ("-1", "501", "abc"):
        with pytest.raises(ValueError):
            static_api.backtest({**q, "slippage_bps": bad}, None)
