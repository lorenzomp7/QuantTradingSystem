from fastapi.testclient import TestClient

import main

AUTH = {"X-API-Key": "test-key"}


def test_health_and_dashboard():
    with TestClient(main.app) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert "QuantTradingSystem" in client.get("/").text


def test_control_requires_api_key():
    with TestClient(main.app) as client:
        assert client.post("/api/bot/start").status_code == 401
        assert client.post("/api/bot/run-once", headers={"X-API-Key": "wrong"}).status_code == 401


def test_run_once_and_status():
    with TestClient(main.app) as client:
        r = client.post("/api/bot/run-once", headers=AUTH)
        assert r.status_code == 200, r.text
        assert r.json()["action"] in {"BUY", "SELL", "HOLD"}
        status = client.get("/api/status").json()
        assert status["cycles"] >= 1
        assert status["last_data_source_used"] == "mock"
        assert len(client.get("/api/signals").json()) >= 1


def test_start_stop():
    with TestClient(main.app) as client:
        assert client.post("/api/bot/start", headers=AUTH).json()["started"] is True
        assert client.get("/health").json()["bot_running"] is True
        assert client.post("/api/bot/stop", headers=AUTH).json()["stopped"] is True


def test_backtest_endpoint():
    with TestClient(main.app) as client:
        r = client.get("/api/backtest", params={"symbol": "SPY", "short_window": 10, "long_window": 30})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["metrics"]["strategy"] == "moving_average_crossover"
        assert body["equity_curve"]
        bad = client.get("/api/backtest", params={"short_window": 60, "long_window": 30})
        assert bad.status_code == 422


def test_logs_endpoint():
    with TestClient(main.app) as client:
        logs = client.get("/api/logs", params={"limit": 10}).json()
        assert isinstance(logs, list) and logs


def test_buy_sizing_works_with_expensive_asset():
    # Con BTC a ~100k e 10k di capitale una quantità fissa di 10 fallirebbe:
    # il sizing percentuale compra una frazione senza superare la liquidità.
    from core.engine import TradingEngine
    from config import Settings
    from execution.broker import PaperBroker

    eng = TradingEngine(Settings(), broker=PaperBroker(initial_cash=10_000, fee_bps=5))
    qty = eng._buy_quantity(100_000.0)
    assert 0 < qty < 0.1
    eng.broker.submit_order("BTC-USD", "BUY", qty, 100_000.0)
    assert eng.broker.get_account()["cash"] >= 0
