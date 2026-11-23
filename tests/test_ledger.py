"""Riconciliazione indipendente del PaperBroker (integrazione QTS 3)."""

from dataclasses import replace

import pytest

from config import Settings
from core.engine import TradingEngine
from execution.broker import (
    ORDER_STATUSES,
    STATUS_FILLED,
    STATUS_REJECTED,
    Order,
    OrderRejected,
    PaperBroker,
)
from execution.reconcile import reconcile, reconcile_broker
from utils.data_fetcher import MarketDataFetcher


def _trade_sequence(broker: PaperBroker) -> None:
    broker.submit_order("BTC-USD", "BUY", 0.05, 60_000)
    broker.submit_order("BTC-USD", "BUY", 0.02, 62_500.5)
    broker.submit_order("SPY", "BUY", 3, 450.25)
    broker.submit_order("BTC-USD", "SELL", 0.03, 64_000)
    broker.submit_order("SPY", "SELL", 3, 440.10)
    broker.submit_order("BTC-USD", "SELL", 1.0, 61_000)  # chiede più del posseduto: vende 0.04
    broker.submit_order("ETH-USD", "BUY", 0.4, 3_000)
    broker.mark_price("ETH-USD", 3_150)


def test_reconcile_matches_paper_broker():
    broker = PaperBroker(initial_cash=10_000, fee_bps=7.5)
    _trade_sequence(broker)
    report = reconcile_broker(broker)
    assert report.issues == [], report.issues
    assert report.ok
    # Valori ricalcolati a mano: posizione residua solo su ETH.
    assert report.positions == {"ETH-USD": pytest.approx(0.4)}
    assert report.avg_cost["ETH-USD"] == pytest.approx(3_000 * (1 + 7.5 / 10_000))  # commissione inclusa
    assert report.market_value == pytest.approx(0.4 * 3_150)


def test_reconcile_by_hand_small_case():
    fills = [
        {"symbol": "X", "side": "BUY", "quantity": 10, "price": 100, "status": "filled"},
        {"symbol": "X", "side": "SELL", "quantity": 4, "price": 110, "status": "filled"},
        {"symbol": "X", "side": "BUY", "quantity": 5, "price": 1_000, "status": "rejected"},
    ]
    r = reconcile(1_000, fee_bps=10, orders=fills, last_prices={"X": 120})
    # BUY: 1000 + 1 di commissione; SELL: +440 - 0,44.
    assert r.fees == pytest.approx(1.44)
    assert r.cash == pytest.approx(1_000 - 1_001 + 439.56)
    assert r.positions == {"X": pytest.approx(6)}
    assert r.avg_cost["X"] == pytest.approx(100.1)   # costo medio commissione inclusa
    assert r.realized_pnl == pytest.approx(4 * 110 - 0.44 - 4 * 100.1)
    assert r.equity == pytest.approx(r.cash + 6 * 120)
    assert r.pnl == pytest.approx(r.equity - 1_000)


class _WrongFeeBroker(PaperBroker):
    """Errore inserito apposta: commissione sulle vendite non addebitata."""

    def submit_order(self, symbol, side, quantity, price):
        order = super().submit_order(symbol, side, quantity, price)
        if order.side == "SELL":
            self.cash += order.quantity * order.price * self.fee_bps / 10_000
        return order


def test_reconcile_catches_wrong_fee_with_exact_field():
    broker = _WrongFeeBroker(initial_cash=10_000, fee_bps=10)
    _trade_sequence(broker)
    report = reconcile_broker(broker)
    assert not report.ok
    fields = {i["field"] for i in report.issues}
    assert "cash" in fields and "equity" in fields and "pnl" in fields
    assert "positions" not in fields  # le quantità tornano: l'errore è solo nei soldi


class _WrongQuantityBroker(PaperBroker):
    def submit_order(self, symbol, side, quantity, price):
        order = super().submit_order(symbol, side, quantity, price)
        self.positions[symbol] += 0.001 if side == "BUY" else 0.0
        return order


def test_reconcile_catches_wrong_position():
    broker = _WrongQuantityBroker(initial_cash=10_000, fee_bps=0)
    broker.submit_order("X", "BUY", 1, 100)
    fields = {i["field"] for i in reconcile_broker(broker).issues}
    assert "positions.X" in fields


# ------------------------------------------------------------ Stati ordine
def test_order_status_is_validated():
    assert {"new", "open", "partially_filled", "filled", "canceled", "rejected", "pending_cancel"} == set(ORDER_STATUSES)
    with pytest.raises(ValueError):
        Order(symbol="X", side="BUY", quantity=1, price=1, status="held")


def test_insufficient_cash_records_rejected_order():
    broker = PaperBroker(initial_cash=100, fee_bps=0)
    with pytest.raises(OrderRejected) as info:
        broker.submit_order("X", "BUY", 10, 50)
    assert isinstance(info.value, ValueError)  # compatibile con chi intercetta ValueError
    rejected = info.value.order
    assert rejected.status == STATUS_REJECTED and "Liquidità" in rejected.reason
    assert broker.orders == [] and list(broker.rejected_orders) == [rejected]
    assert broker.get_account()["order_counts"] == {"filled": 0, "rejected": 1}
    assert broker.cash == 100 and broker.get_position("X") == 0
    assert reconcile_broker(broker).ok  # gli ordini rifiutati non muovono il registro


def test_filled_orders_have_filled_status():
    broker = PaperBroker(initial_cash=1_000, fee_bps=0)
    order = broker.submit_order("X", "BUY", 1, 100)
    assert order.status == STATUS_FILLED and order.reason is None


def test_engine_reports_rejected_order_as_hold(monkeypatch):
    cfg = replace(Settings(), data_source="mock", trade_quantity=10_000.0)
    eng = TradingEngine(cfg, fetcher=MarketDataFetcher(source="mock"),
                        broker=PaperBroker(initial_cash=100, fee_bps=0))
    monkeypatch.setattr(eng.strategy, "latest_signal",
                        lambda data: {"timestamp": "t", "close": 1.0, "signal": 1, "action": "BUY"})
    sig = eng.run_cycle()
    assert sig["action"] == "HOLD"
    assert sig["order"]["status"] == STATUS_REJECTED
    assert eng.last_error is None


def test_rejected_orders_are_bounded_and_counted():
    broker = PaperBroker(initial_cash=100, fee_bps=0)
    broker.submit_order("X", "BUY", 1, 50)
    for _ in range(300):
        with pytest.raises(OrderRejected):
            broker.submit_order("X", "BUY", 10, 50)
    acct = broker.get_account()
    assert acct["order_counts"] == {"filled": 1, "rejected": 300}
    assert len(broker.rejected_orders) == broker.rejected_orders.maxlen
    assert [o["status"] for o in acct["orders"]] == ["filled"]


def test_engine_does_not_repeat_the_same_rejection(monkeypatch):
    cfg = replace(Settings(), data_source="mock", trade_quantity=10_000.0)
    eng = TradingEngine(cfg, fetcher=MarketDataFetcher(source="mock"),
                        broker=PaperBroker(initial_cash=100, fee_bps=0))
    monkeypatch.setattr(eng.strategy, "latest_signal",
                        lambda data: {"timestamp": "t", "close": 1.0, "signal": 1, "action": "BUY"})
    for _ in range(5):
        eng.run_cycle()
    assert eng.broker.get_account()["order_counts"]["rejected"] == 1
    assert eng.last_signal["action"] == "HOLD"
    eng.broker.cash = 1e12                       # la cassa cambia: si riprova
    eng.run_cycle()
    assert eng.broker.get_account()["order_counts"]["filled"] == 1
