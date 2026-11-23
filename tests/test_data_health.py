"""Salute delle fonti dati: provenienza, blocco degli ordini e controlli (integrazione QTS 2)."""

import json
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import utils.data_fetcher as df_mod
from config import Settings
from core.engine import TradingEngine
from execution.broker import PaperBroker
from utils import data_health
from utils.data_fetcher import MarketDataFetcher, generate_mock_ohlcv


def _yahoo_down(*_args, **_kwargs):
    raise df_mod.DataFetchError("Yahoo bloccato nel test")


def _engine(source: str) -> TradingEngine:
    cfg = replace(Settings(), data_source=source, symbol="BTC-USD", data_interval="5m", data_period="5d")
    return TradingEngine(cfg, fetcher=MarketDataFetcher(source=source),
                         broker=PaperBroker(initial_cash=10_000, fee_bps=5))


# ------------------------------------------------------------ Provenienza
def test_fetcher_reports_fallback(monkeypatch):
    monkeypatch.setattr(df_mod, "fetch_yahoo", _yahoo_down)
    f = MarketDataFetcher(source="yahoo")
    data, info = f.get_ohlcv_with_info("BTC-USD", "5d", "5m")
    assert len(data) > 0
    assert info.source == "mock" and info.requested_source == "yahoo"
    assert info.fallback is True
    assert any("Yahoo" in w for w in info.warnings)
    assert info.last_bar == data.index[-1].isoformat()
    assert f.last_info is info
    # Dalla cache arriva la stessa provenienza.
    _, cached = f.get_ohlcv_with_info("BTC-USD", "5d", "5m")
    assert cached.fallback is True


def test_fetcher_reports_explicit_mock_without_fallback():
    _, info = MarketDataFetcher(source="mock").get_ohlcv_with_info("SPY", "1y", "1d")
    assert info.source == "mock" and info.fallback is False


# ------------------------------------------------ Bot: nessun ordine
def test_bot_sends_no_orders_on_mock_fallback(monkeypatch):
    monkeypatch.setattr(df_mod, "fetch_yahoo", _yahoo_down)
    eng = _engine("yahoo")
    for _ in range(3):
        eng.fetcher.clear_cache()
        sig = eng.run_cycle()
        assert sig["action"] == "HOLD" and sig["order"] is None
        assert "mock" in sig["orders_blocked"]
    assert eng.broker.orders == []
    assert eng.status()["data"]["fallback"] is True


def test_bot_sends_no_orders_on_stale_data(monkeypatch):
    old = generate_mock_ohlcv("BTC-USD", "5d", "5m")
    old.index = old.index - pd.Timedelta(days=3)
    monkeypatch.setattr(df_mod, "fetch_yahoo", lambda *a, **k: old.copy())
    eng = _engine("yahoo")
    sig = eng.run_cycle()
    assert sig["order"] is None and "vecchi" in sig["orders_blocked"]
    assert eng.broker.orders == []


def test_bot_trades_with_explicit_mock():
    eng = _engine("mock")
    sig = eng.run_cycle()
    assert sig["orders_blocked"] is None


# --------------------------------------------------------- Controllo frame
def _frame(n=30, end=None, freq="D"):
    idx = pd.date_range(end=end or pd.Timestamp.now().normalize(), periods=n, freq=freq)
    close = np.linspace(100, 110, n)
    return pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                         "close": close, "volume": 1.0}, index=idx)


def test_check_frame_reasons():
    now = pd.Timestamp.now()
    assert data_health.check_frame(_frame(), "1d", now)["status"] == "healthy"
    assert data_health.check_frame(pd.DataFrame(), "1d", now)["reason"] == "empty_frame"
    bad = _frame()
    bad.iloc[3, bad.columns.get_loc("high")] = 0.5
    assert data_health.check_frame(bad, "1d", now)["reason"] in {"invalid_prices_or_volume", "ohlc_order"}
    nan = _frame()
    nan.iloc[2, 0] = np.nan
    assert data_health.check_frame(nan, "1d", now)["reason"] == "nonfinite_values"
    stale = _frame(end=now.normalize() - pd.Timedelta(days=30))
    assert data_health.check_frame(stale, "1d", now)["status"] == "stale"
    future = _frame(end=now.normalize() + pd.Timedelta(days=5))
    assert data_health.check_frame(future, "1d", now)["reason"] == "future_bar"


def test_command_fails_when_yahoo_is_down(monkeypatch, capsys):
    monkeypatch.setattr(df_mod, "fetch_yahoo", _yahoo_down)
    code = data_health.main(["--inline"])
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report["ok"] is False
    assert {r["symbol"] for r in report["sources"]} == set(data_health.CANARY_SYMBOLS)
    assert all(r["status"] == "error" for r in report["sources"])


def test_command_passes_with_healthy_data(monkeypatch, capsys):
    def healthy(symbol, period, interval):
        if interval == "1h":
            return _frame(n=48, end=pd.Timestamp.now(tz="UTC").tz_localize(None).floor("h"), freq="h")
        return _frame()

    monkeypatch.setattr(df_mod, "fetch_yahoo", healthy)
    assert data_health.main(["--inline"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


# ---------------------------------------------------------------- API
def test_api_data_health_reports_mock(monkeypatch):
    import main

    monkeypatch.setattr(df_mod, "fetch_yahoo", _yahoo_down)
    monkeypatch.setattr(main.engine.fetcher, "source", "yahoo")
    main.engine.fetcher.clear_cache()
    try:
        with TestClient(main.app) as client:
            r = client.get("/api/data-health")
            assert r.status_code == 503
            body = r.json()
            assert body["source"] == "mock" and body["fallback"] is True and body["ok"] is False
            run = client.post("/api/bot/run-once", headers={"X-API-Key": "test-key"}).json()
            assert run["order"] is None
    finally:
        main.engine.fetcher.clear_cache()


def test_api_data_health_ok_with_mock_source():
    import main

    with TestClient(main.app) as client:
        r = client.get("/api/data-health")
        assert r.status_code == 200
        assert r.json()["source"] == "mock" and r.json()["ok"] is True
