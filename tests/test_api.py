from pathlib import Path

import pytest
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


def test_backtest_endpoint_annualizes_by_market():
    # BTC è aperto 24/7: 365 barre giornaliere l'anno; SPY segue la borsa: 252.
    with TestClient(main.app) as client:
        btc = client.get("/api/backtest", params={"symbol": "BTC-USD"}).json()["metrics"]
        spy = client.get("/api/backtest", params={"symbol": "SPY"}).json()["metrics"]
        assert btc["bars_per_year"] == 365
        assert spy["bars_per_year"] == 252


def test_dashboard_on_github_pages_can_call_the_api():
    """La dashboard su GitHub Pages è su un'altra origine: servono CORS e il preflight per X-API-Key."""
    pages = main.settings.cors_origins[0]   # da config: non dipende da un .env locale
    with TestClient(main.app) as client:
        r = client.get("/api/status", headers={"Origin": pages})
        assert r.headers["access-control-allow-origin"] == pages
        pre = client.options("/api/bot/start", headers={
            "Origin": pages, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-api-key"})
        assert pre.status_code == 200
        assert "x-api-key" in pre.headers["access-control-allow-headers"].lower()
        other = client.get("/api/status", headers={"Origin": "https://example.com"})
        assert "access-control-allow-origin" not in other.headers
        cfg = client.get("/config.js")
        assert cfg.status_code == 200 and "javascript" in cfg.headers["content-type"]
        assert "QTS_API" not in cfg.text


def test_dashboard_calls_go_through_the_api_address():
    # Su GitHub Pages un percorso relativo ("/api/...") andrebbe a github.io, non alla VM.
    import re

    html = (Path(main.__file__).parent / "templates" / "dashboard.html").read_text(encoding="utf-8")
    calls = re.findall(r"fetch\(\s*([^,)]+)", html)
    assert calls, "nessuna chiamata fetch trovata"
    # L'unica eccezione è siteFile(): file del sito statico (stato del bot, moduli Python, snapshot).
    for arg in calls:
        assert arg.strip().startswith(("API_BASE", "`${API_BASE}", "new URL(name")), \
            f"fetch senza l'indirizzo dell'API: {arg}"
    assert html.count("new URL(name, document.baseURI") == 1
    # Pyodide tratta una globale `API` come il proprio runtime e non si avvia: va evitata.
    assert not re.search(r"\b(const|let|var)\s+API\b", html)
    for name in re.findall(r"siteFile\(\s*([^)]+)\)", html):
        assert name.strip().startswith(('"state/', '"py/', '"data/', "name")), f"file del sito inatteso: {name}"


@pytest.mark.parametrize("path", ["/api/backtest", "/api/backtest/costs", "/api/data-health"])
def test_unexpected_errors_return_502_with_cors(monkeypatch, path):
    # Su GitHub Pages un 500 senza header CORS arriva al browser solo come "Failed to fetch".
    def boom(*_a, **_k):
        raise RuntimeError("guasto inatteso")

    monkeypatch.setattr(main.engine.fetcher, "get_ohlcv", boom)
    monkeypatch.setattr(main.engine.fetcher, "get_ohlcv_with_info", boom)
    origin = main.settings.cors_origins[0]
    with TestClient(main.app, raise_server_exceptions=False) as client:
        r = client.get(path, headers={"Origin": origin})
        assert r.status_code == 502
        assert r.headers["access-control-allow-origin"] == origin
        assert "RuntimeError" in r.json()["detail"]
