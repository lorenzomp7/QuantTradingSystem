"""
tools/build_static_site.py
==========================
Costruisce la dashboard statica per GitHub Pages (senza VM):

    python -m tools.build_static_site --out _site --state state --data state/data

* `index.html`  la dashboard di sempre (templates/dashboard.html)
* `config.js`   `window.QTS_STATIC`: niente API, dati dai file e calcoli con Pyodide
* `py/`         i moduli Python usati nel browser e `manifest.json` con l'elenco
* `state/`      stato del bot scritto da tools/bot_once.py (status, log, equity)
* `data/`       snapshot dei prezzi scritto da tools/data_snapshot.py

Dalla cartella dello stato si copiano solo i file che la dashboard legge.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Moduli eseguiti nel browser: solo numpy e pandas, niente server né rete.
PY_FILES = [
    "strategy/__init__.py", "strategy/quant_model.py", "strategy/metrics.py", "strategy/research.py",
    "strategy/indicators.py", "strategy/rsi_macd.py",
    "utils/__init__.py", "utils/data_fetcher.py", "utils/logger.py",
    "web/__init__.py", "web/static_api.py",
]
PYODIDE_PACKAGES = ["numpy", "pandas"]
PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.8/full/"
STATE_FILES = ["status.json", "logs.json", "equity.csv"]


def build(out: Path, state: Path | None, data: Path | None, actions_url: str, pyodide_url: str) -> None:
    if out.exists():
        shutil.rmtree(out)
    (out / "py").mkdir(parents=True)
    shutil.copyfile(ROOT / "templates" / "dashboard.html", out / "index.html")
    config = {"pyodide": pyodide_url, "packages": PYODIDE_PACKAGES, "actions_url": actions_url}
    (out / "config.js").write_text(
        "// Dashboard statica (GitHub Pages senza VM): bot su GitHub Actions, backtest nel browser.\n"
        f"window.QTS_STATIC = {json.dumps(config, indent=1)};\n", encoding="utf-8")
    for rel in PY_FILES:
        dst = out / "py" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, dst)
    (out / "py" / "manifest.json").write_text(
        json.dumps({"files": PY_FILES, "packages": PYODIDE_PACKAGES}, indent=1) + "\n", encoding="utf-8")

    (out / "state").mkdir()
    if state and state.is_dir():
        for name in STATE_FILES:
            if (state / name).is_file():
                shutil.copyfile(state / name, out / "state" / name)
    (out / "data").mkdir()
    if data and data.is_dir():
        for f in data.iterdir():
            if f.suffix in {".csv", ".json"} and f.is_file():
                shutil.copyfile(f, out / "data" / f.name)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Dashboard statica per GitHub Pages.")
    ap.add_argument("--out", type=Path, default=Path("_site"))
    ap.add_argument("--state", type=Path, help="cartella dello stato del bot")
    ap.add_argument("--data", type=Path, help="cartella dello snapshot dei prezzi")
    ap.add_argument("--actions-url", default="", help="pagina del workflow, per i pulsanti del bot")
    ap.add_argument("--pyodide-url", default=PYODIDE_URL)
    args = ap.parse_args(argv)
    build(args.out, args.state, args.data, args.actions_url, args.pyodide_url)
    print(f"Sito statico in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
