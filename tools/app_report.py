"""
tools/app_report.py
===================
Scheda di un'app di riferimento in docs/app-analizzate/<app>.md, dai risultati
JSON di REA (Reverse Engineer Anything, morluto/REA, MIT) per un APK.

    python tools/app_report.py --app TradingView --package com.tradingview.tradingviewapp \\
        --evidence ~/qts-apk/tradingview --out docs/app-analizzate/tradingview.md

Il JSON di REA cambia da comando a comando; questo script non dipende dai suoi
campi: scorre tutte le stringhe dei file e cerca firme note (librerie di
grafici, notifiche, indicatori, schermate di backtest e replay, framework).
La scheda riporta solo nomi di librerie, permessi e componenti dichiarati e
conteggi, collegati ai punti del Piano: nessun codice o risorsa dell'app
viene copiato. Va riletta a mano prima del commit.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

# Punti del Piano (foglio «Modifiche») a cui rimandano le scoperte.
PIANO = {
    19: "Grafico dei prezzi con medie e trade, drawdown e tabella dei trade",
    26: "Meno passaggi per lanciare un backtest dal telefono",
    27: "Riproduzione candela per candela del backtest",
    29: "Parametri della strategia dichiarati con limiti, per form e ottimizzazione",
    31: "Notifiche di ordini ed errori",
}


@dataclass(frozen=True)
class Signature:
    category: str
    label: str
    pattern: re.Pattern
    points: tuple[int, ...]


def _sig(category: str, label: str, regex: str, points: tuple[int, ...], flags: int = 0) -> Signature:
    return Signature(category, label, re.compile(regex, flags), points)


SIGNATURES: tuple[Signature, ...] = (
    # Grafici -> punti 19 e 27
    _sig("charts", "MPAndroidChart", r"com\.github\.mikephil\.charting", (19, 27)),
    _sig("charts", "TradingView Lightweight Charts", r"lightweight[-_ ]?charts", (19, 27), re.I),
    _sig("charts", "TradingView Charting Library", r"charting_library", (19, 27)),
    _sig("charts", "Highcharts", r"com\.highsoft\.highcharts|highcharts", (19, 27), re.I),
    _sig("charts", "AnyChart", r"com\.anychart", (19, 27)),
    _sig("charts", "Vico (Compose)", r"com\.patrykandpatrick\.vico", (19, 27)),
    _sig("charts", "ECharts", r"\becharts\b", (19, 27), re.I),
    _sig("charts", "fl_chart (Flutter)", r"\bfl_chart\b", (19, 27)),
    _sig("charts", "Syncfusion Charts (Flutter)", r"syncfusion_flutter_charts", (19, 27)),
    _sig("charts", "WebView (grafico in pagina web?)", r"android\.webkit\.WebView\b", (19,)),
    # Notifiche -> punto 31
    _sig("notifications", "Firebase Cloud Messaging", r"com\.google\.firebase\.messaging|FirebaseMessagingService", (31,)),
    _sig("notifications", "OneSignal", r"com\.onesignal", (31,)),
    _sig("notifications", "Permesso POST_NOTIFICATIONS", r"android\.permission\.POST_NOTIFICATIONS", (31,)),
    _sig("notifications", "Canali di notifica", r"NotificationChannel|notification_channel", (31,)),
    # Framework dell'app
    _sig("framework", "Flutter", r"io\.flutter|libflutter\.so", ()),
    _sig("framework", "React Native", r"com\.facebook\.react", ()),
    _sig("framework", "Jetpack Compose", r"androidx\.compose", ()),
    _sig("framework", "Unity", r"com\.unity3d", ()),
)

# Indicatori cercati come parole intere nelle stringhe -> punto 29
INDICATORS = ("RSI", "MACD", "Bollinger", "EMA", "SMA", "Stochastic", "ATR", "VWAP", "Ichimoku",
              "Supertrend", "Parabolic SAR", "ADX", "CCI", "OBV", "Williams %R", "Fibonacci")

# Componenti o classi dichiarati che fanno pensare a backtest o replay -> punti 26 e 27
SCREEN_WORDS = re.compile(r"backtest|replay|strateg|simulat|playback", re.I)
CLASS_NAME = re.compile(r"^[A-Za-z_][\w$]*(\.[A-Za-z_][\w$]*){2,}$")
MAX_EXAMPLES = 8


@dataclass
class Findings:
    files: list[str] = field(default_factory=list)
    signatures: dict[str, int] = field(default_factory=dict)        # label -> occorrenze
    indicators: dict[str, int] = field(default_factory=dict)
    screens: list[str] = field(default_factory=list)
    package: str | None = None
    version_name: str | None = None
    version_code: str | None = None
    sha256: str | None = None


def _walk(obj, key: str | None = None) -> Iterator[tuple[str | None, object]]:
    """Coppie (chiave, valore) per ogni valore scalare del JSON, a qualunque profondità."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, k)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, key)
    else:
        yield key, obj


def _norm_key(k: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (k or "").lower())


def scan(paths: Iterable[Path]) -> Findings:
    """Cerca le firme in tutti i file JSON indicati."""
    f = Findings()
    screens: dict[str, None] = {}
    for path in sorted(paths):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        f.files.append(path.name)
        for key, value in _walk(data):
            nk = _norm_key(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if nk == "versioncode" and f.version_code is None:
                    f.version_code = str(value)
                continue
            if not isinstance(value, str) or not value:
                continue
            if nk == "versionname" and f.version_name is None:
                f.version_name = value
            elif nk == "versioncode" and f.version_code is None:
                f.version_code = value
            elif nk == "sha256" and f.sha256 is None and re.fullmatch(r"[0-9a-f]{64}", value):
                f.sha256 = value
            elif nk in {"package", "packagename"} and f.package is None and "." in value:
                f.package = value
            for s in SIGNATURES:
                hits = len(s.pattern.findall(value))
                if hits:
                    f.signatures[s.label] = f.signatures.get(s.label, 0) + hits
            for ind in INDICATORS:
                hits = len(re.findall(rf"(?<![\w%]){re.escape(ind)}(?![\w])", value))
                if hits:
                    f.indicators[ind] = f.indicators.get(ind, 0) + hits
            if CLASS_NAME.match(value) and SCREEN_WORDS.search(value.rsplit(".", 1)[-1]):
                screens.setdefault(value, None)
    f.screens = list(screens)
    return f


def _points(points: Iterable[int]) -> str:
    return ", ".join(f"punto {p}" for p in sorted(set(points)))


def render(app: str, package: str, f: Findings, today: dt.date | None = None, rea_version: str | None = None) -> str:
    """Testo Markdown della scheda."""
    today = today or dt.date.today()
    by_label = {s.label: s for s in SIGNATURES}

    def section(category: str) -> list[str]:
        rows = [(lbl, n) for lbl, n in f.signatures.items() if by_label[lbl].category == category]
        if not rows:
            return ["- Nessuna firma nota trovata."]
        return [f"- **{lbl}**: {n} occorrenze → {_points(by_label[lbl].points) or 'struttura'}"
                for lbl, n in sorted(rows, key=lambda r: -r[1])]

    mismatch = f.package and f.package != package
    lines = [
        f"# {app}",
        "",
        "> Scheda generata da `tools/app_report.py` sui risultati di REA. **Da rileggere a mano**",
        "> prima del commit. Solo dichiarazioni (librerie, permessi, componenti) e conteggi:",
        "> nessun codice o risorsa dell'app è copiato qui.",
        "",
        "| Campo | Valore |",
        "|---|---|",
        f"| Pacchetto atteso | `{package}` |",
        f"| Pacchetto nell'APK | `{f.package or 'non trovato'}`{' ⚠️ diverso' if mismatch else ''} |",
        f"| Versione | {f.version_name or 'non trovata'} (codice {f.version_code or '—'}) |",
        f"| SHA-256 dell'APK | `{f.sha256 or 'non trovato'}` |",
        f"| Data dell'analisi | {today.isoformat()} |",
        f"| REA | {rea_version or 'versione non indicata'} |",
        f"| File di REA letti | {', '.join(f.files) or 'nessuno'} |",
        "",
        f"## 1. Grafici → punti 19 e 27",
        "",
        f"*{PIANO[19]}. {PIANO[27]}.*",
        "",
        *section("charts"),
        "",
        f"## 2. Indicatori e parametri → punto 29",
        "",
        f"*{PIANO[29]}.*",
        "",
    ]
    if f.indicators:
        lines += [f"- **{k}**: {n} occorrenze nelle stringhe" for k, n in
                  sorted(f.indicators.items(), key=lambda r: -r[1])]
    else:
        lines.append("- Nessun nome di indicatore trovato nelle stringhe.")
    lines += ["", f"## 3. Notifiche → punto 31", "", f"*{PIANO[31]}.*", "", *section("notifications"),
              "", f"## 4. Backtest e replay dal telefono → punti 26 e 27", "",
              f"*{PIANO[26]}. {PIANO[27]}.*", ""]
    if f.screens:
        lines += [f"- `{s}`" for s in f.screens[:MAX_EXAMPLES]]
        if len(f.screens) > MAX_EXAMPLES:
            lines.append(f"- … e altri {len(f.screens) - MAX_EXAMPLES} componenti con nomi simili")
    else:
        lines.append("- Nessun componente con nomi di backtest, replay o strategia.")
    lines += ["", "## Struttura dell'app", "", *section("framework"), "",
              "## Cosa portare in QTS", "",
              "- _Da compilare dopo la rilettura: per ogni scoperta utile, la scelta per il punto del Piano._", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scheda di un'app dai risultati JSON di REA.")
    ap.add_argument("--app", required=True, help="nome dell'app (es. TradingView)")
    ap.add_argument("--package", required=True, help="pacchetto atteso (es. com.tradingview.tradingviewapp)")
    ap.add_argument("--evidence", type=Path, required=True, help="cartella con i JSON di REA (fuori dalla repo)")
    ap.add_argument("--out", type=Path, required=True, help="file Markdown da scrivere")
    ap.add_argument("--rea-version")
    args = ap.parse_args(argv)

    files = list(args.evidence.glob("*.json"))
    if not files:
        print(f"Nessun file JSON in {args.evidence}", file=sys.stderr)
        return 1
    findings = scan(files)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(args.app, args.package, findings, rea_version=args.rea_version), encoding="utf-8")
    print(f"Scheda scritta in {args.out} ({len(findings.signatures)} firme, "
          f"{len(findings.indicators)} indicatori, {len(findings.screens)} componenti)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
