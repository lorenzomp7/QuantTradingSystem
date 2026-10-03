"""
config.py
=========
Configurazione centralizzata dell'applicazione.

Tutti i parametri sono letti da variabili d'ambiente (12-factor app), così lo
stesso codice gira identico in locale (file `.env`) e su Render (Environment
Variables della dashboard o `render.yaml`). Nessun segreto è hard-coded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# In locale carichiamo un eventuale file .env; su Render non serve.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - python-dotenv è opzionale
    pass


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    # --- Applicazione -------------------------------------------------------
    app_name: str = "QuantTradingSystem"
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())

    # Chiave che protegge gli endpoint di controllo (start/stop/run).
    # Se vuota, gli endpoint sono aperti (comodo in locale, sconsigliato in prod).
    control_api_key: str = field(default_factory=lambda: os.getenv("CONTROL_API_KEY", ""))

    # --- Dati di mercato ----------------------------------------------------
    # "yahoo" = dati reali via yfinance (fallback automatico su mock se fallisce)
    # "mock"  = serie sintetica (Geometric Brownian Motion), nessuna rete richiesta
    data_source: str = field(default_factory=lambda: os.getenv("DATA_SOURCE", "yahoo").lower())
    symbol: str = field(default_factory=lambda: os.getenv("SYMBOL", "SPY"))
    data_period: str = field(default_factory=lambda: os.getenv("DATA_PERIOD", "2y"))
    data_interval: str = field(default_factory=lambda: os.getenv("DATA_INTERVAL", "1d"))

    # --- Strategia ----------------------------------------------------------
    short_window: int = field(default_factory=lambda: _env_int("SHORT_WINDOW", 20))
    long_window: int = field(default_factory=lambda: _env_int("LONG_WINDOW", 50))
    initial_capital: float = field(default_factory=lambda: _env_float("INITIAL_CAPITAL", 10_000.0))
    fee_bps: float = field(default_factory=lambda: _env_float("FEE_BPS", 5.0))
    trade_quantity: float = field(default_factory=lambda: _env_float("TRADE_QUANTITY", 10.0))

    # --- Bot ----------------------------------------------------------------
    # Intervallo (secondi) tra un ciclo e l'altro del bot.
    loop_interval_seconds: int = field(default_factory=lambda: _env_int("LOOP_INTERVAL_SECONDS", 300))
    # Avvia automaticamente il bot all'avvio del server.
    auto_start_bot: bool = field(default_factory=lambda: _env_bool("AUTO_START_BOT", False))

    # --- Broker -------------------------------------------------------------
    # "paper"  = broker simulato in memoria (default, sicuro)
    # "alpaca" = placeholder per Alpaca (paper trading endpoint)
    # "ccxt"   = placeholder per exchange crypto via ccxt (es. Binance testnet)
    broker: str = field(default_factory=lambda: os.getenv("BROKER", "paper").lower())
    alpaca_api_key: str = field(default_factory=lambda: os.getenv("ALPACA_API_KEY", ""))
    alpaca_secret_key: str = field(default_factory=lambda: os.getenv("ALPACA_SECRET_KEY", ""))
    alpaca_base_url: str = field(
        default_factory=lambda: os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    )
    ccxt_exchange: str = field(default_factory=lambda: os.getenv("CCXT_EXCHANGE", "binance"))
    ccxt_api_key: str = field(default_factory=lambda: os.getenv("CCXT_API_KEY", ""))
    ccxt_secret: str = field(default_factory=lambda: os.getenv("CCXT_SECRET", ""))
    ccxt_sandbox: bool = field(default_factory=lambda: _env_bool("CCXT_SANDBOX", True))


settings = Settings()
