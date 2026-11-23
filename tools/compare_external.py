"""
tools/compare_external.py
=========================
Confronta il backtest di QTS con un motore indipendente, sugli stessi dati,
commissioni, capitale e regole di esecuzione.

Motore supportato: Vibe-Trading (HKUDS, MIT). Va installato in un ambiente
separato con Python >= 3.11, perché porta molte dipendenze:

    python3.11 -m venv ~/.venvs/vibe && ~/.venvs/vibe/bin/pip install vibe-trading-ai
    python tools/compare_external.py --vibe-python ~/.venvs/vibe/bin/python

Regole di esecuzione rese uguali:
* QTS esegue al close della barra del segnale (posizione in t = segnale di t-1);
  Vibe-Trading esegue all'open della barra successiva. I dati del confronto
  hanno open(t) = close(t-1), quindi i due prezzi di esecuzione coincidono.
* Commissione: `fee_bps` per QTS, `commission = fee_bps / 10000` (maker =
  taker) per Vibe-Trading; slippage e funding a zero.
* Vibe-Trading chiude la posizione aperta all'ultima barra (end_of_backtest)
  e paga la commissione: il confronto toglie la stessa commissione all'equity
  finale di QTS e non conta quella vendita tra i trade.

Esce con 0 se le differenze rientrano nella tolleranza, 1 altrimenti.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy.metrics import bars_per_year  # noqa: E402
from strategy.quant_model import MovingAverageCrossover, StrategyParams  # noqa: E402
from utils.data_fetcher import generate_mock_ohlcv  # noqa: E402

VIBE_SYMBOL = "BTC-USDT"   # simbolo crypto per il motore 24/7 di Vibe-Trading

SIGNAL_ENGINE = '''"""SMA crossover long-only, stessa regola di MovingAverageCrossover di QTS."""

import pandas as pd


class SignalEngine:
    def __init__(self, short_window: int = {short}, long_window: int = {long}):
        self.short_window = short_window
        self.long_window = long_window

    def generate(self, data_map):
        out = {{}}
        for code, df in data_map.items():
            close = df["close"]
            short = close.rolling(self.short_window, min_periods=self.short_window).mean()
            long = close.rolling(self.long_window, min_periods=self.long_window).mean()
            out[code] = ((short > long) & long.notna()).astype(float)
        return out
'''


@dataclass
class Summary:
    engine: str
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    buys: int
    sells: int
    buy_and_hold_pct: float


# ------------------------------------------------------------------ Dati
def comparison_data(symbol: str, period: str, end: str = "2026-11-20") -> pd.DataFrame:
    """
    Dati mock giornalieri 24/7 con open(t) = close(t-1).

    Con questo vincolo il close della barra del segnale (QTS) e l'open della
    barra dopo (Vibe-Trading) sono lo stesso prezzo.
    """
    d = generate_mock_ohlcv(symbol, period, "1d")
    d.index = pd.date_range(end=end, periods=len(d), freq="D", name="date")
    d["open"] = d["close"].shift(1).fillna(d["open"])
    d["high"] = d[["open", "high", "close"]].max(axis=1)
    d["low"] = d[["open", "low", "close"]].min(axis=1)
    return d


# ------------------------------------------------------------------ QTS
def qts_summary(data: pd.DataFrame, params: StrategyParams, bpy: int) -> Summary:
    res = MovingAverageCrossover(params).backtest(data, bars_per_year=bpy)
    m = res.metrics
    final = float(res.equity_curve.iloc[-1])
    if int(res.signals["signal"].iloc[-2]) == 1:   # in posizione all'ultima barra
        final -= final * params.fee_bps / 10_000    # chiusura come Vibe-Trading
    sides = res.trades["side"].value_counts()
    return Summary(
        engine="QTS",
        final_equity=final,
        total_return_pct=(final / params.initial_capital - 1) * 100,
        max_drawdown_pct=m["max_drawdown_pct"],
        buys=int(sides.get("BUY", 0)),
        sells=int(sides.get("SELL", 0)),
        buy_and_hold_pct=m["buy_and_hold_return_pct"],
    )


# ---------------------------------------------------------- Vibe-Trading
def prepare_vibe_run(work: Path, data: pd.DataFrame, params: StrategyParams) -> tuple[Path, Path]:
    """Cartella di run (config.json + code/signal_engine.py) e HOME isolata con i dati."""
    run = work / "run"
    (run / "code").mkdir(parents=True, exist_ok=True)
    home = work / "home"
    bridge = home / ".vibe-trading" / "data-bridge"
    bridge.mkdir(parents=True, exist_ok=True)
    csv = work / "data.csv"
    data.reset_index().to_csv(csv, index=False)
    (bridge / "config.yaml").write_text(
        f'sources:\n  - symbol: "{VIBE_SYMBOL}"\n    type: csv\n    path: "{csv}"\n    date_format: "%Y-%m-%d"\n',
        encoding="utf-8",
    )
    config = {
        "source": "local", "codes": [VIBE_SYMBOL], "interval": "1D", "engine": "daily",
        "start_date": str(data.index[0].date()), "end_date": str(data.index[-1].date()),
        "initial_cash": params.initial_capital, "commission": params.fee_bps / 10_000,
        "slippage": 0.0, "funding_rate": 0.0,
    }
    (run / "config.json").write_text(json.dumps(config, indent=1), encoding="utf-8")
    (run / "code" / "signal_engine.py").write_text(
        SIGNAL_ENGINE.format(short=params.short_window, long=params.long_window), encoding="utf-8"
    )
    return run, home


def run_vibe(vibe_python: str, run: Path, home: Path, timeout: int = 600) -> None:
    env = {**os.environ, "HOME": str(home), "VIBE_TRADING_ALLOWED_RUN_ROOTS": str(run.parent)}
    proc = subprocess.run([vibe_python, "-m", "backtest.runner", str(run)], env=env,
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 or not (run / "artifacts" / "equity.csv").exists():
        raise RuntimeError(f"Vibe-Trading non ha prodotto risultati:\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}")


def vibe_summary(run: Path, initial_capital: float) -> Summary:
    art = run / "artifacts"
    metrics = pd.read_csv(art / "metrics.csv").iloc[0]
    equity = pd.read_csv(art / "equity.csv")
    trades = pd.read_csv(art / "trades.csv")
    signal_trades = trades[trades["reason"] != "end_of_backtest"]
    final = float(equity["equity"].iloc[-1])
    return Summary(
        engine="Vibe-Trading",
        final_equity=final,
        total_return_pct=(final / initial_capital - 1) * 100,
        max_drawdown_pct=float(metrics["max_drawdown"]) * 100,
        buys=int((signal_trades["side"] == "buy").sum()),
        sells=int((signal_trades["side"] == "sell").sum()),
        buy_and_hold_pct=float(metrics["benchmark_return"]) * 100,
    )


# -------------------------------------------------------------- Confronto
def compare(qts: Summary, other: Summary, tolerance_pct: float = 0.1) -> list[dict]:
    """Una riga per misura; `ok` è False se la differenza supera la tolleranza."""
    rows = []
    for field, kind in [("final_equity", "rel"), ("total_return_pct", "abs"), ("max_drawdown_pct", "abs"),
                        ("buy_and_hold_pct", "abs"), ("buys", "exact"), ("sells", "exact")]:
        a, b = getattr(qts, field), getattr(other, field)
        if kind == "exact":
            diff, ok = b - a, a == b
        elif kind == "rel":
            diff = (b / a - 1) * 100 if a else float("inf")
            ok = abs(diff) <= tolerance_pct
        else:
            diff = b - a
            ok = abs(diff) <= tolerance_pct
        rows.append({"misura": field, "QTS": a, other.engine: b, "differenza": diff, "ok": ok})
    return rows


def format_table(rows: list[dict], other: str) -> str:
    lines = [f"{'misura':<18}{'QTS':>14}{other:>16}{'differenza':>13}  esito"]
    for r in rows:
        unit = " %" if r["misura"] == "final_equity" else ""
        lines.append(f"{r['misura']:<18}{r['QTS']:>14.4f}{r[other]:>16.4f}{r['differenza']:>11.4f}{unit:<2}  "
                     f"{'ok' if r['ok'] else 'DIVERSO'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Confronta il backtest di QTS con Vibe-Trading.")
    ap.add_argument("--vibe-python", default=os.getenv("VIBE_TRADING_PYTHON"),
                    help="Python dell'ambiente con vibe-trading-ai (o VIBE_TRADING_PYTHON)")
    ap.add_argument("--symbol", default="BTC-USD", help="seme della serie mock (default BTC-USD)")
    ap.add_argument("--period", default="2y")
    ap.add_argument("--short", type=int, default=20)
    ap.add_argument("--long", type=int, default=50)
    ap.add_argument("--fee-bps", type=float, default=5.0)
    ap.add_argument("--capital", type=float, default=10_000.0)
    ap.add_argument("--tolerance", type=float, default=0.1,
                    help="tolleranza in punti percentuali (equity finale: differenza relativa %%)")
    ap.add_argument("--workdir", type=Path, help="cartella di lavoro (default: temporanea)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not args.vibe_python:
        ap.error("indica --vibe-python o VIBE_TRADING_PYTHON")

    params = StrategyParams(short_window=args.short, long_window=args.long,
                            initial_capital=args.capital, fee_bps=args.fee_bps)
    data = comparison_data(args.symbol, args.period)
    bpy = bars_per_year("1d", "crypto")
    qts = qts_summary(data, params, bpy)

    work = args.workdir or Path(tempfile.mkdtemp(prefix="qts-compare-"))
    run, home = prepare_vibe_run(work, data, params)
    run_vibe(args.vibe_python, run, home)
    vibe = vibe_summary(run, args.capital)

    rows = compare(qts, vibe, args.tolerance)
    if args.json:
        print(json.dumps({"rows": rows, "workdir": str(work)}, indent=2, default=float))
    else:
        print(f"Dati: mock {args.symbol} {args.period} ({len(data)} barre, open = close precedente), "
              f"SMA {args.short}/{args.long}, fee {args.fee_bps} bps, capitale {args.capital:g}, "
              f"serie giornaliera 24/7 (365 barre/anno, motore crypto di Vibe-Trading)")
        print(format_table(rows, vibe.engine))
        print(f"Artefatti di Vibe-Trading: {run / 'artifacts'}")
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
