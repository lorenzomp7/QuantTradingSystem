"""Schede delle app di riferimento dai risultati di REA (integrazione QTS 7), su JSON sintetici."""

import datetime as dt
import json
import subprocess
from pathlib import Path

from tools.app_report import PIANO, main, render, scan

ROOT = Path(__file__).resolve().parent.parent


def _write(tmp_path: Path, name: str, data) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _evidence(tmp_path: Path) -> list[Path]:
    package = {"subject": {"identity": {"sha256": "a" * 64}},
               "result": {"manifest": {"package": "com.example.trade", "versionName": "4.2.1", "versionCode": 4210,
                                       "permissions": ["android.permission.INTERNET",
                                                       "android.permission.POST_NOTIFICATIONS"],
                                       "services": [{"name": "com.example.trade.push.FirebaseMessagingService"}],
                                       "activities": [{"name": "com.example.trade.ui.BacktestActivity"},
                                                      {"name": "com.example.trade.ui.ChartReplayActivity"},
                                                      {"name": "com.example.trade.ui.SettingsActivity"}]}}}
    resources = {"strings": [{"name": "ind_rsi", "value": "RSI"}, {"name": "ind_macd", "value": "MACD (12, 26, 9)"},
                             {"name": "x", "value": "Bande di Bollinger, RSI"},
                             {"name": "no", "value": "PRSIDENT EMAIL"}]}
    classes = {"matches": ["com.github.mikephil.charting.charts.CandleStickChart",
                           "com.github.mikephil.charting.data.CandleEntry", "android.webkit.WebView"]}
    flutter = {"result": {"flutter": False}}
    return [_write(tmp_path, n, d) for n, d in
            [("package.json", package), ("resources.json", resources), ("classes-charting.json", classes),
             ("flutter.json", flutter)]]


def test_scan_finds_declarations(tmp_path):
    f = scan(_evidence(tmp_path))
    assert f.package == "com.example.trade"
    assert (f.version_name, f.version_code) == ("4.2.1", "4210")
    assert f.sha256 == "a" * 64
    assert f.signatures["MPAndroidChart"] == 2
    assert f.signatures["Firebase Cloud Messaging"] == 1
    assert f.signatures["Permesso POST_NOTIFICATIONS"] == 1
    assert "WebView (grafico in pagina web?)" in f.signatures
    assert f.indicators == {"RSI": 2, "MACD": 1, "Bollinger": 1}   # parole intere: "PRSIDENT" ed "EMAIL" no
    assert f.screens == ["com.example.trade.ui.BacktestActivity", "com.example.trade.ui.ChartReplayActivity"]


def test_render_links_every_finding_to_the_plan(tmp_path):
    md = render("Esempio", "com.example.trade", scan(_evidence(tmp_path)), today=dt.date(2026, 11, 24),
                rea_version="6.10.1")
    assert md.startswith("# Esempio")
    for point in (19, 26, 27, 29, 31):
        assert PIANO[point] in md, point
    assert "**MPAndroidChart**: 2 occorrenze → punto 19, punto 27" in md
    assert "**Firebase Cloud Messaging**: 1 occorrenze → punto 31" in md
    assert "| Versione | 4.2.1 (codice 4210) |" in md and "| Data dell'analisi | 2026-11-24 |" in md
    assert "Da rileggere a mano" in md and "⚠️" not in md


def test_render_flags_package_mismatch_and_empty_sections(tmp_path):
    p = _write(tmp_path, "package.json", {"package": "com.other.app"})
    md = render("Altro", "com.expected.app", scan([p]))
    assert "⚠️ diverso" in md
    assert "Nessuna firma nota trovata." in md and "Nessun nome di indicatore" in md


def test_cli_writes_the_card(tmp_path):
    _evidence(tmp_path)
    out = tmp_path / "docs" / "esempio.md"
    assert main(["--app", "Esempio", "--package", "com.example.trade", "--evidence", str(tmp_path),
                 "--out", str(out)]) == 0
    assert "MPAndroidChart" in out.read_text(encoding="utf-8")
    assert main(["--app", "X", "--package", "y.z", "--evidence", str(tmp_path / "vuota"), "--out", str(out)]) == 1


def test_apks_stay_out_of_the_repo():
    res = subprocess.run(["git", "check-ignore", "-q", "qualcosa.apk"], cwd=ROOT)
    assert res.returncode == 0, ".gitignore deve escludere gli APK di terzi"
    script = (ROOT / "tools" / "analyze_apps.sh").read_text(encoding="utf-8")
    assert "fuori dalla repo" in script and 'APK_DIR="${APK_DIR:-$HOME/qts-apk}"' in script
