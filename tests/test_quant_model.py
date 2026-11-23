import numpy as np
import pandas as pd
import pytest

from execution.broker import PaperBroker
from strategy.quant_model import MovingAverageCrossover, StrategyParams
from utils.data_fetcher import MarketDataFetcher, generate_mock_ohlcv, normalize_ohlcv


@pytest.fixture
def data() -> pd.DataFrame:
    return generate_mock_ohlcv("TEST", period="2y")


def test_mock_data_is_deterministic_and_consistent(data):
    again = generate_mock_ohlcv("TEST", period="2y")
    pd.testing.assert_frame_equal(data, again)
    assert list(data.columns) == ["open", "high", "low", "close", "volume"]
    assert (data["high"] >= data[["open", "close"]].max(axis=1)).all()
    assert (data["low"] <= data[["open", "close"]].min(axis=1)).all()


def test_normalize_handles_yfinance_multiindex(data):
    raw = data.rename(columns=str.title)
    raw.columns = pd.MultiIndex.from_product([raw.columns, ["SPY"]])
    out = normalize_ohlcv(raw)
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]


def test_signals_match_sma_definition(data):
    strat = MovingAverageCrossover(StrategyParams(short_window=5, long_window=20))
    sig = strat.generate_signals(data)
    expected_short = data["close"].rolling(5).mean()
    np.testing.assert_allclose(sig["sma_short"].dropna(), expected_short.dropna())
    # Nessun segnale finché la SMA lunga non è definita.
    assert (sig["signal"].iloc[:19] == 0).all()
    assert set(sig["position"].unique()) <= {-1, 0, 1}


def test_backtest_has_no_lookahead():
    # Prezzo che sale sempre: la prima barra con signal=1 non deve guadagnare.
    idx = pd.date_range("2024-01-01", periods=60, freq="B")
    close = pd.Series(np.linspace(100, 160, 60), index=idx)
    df = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0})
    res = MovingAverageCrossover(StrategyParams(short_window=3, long_window=10, fee_bps=0)).backtest(df)
    first_long = res.signals.index[res.signals["signal"] == 1][0]
    assert res.equity_curve.loc[first_long] == pytest.approx(10_000.0)
    assert res.metrics["total_return_pct"] > 0


def test_backtest_metrics(data):
    res = MovingAverageCrossover().backtest(data)
    m = res.metrics
    assert m["bars"] == len(data)
    assert m["max_drawdown_pct"] <= 0
    assert m["num_trades"] == len(res.trades)
    assert "equity_curve" in res.to_dict()


def test_invalid_params():
    with pytest.raises(ValueError):
        StrategyParams(short_window=50, long_window=20)


def test_insufficient_data(data):
    with pytest.raises(ValueError):
        MovingAverageCrossover().generate_signals(data.head(10))


def test_fetcher_cache():
    f = MarketDataFetcher(source="mock")
    a = f.get_ohlcv("ABC", "1y")
    b = f.get_ohlcv("ABC", "1y")
    pd.testing.assert_frame_equal(a, b)
    assert f.last_source_used == "mock"


def test_paper_broker_round_trip():
    b = PaperBroker(initial_cash=1_000, fee_bps=0)
    b.submit_order("X", "BUY", 5, 100)
    assert b.get_position("X") == 5
    b.submit_order("X", "SELL", 5, 110)
    acct = b.get_account()
    assert acct["cash"] == pytest.approx(1_050)
    with pytest.raises(ValueError):
        b.submit_order("X", "SELL", 1, 110)


def test_clamp_period_for_intraday():
    from utils.data_fetcher import clamp_period

    assert clamp_period("2y", "5m") == "60d"
    assert clamp_period("5d", "5m") == "5d"
    assert clamp_period("2y", "1d") == "2y"
    assert clamp_period("1mo", "1m") == "7d"


def test_mock_intraday_bars():
    df = generate_mock_ohlcv("BTC-USD", period="5d", interval="5m")
    assert len(df) == 5 * 288
    assert (df.index.to_series().diff().dropna() == pd.Timedelta(minutes=5)).all()


@pytest.mark.parametrize("tz", ["America/New_York", "Asia/Tokyo", "UTC"])
def test_mock_ends_now_in_utc_whatever_the_local_timezone(monkeypatch, tz):
    # La serie mock si confronta con l'ora UTC (età dei dati): deve finire adesso in UTC.
    import time

    from utils.data_fetcher import utc_now

    monkeypatch.setenv("TZ", tz)
    time.tzset()
    try:
        df = generate_mock_ohlcv("BTC-USD", period="5d", interval="5m")
        age = utc_now() - df.index[-1]
        assert pd.Timedelta(0) <= age <= pd.Timedelta(minutes=10)
    finally:
        monkeypatch.undo()
        time.tzset()


def test_mock_daily_calendar_follows_market():
    btc = generate_mock_ohlcv("BTC-USD", period="2y", interval="1d")
    spy = generate_mock_ohlcv("SPY", period="2y", interval="1d")
    assert (btc.index.to_series().diff().dropna() == pd.Timedelta(days=1)).all()   # 24/7
    assert len(btc) == 730 and len(spy) == 504
    assert spy.index.dayofweek.max() <= 4                                          # giorni feriali
