"""
tools/bench_grid.py
===================
Benchmark della griglia di parametri SMA: ciclo coppia per coppia
(`grid_search`, un backtest per combinazione) contro versione vettorizzata
(`sma_grid_vectorized`). Prima controlla che i risultati coincidano, poi
misura i tempi con più ripetizioni.

    python tools/bench_grid.py                       # confronto completo
    python tools/bench_grid.py --mode vector --repeat 1   # una sola versione (per i profiler)

Con flameox (https://github.com/morluto/flameox, MIT) la misura diventa una
prova registrata, con il campionamento di py-spy:

    flameox capture --provider py-spy -- python tools/bench_grid.py --mode loop --repeat 1

Esce con 1 se le due versioni danno risultati diversi o se lo scarto tra le
ripetizioni (deviazione standard / media) supera `--max-spread` (default 10%).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy.optimization import grid_search, sma_grid_vectorized  # noqa: E402
from strategy.quant_model import MovingAverageCrossover, StrategyParams  # noqa: E402
from utils.data_fetcher import generate_mock_ohlcv  # noqa: E402


def run_loop(data, shorts, longs, base, bpy):
    return grid_search(MovingAverageCrossover, data, {"short_window": shorts, "long_window": longs},
                       base_params=base, bars_per_year=bpy)


def run_vector(data, shorts, longs, base, bpy):
    return sma_grid_vectorized(data, shorts, longs, base_params=base, bars_per_year=bpy)


def same_results(a: list[dict], b: list[dict]) -> list[str]:
    """Differenze tra le due griglie (vuoto se coincidono)."""
    key = lambda r: (r["parameters"]["short_window"], r["parameters"]["long_window"])  # noqa: E731
    ka, kb = {key(r): r for r in a}, {key(r): r for r in b}
    problems = [f"combinazioni diverse: {sorted(set(ka) ^ set(kb))[:5]}"] if set(ka) != set(kb) else []
    for k in sorted(set(ka) & set(kb)):
        for name, v in ka[k]["metrics"].items():
            if abs(v - kb[k]["metrics"][name]) > 0.011:
                problems.append(f"{k} {name}: ciclo {v}, vettorizzata {kb[k]['metrics'][name]}")
    if a and b and key(a[0]) != key(b[0]):
        problems.append(f"migliore diverso: ciclo {key(a[0])}, vettorizzata {key(b[0])}")
    return problems


def timed(fn, repeat: int, min_time: float = 0.5) -> tuple[list[float], int]:
    """
    Tempo per chiamata, `repeat` misure. Come `timeit`, ogni misura ripete la
    funzione finché dura almeno `min_time` secondi: i tempi brevi (pochi ms)
    altrimenti oscillano molto più del 10%.
    """
    t = time.perf_counter()
    fn()
    single = time.perf_counter() - t
    number = max(1, int(min_time / single) + 1) if single < min_time else 1
    out = []
    for _ in range(repeat):
        t = time.perf_counter()
        for _ in range(number):
            fn()
        out.append((time.perf_counter() - t) / number)
    return out, number


def summary(times: list[float]) -> dict:
    """Mediana, minimo, massimo e scarto = deviazione standard / media (come pyperf)."""
    med, mean = statistics.median(times), statistics.fmean(times)
    sd = statistics.stdev(times) if len(times) > 1 else 0.0
    return {"median_s": round(med, 4), "min_s": round(min(times), 4), "max_s": round(max(times), 4),
            "spread_pct": round(sd / mean * 100, 1) if mean > 0 else 0.0}


def peak_memory_mb(fn) -> float:
    """Picco di memoria allocata da Python durante una chiamata (tracemalloc), in MB."""
    tracemalloc.start()
    try:
        fn()
        return round(tracemalloc.get_traced_memory()[1] / 1e6, 1)
    finally:
        tracemalloc.stop()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Benchmark della griglia SMA: ciclo contro vettorizzata.")
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--period", default="5y")
    ap.add_argument("--shorts", default="2:60:2", help="inizio:fine:passo (fine esclusa)")
    ap.add_argument("--longs", default="20:250:10")
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=1, help="esecuzioni non misurate prima dei tempi")
    ap.add_argument("--mode", choices=["both", "loop", "vector"], default="both")
    ap.add_argument("--max-spread", type=float, default=10.0,
                    help="scarto massimo ammesso tra le ripetizioni, %% (deviazione standard / media)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rng = lambda spec: list(range(*map(int, spec.split(":"))))  # noqa: E731
    shorts, longs = rng(args.shorts), rng(args.longs)
    data = generate_mock_ohlcv(args.symbol, args.period)
    base, bpy = StrategyParams(fee_bps=5), 252
    combos = sum(1 for s in shorts for l in longs if s < l)

    jobs = {"loop": lambda: run_loop(data, shorts, longs, base, bpy),
            "vector": lambda: run_vector(data, shorts, longs, base, bpy)}
    if args.mode != "both":
        jobs = {args.mode: jobs[args.mode]}

    report = {"symbol": args.symbol, "bars": len(data), "combinations": combos, "repeat": args.repeat}
    code = 0
    if args.mode == "both":
        problems = same_results(jobs["loop"](), jobs["vector"]())
        report["same_results"] = not problems
        if problems:
            report["differences"] = problems[:10]
            code = 1
    for name, fn in jobs.items():
        for _ in range(args.warmup):
            fn()
        times, number = timed(fn, args.repeat)
        report[name] = {**summary(times), "calls_per_measure": number, "peak_mb": peak_memory_mb(fn)}
        if args.repeat > 1 and report[name]["spread_pct"] > args.max_spread:
            code = 1
    if args.mode == "both":
        report["speedup"] = round(report["loop"]["median_s"] / report["vector"]["median_s"], 1)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Dati mock {args.symbol} {args.period}: {len(data)} barre, {combos} combinazioni, "
              f"{args.repeat} ripetizioni")
        if "same_results" in report:
            print("Risultati uguali:", "sì" if report["same_results"] else "NO", *report.get("differences", []))
        for name in jobs:
            r = report[name]
            print(f"{name:<7} mediana {r['median_s']:.4f} s per griglia  (min {r['min_s']:.4f}, "
                  f"max {r['max_s']:.4f}, scarto {r['spread_pct']}%, {r['calls_per_measure']} chiamate/misura, "
                  f"picco memoria {r['peak_mb']} MB)")
        if "speedup" in report:
            print(f"Vettorizzata {report['speedup']}× più veloce")
    return code


if __name__ == "__main__":
    sys.exit(main())
