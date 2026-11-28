"""Riscaldamento degli indicatori e Buy & Hold sullo stesso periodo (punto 4 del Piano)."""

import numpy as np
import pandas as pd
import pytest

from strategy.optimization import grid_search, sma_grid_vectorized
from strategy.quant_model import BaseStrategy, MovingAverageCrossover, StrategyParams
from strategy.rsi_macd import RsiMacdFuzzy, RsiMacdParams
from utils.data_fetcher import generate_mock_ohlcv


@pytest.fixture
def data() -> pd.DataFrame:
    return generate_mock_ohlcv("WARMUP", period="2y")


def test_benchmark_starts_with_strategy(data):
    res = MovingAverageCrossover(StrategyParams(short_window=20, long_window=50)).backtest(data, bars_per_year=252)
    first = data.index[49]                                  # prima barra con la SMA lunga definita
    assert res.equity_curve.index[0] == first and res.benchmark_curve.index[0] == first
    assert res.equity_curve.index.equals(res.benchmark_curve.index)
    assert res.equity_curve.iloc[0] == pytest.approx(10_000.0)
    assert res.benchmark_curve.iloc[0] == pytest.approx(10_000.0)
    point = res.to_dict()["equity_curve"][0]
    assert point == {"date": first.strftime("%Y-%m-%d"), "equity": 10_000.0, "benchmark": 10_000.0, "in_market": 0}
    assert res.to_dict()["equity_curve"][-1]["benchmark"] == round(float(res.benchmark_curve.iloc[-1]), 2)


@pytest.mark.parametrize("execution", ["next_open", "close"])
def test_benchmark_pays_entry_fee(data, execution):
    c = 25 / 10_000
    params = StrategyParams(short_window=20, long_window=50, fee_bps=25, execution=execution)
    res = MovingAverageCrossover(params).backtest(data, bars_per_year=252)
    # Il Buy & Hold compra sulla barra dopo l'inizio, con la stessa regola della strategia, e non vende.
    entry = data["open"].iloc[50] if execution == "next_open" else data["close"].iloc[49]
    expected = 10_000 * data["close"].iloc[-1] / entry * (1 - c)
    assert res.benchmark_curve.iloc[-1] == pytest.approx(expected)
    assert res.benchmark_curve.iloc[1] == pytest.approx(10_000 * data["close"].iloc[50] / entry * (1 - c))
    assert res.metrics["buy_and_hold_return_pct"] == pytest.approx((expected / 10_000 - 1) * 100, abs=0.006)
    free = MovingAverageCrossover(StrategyParams(short_window=20, long_window=50, fee_bps=0, execution=execution))
    assert free.backtest(data, bars_per_year=252).benchmark_curve.iloc[-1] == pytest.approx(expected / (1 - c))


def test_longer_flat_history_same_result_with_start():
    # Serie piatta per tutto il riscaldamento, poi si muove: allungare lo storico piatto non cambia nulla.
    rng = np.random.default_rng(3)
    move = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.02, 200)))

    def frame(flat_bars: int) -> pd.DataFrame:
        close = np.r_[np.full(flat_bars, 100.0), move]
        idx = pd.date_range(end="2025-06-30", periods=len(close), freq="B")
        open_ = np.r_[100.0, close[:-1]] * 1.001
        return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * 1.01,
                             "low": np.minimum(open_, close) * 0.99, "close": close, "volume": 1.0}, index=idx)

    strat = MovingAverageCrossover(StrategyParams(short_window=5, long_window=20, fee_bps=10))
    short, long_ = frame(19), frame(150)
    start = short.index[19]
    a = strat.backtest(short, bars_per_year=252)
    b = strat.backtest(long_, bars_per_year=252, start=start)
    assert a.metrics["start"] == b.metrics["start"] == start.strftime("%Y-%m-%d")
    pd.testing.assert_series_equal(a.equity_curve, b.equity_curve)
    pd.testing.assert_series_equal(a.benchmark_curve, b.benchmark_curve)
    pd.testing.assert_frame_equal(a.trades, b.trades)
    skip = {"data_start", "params"}
    assert {k: v for k, v in a.metrics.items() if k not in skip} == {k: v for k, v in b.metrics.items() if k not in skip}
    assert a.metrics["num_trades"] > 0
    # Senza `start` lo storico più lungo anticipa l'inizio della valutazione.
    assert strat.backtest(long_, bars_per_year=252).metrics["bars"] == len(long_) - 19
    # `start` prima del riscaldamento: vale il riscaldamento; dopo la fine dei dati: errore.
    assert strat.backtest(short, bars_per_year=252, start=short.index[0]).metrics["start"] == a.metrics["start"]
    with pytest.raises(ValueError):
        strat.backtest(short, start="2030-01-01")


def test_start_begins_flat_even_if_signal_was_already_long(data):
    strat = MovingAverageCrossover(StrategyParams(short_window=5, long_window=20, fee_bps=0))
    sig = strat.generate_signals(data)
    i = int(np.flatnonzero((sig["signal"].to_numpy()[:-2] == 1) & (sig["signal"].to_numpy()[1:-1] == 1))[5]) + 1
    res = strat.backtest(data, bars_per_year=252, start=data.index[i])
    # Segnale già long prima dell'inizio: si parte senza posizione e si compra all'open della barra dopo.
    assert res.signals["in_market"].iloc[: i + 1].sum() == 0 and res.signals["in_market"].iloc[i + 1] == 1
    assert res.equity_curve.iloc[0] == pytest.approx(10_000.0)
    assert res.trades.index[0] == data.index[i + 1] and res.trades["price"].iloc[0] == data["open"].iloc[i + 1]
    assert res.equity_curve.iloc[1] == pytest.approx(res.benchmark_curve.iloc[1])


def test_warmup_bars_reported(data):
    class NoWarmup(BaseStrategy):
        name = "no_warmup"

        def generate_signals(self, d):
            df = d.copy()
            df["signal"] = 1
            df["position"] = 0
            return df

    assert NoWarmup().warmup_bars() == 0
    assert NoWarmup().backtest(data, bars_per_year=252).metrics["bars"] == len(data)
    sma = MovingAverageCrossover(StrategyParams(short_window=10, long_window=30))
    assert sma.warmup_bars() == 29
    m = sma.backtest(data, bars_per_year=252).metrics
    assert m["warmup_bars"] == 29 and m["bars"] == len(data) - 29
    assert m["data_start"] == data.index[0].strftime("%Y-%m-%d")
    assert m["start"] == data.index[29].strftime("%Y-%m-%d") and m["end"] == data.index[-1].strftime("%Y-%m-%d")
    fuzzy = RsiMacdFuzzy(RsiMacdParams())
    mf = fuzzy.backtest(data, bars_per_year=252).metrics
    assert mf["warmup_bars"] == fuzzy.warmup_bars() == 74
    assert mf["start"] == data.index[74].strftime("%Y-%m-%d") and mf["bars"] == len(data) - 74


def test_curve_excludes_warmup(data):
    res = MovingAverageCrossover(StrategyParams(short_window=20, long_window=50)).backtest(data, bars_per_year=252)
    out = res.to_dict()
    assert len(out["equity_curve"]) == len(data) - 49 == len(res.equity_curve)
    assert out["equity_curve"][0]["date"] == data.index[49].strftime("%Y-%m-%d")
    assert len(res.signals) == len(data)                     # i segnali restano su tutta la serie
    assert res.signals["in_market"].iloc[:50].sum() == 0
    # CAGR sul periodo valutato, non su quello scaricato.
    years = (len(data) - 49) / 252
    assert res.metrics["cagr_pct"] == pytest.approx(((res.equity_curve.iloc[-1] / 10_000) ** (1 / years) - 1) * 100,
                                                    abs=0.006)


def test_grid_with_common_start_uses_one_period(data):
    shorts, longs = [5, 10], [20, 60, 120]
    base = StrategyParams(fee_bps=7)
    start = data.index[150]
    loop = grid_search(MovingAverageCrossover, data, {"short_window": shorts, "long_window": longs},
                       base_params=base, bars_per_year=252, start=start)
    vec = sma_grid_vectorized(data, shorts, longs, base_params=base, bars_per_year=252, start=start)
    key = lambda r: (r["parameters"]["short_window"], r["parameters"]["long_window"])  # noqa: E731
    by_loop = {key(r): r for r in loop}
    assert len(vec) == len(loop) == 6
    for r in vec:
        for k, v in by_loop[key(r)]["metrics"].items():
            assert r["metrics"][k] == pytest.approx(v, abs=0.011), (key(r), k)
    # Con l'inizio comune la coppia 5/20 è valutata dalla stessa barra della 10/120.
    direct = MovingAverageCrossover(StrategyParams(short_window=5, long_window=20, fee_bps=7))
    assert by_loop[(5, 20)]["metrics"]["total_return_pct"] == \
        direct.backtest(data, bars_per_year=252, start=start).metrics["total_return_pct"]
    assert direct.backtest(data, bars_per_year=252, start=start).metrics["bars"] == len(data) - 150
    with pytest.raises(ValueError):
        sma_grid_vectorized(data, shorts, longs, base_params=base, start="2100-01-01")
