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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from config import Settings
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Order:
    symbol: str
    side: str          # "BUY" | "SELL"
    quantity: float
    price: float
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    status: str = "filled"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


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
        self.orders: list[Order] = []
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
                    raise ValueError("Liquidità insufficiente")
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
