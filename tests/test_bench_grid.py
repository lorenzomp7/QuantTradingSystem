"""Benchmark della griglia: lo script gira e controlla che le due versioni coincidano."""

import json

from tools import bench_grid


def test_bench_grid_small_run(capsys):
    code = bench_grid.main(["--period", "1y", "--shorts", "3:12:3", "--longs", "20:50:10", "--repeat", "2",
                            "--warmup", "0", "--max-spread", "1000", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["same_results"] is True and report["combinations"] == 9
    assert report["loop"]["median_s"] > 0 and report["vector"]["peak_mb"] >= 0
    assert report["speedup"] > 0


def test_same_results_reports_differences():
    a = [{"parameters": {"short_window": 5, "long_window": 20}, "metrics": {"total_return_pct": 1.0}}]
    b = [{"parameters": {"short_window": 5, "long_window": 20}, "metrics": {"total_return_pct": 1.5}}]
    assert bench_grid.same_results(a, a) == []
    assert bench_grid.same_results(a, b)
