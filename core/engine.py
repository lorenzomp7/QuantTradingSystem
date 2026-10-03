"""
core/engine.py
==============
Motore del bot: collega dati -> strategia -> broker in un ciclo periodico.

Il loop gira come task asyncio nello stesso processo del web server, così un
singolo Web Service su Render ospita sia la dashboard/API sia il bot. Le
operazioni bloccanti (download dati, calcoli Pandas) sono delegate a un
thread con `asyncio.to_thread` per non bloccare l'event loop di FastAPI.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from datetime import datetime, timezone

from config import Settings
from execution.broker import BaseBroker, PaperBroker, create_broker
from strategy.quant_model import BaseStrategy, MovingAverageCrossover, StrategyParams
from utils.data_fetcher import MarketDataFetcher
from utils.logger import get_logger

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TradingEngine:
    def __init__(
        self,
        cfg: Settings,
        strategy: BaseStrategy | None = None,
        fetcher: MarketDataFetcher | None = None,
        broker: BaseBroker | None = None,
    ) -> None:
        self.cfg = cfg
        self.symbol = cfg.symbol
        self.strategy = strategy or MovingAverageCrossover(
            StrategyParams(
                short_window=cfg.short_window,
                long_window=cfg.long_window,
                initial_capital=cfg.initial_capital,
                fee_bps=cfg.fee_bps,
            )
        )
        self.fetcher = fetcher or MarketDataFetcher(
            source=cfg.data_source, cache_ttl_seconds=min(cfg.loop_interval_seconds, 300)
        )
        self.broker = broker or create_broker(cfg)

        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self._cycle_lock = threading.Lock()   # evita due cicli in parallelo

        self.started_at: str | None = None
        self.last_run_at: str | None = None
        self.last_error: str | None = None
        self.cycles = 0
        self.last_signal: dict | None = None
        self.signal_history: deque[dict] = deque(maxlen=200)

    # ------------------------------------------------------------ Stato
    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict:
        try:
            account = self.broker.get_account()
        except NotImplementedError:
            account = {"broker": self.broker.name, "detail": "account non disponibile (placeholder)"}
        return {
            "running": self.is_running,
            "symbol": self.symbol,
            "strategy": self.strategy.name,
            "params": {
                "short_window": self.strategy.params.short_window,
                "long_window": self.strategy.params.long_window,
                "fee_bps": self.strategy.params.fee_bps,
            },
            "data_source": self.cfg.data_source,
            "last_data_source_used": self.fetcher.last_source_used,
            "loop_interval_seconds": self.cfg.loop_interval_seconds,
            "started_at": self.started_at,
            "last_run_at": self.last_run_at,
            "cycles": self.cycles,
            "last_signal": self.last_signal,
            "last_error": self.last_error,
            "account": account,
        }

    # ------------------------------------------------------- Un ciclo
    def run_cycle(self) -> dict:
        """
        Esegue un singolo ciclo (sincrono):
            1. scarica i dati più recenti
            2. calcola il segnale
            3. allinea la posizione al segnale (signal=1 -> long, 0 -> flat)

        Si confronta lo *stato* del segnale con la posizione reale (invece di
        reagire solo all'evento di crossover): è più robusto a riavvii del
        servizio, frequenti su Render (deploy, sleep del piano free).
        """
        if not self._cycle_lock.acquire(blocking=False):
            raise RuntimeError("Un ciclo è già in esecuzione.")
        try:
            data = self.fetcher.get_ohlcv(self.symbol, self.cfg.data_period, self.cfg.data_interval)
            signal = self.strategy.latest_signal(data)
            price = signal["close"]

            if isinstance(self.broker, PaperBroker):
                self.broker.mark_price(self.symbol, price)

            position = self.broker.get_position(self.symbol)
            order = None
            if signal["signal"] == 1 and position <= 0:
                order = self.broker.submit_order(self.symbol, "BUY", self.cfg.trade_quantity, price)
            elif signal["signal"] == 0 and position > 0:
                order = self.broker.submit_order(self.symbol, "SELL", position, price)

            # `crossover` = evento sull'ultima barra; `action` = ciò che il bot ha fatto davvero.
            signal["crossover"] = signal["action"]
            signal["action"] = order.side if order else "HOLD"
            signal["order"] = order.to_dict() if order else None
            signal["evaluated_at"] = _now()

            self.last_signal = signal
            self.signal_history.append(signal)
            self.last_run_at = signal["evaluated_at"]
            self.last_error = None
            self.cycles += 1
            logger.info(
                "Ciclo #%d %s: close=%.2f signal=%d action=%s order=%s",
                self.cycles, self.symbol, price, signal["signal"], signal["action"],
                order.side if order else "-",
            )
            return signal
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.exception("Errore durante il ciclo di trading")
            raise
        finally:
            self._cycle_lock.release()

    # ------------------------------------------------------------ Loop
    async def _loop(self) -> None:
        assert self._stop_event is not None
        logger.info("Bot avviato su %s (intervallo %ss).", self.symbol, self.cfg.loop_interval_seconds)
        while not self._stop_event.is_set():
            try:
                await asyncio.to_thread(self.run_cycle)
            except Exception:
                pass  # già loggato in run_cycle; il loop continua
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.cfg.loop_interval_seconds)
            except asyncio.TimeoutError:
                continue
        logger.info("Bot fermato.")

    async def start(self) -> bool:
        if self.is_running:
            return False
        self._stop_event = asyncio.Event()
        self.started_at = _now()
        self._task = asyncio.create_task(self._loop(), name="trading-loop")
        return True

    async def stop(self) -> bool:
        if not self.is_running:
            return False
        assert self._stop_event is not None and self._task is not None
        self._stop_event.set()
        await self._task
        self._task = None
        return True
