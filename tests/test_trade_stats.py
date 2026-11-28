"""Tabella dei trade e statistiche nette di commissioni (punti 6, 33 e 35 del Piano)."""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from strategy.metrics import round_trip_stats, round_trip_table
from strategy.quant_model import MovingAverageCrossover, StrategyParams, compute_metrics
from tests.test_execution import CLOSE, OPEN, FixedSignal
from utils.data_fetcher import generate_mock_ohlcv
from web import static_api

EMPTY = pd.DataFrame(columns=["side", "price"])


def _trades(prices, idx=None) -> pd.DataFrame:
    """BUY e SELL alternati ai prezzi dati, una barra sì e una no."""
    idx = pd.date_range("2024-01-01", periods=2 * len(prices), freq="D") if idx is None else idx
    return pd.DataFrame({"side": ["BUY", "SELL"] * (len(prices) // 2) + ["BUY"] * (len(prices) % 2),
                         "price": [float(p) for p in prices]}, index=idx[: len(prices)])


def _pct_trades(pcts) -> pd.DataFrame:
    """Round-trip con i rendimenti lordi dati (in %), ingresso sempre a 100."""
    prices = []
    for p in pcts:
        prices += [100.0, 100.0 + p]
    return _trades(prices)


# ----------------------------------------------------------------- Punto 6
def test_three_trades_net_by_hand():
    idx = pd.date_range("2024-01-01", periods=10, freq="D")
    trades = pd.DataFrame({"side": ["BUY", "SELL", "BUY", "SELL", "BUY", "SELL"],
                           "price": [100.0, 110.0, 100.0, 95.0, 100.0, 98.0]}, index=idx[[0, 2, 3, 4, 5, 8]])
    c = 10 / 10_000
    net = [1.10 * (1 - c) ** 2 - 1, 0.95 * (1 - c) ** 2 - 1, 0.98 * (1 - c) ** 2 - 1]
    table = round_trip_table(trades, idx, fee_bps=10, initial_capital=10_000)
    assert list(table.columns) == ["entry_time", "exit_time", "entry_price", "exit_price", "bars", "gross_pct",
                                   "net_pct", "pnl", "fees", "quantity", "mae_pct", "mfe_pct", "status"]
    assert list(table["status"]) == ["closed"] * 3 and list(table["bars"]) == [2, 1, 3]
    np.testing.assert_allclose(table["gross_pct"], [10.0, -5.0, -2.0])
    np.testing.assert_allclose(table["net_pct"], [n * 100 for n in net])
    # Capitale tutto investito: 10.000 al primo ingresso, poi quello che resta dopo ogni trade.
    e1 = 10_000 * (1 + net[0])
    e2 = e1 * (1 + net[1])
    np.testing.assert_allclose(table["pnl"], [10_000 * net[0], e1 * net[1], e2 * net[2]])
    assert table["quantity"].iloc[0] == pytest.approx(10_000 * (1 - c) / 100)
    assert table["fees"].iloc[0] == pytest.approx(10_000 * c + 10_000 * (1 - c) / 100 * 110 * c)
    assert table["mae_pct"].isna().all() and table["mfe_pct"].isna().all()      # senza high e low

    s = round_trip_stats(trades, idx, fee_bps=10)
    assert s["round_trips"] == 3 and s["open_trades"] == 0
    assert s["win_rate_pct"] == pytest.approx(100 / 3, abs=0.01)
    assert s["avg_win_pct"] == pytest.approx(net[0] * 100, abs=0.006)
    assert s["avg_loss_pct"] == pytest.approx((net[1] + net[2]) / 2 * 100, abs=0.006)
    assert s["profit_factor"] == pytest.approx(net[0] / abs(net[1] + net[2]), abs=1e-3)
    assert s["profit_loss_ratio"] == pytest.approx(net[0] / abs((net[1] + net[2]) / 2), abs=1e-3)
    assert s["avg_trade_pct"] == pytest.approx(sum(net) / 3 * 100, abs=0.006)
    assert s["max_consecutive_losses"] == 2 and s["avg_holding_bars"] == pytest.approx(2.0)
    # Al netto si guadagna meno e si perde di più che al lordo.
    gross = round_trip_stats(trades, idx)
    assert gross["avg_win_pct"] == 10.0 and gross["avg_loss_pct"] == -3.5 and gross["avg_trade_pct"] == 1.0
    assert s["avg_win_pct"] < gross["avg_win_pct"] and s["profit_factor"] < gross["profit_factor"]


def test_small_gross_win_is_net_loss():
    trades = _trades([100.0, 100.05])                       # +0,05% lordo
    gross = round_trip_stats(trades)
    assert gross["win_rate_pct"] == 100.0 and gross["profit_factor"] is None
    net = round_trip_stats(trades, fee_bps=10)              # 10 bps all'ingresso e 10 all'uscita
    assert net["win_rate_pct"] == 0.0
    assert net["avg_loss_pct"] == pytest.approx(-0.15, abs=0.005) and net["avg_win_pct"] is None
    table = round_trip_table(trades, fee_bps=10)
    assert table["net_pct"].iloc[0] == pytest.approx((1.0005 * 0.999 ** 2 - 1) * 100)
    assert table["gross_pct"].iloc[0] == pytest.approx(0.05)


def _hand_backtest(**params):
    idx = pd.date_range("2024-01-01", periods=8, freq="D")
    data = pd.DataFrame({
        "open": np.asarray(OPEN, dtype=float), "close": np.asarray(CLOSE, dtype=float),
        "high": [10.0, 10.0, 11.0, 12.2, 13.5, 12.6, 9.0, 10.3],
        "low": [10.0, 10.0, 10.0, 10.5, 11.5, 9.0, 9.0, 9.2],
        "sig": [0, 0, 1, 1, 0, 0, 1, 1]}, index=idx)
    params.setdefault("fee_bps", 10)
    return data, FixedSignal(StrategyParams(short_window=1, long_window=2, **params)).backtest(data, bars_per_year=252)


def test_open_trade_listed_not_counted():
    c = 10 / 10_000
    data, res = _hand_backtest()
    rt = res.round_trips
    assert list(rt["status"]) == ["closed", "open"]
    last = rt.iloc[-1]
    # Trade aperto: valorizzato all'ultimo close, con la sola commissione d'ingresso.
    assert last["entry_time"] == data.index[7] and last["exit_time"] == data.index[-1]
    assert last["entry_price"] == 9.5 and last["exit_price"] == 10.0
    assert last["net_pct"] == pytest.approx((10 / 9.5 * (1 - c) - 1) * 100)
    capital = 10_000 * (1 + rt["net_pct"].iloc[0] / 100)
    assert last["fees"] == pytest.approx(capital * c)
    assert last["pnl"] == pytest.approx(res.equity_curve.iloc[-1] - capital)
    # Fuori dalle statistiche: un solo round-trip chiuso, vincente.
    m = res.metrics
    assert m["round_trips"] == 1 and m["open_trades"] == 1 and m["win_rate_pct"] == 100.0
    assert m["avg_trade_pct"] == pytest.approx((12 / 11 * (1 - c) ** 2 - 1) * 100, abs=0.006)
    out = res.to_dict()
    assert [r["status"] for r in out["round_trips"]] == ["closed", "open"]
    assert out["round_trips"][0] == {
        "entry_time": "2024-01-04", "exit_time": "2024-01-06", "entry_price": 11.0, "exit_price": 12.0, "bars": 2,
        "gross_pct": round((12 / 11 - 1) * 100, 4), "net_pct": round((12 / 11 * (1 - c) ** 2 - 1) * 100, 4),
        "pnl": round(10_000 * (12 / 11 * (1 - c) ** 2 - 1), 2),
        "fees": round(10_000 * c + 10_000 * (1 - c) / 11 * 12 * c, 2),
        "quantity": round(10_000 * (1 - c) / 11, 6),
        "mae_pct": round((10.5 / 11 - 1) * 100, 4), "mfe_pct": round((13.5 / 11 - 1) * 100, 4), "status": "closed"}
    assert len(out["trades"]) == 3                                     # `trades` resta com'era


def test_mae_mfe_from_high_low():
    data, res = _hand_backtest()
    rt = res.round_trips
    # Ingresso all'open della barra 3 (11), uscita all'open della barra 5 (12): contano le barre 3 e 4
    # intere; sulla barra d'uscita solo il prezzo d'uscita (il minimo 9,0 viene dopo la vendita).
    assert rt["mae_pct"].iloc[0] == pytest.approx((10.5 / 11 - 1) * 100)
    assert rt["mfe_pct"].iloc[0] == pytest.approx((13.5 / 11 - 1) * 100)
    assert rt["mae_pct"].iloc[1] == pytest.approx((9.2 / 9.5 - 1) * 100)
    assert rt["mfe_pct"].iloc[1] == pytest.approx((10.3 / 9.5 - 1) * 100)
    # Senza high e low: None, anche nel JSON.
    no_hl = FixedSignal(StrategyParams(short_window=1, long_window=2)).backtest(data.drop(columns=["high", "low"]))
    assert no_hl.round_trips["mae_pct"].isna().all()
    assert no_hl.to_dict()["round_trips"][0]["mae_pct"] is None
    assert no_hl.to_dict()["round_trips"][0]["mfe_pct"] is None
    # Modo close: ingresso al close della barra 2, quindi contano le barre 3 e 4 (uscita al close della 4).
    _, cls = _hand_backtest(execution="close")
    assert cls.round_trips["entry_price"].iloc[0] == 11.0 and cls.round_trips["exit_price"].iloc[0] == 12.5
    assert cls.round_trips["mae_pct"].iloc[0] == pytest.approx((10.5 / 11 - 1) * 100)
    assert cls.round_trips["mfe_pct"].iloc[0] == pytest.approx((13.5 / 11 - 1) * 100)


def test_sortino_downside_deviation_by_hand():
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    ret = pd.Series([0.0, 0.02, -0.01, 0.03, -0.02], index=idx)
    equity = 10_000 * (1 + ret).cumprod()
    m = compute_metrics(ret, ret, equity, EMPTY, 10_000, bars_per_year=252)
    downside = np.sqrt((0.01 ** 2 + 0.02 ** 2) / 5)          # media su tutte le barre, non solo le negative
    assert m["sortino_ratio"] == pytest.approx(ret.mean() / downside * np.sqrt(252), abs=1e-3)
    # Con il tasso risk-free conta l'eccesso di rendimento.
    rf = compute_metrics(ret, ret, equity, EMPTY, 10_000, risk_free_rate=0.0504, bars_per_year=252)
    ex = ret - 0.0002
    expected = ex.mean() / np.sqrt((np.minimum(ex, 0) ** 2).mean()) * np.sqrt(252)
    assert rf["sortino_ratio"] == pytest.approx(expected, abs=1e-3)
    # Nessun rendimento negativo: 0, come prima.
    up = pd.Series([0.0, 0.01, 0.02], index=idx[:3])
    assert compute_metrics(up, up, 10_000 * (1 + up).cumprod(), EMPTY, 10_000, bars_per_year=252)["sortino_ratio"] == 0.0


def test_exposure_calmar_dd_duration():
    idx = pd.date_range("2024-01-01", periods=8, freq="D")
    equity = pd.Series([100.0, 110.0, 105.0, 99.0, 108.0, 112.0, 111.0, 113.0], index=idx)
    ret = equity.pct_change().fillna(0.0)
    held = pd.Series([0, 1, 1, 1, 1, 1, 0, 1], index=idx, dtype=float)
    m = compute_metrics(ret, ret, equity, EMPTY, 100.0, bars_per_year=8, positions=held)
    assert m["exposure_pct"] == pytest.approx(6 / 8 * 100)
    assert m["max_drawdown_pct"] == pytest.approx(-10.0)                 # 110 -> 99
    assert m["max_drawdown_duration_bars"] == 3                          # barre 2, 3, 4 sotto il massimo di 110
    assert m["calmar_ratio"] == pytest.approx(m["cagr_pct"] / 10.0, abs=1e-3)
    assert m["cagr_pct"] == pytest.approx(13.0)                          # 8 barre = 1 anno
    # Senza drawdown il Calmar non è definito; senza posizioni l'esposizione non si conosce.
    up = pd.Series([100.0, 101.0, 102.0], index=idx[:3])
    m_up = compute_metrics(up.pct_change().fillna(0.0), up.pct_change().fillna(0.0), up, EMPTY, 100.0, bars_per_year=252)
    assert m_up["calmar_ratio"] is None and m_up["max_drawdown_duration_bars"] == 0 and m_up["exposure_pct"] is None
    # Dal backtest: esposizione = quota di barre valutate con posizione.
    data = generate_mock_ohlcv("EXPO", period="2y")
    res = MovingAverageCrossover(StrategyParams(short_window=5, long_window=20)).backtest(data, bars_per_year=252)
    in_market = res.signals["in_market"].loc[res.equity_curve.index]
    assert res.metrics["exposure_pct"] == pytest.approx(in_market.mean() * 100, abs=0.006)


@pytest.mark.parametrize("execution", ["next_open", "close"])
def test_trade_pnl_sums_to_equity_change(execution):
    data = generate_mock_ohlcv("PNL", period="2y")
    params = StrategyParams(short_window=5, long_window=20, fee_bps=7, slippage_bps=3, execution=execution)
    res = MovingAverageCrossover(params).backtest(data, bars_per_year=252)
    rt = res.round_trips
    assert len(rt) >= 5 and res.metrics["round_trips"] == int((rt["status"] == "closed").sum())
    assert rt["pnl"].sum() == pytest.approx(res.equity_curve.iloc[-1] - 10_000, rel=1e-9)
    assert (rt["fees"] > 0).all() and (rt["mae_pct"] <= 0).all() and (rt["mfe_pct"] >= 0).all()
    # Il rendimento netto di ogni trade chiuso sta tra il peggiore e il migliore toccati.
    closed = rt[rt["status"] == "closed"]
    assert (closed["gross_pct"] >= closed["mae_pct"] - 1e-9).all() and (closed["gross_pct"] <= closed["mfe_pct"] + 1e-9).all()
    assert (closed["net_pct"] < closed["gross_pct"]).all()


def test_static_and_api_round_trips_equal():
    import main

    q = {"symbol": "SPY", "short_window": "5", "long_window": "20", "period": "2y", "fee_bps": "10"}
    with TestClient(main.app) as client:
        api = client.get("/api/backtest", params=q).json()
    mine = static_api.backtest(q, None)
    assert mine["round_trips"] == api["round_trips"] and len(api["round_trips"]) > 3
    assert mine["metrics"] == api["metrics"]
    for key in ("avg_trade_pct", "exposure_pct", "calmar_ratio", "max_drawdown_duration_bars", "open_trades"):
        assert key in api["metrics"]


# ---------------------------------------------------------------- Punto 33
def test_tradetracer_five_trades():
    s = round_trip_stats(_pct_trades([2, 4, 6, -1, -15]))
    assert s["round_trips"] == 5
    # expectancy = 3/5 · 4 − 2/5 · 8
    assert s["expectancy_pct"] == pytest.approx(-0.8, abs=1e-9)
    # accuracy per entità = 12 / (12 + 16); per frequenza = 3 / 5
    assert s["accuracy_magnitude_pct"] == pytest.approx(42.86, abs=0.005)
    assert s["accuracy_frequency_pct"] == pytest.approx(60.0) == s["win_rate_pct"]
    assert s["max_consecutive_wins"] == 3 and s["max_consecutive_losses"] == 2
    assert s["best_trade_pct"] == pytest.approx(6.0) and s["worst_trade_pct"] == pytest.approx(-15.0)
    # guadagno mediano 4; perdite di coda (>= 12): solo −15 -> 15/4 · 1/5
    assert s["fragility_ratio"] == pytest.approx(0.75, abs=1e-9)
    # Sono calcolate sui netti: con le commissioni l'expectancy scende di circa due commissioni.
    net = round_trip_stats(_pct_trades([2, 4, 6, -1, -15]), fee_bps=10)
    assert net["expectancy_pct"] == pytest.approx(-0.8 - 0.2, abs=0.005)
    assert net["best_trade_pct"] < 6.0 and net["worst_trade_pct"] < -15.0
    assert net["expectancy_pct"] == pytest.approx(net["avg_trade_pct"], abs=0.006)


def test_fragility_zero_without_tail():
    # Guadagno mediano 4: una perdita è di coda solo da 12 in su.
    assert round_trip_stats(_pct_trades([2, 4, 6, -1, -11]))["fragility_ratio"] == 0.0
    assert round_trip_stats(_pct_trades([2, 4, 6]))["fragility_ratio"] == 0.0
    # Due perdite di coda: (massima / mediana) × (2 / 6).
    two = round_trip_stats(_pct_trades([2, 4, 6, -1, -12, -20]))
    assert two["fragility_ratio"] == pytest.approx(20 / 4 * 2 / 6, abs=1e-4)
    # Senza vincite il guadagno mediano non esiste; senza trade nessuna statistica.
    only_losses = round_trip_stats(_pct_trades([-1, -2]))
    assert only_losses["fragility_ratio"] is None and only_losses["accuracy_magnitude_pct"] == 0.0
    assert only_losses["expectancy_pct"] == pytest.approx(-1.5) and only_losses["max_consecutive_wins"] == 0
    none = round_trip_stats(EMPTY)
    for key in ("expectancy_pct", "accuracy_magnitude_pct", "accuracy_frequency_pct", "best_trade_pct",
                "worst_trade_pct", "fragility_ratio"):
        assert none[key] is None, key
    assert none["max_consecutive_wins"] == 0


def test_backtest_metrics_include_trade_quality():
    data = generate_mock_ohlcv("QUALITY", period="2y")
    res = MovingAverageCrossover(StrategyParams(short_window=5, long_window=20, fee_bps=10)).backtest(data)
    m = res.metrics
    closed = res.round_trips[res.round_trips["status"] == "closed"]["net_pct"]
    assert m["best_trade_pct"] == pytest.approx(closed.max(), abs=0.006)
    assert m["worst_trade_pct"] == pytest.approx(closed.min(), abs=0.006)
    assert m["expectancy_pct"] == pytest.approx(closed.mean(), abs=1e-3)
    assert m["accuracy_frequency_pct"] == m["win_rate_pct"]
