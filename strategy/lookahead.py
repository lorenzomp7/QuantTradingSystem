"""
strategy/lookahead.py
=====================
Controllo automatico del look-ahead per troncamento dei dati (Piano, voce 5).

Idea presa da Freqtrade (`lookahead-analysis` e `recursive-analysis`), riscritta
da zero per l'interfaccia di QTS (`BaseStrategy.generate_signals`):

* `check_lookahead`: il valore di una colonna alla barra t non deve cambiare se
  si tolgono le barre successive. Per un campione riproducibile di istanti t si
  confronta l'ultima riga di `generate_signals(data[:t+1])` con la riga t della
  versione calcolata sull'intera serie. Ogni differenza è un dato futuro usato
  nel passato: `shift(-1)`, una media centrata, una normalizzazione fatta
  sull'intera serie.
* `check_recursive`: le ultime barre non devono dipendere da dove comincia lo
  storico. Si ricalcola la strategia su `data[-L:]` per più lunghezze L e si
  confronta la coda con quella della serie intera. Qui una differenza non è
  look-ahead: è un indicatore ricorsivo (medie esponenziali, stato del segnale)
  che non ha ancora dimenticato il suo punto di partenza, quindi backtest e bot
  live possono dare segnali diversi se partono da storici diversi.
* `assert_no_lookahead`: come `check_lookahead`, ma solleva `AssertionError`
  con un messaggio leggibile. È la forma da usare nei test.

Limiti: `check_lookahead` è un campionamento, non una dimostrazione; una
colonna 0/1 come `signal` può coincidere per caso, per questo si controllano
anche tutte le colonne aggiunte dalla strategia. Il controllo riguarda
`generate_signals`; il ritardo di una barra del backtest (`signal.shift(1)`)
resta coperto dai test di `tests/test_quant_model.py`.

Solo pandas e NumPy: nessun import di rete, funziona anche in Pyodide (pandas 3).
"""

from __future__ import annotations

import numbers

import numpy as np
import pandas as pd

# Colonne che ogni strategia deve produrre: si controllano sempre.
_REQUIRED = ("signal", "position")
# Storici con cui `check_recursive` ricalcola la coda (in barre).
DEFAULT_LENGTHS = (150, 250, 400)


# ================================================================ Supporto
def _columns_to_check(data: pd.DataFrame, full: pd.DataFrame, columns) -> list:
    """`signal`, `position` e ogni colonna aggiunta dalla strategia, oppure quelle chieste."""
    if columns is not None:
        cols = list(columns)
        missing = [c for c in cols if c not in full.columns]
        if missing:
            raise ValueError(f"Colonne non prodotte dalla strategia: {missing}.")
        return cols
    missing = [c for c in _REQUIRED if c not in full.columns]
    if missing:
        raise ValueError(f"generate_signals deve produrre le colonne {missing}.")
    added = [c for c in full.columns if c not in data.columns and c not in _REQUIRED]
    return [*_REQUIRED, *added]


def _scalar(value):
    """Valore Python semplice (non uno scalare NumPy), per elenchi leggibili e JSON."""
    return value.item() if isinstance(value, np.generic) else value


def _is_number(value) -> bool:
    return isinstance(value, (numbers.Number, np.number)) and not isinstance(value, complex)


def _same(a, b, rtol: float, atol: float) -> bool:
    """Uguaglianza con tolleranza; NaN = NaN, un NaN contro un numero è una differenza."""
    a_na, b_na = bool(pd.isna(a)), bool(pd.isna(b))
    if a_na or b_na:
        return a_na and b_na
    if _is_number(a) and _is_number(b):
        a, b = float(a), float(b)
        if a == b:                      # comprende gli infiniti dello stesso segno
            return True
        return bool(abs(a - b) <= atol + rtol * abs(b))
    return bool(a == b)


def _accepts(strategy, data: pd.DataFrame, n: int) -> bool:
    try:
        strategy.generate_signals(data.iloc[:n])
    except ValueError:
        return False
    return True


def min_bars(strategy, data: pd.DataFrame) -> int:
    """
    Numero minimo di barre iniziali di `data` che `generate_signals` accetta
    senza sollevare `ValueError` (dati insufficienti).

    Ricerca binaria: presuppone che, se n barre bastano, bastino anche n + 1.
    Solleva `ValueError` se la strategia rifiuta anche la serie intera.
    """
    n = len(data)
    if n == 0 or not _accepts(strategy, data, n):
        raise ValueError("La strategia non accetta la serie intera: impossibile cercare il look-ahead.")
    lo, hi = 1, n                       # hi è sempre accettato
    while lo < hi:
        mid = (lo + hi) // 2
        if _accepts(strategy, data, mid):
            hi = mid
        else:
            lo = mid + 1
    return hi


# ============================================================== Look-ahead
def check_lookahead(
    strategy,
    data: pd.DataFrame,
    samples: int = 50,
    seed: int = 0,
    columns=None,
    rtol: float = 1e-9,
    atol: float = 1e-12,
) -> list[dict]:
    """
    Cerca il look-ahead in `strategy.generate_signals` troncando i dati.

    Per `samples` istanti t (scelti con `seed`, quindi sempre gli stessi a parità
    di argomenti) confronta `generate_signals(data[:t+1]).iloc[-1]` con
    `generate_signals(data).iloc[t]` su `signal`, `position` e ogni colonna
    aggiunta dalla strategia (oppure solo su `columns`). NaN conta uguale a NaN;
    i numeri sono uguali se |troncato − completo| <= atol + rtol · |completo|.

    I t vanno dalla prima barra per cui la strategia non solleva `ValueError`
    (compresa sempre nel campione, perché è il bordo del riscaldamento) alla
    penultima: sull'ultima le due serie coincidono e il confronto non direbbe nulla.

    Restituisce una lista vuota se non trova differenze. Altrimenti un elemento
    per ogni differenza, con le chiavi:
        t         -> posizione della barra (0 = prima barra)
        date      -> etichetta dell'indice in quella posizione
        column    -> colonna diversa
        truncated -> valore calcolato senza le barre successive
        full      -> valore calcolato con l'intera serie
    """
    if samples < 1:
        raise ValueError("samples deve essere almeno 1.")
    full = strategy.generate_signals(data)
    if len(full) != len(data):
        raise ValueError("generate_signals deve restituire una riga per ogni barra di data.")
    cols = _columns_to_check(data, full, columns)

    first = min_bars(strategy, data) - 1            # primo t calcolabile
    last = len(data) - 2                            # penultima barra
    if last < first:
        raise ValueError(
            f"Serie troppo corta: servono almeno {first + 2} barre per confrontare, ricevute {len(data)}."
        )
    chosen = {first}
    others = np.arange(first + 1, last + 1)
    extra = min(samples - 1, len(others))
    if extra > 0:
        rng = np.random.default_rng(seed)
        chosen.update(int(t) for t in rng.choice(others, size=extra, replace=False))

    found: list[dict] = []
    for t in sorted(chosen):
        part = strategy.generate_signals(data.iloc[: t + 1])
        if len(part) != t + 1:
            raise ValueError("generate_signals deve restituire una riga per ogni barra di data.")
        for col in cols:
            # Una colonna che sparisce con meno dati dipende anch'essa dal futuro.
            truncated = part[col].iloc[-1] if col in part.columns else None
            complete = full[col].iloc[t]
            if truncated is None or not _same(truncated, complete, rtol, atol):
                found.append({
                    "t": int(t),
                    "date": full.index[t],
                    "column": col,
                    "truncated": _scalar(truncated),
                    "full": _scalar(complete),
                })
    return found


def assert_no_lookahead(strategy, data: pd.DataFrame, **kw) -> None:
    """
    Solleva `AssertionError` se `check_lookahead(strategy, data, **kw)` trova
    differenze. Il messaggio dice quante sono, in quali colonne e mostra le prime.
    """
    found = check_lookahead(strategy, data, **kw)
    if not found:
        return
    name = getattr(strategy, "name", type(strategy).__name__)
    by_column: dict = {}
    for row in found:
        by_column[row["column"]] = by_column.get(row["column"], 0) + 1
    bars = len({row["t"] for row in found})
    lines = [
        f"Look-ahead nella strategia {name!r}: {len(found)} differenze su {bars} barre "
        "tra dati troncati e serie intera.",
        "Colonne: " + ", ".join(f"{col} ({n})" for col, n in by_column.items()),
    ]
    shown = found[:8]
    for row in shown:
        lines.append(
            f"  t={row['t']} ({row['date']}) {row['column']}: "
            f"troncato={row['truncated']!r}, completo={row['full']!r}"
        )
    if len(found) > len(shown):
        lines.append(f"  ... e altre {len(found) - len(shown)}.")
    raise AssertionError("\n".join(lines))


# ==================================================== Dipendenza dallo storico
def check_recursive(
    strategy,
    data: pd.DataFrame,
    lengths=DEFAULT_LENGTHS,
    tail: int = 50,
    columns=None,
    rtol: float = 1e-6,
    atol: float = 1e-12,
) -> list[dict]:
    """
    Misura quanto le ultime `tail` barre dipendono dall'inizio dello storico.

    Per ogni L in `lengths` calcola `generate_signals(data[-L:])` e ne confronta
    le ultime `tail` barre con quelle della serie intera, sulle stesse colonne
    di `check_lookahead`. Le lunghezze non minori di `len(data)` si saltano
    (sarebbero la serie intera); se non ne resta nessuna, o se una è più corta
    di `tail`, solleva `ValueError`, come fa la strategia se L non le basta.

    Lista vuota = la coda è la stessa con ogni storico. Altrimenti un elemento
    per ogni coppia (lunghezza, colonna) diversa, con le chiavi:
        length       -> L, barre dello storico corto
        column       -> colonna diversa
        n_diff       -> barre della coda che differiscono (su `tail`)
        max_abs_diff -> massima differenza assoluta (inf se un lato è NaN,
                        NaN per colonne non numeriche)
        max_rel_diff -> massimo di |corto − completo| / |completo|
        date         -> barra della differenza assoluta più grande
        short, full  -> i due valori in quella barra

    Non è look-ahead: è il segno di indicatori ricorsivi (EMA, RSI di Wilder) o
    di un segnale a stato. Serve a sapere quante barre di storico dare al bot.
    """
    if tail < 1:
        raise ValueError("tail deve essere almeno 1.")
    n = len(data)
    if tail > n:
        raise ValueError(f"tail ({tail}) supera le barre disponibili ({n}).")
    usable = sorted({int(length) for length in lengths if int(length) < n})
    if not usable:
        raise ValueError(f"Nessuna lunghezza minore di {n} barre: niente da confrontare.")
    if usable[0] < tail:
        raise ValueError(f"Lunghezza {usable[0]} minore di tail ({tail}).")

    full = strategy.generate_signals(data)
    cols = _columns_to_check(data, full, columns)
    ref = full.iloc[-tail:]

    found: list[dict] = []
    for length in usable:
        short = strategy.generate_signals(data.iloc[-length:]).iloc[-tail:]
        for col in cols:
            if col not in short.columns:
                found.append({
                    "length": length, "column": col, "n_diff": tail,
                    "max_abs_diff": float("inf"), "max_rel_diff": float("inf"),
                    "date": ref.index[-1], "short": None, "full": _scalar(ref[col].iloc[-1]),
                })
                continue
            a, b = short[col], ref[col]
            diff_mask = np.array([not _same(x, y, rtol, atol) for x, y in zip(a, b)])
            if not diff_mask.any():
                continue
            numeric = pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b)
            if numeric:
                x, y = a.to_numpy(dtype=float), b.to_numpy(dtype=float)
                with np.errstate(divide="ignore", invalid="ignore"):
                    abs_diff = np.abs(x - y)
                    rel_diff = abs_diff / np.abs(y)
                # NaN da un lato solo: differenza non misurabile, la peggiore.
                abs_diff = np.where(np.isnan(abs_diff), np.inf, abs_diff)
                rel_diff = np.where(np.isnan(rel_diff), np.inf, rel_diff)
                abs_diff = np.where(diff_mask, abs_diff, 0.0)
                rel_diff = np.where(diff_mask, rel_diff, 0.0)
                worst = int(np.argmax(abs_diff))
                max_abs, max_rel = float(abs_diff.max()), float(rel_diff.max())
            else:
                worst = int(np.argmax(diff_mask))
                max_abs = max_rel = float("nan")
            found.append({
                "length": length,
                "column": col,
                "n_diff": int(diff_mask.sum()),
                "max_abs_diff": max_abs,
                "max_rel_diff": max_rel,
                "date": ref.index[worst],
                "short": _scalar(a.iloc[worst]),
                "full": _scalar(b.iloc[worst]),
            })
    return found
