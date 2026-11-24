"""
execution/broker.py
===================
Livello di esecuzione ordini.

* `PaperBroker`  -> broker simulato in memoria (default). Nessun denaro reale.
* `AlpacaBroker` -> placeholder per Alpaca (azioni USA, endpoint paper).
* `CcxtBroker`   -> placeholder per exchange crypto via ccxt (Binance, ecc.).

I placeholder mostrano dove e come collegare le API reali: le chiavi vengono
lette SOLO da variabili d'ambiente (mai committate su GitHub).
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from config import Settings
from utils.logger import get_logger

logger = get_logger(__name__)

# Stati dell'ordine, dall'elenco di Vibe-Trading (agent/src/tools/paper_trade/
# order_lifecycle.py, MIT), che usa i nomi di JoinQuant. Lì "filled" vuol dire
# eseguito in parte e "held" eseguito per intero: qui si tengono i nomi
# dell'uso comune, quindi "partially_filled" e "filled".
STATUS_NEW = "new"
STATUS_OPEN = "open"
STATUS_PARTIALLY_FILLED = "partially_filled"
STATUS_FILLED = "filled"
STATUS_CANCELED = "canceled"
STATUS_REJECTED = "rejected"
STATUS_PENDING_CANCEL = "pending_cancel"
ORDER_STATUSES = (
    STATUS_NEW, STATUS_OPEN, STATUS_PARTIALLY_FILLED, STATUS_FILLED,
    STATUS_CANCELED, STATUS_REJECTED, STATUS_PENDING_CANCEL,
)
TERMINAL_STATUSES = frozenset({STATUS_FILLED, STATUS_CANCELED, STATUS_REJECTED})


@dataclass
class Order:
    symbol: str
    side: str          # "BUY" | "SELL"
    quantity: float
    price: float
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    status: str = STATUS_FILLED
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reason: str | None = None   # motivo del rifiuto o dell'annullamento

    def __post_init__(self) -> None:
        if self.status not in ORDER_STATUSES:
            raise ValueError(f"Stato ordine non valido: {self.status!r} (attesi: {ORDER_STATUSES})")

    def to_dict(self) -> dict:
        return asdict(self)


class OrderRejected(ValueError):
    """Ordine rifiutato dal broker. L'ordine (stato `rejected`) è in `.order`."""

    def __init__(self, order: Order) -> None:
        super().__init__(order.reason or "Ordine rifiutato")
        self.order = order


class BaseBroker(ABC):
    name = "base"

    @abstractmethod
    def submit_order(self, symbol: str, side: str, quantity: float, price: float) -> Order: ...

    @abstractmethod
    def get_position(self, symbol: str) -> float: ...

    @abstractmethod
    def get_account(self) -> dict: ...


class PaperBroker(BaseBroker):
    """Broker simulato: esegue gli ordini istantaneamente al prezzo indicato."""

    name = "paper"

    def __init__(self, initial_cash: float = 10_000.0, fee_bps: float = 5.0) -> None:
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.fee_bps = fee_bps
        self.positions: dict[str, float] = {}
        self.last_prices: dict[str, float] = {}
        self.orders: list[Order] = []                      # ordini eseguiti (il registro)
        self.rejected_orders: deque[Order] = deque(maxlen=50)   # ultimi rifiutati, non nel registro
        self.rejected_count = 0
        self._lock = threading.Lock()

    def submit_order(self, symbol: str, side: str, quantity: float, price: float) -> Order:
        side = side.upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError(f"Side non valido: {side}")
        if quantity <= 0 or price <= 0:
            raise ValueError("quantity e price devono essere > 0")

        with self._lock:
            held = self.positions.get(symbol, 0.0)
            if side == "SELL":
                quantity = min(quantity, held)  # long-only: niente short
                if quantity <= 0:
                    raise ValueError(f"Nessuna posizione da vendere su {symbol}")

            notional = quantity * price
            fee = notional * self.fee_bps / 10_000
            if side == "BUY":
                if notional + fee > self.cash:
                    rejected = Order(
                        symbol=symbol, side=side, quantity=quantity, price=price,
                        status=STATUS_REJECTED,
                        reason=f"Liquidità insufficiente: servono {notional + fee:.2f}, disponibili {self.cash:.2f}",
                    )
                    self.rejected_orders.append(rejected)
                    self.rejected_count += 1
                    logger.warning("[PAPER] ordine rifiutato: %s", rejected.reason)
                    raise OrderRejected(rejected)
                self.cash -= notional + fee
                self.positions[symbol] = held + quantity
            else:
                self.cash += notional - fee
                self.positions[symbol] = held - quantity

            self.last_prices[symbol] = price
            order = Order(symbol=symbol, side=side, quantity=quantity, price=price)
            self.orders.append(order)

        logger.info("[PAPER] %s %.4f %s @ %.4f (fee %.2f)", side, quantity, symbol, price, fee)
        return order

    def get_position(self, symbol: str) -> float:
        return self.positions.get(symbol, 0.0)

    def mark_price(self, symbol: str, price: float) -> None:
        """Aggiorna il prezzo di mercato per la valorizzazione del portafoglio."""
        self.last_prices[symbol] = price

    # ------------------------------------------------------- Stato su disco
    def to_state(self) -> dict:
        """Stato completo, serializzabile in JSON (bot su GitHub Actions: un ciclo per esecuzione)."""
        with self._lock:
            return {
                "version": 1,
                "initial_cash": self.initial_cash,
                "fee_bps": self.fee_bps,
                "cash": self.cash,
                "positions": dict(self.positions),
                "last_prices": dict(self.last_prices),
                "orders": [o.to_dict() for o in self.orders],
                "rejected_orders": [o.to_dict() for o in self.rejected_orders],
                "rejected_count": self.rejected_count,
            }

    @classmethod
    def from_state(cls, state: dict) -> "PaperBroker":
        """
        Ricrea il broker da `to_state()` e lo verifica con la riconciliazione
        indipendente: uno stato modificato a mano o corrotto (cassa o posizioni
        che non tornano con gli ordini) solleva ValueError invece di ripartire.
        """
        from execution.reconcile import reconcile_broker

        b = cls(initial_cash=float(state["initial_cash"]), fee_bps=float(state["fee_bps"]))
        b.cash = float(state["cash"])
        b.positions = {k: float(v) for k, v in state.get("positions", {}).items()}
        b.last_prices = {k: float(v) for k, v in state.get("last_prices", {}).items()}
        b.orders = [Order(**o) for o in state.get("orders", [])]
        b.rejected_orders.extend(Order(**o) for o in state.get("rejected_orders", []))
        b.rejected_count = int(state.get("rejected_count", len(b.rejected_orders)))
        issues = reconcile_broker(b).issues
        if issues:
            raise ValueError(f"Stato del broker incoerente con i suoi ordini: {issues}")
        return b

    def get_account(self) -> dict:
        with self._lock:
            market_value = sum(q * self.last_prices.get(s, 0.0) for s, q in self.positions.items())
            equity = self.cash + market_value
            return {
                "broker": self.name,
                "cash": round(self.cash, 2),
                "market_value": round(market_value, 2),
                "equity": round(equity, 2),
                "pnl": round(equity - self.initial_cash, 2),
                "positions": {s: q for s, q in self.positions.items() if q},
                "orders": [o.to_dict() for o in self.orders[-50:]],
                "rejected_orders": [o.to_dict() for o in list(self.rejected_orders)[-10:]],
                "order_counts": {"filled": len(self.orders), "rejected": self.rejected_count},
            }


class AlpacaBroker(BaseBroker):
    """
    Placeholder per Alpaca. Per attivarlo:
        1. `pip install alpaca-py`
        2. Impostare ALPACA_API_KEY / ALPACA_SECRET_KEY (paper trading!)
        3. Implementare i metodi usando `alpaca.trading.client.TradingClient`.
    """

    name = "alpaca"

    def __init__(self, api_key: str, secret_key: str, base_url: str) -> None:
        if not api_key or not secret_key:
            raise RuntimeError("ALPACA_API_KEY e ALPACA_SECRET_KEY sono obbligatorie per BROKER=alpaca")
        self.base_url = base_url
        # Esempio:
        # from alpaca.trading.client import TradingClient
        # self.client = TradingClient(api_key, secret_key, paper="paper" in base_url)

    def submit_order(self, symbol: str, side: str, quantity: float, price: float) -> Order:
        # from alpaca.trading.requests import MarketOrderRequest
        # from alpaca.trading.enums import OrderSide, TimeInForce
        # req = MarketOrderRequest(symbol=symbol, qty=quantity,
        #                          side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
        #                          time_in_force=TimeInForce.DAY)
        # self.client.submit_order(req)
        raise NotImplementedError("Integrazione Alpaca da completare (vedi commenti).")

    def get_position(self, symbol: str) -> float:
        raise NotImplementedError

    def get_account(self) -> dict:
        raise NotImplementedError


class CcxtBroker(BaseBroker):
    """
    Placeholder per exchange crypto via ccxt. Per attivarlo:
        1. `pip install ccxt`
        2. Impostare CCXT_EXCHANGE, CCXT_API_KEY, CCXT_SECRET (CCXT_SANDBOX=true)
        3. Implementare i metodi con `exchange.create_market_order(...)`.
    """

    name = "ccxt"

    def __init__(self, exchange: str, api_key: str, secret: str, sandbox: bool = True) -> None:
        if not api_key or not secret:
            raise RuntimeError("CCXT_API_KEY e CCXT_SECRET sono obbligatorie per BROKER=ccxt")
        self.exchange_id = exchange
        # Esempio:
        # import ccxt
        # self.exchange = getattr(ccxt, exchange)({"apiKey": api_key, "secret": secret})
        # self.exchange.set_sandbox_mode(sandbox)

    def submit_order(self, symbol: str, side: str, quantity: float, price: float) -> Order:
        # self.exchange.create_market_order(symbol, side.lower(), quantity)
        raise NotImplementedError("Integrazione ccxt da completare (vedi commenti).")

    def get_position(self, symbol: str) -> float:
        raise NotImplementedError

    def get_account(self) -> dict:
        raise NotImplementedError


def create_broker(cfg: Settings) -> BaseBroker:
    """Factory: sceglie il broker in base alla variabile d'ambiente BROKER."""
    if cfg.broker == "alpaca":
        return AlpacaBroker(cfg.alpaca_api_key, cfg.alpaca_secret_key, cfg.alpaca_base_url)
    if cfg.broker == "ccxt":
        return CcxtBroker(cfg.ccxt_exchange, cfg.ccxt_api_key, cfg.ccxt_secret, cfg.ccxt_sandbox)
    if cfg.broker != "paper":
        logger.warning("BROKER=%r sconosciuto: uso il PaperBroker.", cfg.broker)
    return PaperBroker(initial_cash=cfg.initial_capital, fee_bps=cfg.fee_bps)
