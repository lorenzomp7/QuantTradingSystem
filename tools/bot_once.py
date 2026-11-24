"""
tools/bot_once.py
=================
Un ciclo del bot con stato su disco: è il bot quando gira su GitHub Actions
(senza VM), una volta ogni ora.

    python -m tools.bot_once --state-dir state

Legge da `state/` il registro del paper broker e lo stato precedente, esegue un
ciclo di `TradingEngine` (dati -> segnale -> ordine), poi riscrive:

* `broker.json`  stato completo del PaperBroker (verificato al caricamento)
* `status.json`  come `/api/status`, più `mode` e il numero totale di cicli
* `logs.json`    ultimi log, come `/api/logs`
* `equity.csv`   una riga per ciclo: ora, prezzo, equity, posizione, azione

La dashboard su GitHub Pages pubblica questi file. Con `BOT_ENABLED=false` il
ciclo si salta ma lo stato si riscrive (badge STOPPED). Esce con 1 se il ciclo
fallisce, dopo aver salvato lo stato con l'errore.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import Settings  # noqa: E402
from core.engine import TradingEngine  # noqa: E402
from execution.broker import PaperBroker  # noqa: E402
from utils.logger import InMemoryLogHandler, get_logger, setup_logging  # noqa: E402

logger = get_logger("bot_once")
MAX_LOGS = 300
EQUITY_FIELDS = ["timestamp", "price", "cash", "equity", "position", "action", "data_source", "orders_blocked"]


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def run(state_dir: Path, enabled: bool) -> int:
    state_dir.mkdir(parents=True, exist_ok=True)
    cfg = Settings()
    setup_logging(cfg.log_level)
    handler = InMemoryLogHandler(capacity=MAX_LOGS)
    logging.getLogger().addHandler(handler)

    try:
        prev = _read_json(state_dir / "status.json", {})
        broker_state = _read_json(state_dir / "broker.json", None)
        broker = (PaperBroker.from_state(broker_state) if broker_state
                  else PaperBroker(initial_cash=cfg.initial_capital, fee_bps=cfg.fee_bps))
    except Exception as exc:  # JSON illeggibile, registro alterato o incoerente
        # Il conto non si tocca (niente ripartenza da zero): l'errore va sulla pagina e la run fallisce.
        error = f"Stato salvato non valido, ciclo non eseguito: {type(exc).__name__}: {exc}"
        logger.error(error)
        logging.getLogger().removeHandler(handler)
        try:
            prev = json.loads((state_dir / "status.json").read_text(encoding="utf-8"))
            assert isinstance(prev, dict)
        except Exception:
            prev = {}
        prev.update({"mode": "github-actions", "running": False, "last_error": error,
                     "written_at": datetime.now(timezone.utc).isoformat()})
        _write_json(state_dir / "status.json", prev)
        try:
            logs = _read_json(state_dir / "logs.json", [])
            assert isinstance(logs, list)
        except Exception:
            logs = []
        _write_json(state_dir / "logs.json", (logs + handler.get_logs(limit=MAX_LOGS))[-MAX_LOGS:])
        return 1
    engine = TradingEngine(cfg, broker=broker)
    engine.cycles = int(prev.get("cycles", 0))
    engine.started_at = prev.get("started_at") or datetime.now(timezone.utc).isoformat()
    engine.last_run_at = prev.get("last_run_at")
    engine.last_signal = prev.get("last_signal")
    if prev.get("last_rejection"):
        engine._last_rejection = tuple(prev["last_rejection"])

    code, error = 0, None
    try:
        if enabled:
            engine.run_cycle()
        else:
            logger.info("Bot disattivato (BOT_ENABLED=false): ciclo saltato.")
    except Exception as exc:
        code, error = 1, f"{type(exc).__name__}: {exc}"
        logger.error("Ciclo non riuscito: %s", error)
    finally:
        logging.getLogger().removeHandler(handler)

    status = engine.status()
    status.update({
        "running": enabled,
        "mode": "github-actions",
        "schedule": os.getenv("BOT_SCHEDULE", "ogni ora"),
        "last_error": error or status.get("last_error"),
        "last_rejection": list(engine._last_rejection) if engine._last_rejection else None,
        "written_at": datetime.now(timezone.utc).isoformat(),
    })
    if status["data"] is None and prev.get("data"):
        status["data"] = prev["data"]          # ciclo saltato: resta l'ultima provenienza nota
    _write_json(state_dir / "broker.json", broker.to_state())
    _write_json(state_dir / "status.json", status)
    logs = _read_json(state_dir / "logs.json", []) + handler.get_logs(limit=MAX_LOGS)
    _write_json(state_dir / "logs.json", logs[-MAX_LOGS:])

    # Una riga per ciclo riuscito su dati veri: con la serie mock di ripiego il prezzo è finto.
    fallback = bool(engine.last_data_info and engine.last_data_info.fallback)
    if enabled and not error and not fallback:
        acct, sig = status["account"], status["last_signal"] or {}
        equity_csv = state_dir / "equity.csv"
        new = not equity_csv.exists()
        with equity_csv.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=EQUITY_FIELDS)
            if new:
                w.writeheader()
            w.writerow({
                "timestamp": status["last_run_at"], "price": sig.get("close"), "cash": acct.get("cash"),
                "equity": acct.get("equity"), "position": acct.get("positions", {}).get(cfg.symbol, 0),
                "action": sig.get("action"), "data_source": sig.get("data_source"),
                "orders_blocked": sig.get("orders_blocked") or "",
            })
    return code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Un ciclo del bot con stato su disco (GitHub Actions).")
    ap.add_argument("--state-dir", type=Path, default=Path("state"))
    args = ap.parse_args(argv)
    enabled = os.getenv("BOT_ENABLED", "true").strip().lower() not in {"false", "0", "no", "off"}
    return run(args.state_dir, enabled)


if __name__ == "__main__":
    sys.exit(main())
