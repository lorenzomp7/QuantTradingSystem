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

La strategia riceve sempre una copia dei dati: se li modifica sul posto non
tocca quelli di chi chiama e non può lasciare, da una chiamata all'altra,
colonne già calcolate con il futuro. Si confrontano tutte le colonne che la
strategia restituisce, comprese quelle d'ingresso (una `close` o un `volume`
riscritti con dati futuri sono look-ahead come un indicatore).

Limiti:
* `check_lookahead` è un campionamento, non una dimostrazione (con
  `samples=len(data)` controlla tutte le barre utili); una colonna 0/1 come
  `signal` può coincidere per caso, per questo si controllano tutte le colonne.
* Le barre prima di `min_bars − 1` non sono verificabili se la strategia
  rifiuta le serie corte: lì il valore troncato non si può calcolare.
* Lo stato interno della strategia non è sotto controllo: una strategia che
  conserva risultati tra una chiamata e l'altra (cache, attributi aggiornati
  da `generate_signals`) può dare valori coerenti pur avendo visto il futuro.
* Il controllo riguarda `generate_signals`; il ritardo di una barra del backtest
  (`signal.shift(1)`) resta coperto dai test di `tests/test_quant_model.py`.

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


class _Missing:
    """Segnaposto di «colonna assente»: distinto da `None`, che è un valore lecito."""

    def __repr__(self) -> str:
        return "<colonna mancante>"


_MISSING = _Missing()


# ================================================================ Supporto
def _name(strategy) -> str:
    return str(getattr(strategy, "name", type(strategy).__name__))


def _run(strategy, frame: pd.DataFrame, what: str) -> pd.DataFrame:
    """
    `generate_signals` su una COPIA di `frame`. `ValueError` (dati insufficienti)
    passa com'è; ogni altro errore diventa un `RuntimeError` che dice dove (`what`).
    """
    try:
        out = strategy.generate_signals(frame.copy())
    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"generate_signals della strategia {_name(strategy)!r} è fallita su {what}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if len(out) != len(frame):
        raise ValueError(
            f"generate_signals deve restituire una riga per ogni barra: {len(out)} righe "
            f"per {len(frame)} barre ({what})."
        )
    return out


def _columns_to_check(full: pd.DataFrame, columns) -> list:
    """
    Le colonne chieste (anche una sola, come stringa), oppure `signal`, `position`
    e tutte le altre colonne restituite dalla strategia, comprese quelle d'ingresso.
    """
    if columns is not None:
        cols = [columns] if isinstance(columns, str) else list(columns)
        missing = [c for c in cols if c not in full.columns]
        if missing:
            raise ValueError(f"Colonne non prodotte dalla strategia: {missing}.")
        return cols
    missing = [c for c in _REQUIRED if c not in full.columns]
    if missing:
        raise ValueError(f"generate_signals deve produrre le colonne {missing}.")
    return [*_REQUIRED, *(c for c in full.columns if c not in _REQUIRED)]


def _scalar(value):
    """Scalare NumPy -> valore Python semplice, più leggibile negli elenchi e nei messaggi."""
    return value.item() if isinstance(value, np.generic) else value


def _is_number(value) -> bool:
    return isinstance(value, (numbers.Number, np.number)) and not isinstance(value, complex)


def _same(a, b, rtol: float, atol: float) -> bool:
    """
    Uguaglianza con tolleranza. NaN = NaN (e `None` = NaN); un NaN contro un
    numero è una differenza; gli infiniti sono uguali solo a se stessi.
    """
    a_na, b_na = bool(pd.isna(a)), bool(pd.isna(b))
    if a_na or b_na:
        return a_na and b_na
    if _is_number(a) and _is_number(b):
        a, b = float(a), float(b)
        if a == b:                      # comprende gli infiniti dello stesso segno
            return True
        if not (np.isfinite(a) and np.isfinite(b)):
            return False                # infinito contro finito o contro l'infinito opposto
        return bool(abs(a - b) <= atol + rtol * abs(b))
    try:
        return bool(a == b)
    except (TypeError, ValueError):     # oggetti non confrontabili
        return a is b


def _accepts(strategy, data: pd.DataFrame, n: int) -> bool:
    try:
        _run(strategy, data.iloc[:n], f"le prime {n} barre")
    except ValueError:
        return False
    return True


def _min_bars(strategy, data: pd.DataFrame) -> int:
    """Come `min_bars`, sapendo già che la strategia accetta la serie intera."""
    lo, hi = 1, len(data)               # hi è sempre accettato
    while lo < hi:
        mid = (lo + hi) // 2
        if _accepts(strategy, data, mid):
            hi = mid
        else:
            lo = mid + 1
    return hi


def min_bars(strategy, data: pd.DataFrame) -> int:
    """
    Numero minimo di barre iniziali di `data` che `generate_signals` accetta
    senza sollevare `ValueError` (dati insufficienti).

    Ricerca binaria: presuppone che, se n barre bastano, bastino anche n + 1.
    Solleva `ValueError` se la strategia rifiuta anche la serie intera.
    """
    if len(data) == 0 or not _accepts(strategy, data, len(data)):
        raise ValueError("La strategia non accetta la serie intera: impossibile cercare il look-ahead.")
    return _min_bars(strategy, data)


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
    `generate_signals(data).iloc[t]` su `signal`, `position` e ogni altra
    colonna restituita, comprese quelle d'ingresso (oppure solo su `columns`,
    una lista o un nome). NaN conta uguale a NaN; i numeri sono uguali se
    |troncato − completo| <= atol + rtol · |completo|; un infinito è uguale solo
    a un infinito dello stesso segno. La strategia lavora sempre su copie:
    `data` non viene modificato.

    I t vanno dalla prima barra per cui la strategia non solleva `ValueError`
    (compresa sempre nel campione, perché è il bordo del riscaldamento) alla
    penultima: sull'ultima le due serie coincidono e il confronto non direbbe nulla.

    Restituisce una lista vuota se non trova differenze. Altrimenti un elemento
    per ogni differenza, con le chiavi:
        t         -> posizione della barra (0 = prima barra)
        date      -> etichetta dell'indice in quella posizione
        column    -> colonna diversa
        truncated -> valore calcolato senza le barre successive (`<colonna
                     mancante>` se con meno barre la colonna non esiste)
        full      -> valore calcolato con l'intera serie

    Un errore della strategia diverso da `ValueError` diventa un `RuntimeError`
    che indica la barra t su cui è avvenuto.
    """
    if samples < 1:
        raise ValueError("samples deve essere almeno 1.")
    data = data.copy()                              # d'ora in poi nessuno tocca i dati di chi chiama
    full = _run(strategy, data, "la serie intera")
    cols = _columns_to_check(full, columns)

    first = _min_bars(strategy, data) - 1           # primo t calcolabile
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
        what = f"data[:t+1] con t={t} ({data.index[t]})"
        try:
            part = _run(strategy, data.iloc[: t + 1], what)
        except ValueError as exc:
            raise ValueError(
                f"La strategia {_name(strategy)!r} rifiuta {what} pur accettando serie più corte: {exc}"
            ) from exc
        for col in cols:
            # Una colonna che sparisce con meno dati dipende anch'essa dal futuro.
            truncated = part[col].iloc[-1] if col in part.columns else _MISSING
            complete = full[col].iloc[t]
            if truncated is _MISSING or not _same(truncated, complete, rtol, atol):
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
    name = _name(strategy)
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
    di `check_lookahead` e sempre su copie dei dati. Le lunghezze non minori di `len(data)` si saltano
    (sarebbero la serie intera); se non ne resta nessuna, o se una è più corta
    di `tail`, solleva `ValueError`, come fa la strategia se L non le basta.

    Lista vuota = la coda è la stessa con ogni storico. Altrimenti un elemento
    per ogni coppia (lunghezza, colonna) diversa, con le chiavi:
        length       -> L, barre dello storico corto
        column       -> colonna diversa
        n_diff       -> barre della coda che differiscono (su `tail`)
        max_abs_diff -> massima differenza assoluta (inf se un lato è NaN o
                        infinito, o se la colonna manca; NaN per colonne non numeriche)
        max_rel_diff -> massimo di |corto − completo| / |completo|
        date         -> barra della differenza assoluta più grande
        short, full  -> i due valori in quella barra (`short` è `<colonna
                        mancante>` se con lo storico corto la colonna non esiste)

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

    data = data.copy()
    full = _run(strategy, data, "la serie intera")
    cols = _columns_to_check(full, columns)
    ref = full.iloc[-tail:]

    found: list[dict] = []
    for length in usable:
        short = _run(strategy, data.iloc[-length:], f"le ultime {length} barre").iloc[-tail:]
        for col in cols:
            if col not in short.columns:
                found.append({
                    "length": length, "column": col, "n_diff": tail,
                    "max_abs_diff": float("inf"), "max_rel_diff": float("inf"),
                    "date": ref.index[0], "short": _MISSING, "full": _scalar(ref[col].iloc[0]),
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
                # NaN da un lato solo, o infiniti diversi (inf − inf = NaN): differenza
                # non misurabile, la peggiore. Dove i valori sono uguali si azzera sotto.
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
