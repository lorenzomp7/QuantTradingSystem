"""
utils/logger.py
===============
Logging centralizzato.

Oltre allo stream su stdout (che Render raccoglie nella sezione "Logs"),
manteniamo in memoria gli ultimi N record in un buffer circolare, così da
poterli esporre via API (`GET /logs`) e mostrarli nella dashboard web.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections import deque
from datetime import datetime, timezone

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


class InMemoryLogHandler(logging.Handler):
    """Handler thread-safe che conserva gli ultimi `capacity` log come dict."""

    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        self._records: deque[dict] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            with self._lock:
                self._records.append(entry)
        except Exception:  # pragma: no cover - un handler non deve mai sollevare
            self.handleError(record)

    def get_logs(self, limit: int = 100, level: str | None = None) -> list[dict]:
        with self._lock:
            records = list(self._records)
        if level:
            min_level = logging.getLevelName(level.upper())
            if isinstance(min_level, int):
                records = [r for r in records if logging.getLevelName(r["level"]) >= min_level]
        return records[-limit:]


memory_handler = InMemoryLogHandler()
_configured = False


def setup_logging(level: str = "INFO") -> None:
    """Configura il root logger (idempotente)."""
    global _configured
    if _configured:
        return
    root = logging.getLogger()
    root.setLevel(level)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter(_LOG_FORMAT))
    memory_handler.setFormatter(logging.Formatter(_LOG_FORMAT))

    root.addHandler(stream)
    root.addHandler(memory_handler)

    # Riduciamo il rumore delle librerie esterne.
    for noisy in ("urllib3", "yfinance", "peewee"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
