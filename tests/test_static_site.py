"""Sito statico per GitHub Pages: snapshot dei dati, API in Pyodide, costruzione del sito."""

import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import utils.data_fetcher as df_mod
from tools import build_static_site, data_snapshot
from utils.data_fetcher import generate_mock_ohlcv
from web import static_api


def _csv(symbol="SPY", period="5y") -> str:
    d = generate_mock_ohlcv(symbol, period, "1d")
    d.index.name = "date"
    return d.round(6).to_csv()


# --------------------------------------------------------------- Snapshot
def test_snapshot_writes_csv_and_index(tmp_path, monkeypatch):
    monkeypatch.setattr(df_mod, "fetch_yahoo", lambda s, p, i: generate_mock_ohlcv(s, "1y", i))
    assert data_snapshot.main(["--out", str(tmp_path), "--symbols", "SPY,BTC-USD"]) == 0
    index = json.loads((tmp_path / "index.json").read_text())
    assert set(index["symbols"]) == {"SPY", "BTC-USD"}
    assert index["symbols"]["SPY"]["ok"] is True and index["symbols"]["SPY"]["rows"] > 200
    df = pd.read_csv(tmp_path / "SPY.csv", index_col="date", parse_dates=True)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_snapshot_keeps_old_file_when_yahoo_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(df_mod, "fetch_yahoo", lambda s, p, i: generate_mock_ohlcv(s, "1y", i))
    data_snapshot.main(["--out", str(tmp_path), "--symbols", "SPY"])
    before = (tmp_path / "SPY.csv").read_text()

    def down(*_a, **_k):
        raise df_mod.DataFetchError("bloccato")

    monkeypatch.setattr(df_mod, "fetch_yahoo", down)
    assert data_snapshot.main(["--out", str(tmp_path), "--symbols", "SPY"]) == 1
    assert (tmp_path / "SPY.csv").read_text() == before
    entry = json.loads((tmp_path / "index.json").read_text())["symbols"]["SPY"]
    assert entry["ok"] is False and "bloccato" in entry["error"] and entry["rows"] > 200


def test_snapshot_skips_when_fresh_and_retries_after_a_failure(tmp_path, monkeypatch):
    calls = []

    def fake(s, p, i):
        calls.append(s)
        return generate_mock_ohlcv(s, "1y", i)

    monkeypatch.setattr(df_mod, "fetch_yahoo", fake)
    args = ["--out", str(tmp_path), "--symbols", "SPY,QQQ", "--if-older-than", "20"]
    assert data_snapshot.main(args) == 0 and calls == ["SPY", "QQQ"]
    assert data_snapshot.main(args) == 0 and calls == ["SPY", "QQQ"]          # recenti: niente rete
    # Un simbolo nuovo o un aggiornamento fallito fanno ripartire il giro.
    assert data_snapshot.main(args[:3] + ["SPY,QQQ,AAPL"] + args[4:]) == 0 and len(calls) == 5
    index_path = tmp_path / "index.json"
    index = json.loads(index_path.read_text())
    index["symbols"]["SPY"]["ok"] = False
    index_path.write_text(json.dumps(index))
    assert data_snapshot.main(args) == 0 and len(calls) == 7
    # Più vecchi della soglia: si aggiornano.
    index = json.loads(index_path.read_text())
    for e in index["symbols"].values():
        e["updated_at"] = "2020-01-01T00:00:00+00:00"
    index_path.write_text(json.dumps(index))
    assert data_snapshot.main(args) == 0 and len(calls) == 9


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "SPY;rm"])
def test_snapshot_rejects_unsafe_symbols(bad):
    with pytest.raises(ValueError):
        data_snapshot.file_name(bad)


# ----------------------------------------------------- API in Pyodide
@pytest.mark.parametrize("symbol", ["SPY", "BTC-USD"])
def test_static_backtest_matches_the_api_on_mock_data(symbol):
    import main

    q = {"symbol": symbol, "short_window": "10", "long_window": "30", "period": "2y"}
    with TestClient(main.app) as client:
        api = client.get("/api/backtest", params=q).json()
        api_costs = client.get("/api/backtest/costs", params=q).json()
    mine = static_api.backtest(q, None)
    assert mine["metrics"] == api["metrics"]
    assert mine["equity_curve"] == api["equity_curve"] and mine["trades"] == api["trades"]
    assert mine["data_source"] == "mock"
    assert static_api.costs(q, None)["rows"] == api_costs["rows"]


def test_static_backtest_uses_the_snapshot_and_slices_the_period():
    csv = _csv("SPY", "5y")
    out = static_api.backtest({"symbol": "SPY", "short_window": "20", "long_window": "50", "period": "1y"}, csv)
    assert out["data_source"] == "snapshot"
    first, last = pd.Timestamp(out["equity_curve"][0]["date"]), pd.Timestamp(out["equity_curve"][-1]["date"])
    assert pd.Timedelta(days=360) <= last - first <= pd.Timedelta(days=366)
    full = static_api.backtest({"symbol": "SPY", "short_window": "20", "long_window": "50", "period": "max"}, csv)
    assert len(full["equity_curve"]) > len(out["equity_curve"])


@pytest.mark.parametrize("q", [
    {"short_window": "50", "long_window": "20"},
    {"short_window": "0"},
    {"period": "3w"},
    {"symbol": ""},
])
def test_static_backtest_validates_like_the_api(q):
    params = {"symbol": "SPY", "short_window": "20", "long_window": "50", "period": "2y", **q}
    with pytest.raises(ValueError):
        static_api.backtest(params, None)


# ------------------------------------------------------- Costruzione del sito
def test_build_static_site(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "status.json").write_text('{"running": true}')
    (state / "notes.apk").write_text("non deve finire nel sito")
    data = tmp_path / "data"
    data.mkdir()
    (data / "SPY.csv").write_text(_csv())
    out = tmp_path / "_site"
    assert build_static_site.main(["--out", str(out), "--state", str(state), "--data", str(data),
                                   "--actions-url", "https://example.org/actions"]) == 0
    assert (out / "index.html").read_text().lower().startswith("<!doctype html>")
    config = (out / "config.js").read_text()
    assert "window.QTS_STATIC" in config and "https://example.org/actions" in config
    assert "QTS_API" not in config
    manifest = json.loads((out / "py" / "manifest.json").read_text())
    assert "web/static_api.py" in manifest["files"] and "strategy/quant_model.py" in manifest["files"]
    for f in manifest["files"]:
        assert (out / "py" / f).is_file(), f
    assert manifest["packages"] == ["numpy", "pandas"]
    assert (out / "state" / "status.json").exists() and not (out / "state" / "notes.apk").exists()
    assert (out / "data" / "SPY.csv").exists()


def test_static_modules_do_not_import_server_only_packages():
    # In Pyodide non ci sono fastapi, yfinance e uvicorn: i moduli del sito non devono importarli.
    import ast
    from pathlib import Path

    root = Path(build_static_site.ROOT)
    for f in build_static_site.PY_FILES:
        tree = ast.parse((root / f).read_text(encoding="utf-8"))
        top = {a.name.split(".")[0] for n in tree.body if isinstance(n, ast.Import) for a in n.names}
        top |= {n.module.split(".")[0] for n in tree.body if isinstance(n, ast.ImportFrom) and n.module}
        assert not top & {"fastapi", "yfinance", "uvicorn", "requests", "httpx"}, (f, top)
