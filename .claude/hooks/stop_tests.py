#!/usr/bin/env python3
"""Hook Stop di Claude Code: prima che Claude chiuda il turno lancia i test veloci.

Legge i passi da `.claude/hooks/verify.json`. Parte solo se nel working tree ci
sono modifiche ai file indicati in "watch" e se quelle modifiche non sono già
state verificate con successo (lo stato è in `.claude/hooks/.state/`, ignorato
da git). Se un test fallisce esce con codice 2: Claude riceve l'output e deve
correggere prima di fermarsi. Al secondo tentativo consecutivo non blocca più,
così non si crea un ciclo infinito, ma l'avviso resta visibile.

Per saltarlo in una sessione: CLAUDE_SKIP_STOP_TESTS=1
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True,
                              text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def changed_files(root: Path) -> list[str]:
    files = []
    for line in git(root, "status", "--porcelain", "--untracked-files=all").splitlines():
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        if path:
            files.append(path)
    return files


def fingerprint(root: Path, files: list[str]) -> str:
    h = hashlib.sha256()
    h.update(git(root, "diff", "HEAD", "--", *files).encode())
    for name in sorted(files):
        p = root / name
        if p.is_file():
            h.update(name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def resolve(cmd: list[str]) -> list[str] | None:
    exe = shutil.which(cmd[0])
    return [exe, *cmd[1:]] if exe else None


def main() -> int:
    if os.environ.get("CLAUDE_SKIP_STOP_TESTS") == "1":
        return 0
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or ".").resolve()
    config_path = root / ".claude/hooks/verify.json"
    if not config_path.is_file():
        return 0
    config = json.loads(config_path.read_text(encoding="utf-8"))

    watched = [f for f in changed_files(root)
               if any(fnmatch.fnmatch(f, pat) for pat in config.get("watch", ["*"]))]
    if not watched:
        return 0

    state_dir = root / ".claude/hooks/.state"
    stamp = state_dir / "last_green"
    digest = fingerprint(root, watched)
    if stamp.is_file() and stamp.read_text().strip() == digest:
        return 0

    failures, skipped = [], []
    for step in config["steps"]:
        missing = [r for r in step.get("requires", []) if not (root / r).exists()]
        probe = resolve(step["probe"]) if step.get("probe") else []
        probe_ok = probe is not None and (not probe or subprocess.run(
            probe, cwd=root, capture_output=True).returncode == 0)
        cmd = resolve(step["run"])
        if missing or not probe_ok or cmd is None:
            skipped.append(f"{step['name']} (ambiente non pronto: {step.get('setup', 'vedi AGENTS.md')})")
            continue
        try:
            proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True,
                                  timeout=step.get("timeout", 240))
        except subprocess.TimeoutExpired:
            failures.append(f"### {step['name']}: tempo scaduto")
            continue
        if proc.returncode != 0:
            tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-60:])
            failures.append(f"### {step['name']} fallito ({' '.join(step['run'])})\n{tail}")

    if failures:
        message = ("Test automatici falliti sulle modifiche in corso. Correggi prima di "
                   "chiudere, oppure spiega all'utente perché il fallimento non dipende "
                   "dalle tue modifiche.\n\n" + "\n\n".join(failures))
        if data.get("stop_hook_active"):
            print(json.dumps({"systemMessage": "Attenzione: i test falliscono ancora.\n"
                              + "\n\n".join(failures)[-3000:]}))
            return 0
        print(message, file=sys.stderr)
        return 2

    # Nessun fallimento: questa versione delle modifiche non viene ricontrollata.
    state_dir.mkdir(parents=True, exist_ok=True)
    stamp.write_text(digest)
    if skipped:
        print(json.dumps({"systemMessage": "Test non eseguiti: " + "; ".join(skipped)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
