"""Strumento di confronto con Vibe-Trading: parti che non richiedono il pacchetto."""

import json

import pandas as pd
import pytest

from strategy.quant_model import StrategyParams
from tools.compare_external import (
    Summary,
    compare,
    comparison_data,
    prepare_vibe_run,
    qts_summary,
    vibe_summary,
)


def test_comparison_data_aligns_execution_prices():
    d = comparison_data("BTC-USD", "1y")
    assert (d["open"].iloc[1:].values == d["close"].iloc[:-1].values).all()
    assert (d["high"] >= d[["open", "close"]].max(axis=1)).all()
    assert (d["low"] <= d[["open", "close"]].min(axis=1)).all()
    assert (d.index.to_series().diff().dropna() == pd.Timedelta(days=1)).all()   # 24/7


def test_prepare_vibe_run_writes_inputs(tmp_path):
    d = comparison_data("BTC-USD", "1y")
    params = StrategyParams(short_window=7, long_window=21, fee_bps=8)
    run, home = prepare_vibe_run(tmp_path, d, params)
    config = json.loads((run / "config.json").read_text())
    assert config["commission"] == pytest.approx(0.0008) and config["slippage"] == 0.0
    assert config["source"] == "local" and config["initial_cash"] == 10_000
    code = (run / "code" / "signal_engine.py").read_text()
    assert "short_window: int = 7" in code and "long_window: int = 21" in code
    compile(code, "signal_engine.py", "exec")
    assert (home / ".vibe-trading" / "data-bridge" / "config.yaml").read_text().count("BTC-USDT") == 1


def _fake_artifacts(run, final, trades):
    art = run / "artifacts"
    art.mkdir(parents=True)
    pd.DataFrame({"max_drawdown": [-0.1], "benchmark_return": [0.2]}).to_csv(art / "metrics.csv", index=False)
    pd.DataFrame({"equity": [10_000, final]}).to_csv(art / "equity.csv", index=False)
    pd.DataFrame(trades, columns=["side", "reason"]).to_csv(art / "trades.csv", index=False)


def test_vibe_summary_ignores_end_of_backtest_close(tmp_path):
    _fake_artifacts(tmp_path, 11_000, [("buy", "signal"), ("sell", "signal"), ("buy", "signal"),
                                       ("sell", "end_of_backtest")])
    s = vibe_summary(tmp_path, 10_000)
    assert (s.buys, s.sells) == (2, 1)
    assert s.total_return_pct == pytest.approx(10.0)
    assert s.max_drawdown_pct == pytest.approx(-10.0) and s.buy_and_hold_pct == pytest.approx(20.0)


def test_compare_flags_differences():
    a = Summary("QTS", 11_000, 10.0, -10.0, 2, 1, 20.0)
    same = Summary("Vibe-Trading", 11_000.5, 10.005, -10.002, 2, 1, 20.001)
    assert all(r["ok"] for r in compare(a, same, 0.1))
    off = Summary("Vibe-Trading", 11_200, 12.0, -10.0, 3, 1, 20.0)
    bad = {r["misura"] for r in compare(a, off, 0.1) if not r["ok"]}
    assert bad == {"final_equity", "total_return_pct", "buys"}


def test_qts_summary_charges_final_close_when_in_position():
    d = comparison_data("BTC-USD", "2y")
    p0 = StrategyParams(fee_bps=0)
    p5 = StrategyParams(fee_bps=5)
    s0 = qts_summary(d, p0, 365)
    s5 = qts_summary(d, p5, 365)
    assert s5.final_equity < s0.final_equity
    assert s0.buys >= s0.sells
