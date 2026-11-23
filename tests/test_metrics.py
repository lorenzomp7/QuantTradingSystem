"""Test dell'annualizzazione e delle statistiche per trade (strategy/metrics.py)."""

import numpy as np
import pandas as pd
import pytest

from strategy.metrics import (
    bars_per_year,
    effective_bars_per_year,
    infer_market,
    round_trip_stats,
    years_elapsed,
)
from strategy.quant_model import MovingAverageCrossover, StrategyParams, compute_metrics


# ------------------------------------------------------- Barre per anno
@pytest.mark.parametrize(
    ("interval", "market", "expected"),
    [
        ("1d", "crypto", 365),
        ("1d", "stocks", 252),
        ("1h", "crypto", 365 * 24),
        ("60m", "crypto", 365 * 24),
        ("5m", "crypto", 365 * 288),
        ("5m", "stocks", 252 * 78),
        ("1h", "stocks", 252 * 7),
        ("1d", "forex", 260),
        ("1wk", "crypto", 52),
        ("1mo", "stocks", 12),
        ("1M", "crypto", 12),     # 1M è un mese, non un minuto
        ("1m", "crypto", 365 * 1440),
        ("1H", "stocks", 252 * 7),  # maiuscole accettate per ore e giorni
        ("1D", "crypto", 365),
    ],
)
def test_bars_per_year_table(interval, market, expected):
    assert bars_per_year(interval, market) == expected


def test_bars_per_year_rejects_unknown_values():
    with pytest.raises(ValueError):
        bars_per_year("7x", "crypto")
    with pytest.raises(ValueError):
        bars_per_year("1d", "luna")


@pytest.mark.parametrize(
    ("symbol", "market"),
    [("BTC-USD", "crypto"), ("eth-usdt", "crypto"), ("SPY", "stocks"),
     ("BRK-B", "stocks"), ("EURUSD=X", "forex"), ("^GSPC", "stocks")],
)
def test_infer_market(symbol, market):
    assert infer_market(symbol) == market


def test_daily_btc_years_use_365_days():
    # 504 barre giornaliere di BTC sono 504/365 = 1,38 anni, non 2.
    assert 504 / bars_per_year("1d", "crypto") == pytest.approx(1.38, abs=0.005)


# ------------------------------------------- Fattore misurato dalle date
def test_effective_bars_per_year_counts_fractional_days():
    # 72 barre orarie: da t0 a t0 + 71 h = 2 giorni e 23 ore, non 2 giorni.
    idx = pd.date_range("2024-01-01", periods=72, freq="h")
    years = (71 / 24) / 365.25
    assert effective_bars_per_year(idx) == int(72 / years)
    assert years_elapsed(idx) == pytest.approx(years)


def test_effective_bars_per_year_edge_cases():
    assert effective_bars_per_year(pd.DatetimeIndex([])) == 252
    assert effective_bars_per_year(pd.DatetimeIndex([]), default=365) == 365
    # Meno di un giorno di dati: conta come un anno (come Vibe-Trading).
    short = pd.date_range("2024-01-01", periods=10, freq="min")
    assert effective_bars_per_year(short) == 10
    # Un anno di barre giornaliere 24/7 dà circa 365.
    daily = pd.date_range("2023-01-01", periods=366, freq="D")
    assert effective_bars_per_year(daily) == pytest.approx(365, abs=1)


# ------------------------------------------------- compute_metrics
def _flat_series(n: int, freq: str, daily_ret: float = 0.001) -> tuple[pd.Series, pd.Series]:
    idx = pd.date_range("2024-01-01", periods=n, freq=freq)
    ret = pd.Series(daily_ret, index=idx)
    ret.iloc[0] = 0.0
    equity = 10_000 * (1 + ret).cumprod()
    return ret, equity


def test_compute_metrics_uses_given_bars_per_year():
    ret, equity = _flat_series(365, "D")
    empty = pd.DataFrame(columns=["side", "price"])
    m_crypto = compute_metrics(ret, ret, equity, empty, 10_000, bars_per_year=365)
    m_252 = compute_metrics(ret, ret, equity, empty, 10_000, bars_per_year=252)
    # Stessa serie: con 365 barre/anno è un anno esatto, quindi CAGR = rendimento totale.
    assert m_crypto["cagr_pct"] == pytest.approx(m_crypto["total_return_pct"], abs=0.01)
    # Con 252 barre/anno le stesse 365 barre sembrano 1,45 anni: CAGR sottostimato.
    assert m_252["cagr_pct"] < m_crypto["cagr_pct"]
    assert m_crypto["bars_per_year"] == 365
    expected_vol = ret.std(ddof=0) * np.sqrt(365) * 100
    assert m_crypto["annual_volatility_pct"] == pytest.approx(expected_vol, abs=0.01)


def test_compute_metrics_measures_factor_when_not_given():
    # 5 giorni di candele da 5 minuti 24/7: il fattore misurato è vicino a 365*288.
    ret, equity = _flat_series(5 * 288, "5min", daily_ret=0.00001)
    empty = pd.DataFrame(columns=["side", "price"])
    m = compute_metrics(ret, ret, equity, empty, 10_000)
    assert m["bars_per_year"] == effective_bars_per_year(ret.index)
    assert m["bars_per_year"] == pytest.approx(365 * 288, rel=0.01)


def test_backtest_passes_bars_per_year(monkeypatch):
    from utils.data_fetcher import generate_mock_ohlcv

    data = generate_mock_ohlcv("TEST", period="2y")
    strat = MovingAverageCrossover(StrategyParams(short_window=5, long_window=20))
    m252 = strat.backtest(data, bars_per_year=252).metrics
    m365 = strat.backtest(data, bars_per_year=365).metrics
    assert m252["total_return_pct"] == m365["total_return_pct"]
    assert m365["sharpe_ratio"] == pytest.approx(m252["sharpe_ratio"] * np.sqrt(365 / 252), abs=0.002)


# ------------------------------------------------ Statistiche per trade
def test_round_trip_stats_by_hand():
    # Tre round-trip: +10%, -5%, -2%; poi un BUY rimasto aperto (escluso).
    idx = pd.date_range("2024-01-01", periods=10, freq="D")
    trades = pd.DataFrame(
        {"side": ["BUY", "SELL", "BUY", "SELL", "BUY", "SELL", "BUY"],
         "price": [100.0, 110.0, 100.0, 95.0, 100.0, 98.0, 50.0]},
        index=idx[[0, 2, 3, 4, 5, 8, 9]],
    )
    s = round_trip_stats(trades, idx)
    assert s["round_trips"] == 3
    assert s["win_rate_pct"] == pytest.approx(100 / 3, abs=0.01)
    assert s["avg_win_pct"] == pytest.approx(10.0)
    assert s["avg_loss_pct"] == pytest.approx(-3.5)
    assert s["profit_loss_ratio"] == pytest.approx(10 / 3.5, abs=1e-3)
    assert s["profit_factor"] == pytest.approx(10 / 7, abs=1e-3)
    assert s["max_consecutive_losses"] == 2
    assert s["avg_holding_bars"] == pytest.approx((2 + 1 + 3) / 3, abs=0.05)


def test_round_trip_stats_without_losses_or_trades():
    idx = pd.date_range("2024-01-01", periods=3, freq="D")
    win_only = pd.DataFrame({"side": ["BUY", "SELL"], "price": [100.0, 120.0]}, index=idx[[0, 2]])
    s = round_trip_stats(win_only, idx)
    assert s["profit_factor"] is None and s["profit_loss_ratio"] is None
    none = round_trip_stats(pd.DataFrame(columns=["side", "price"]), idx)
    assert none["round_trips"] == 0 and none["win_rate_pct"] is None
