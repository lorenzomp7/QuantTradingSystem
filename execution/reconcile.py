"""
execution/reconcile.py
======================
Riconciliazione indipendente del registro del PaperBroker.

Rifà da capo, con aritmetica `Decimal`, commissioni, cassa, posizioni, costo
medio e NAV partendo dal capitale iniziale e dagli ordini eseguiti, **senza**
usare il codice che li ha prodotti: così trova anche gli errori del broker.
Poi confronta campo per campo con `get_account()` e riporta i campi che non
tornano.

Principio ripreso da Vibe-Trading (HKUDS), `agent/src/tools/paper_trade/
reconcile.py` (commit 597d902, 23/11/2026), ridotto al broker long-only di QTS.

    Copyright (c) 2026 Vibe-Trading Contributors — MIT License
    https://github.com/HKUDS/Vibe-Trading/blob/main/LICENSE
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Mapping

# Il broker arrotonda i campi monetari del conto a 2 decimali.
MONEY_TOLERANCE = Decimal("0.01")
QTY_TOLERANCE = Decimal("1e-9")
_BPS = Decimal(10_000)


def _d(value: Any) -> Decimal:
    """Decimal da float passando per `repr`, senza la coda binaria del float."""
    return Decimal(repr(float(value)))


@dataclass
class Reconciliation:
    cash: float
    positions: dict[str, float]
    avg_cost: dict[str, float]
    fees: float
    realized_pnl: float
    market_value: float
    equity: float
    pnl: float
    issues: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def reconcile(
    initial_cash: float,
    fee_bps: float,
    orders: Iterable[Mapping[str, Any]],
    last_prices: Mapping[str, float] | None = None,
) -> Reconciliation:
    """
    Ricalcola il registro dagli ordini. Contano solo quelli con stato `filled`.

    Il costo medio include la commissione d'acquisto; l'utile realizzato di una
    vendita è ricavo netto meno quantità × costo medio.
    """
    fee_rate = _d(fee_bps) / _BPS
    cash = _d(initial_cash)
    qty: dict[str, Decimal] = {}
    cost: dict[str, Decimal] = {}     # costo totale della posizione aperta
    fees = realized = Decimal(0)

    for o in orders:
        if o.get("status", "filled") != "filled":
            continue
        sym, side = o["symbol"], str(o["side"]).upper()
        q, px = _d(o["quantity"]), _d(o["price"])
        notional = q * px
        fee = notional * fee_rate
        fees += fee
        held = qty.get(sym, Decimal(0))
        if side == "BUY":
            cash -= notional + fee
            qty[sym] = held + q
            cost[sym] = cost.get(sym, Decimal(0)) + notional + fee
        elif side == "SELL":
            avg = cost[sym] / held if held else Decimal(0)
            cash += notional - fee
            realized += notional - fee - q * avg
            qty[sym] = held - q
            cost[sym] = cost.get(sym, Decimal(0)) - q * avg
        else:
            raise ValueError(f"Side non valido nell'ordine {o!r}")

    prices = {s: _d(p) for s, p in (last_prices or {}).items()}
    open_pos = {s: q for s, q in qty.items() if abs(q) > QTY_TOLERANCE}
    market_value = sum((q * prices.get(s, Decimal(0)) for s, q in open_pos.items()), Decimal(0))
    equity = cash + market_value
    return Reconciliation(
        cash=float(cash),
        positions={s: float(q) for s, q in open_pos.items()},
        avg_cost={s: float(cost[s] / q) for s, q in open_pos.items()},
        fees=float(fees),
        realized_pnl=float(realized),
        market_value=float(market_value),
        equity=float(equity),
        pnl=float(equity - _d(initial_cash)),
    )


def compare_account(expected: Reconciliation, account: Mapping[str, Any]) -> list[dict]:
    """Confronta il ricalcolo con `get_account()`: un elemento per campo diverso."""
    issues: list[dict] = []
    for name in ("cash", "market_value", "equity", "pnl"):
        exp, act = _d(getattr(expected, name)), _d(account[name])
        if abs(exp - act) > MONEY_TOLERANCE:
            issues.append({"field": name, "expected": round(float(exp), 2), "actual": float(act)})
    actual_pos = {s: q for s, q in account.get("positions", {}).items() if q}
    for sym in sorted(set(expected.positions) | set(actual_pos)):
        exp, act = _d(expected.positions.get(sym, 0.0)), _d(actual_pos.get(sym, 0.0))
        if abs(exp - act) > QTY_TOLERANCE:
            issues.append({"field": f"positions.{sym}", "expected": float(exp), "actual": float(act)})
    return issues


def reconcile_broker(broker) -> Reconciliation:
    """Ricalcola il registro di un `PaperBroker` e lo confronta con il suo conto."""
    with broker._lock:
        orders = [o.to_dict() for o in broker.orders]
        prices = dict(broker.last_prices)
    result = reconcile(broker.initial_cash, broker.fee_bps, orders, prices)
    result.issues = compare_account(result, broker.get_account())
    return result
