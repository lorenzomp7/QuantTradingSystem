"""Bot a ciclo singolo per GitHub Actions: stato persistente tra un'esecuzione e l'altra."""

import json

import pytest

import utils.data_fetcher as df_mod
from execution.broker import STATUS_REJECTED, PaperBroker
from execution.reconcile import reconcile_broker
from tools import bot_once


# --------------------------------------------------- Stato del broker
def test_paper_broker_state_round_trip():
    b = PaperBroker(initial_cash=10_000, fee_bps=7)
    b.submit_order("BTC-USD", "BUY", 0.05, 60_000)
    b.submit_order("BTC-USD", "SELL", 0.02, 62_000)
    with pytest.raises(ValueError):
        b.submit_order("BTC-USD", "BUY", 100, 60_000)
    b.mark_price("BTC-USD", 61_000)
    state = json.loads(json.dumps(b.to_state()))          # passa da JSON come su disco
    c = PaperBroker.from_state(state)
    assert c.get_account() == b.get_account()
    assert c.rejected_count == 1 and c.rejected_orders[0].status == STATUS_REJECTED
    assert reconcile_broker(c).ok
    c.submit_order("BTC-USD", "SELL", 0.03, 63_000)        # il registro continua
    assert reconcile_broker(c).ok and c.get_position("BTC-USD") == pytest.approx(0, abs=1e-12)


def test_from_state_rejects_inconsistent_ledger():
    b = PaperBroker(initial_cash=1_000, fee_bps=0)
    b.submit_order("X", "BUY", 1, 100)
    state = b.to_state()
    state["cash"] = 5_000                                  # cassa che non torna con gli ordini
    with pytest.raises(ValueError, match="cash"):
        PaperBroker.from_state(state)


@pytest.mark.parametrize("change", [
    lambda st: st.update(version=2),
    lambda st: st["orders"][0].update(quantity=-1),                    # coerente ma senza senso
    lambda st: st["orders"][0].update(side="HOLD"),
    lambda st: st["last_prices"].update(X=-5),
])
def test_from_state_rejects_invalid_values(change):
    b = PaperBroker(initial_cash=1_000, fee_bps=0)
    b.submit_order("X", "BUY", 1, 100)
    b.mark_price("X", 101)
    state = json.loads(json.dumps(b.to_state()))
    change(state)
    with pytest.raises(ValueError):
        PaperBroker.from_state(state)


# ------------------------------------------------------ Ciclo singolo
@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_SOURCE", "mock")
    monkeypatch.setenv("SYMBOL", "BTC-USD")
    monkeypatch.setenv("DATA_INTERVAL", "1h")
    monkeypatch.setenv("DATA_PERIOD", "60d")
    monkeypatch.setenv("INITIAL_CAPITAL", "10000")
    return tmp_path / "state"


def test_first_run_creates_state(env):
    assert bot_once.main(["--state-dir", str(env)]) == 0
    status = json.loads((env / "status.json").read_text())
    assert status["mode"] == "github-actions" and status["cycles"] == 1
    assert status["running"] is True and status["symbol"] == "BTC-USD"
    assert status["last_signal"]["action"] in {"BUY", "SELL", "HOLD"}
    assert (env / "broker.json").exists()
    logs = json.loads((env / "logs.json").read_text())
    assert logs and {"timestamp", "level", "message"} <= set(logs[0])
    assert (env / "equity.csv").read_text().count("\n") == 2  # intestazione + una riga


def test_state_carries_over_between_runs(env):
    for _ in range(3):
        assert bot_once.main(["--state-dir", str(env)]) == 0
    status = json.loads((env / "status.json").read_text())
    assert status["cycles"] == 3
    first = status["started_at"]
    bot_once.main(["--state-dir", str(env)])
    assert json.loads((env / "status.json").read_text())["started_at"] == first
    assert (env / "equity.csv").read_text().count("\n") == 5
    broker = PaperBroker.from_state(json.loads((env / "broker.json").read_text()))
    assert reconcile_broker(broker).ok


def test_logs_are_bounded(env, monkeypatch):
    monkeypatch.setattr(bot_once, "MAX_LOGS", 5)
    for _ in range(4):
        bot_once.main(["--state-dir", str(env)])
    assert len(json.loads((env / "logs.json").read_text())) == 5


def test_disabled_bot_skips_the_cycle(env, monkeypatch):
    bot_once.main(["--state-dir", str(env)])
    monkeypatch.setenv("BOT_ENABLED", "false")
    assert bot_once.main(["--state-dir", str(env)]) == 0
    status = json.loads((env / "status.json").read_text())
    assert status["running"] is False and status["cycles"] == 1


def test_mock_fallback_blocks_orders_and_is_recorded(env, monkeypatch):
    monkeypatch.setenv("DATA_SOURCE", "yahoo")
    monkeypatch.setattr(df_mod, "fetch_yahoo", lambda *a, **k: (_ for _ in ()).throw(df_mod.DataFetchError("no")))
    assert bot_once.main(["--state-dir", str(env)]) == 0
    status = json.loads((env / "status.json").read_text())
    assert status["data"]["fallback"] is True and status["last_signal"]["order"] is None
    assert "mock" in status["data"]["orders_blocked"]


def test_cycle_error_exits_1_but_saves_state(env, monkeypatch):
    bot_once.main(["--state-dir", str(env)])
    from core.engine import TradingEngine

    monkeypatch.setattr(TradingEngine, "run_cycle", lambda self: (_ for _ in ()).throw(RuntimeError("guasto")))
    assert bot_once.main(["--state-dir", str(env)]) == 1
    status = json.loads((env / "status.json").read_text())
    assert "guasto" in status["last_error"] and status["cycles"] == 1


def _open_position(env, qty=0.1, price=60_000.0):
    env.mkdir(parents=True, exist_ok=True)
    b = PaperBroker(initial_cash=10_000, fee_bps=5)
    b.submit_order("BTC-USD", "BUY", qty, price)
    b.mark_price("BTC-USD", price)
    (env / "broker.json").write_text(json.dumps(b.to_state()))
    return b.get_account()


def test_fallback_does_not_mark_the_account_at_mock_prices(env, monkeypatch):
    # Con Yahoo giù il prezzo è quello della serie mock (~100): il conto non deve cambiare valore.
    before = _open_position(env)
    monkeypatch.setenv("DATA_SOURCE", "yahoo")
    monkeypatch.setattr(df_mod, "fetch_yahoo", lambda *a, **k: (_ for _ in ()).throw(df_mod.DataFetchError("no")))
    assert bot_once.main(["--state-dir", str(env)]) == 0
    broker = json.loads((env / "broker.json").read_text())
    assert broker["last_prices"] == {"BTC-USD": 60_000.0}
    status = json.loads((env / "status.json").read_text())
    assert status["account"]["equity"] == before["equity"]
    assert not (env / "equity.csv").exists()          # nessuna riga con un prezzo finto


@pytest.mark.parametrize("damage", ["tamper", "status", "broker"])
def test_corrupted_state_is_reported_and_not_reset(env, damage):
    assert bot_once.main(["--state-dir", str(env)]) == 0
    broker_before = (env / "broker.json").read_text()
    if damage == "tamper":
        st = json.loads(broker_before); st["cash"] += 100
        (env / "broker.json").write_text(json.dumps(st))
        broker_before = (env / "broker.json").read_text()
    elif damage == "status":
        (env / "status.json").write_text("{non è json")
    else:
        (env / "broker.json").write_text("{rotto")
        broker_before = "{rotto"
    assert bot_once.main(["--state-dir", str(env)]) == 1
    assert (env / "broker.json").read_text() == broker_before    # il conto non riparte da zero
    status = json.loads((env / "status.json").read_text())
    assert "Stato salvato non valido" in status["last_error"] and status["running"] is False
    logs = json.loads((env / "logs.json").read_text())
    assert any("Stato salvato non valido" in l["message"] for l in logs)
