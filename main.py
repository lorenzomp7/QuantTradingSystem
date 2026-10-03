"""
main.py
=======
Entry point di QuantTradingSystem.

Avvia un server FastAPI che espone:
    GET  /                    -> dashboard web (stato bot, segnali, backtest, log)
    GET  /health              -> health check (usato da Render)
    GET  /api/status          -> stato del bot, ultimo segnale, account
    POST /api/bot/start       -> avvia il loop di trading        (protetto)
    POST /api/bot/stop        -> ferma il loop di trading        (protetto)
    POST /api/bot/run-once    -> esegue un singolo ciclo         (protetto)
    GET  /api/signals         -> storico dei segnali generati
    GET  /api/backtest        -> backtest della strategia con parametri custom
    GET  /api/logs            -> ultimi log applicativi
    GET  /docs                -> documentazione OpenAPI interattiva (Swagger)

Gli endpoint "protetti" richiedono l'header `X-API-Key` se la variabile
d'ambiente CONTROL_API_KEY è impostata (fortemente consigliato su Render,
dato che il servizio è pubblicamente raggiungibile).

Avvio locale:
    uvicorn main:app --reload --port 10000
"""

from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import HTMLResponse

from config import settings
from core.engine import TradingEngine
from strategy.quant_model import MovingAverageCrossover, StrategyParams
from utils.logger import get_logger, memory_handler, setup_logging

__version__ = "1.0.0"

setup_logging(settings.log_level)
logger = get_logger("quant.main")

engine = TradingEngine(settings)
DASHBOARD_HTML = (Path(__file__).parent / "templates" / "dashboard.html").read_text(encoding="utf-8")


# ------------------------------------------------------------- Lifecycle
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Avvio/arresto ordinato: il bot si ferma pulito quando Render riavvia."""
    logger.info(
        "Avvio %s v%s (env=%s, broker=%s, data=%s, symbol=%s)",
        settings.app_name, __version__, settings.environment,
        settings.broker, settings.data_source, settings.symbol,
    )
    if not settings.control_api_key:
        logger.warning("CONTROL_API_KEY non impostata: gli endpoint di controllo sono pubblici!")
    if settings.auto_start_bot:
        await engine.start()
    yield
    await engine.stop()
    logger.info("Shutdown completato.")


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description="Sistema di trading quantitativo con dashboard di monitoraggio.",
    lifespan=lifespan,
)


# ------------------------------------------------------------ Sicurezza
def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Dependency che protegge gli endpoint di controllo."""
    expected = settings.control_api_key
    if not expected:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key mancante o non valida")


# ------------------------------------------------------------- Endpoints
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> str:
    return DASHBOARD_HTML


@app.get("/health", tags=["system"])
async def health() -> dict:
    return {"status": "ok", "version": __version__, "bot_running": engine.is_running}


@app.get("/api/status", tags=["bot"])
async def get_status() -> dict:
    return engine.status()


@app.post("/api/bot/start", tags=["bot"], dependencies=[Depends(require_api_key)])
async def start_bot() -> dict:
    started = await engine.start()
    return {"started": started, "running": engine.is_running,
            "detail": "Bot avviato" if started else "Bot già in esecuzione"}


@app.post("/api/bot/stop", tags=["bot"], dependencies=[Depends(require_api_key)])
async def stop_bot() -> dict:
    stopped = await engine.stop()
    return {"stopped": stopped, "running": engine.is_running,
            "detail": "Bot fermato" if stopped else "Bot non in esecuzione"}


@app.post("/api/bot/run-once", tags=["bot"], dependencies=[Depends(require_api_key)])
async def run_once() -> dict:
    try:
        return await asyncio.to_thread(engine.run_cycle)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Ciclo fallito: {exc}") from exc


@app.get("/api/signals", tags=["bot"])
async def get_signals(limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    return list(engine.signal_history)[-limit:]


@app.get("/api/backtest", tags=["strategy"])
async def run_backtest(
    symbol: str = Query(default=settings.symbol, min_length=1, max_length=20),
    short_window: int = Query(default=settings.short_window, ge=1, le=400),
    long_window: int = Query(default=settings.long_window, ge=2, le=500),
    period: str = Query(default=settings.data_period, pattern=r"^(1mo|3mo|6mo|1y|2y|5y|10y|ytd|max)$"),
    fee_bps: float = Query(default=settings.fee_bps, ge=0, le=500),
) -> dict:
    try:
        params = StrategyParams(
            short_window=short_window, long_window=long_window,
            initial_capital=settings.initial_capital, fee_bps=fee_bps,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    def _job() -> dict:
        data = engine.fetcher.get_ohlcv(symbol, period, "1d")
        result = MovingAverageCrossover(params).backtest(data)
        out = result.to_dict()
        out["symbol"] = symbol.upper()
        out["data_source"] = engine.fetcher.last_source_used
        return out

    try:
        return await asyncio.to_thread(_job)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/logs", tags=["system"])
async def get_logs(
    limit: int = Query(100, ge=1, le=500),
    level: str | None = Query(None, pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$"),
) -> list[dict]:
    return memory_handler.get_logs(limit=limit, level=level)


if __name__ == "__main__":  # avvio diretto: `python main.py`
    import os

    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
