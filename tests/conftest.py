"""Configurazione dei test: dati mock e API key note, nessuna chiamata di rete."""

import os
import sys
from pathlib import Path

os.environ.setdefault("DATA_SOURCE", "mock")
os.environ.setdefault("CONTROL_API_KEY", "test-key")
os.environ.setdefault("AUTO_START_BOT", "false")
os.environ.setdefault("LOOP_INTERVAL_SECONDS", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
