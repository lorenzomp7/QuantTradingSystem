"""Dashboard (templates/dashboard.html): ogni sezione si carica per conto suo (punto 53 del Piano).

Controlli statici sulla pagina e, se c'è node, prova delle funzioni pure (date relative,
soglia «in ritardo»). Il comportamento nel browser lo provano tools/smoke_dashboard.sh e
tools/smoke_static.sh.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = (Path(__file__).resolve().parent.parent / "templates" / "dashboard.html").read_text(encoding="utf-8")
# Lo script della pagina (l'ultimo: gli altri due sono Chart.js e config.js, senza corpo).
SCRIPT = re.findall(r"<script>(.*?)</script>", HTML, re.S)[-1]


def function_body(name: str) -> str:
    """Corpo di `function name(...) { ... }` nello script, contando le graffe."""
    m = re.search(rf"(?:async\s+)?function {re.escape(name)}\([^)]*\)\s*\{{", SCRIPT)
    assert m, f"funzione {name} non trovata"
    depth, i = 1, m.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(SCRIPT[i], 0)
        i += 1
    return SCRIPT[m.end():i - 1]


# ------------------------------------------------------------ Sezioni
def test_load_section_has_the_five_states():
    code = function_body("loadSection") + function_body("setState")
    for state in ("idle", "loading", "ok", "error", "stale"):
        assert f'"{state}"' in code, f"stato {state} mancante"
    # Un errore non deve uscire da loadSection: fermerebbe le altre sezioni.
    assert "catch" in function_body("loadSection")


def test_status_account_and_logs_are_separate_sections():
    for section in ("status", "account", "logs"):
        assert f'loadSection("{section}"' in SCRIPT, f"la sezione {section} non passa da loadSection"
        assert f'id="{section}-note"' in HTML, f"manca lo spazio per l'avviso della sezione {section}"
    # refresh() non scarica e non disegna più nulla da sola, dentro un unico try.
    refresh = function_body("refresh")
    assert "try" not in refresh and "apiGet" not in refresh and "innerHTML" not in refresh
    assert "Errore di aggiornamento" not in HTML
    # Le due richieste stanno in funzioni diverse: se una fallisce l'altra non se ne accorge.
    assert '"/api/status"' in function_body("loadBot") and '"/api/logs"' not in function_body("loadBot")
    assert '"/api/logs"' in function_body("loadLogs") and '"/api/status"' not in function_body("loadLogs")


def test_refresh_is_global_and_returns_a_promise():
    # tools/smoke_static.sh fa `refresh().then(...)`.
    assert re.search(r"^function refresh\(\)", SCRIPT, re.M)
    assert "return Promise.all(" in function_body("refresh")
    assert re.search(r"setInterval\(refresh,", SCRIPT)


def test_failed_section_shows_an_alert_and_a_real_retry_button():
    code = function_body("setState")
    assert 'createElement("button")' in code and 'type = "button"' in code and '"Riprova"' in code
    assert '"role", "alert"' in code
    assert "RETRY[id]" in code
    for section in ("status", "account", "logs"):
        assert re.search(rf"RETRY\.{section}\s*=", SCRIPT), f"«Riprova» non sa cosa ricaricare per {section}"


# ------------------------------------------------------------ Offline
def test_offline_banner_and_listeners():
    assert re.search(r'<div id="offline-banner"[^>]*role="alert"[^>]*hidden', HTML)
    assert re.search(r'addEventListener\("offline",', SCRIPT)
    online = re.search(r'addEventListener\("online",(.*?)\);\n', SCRIPT, re.S)
    assert online and "refresh()" in online.group(1), "al ritorno della rete la pagina deve ricaricare"
    assert "navigator.onLine === false" in function_body("showOffline")
    assert "offline" in function_body("showOffline").lower()


# --------------------------------------------------------- Avvia / Ferma
def test_control_handles_network_errors():
    body = function_body("control")
    assert "try {" in body and "catch" in body
    assert body.index("try {") < body.index("fetch("), "la richiesta deve stare dentro il try"
    assert "Comando non inviato" in body
    assert "refresh()" in body
    assert re.search(r'<div id="msg"[^>]*(role="status"|aria-live)', HTML)


# ------------------------------------------------- Cose da non rompere
@pytest.mark.parametrize("element_id", [
    "badge", "status", "account", "logs", "msg", "metrics", "costs", "controls",
    "apikey-box", "static-controls", "chart",
])
def test_ids_used_by_the_smoke_tests_are_still_there(element_id):
    assert HTML.count(f'id="{element_id}"') == 1


def test_badge_texts_and_metric_labels_are_unchanged():
    for text in ('"ATTIVO"', '"IN RITARDO"', '"Rendimento %"', '"Trade"', '"Dati"'):
        assert text in SCRIPT, text
    assert 'class="metric"' in SCRIPT


def test_dates_are_relative_with_the_full_date_in_the_tooltip():
    assert 'title="${esc(fullDate(iso))}"' in function_body("ago")
    assert 'ago(s.written_at, "aggiornato ")' in function_body("drawStatus")
    assert '"Stato scritto": s.written_at' not in SCRIPT


# ------------------------------------------------------ Funzioni pure
PURE = re.search(r"// --- funzioni pure: inizio.*?\n(.*?)// --- funzioni pure: fine ---", SCRIPT, re.S)
NOW = "2026-11-28T12:00:00Z"


def run_js(expression: str):
    assert PURE, "blocco delle funzioni pure non trovato"
    code = PURE.group(1) + f"""
const NOW = Date.parse("{NOW}");
const back = (seconds) => new Date(NOW - seconds * 1000).toISOString();
console.log(JSON.stringify({expression}));
"""
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node non installato")


@needs_node
@pytest.mark.parametrize("seconds, expected", [
    (0, "pochi secondi fa"), (59, "pochi secondi fa"),
    (60, "1 minuto fa"), (12 * 60, "12 minuti fa"), (3599, "59 minuti fa"),
    (3600, "1 ora fa"), (3 * 3600 + 300, "3 ore fa"), (24 * 3600 - 1, "23 ore fa"),
    (24 * 3600, "1 giorno fa"), (3 * 24 * 3600, "3 giorni fa"),
    (-300, "pochi secondi fa"),   # orologio del server avanti: non «tra 5 minuti»
])
def test_time_ago(seconds, expected):
    assert run_js(f"timeAgo(back({seconds}), NOW)") == expected


@needs_node
def test_time_ago_of_something_that_is_not_a_date():
    assert run_js('[timeAgo(null, NOW), timeAgo("", NOW), timeAgo("boh", NOW), timeAgo(undefined, NOW)]') == [None] * 4


@needs_node
def test_stale_limit():
    hour = 3600 * 1000
    assert run_js("""[
      staleLimitMs({ mode: "github-actions", loop_interval_seconds: 60 }),
      staleLimitMs({ loop_interval_seconds: 60 }),
      staleLimitMs({ loop_interval_seconds: 10 }),
      staleLimitMs({ loop_interval_seconds: 3600 }),
      staleLimitMs({}),
    ]""") == [3 * hour, 120_000, 120_000, 2 * hour, 120_000]
    assert run_js("[duration(3 * 3600e3), duration(120000), duration(7200e3), duration(5400e3)]") == \
        ["3 ore", "2 minuti", "2 ore", "90 minuti"]


@needs_node
def test_stale_without_a_server_is_three_hours_from_the_state_file():
    actions = '{ mode: "github-actions", running: true, last_run_at: back(60), written_at: back(%d) }'
    assert run_js(f"staleSince({actions % (3 * 3600 - 60)}, NOW)") is None
    late = run_js(f"staleSince({actions % (3 * 3600 + 1)}, NOW)")
    assert late["limitMs"] == 3 * 3600 * 1000 and late["at"] == run_js(f"back({3 * 3600 + 1})")
    # Bot spento (BOT_ENABLED=false): lo stato vecchio è normale.
    assert run_js('staleSince({ mode: "github-actions", running: false, written_at: back(99999) }, NOW)') is None
    assert run_js("[staleSince(null, NOW), staleSince({ running: true }, NOW)]") == [None, None]


@needs_node
def test_stale_with_the_server_is_two_cycles_since_the_last_one():
    server = "{ running: true, loop_interval_seconds: %d, last_run_at: %s, started_at: %s }"
    assert run_js(f"staleSince({server % (60, 'back(119)', 'back(9000)')}, NOW)") is None
    late = run_js(f"staleSince({server % (60, 'back(600)', 'back(9000)')}, NOW)")
    assert late["limitMs"] == 120_000 and late["at"] == run_js("back(600)")
    assert run_js(f"staleSince({server % (3600, 'back(600)', 'back(9000)')}, NOW)") is None
    # Appena riavviato: l'ultimo ciclo è vecchio, ma il primo del nuovo avvio deve ancora finire.
    assert run_js(f"staleSince({server % (60, 'back(9000)', 'back(30)')}, NOW)") is None
    assert run_js(f"staleSince({server % (60, 'null', 'back(30)')}, NOW)") is None
    assert run_js(f"staleSince({server % (60, 'null', 'back(600)')}, NOW)")["at"] == run_js("back(600)")
    assert run_js("staleSince({ running: false, loop_interval_seconds: 60, last_run_at: back(9000) }, NOW)") is None


@needs_node
def test_error_text():
    assert run_js("""[
      errorText(new TypeError("Failed to fetch")),
      errorText(new TypeError("Failed to fetch"), false),
      errorText(new Error("errore 500: guasto")),
      errorText("boh"),
    ]""") == ["rete o server non raggiungibili", "sei offline", "errore 500: guasto", "boh"]


@needs_node
def test_page_script_is_valid_javascript(tmp_path):
    f = tmp_path / "dashboard.js"
    f.write_text(SCRIPT, encoding="utf-8")
    out = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
