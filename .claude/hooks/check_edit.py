#!/usr/bin/env python3
"""Hook PostToolUse di Claude Code: controllo rapido del file appena modificato.

Gira dopo ogni Edit/Write. Fa solo controlli veloci e sicuri (sintassi, nomi non
definiti, JSON/YAML validi) e non riformatta mai il file. Se trova un errore esce
con codice 2: Claude Code mostra il messaggio a Claude, che lo corregge subito.
Se uno strumento non è installato il relativo controllo viene saltato.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# File che non sono JSON puro (ammettono commenti) e non vanno validati.
JSON_WITH_COMMENTS = ("tsconfig", "jsconfig", ".vscode", "devcontainer")


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess | None:
    exe = shutil.which(cmd[0])
    if not exe:
        return None
    try:
        return subprocess.run([exe, *cmd[1:]], cwd=cwd, capture_output=True,
                              text=True, timeout=50)
    except (OSError, subprocess.TimeoutExpired):
        return None


def failed(proc: subprocess.CompletedProcess | None) -> str | None:
    if proc is None or proc.returncode == 0:
        return None
    return (proc.stdout + proc.stderr).strip()[-3000:] or f"exit {proc.returncode}"


def check(f: Path, root: Path) -> list[str]:
    suffix = f.suffix.lower()
    problems: list[str] = []

    if suffix == ".py":
        try:
            compile(f.read_text(encoding="utf-8"), str(f), "exec")
        except SyntaxError as e:
            problems.append(f"SyntaxError riga {e.lineno}: {e.msg}")
        except (UnicodeDecodeError, ValueError) as e:
            problems.append(str(e))
        if not problems:
            # E9 = errori di sintassi/IO, F63/F7 = errori logici, F82 = nomi non definiti
            out = failed(run(["ruff", "check", "--isolated", "--no-cache",
                              "--select", "E9,F63,F7,F82",
                              "--output-format", "concise", str(f)], root))
            if out:
                problems.append(out)

    elif suffix == ".json" and not any(m in str(f) for m in JSON_WITH_COMMENTS):
        try:
            json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            problems.append(f"JSON non valido: {e}")

    elif suffix in (".yml", ".yaml"):
        try:
            import yaml  # type: ignore
        except ImportError:
            yaml = None
        if yaml is not None:
            try:
                yaml.safe_load(f.read_text(encoding="utf-8"))
            except yaml.YAMLError as e:
                problems.append(f"YAML non valido: {e}")

    elif suffix in (".js", ".mjs", ".cjs"):
        out = failed(run(["node", "--check", str(f)], root))
        if out:
            problems.append(out)

    elif suffix == ".sh":
        out = failed(run(["bash", "-n", str(f)], root))
        if out:
            problems.append(out)

    elif suffix == ".dart":
        out = failed(run(["dart", "analyze", "--no-fatal-warnings", str(f)], root))
        if out:
            problems.append(out)

    elif suffix in (".kt", ".kts"):
        out = failed(run(["ktlint", "--relative", str(f)], root))
        if out:
            problems.append(out)

    return problems


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("path")
    if not path:
        return 0
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or ".").resolve()
    f = Path(path)
    if not f.is_absolute():
        f = root / f
    if not f.is_file():
        return 0

    problems = check(f, root)
    if problems:
        rel = f.relative_to(root) if f.is_relative_to(root) else f
        print(f"Controllo automatico fallito su {rel}:\n" + "\n".join(problems),
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
