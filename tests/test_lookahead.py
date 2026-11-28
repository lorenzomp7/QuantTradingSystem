"""Controllo automatico del look-ahead per troncamento dei dati (Piano, voce 5)."""

import numpy as np
import pandas as pd
import pytest

from strategy.lookahead import assert_no_lookahead, check_lookahead, check_recursive, min_bars
from strategy.quant_model import BaseStrategy, MovingAverageCrossover, StrategyParams
from strategy.rsi_macd import RsiMacdFuzzy, RsiMacdParams
from utils.data_fetcher import generate_mock_ohlcv


@pytest.fixture
def data() -> pd.DataFrame:
    return generate_mock_ohlcv("FUZZY", period="2y")      # 504 barre, deterministica


# ------------------------------------------------------- Strategie di prova
# Ognuna ha un look-ahead diverso se `future=True` e la versione corretta, che
# usa solo il passato, se `future=False`: la seconda fa da controllo.
class _Fake(BaseStrategy):
    def __init__(self, future: bool = True) -> None:
        super().__init__(StrategyParams())
        self.future = future

    def _finish(self, df: pd.DataFrame, long: pd.Series) -> pd.DataFrame:
        df["signal"] = long.fillna(False).astype(int)
        df["position"] = df["signal"].diff().fillna(0).astype(int)
        return df


class ShiftMinusOne(_Fake):
    """Compra se la chiusura di DOMANI è più alta: `shift(-1)`."""

    name = "fake_shift_minus_one"

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()
        df["other_close"] = df["close"].shift(-1 if self.future else 1)
        up = df["other_close"] > df["close"] if self.future else df["close"] > df["other_close"]
        return self._finish(df, up)


class CenteredMean(_Fake):
    """Media mobile centrata: metà della finestra è nel futuro."""

    name = "fake_centered_mean"

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()
        df["mean"] = df["close"].rolling(9, center=self.future, min_periods=9).mean()
        return self._finish(df, df["close"] > df["mean"])


class FullSeriesNormalisation(_Fake):
    """Z-score con media e deviazione standard dell'INTERA serie (come nella tesi fuzzy)."""

    name = "fake_full_series_normalisation"

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()
        close = df["close"]
        if self.future:
            df["z"] = (close - close.mean()) / close.std()
        else:
            df["z"] = (close - close.expanding(2).mean()) / close.expanding(2).std()
        return self._finish(df, df["z"] > 0)


def _assert_caught(strategy, data, column):
    """Il controllo trova il look-ahead nella colonna attesa e lo descrive per intero."""
    found = check_lookahead(strategy, data)
    assert found, "look-ahead non rilevato"
    assert {row["column"] for row in found} >= {column}
    for row in found:
        assert set(row) == {"t", "date", "column", "truncated", "full"}
        assert 0 <= row["t"] < len(data) - 1
        assert row["date"] == data.index[row["t"]]
        # La differenza riportata è vera: ricalcolata qui a mano.
        part = strategy.generate_signals(data.iloc[: row["t"] + 1])[row["column"]].iloc[-1]
        whole = strategy.generate_signals(data)[row["column"]].iloc[row["t"]]
        assert (pd.isna(part) and pd.isna(row["truncated"])) or part == row["truncated"]
        assert whole == row["full"]
        assert pd.isna(part) or part != whole
    with pytest.raises(AssertionError) as err:
        assert_no_lookahead(strategy, data)
    assert strategy.name in str(err.value) and column in str(err.value)
    # Controllo: la stessa strategia senza dati futuri passa.
    clean = type(strategy)(future=False)
    assert check_lookahead(clean, data) == []
    assert_no_lookahead(clean, data)


# --------------------------------------------------------------- Look-ahead
def test_ma_crossover_passes(data):
    strat = MovingAverageCrossover(StrategyParams(short_window=20, long_window=50))
    assert check_lookahead(strat, data) == []
    assert check_lookahead(strat, data, samples=len(data)) == []     # tutte le barre utili
    assert_no_lookahead(strat, data)


def test_rsi_macd_passes(data):
    strat = RsiMacdFuzzy(RsiMacdParams())
    # Anche le colonne degli indicatori, non solo il segnale.
    assert {"rsi", "macd_z", "sto", "buy_strength", "sell_strength"} <= set(strat.generate_signals(data).columns)
    assert check_lookahead(strat, data) == []
    assert check_lookahead(strat, data, samples=120, seed=3) == []
    assert_no_lookahead(strat, data)


def test_shift_minus_one_is_caught(data):
    _assert_caught(ShiftMinusOne(), data, "other_close")
    # Si vede anche guardando il solo segnale.
    assert check_lookahead(ShiftMinusOne(), data, columns=["signal"])


def test_centered_mean_is_caught(data):
    _assert_caught(CenteredMean(), data, "mean")


def test_full_series_normalisation_is_caught(data):
    _assert_caught(FullSeriesNormalisation(), data, "z")


def test_sample_is_reproducible_and_starts_after_warmup(data):
    strat = ShiftMinusOne()
    a = check_lookahead(strat, data, samples=10, seed=1)
    b = check_lookahead(strat, data, samples=10, seed=1)
    assert repr(a) == repr(b)                                        # repr: NaN non è uguale a se stesso
    assert len({row["t"] for row in a}) == 10                        # shift(-1) sbaglia a ogni barra
    assert {row["t"] for row in a} != {row["t"] for row in check_lookahead(strat, data, samples=10, seed=2)}
    # I t partono dalla prima barra per cui la strategia non solleva ValueError.
    ma = MovingAverageCrossover(StrategyParams(short_window=20, long_window=50))
    assert min_bars(ma, data) == 50
    assert min_bars(RsiMacdFuzzy(), data) == RsiMacdFuzzy().warmup_bars() + 2
    with pytest.raises(ValueError):
        check_lookahead(ma, data.head(50))                           # nessuna barra da confrontare
    with pytest.raises(ValueError):
        check_lookahead(ma, data, columns=["non_esiste"])


def test_tolerances_and_nan(data):
    class Noisy(_Fake):
        """Rumore numerico che dipende dalla lunghezza: non è look-ahead entro la tolleranza."""

        name = "fake_noisy"

        def generate_signals(self, d: pd.DataFrame) -> pd.DataFrame:
            df = d.copy()
            df["noisy"] = df["close"] * (1 + 1e-12 * len(df))
            df["always_nan"] = np.nan
            return self._finish(df, df["close"] > df["close"].shift(1))

    assert check_lookahead(Noisy(), data) == []                      # NaN = NaN, 1e-10 relativo tollerato
    strict = check_lookahead(Noisy(), data, rtol=1e-13, atol=0.0)
    assert strict and {row["column"] for row in strict} == {"noisy"}


# ---------------------------------------------- Dipendenza dallo storico
def test_recursive_ma_stable(data):
    # Le SMA sono finestre finite: con almeno long_window barre prima della coda
    # il risultato non dipende da dove comincia lo storico.
    strat = MovingAverageCrossover(StrategyParams(short_window=20, long_window=50))
    assert check_recursive(strat, data) == []
    assert check_recursive(strat, data, lengths=(100, 150, 250, 400), tail=50, rtol=1e-12) == []
    # Con meno di long_window barre prima della coda le SMA mancano: va segnalato.
    short = check_recursive(strat, data, lengths=(80,), tail=50)
    assert {row["column"] for row in short} >= {"sma_long"}
    assert all(row["length"] == 80 and row["n_diff"] > 0 for row in short)


def test_recursive_rsi_macd_indicators_within_tolerance(data):
    """
    Limite noto, misurato sulla serie mock FUZZY (504 barre), coda di 50 barre.

    RSI di Wilder e MACD sono medie esponenziali: ricordano l'inizio dello
    storico e lo dimenticano in modo geometrico. Differenze massime misurate
    rispetto alla serie intera (assoluta; relativa tra parentesi):

        storico   rsi (punti)        macd_z (dev. std)     buy/sell_strength
        150       0,073 (1,3e-3)     0,013 (0,27)          0,0028 (4,9e-2)
        250       < 1e-6 relativo    3,5e-6 (7,3e-5)       7,5e-7 (1,3e-5)
        400       nessuna differenza oltre rtol = 1e-6 su tutte le colonne

    Lo stocastico è una finestra finita e non dipende dallo storico. Il segnale
    è a stato (resta com'era tra una soglia e l'altra): su questa serie coincide
    da 150 barre in su, ma con 100 barre (26 utili prima della coda, dopo il
    riscaldamento di 74) è diverso in 25 barre su 50. Regola pratica ricavata
    da queste misure: dare alla strategia almeno 250 barre di storico. Vale per
    questa serie e per i parametri predefiniti, non è una garanzia.
    """
    strat = RsiMacdFuzzy(RsiMacdParams())
    by = {(row["length"], row["column"]): row for row in check_recursive(strat, data, lengths=(150, 250, 400))}

    # 400 barre: tutto entro la tolleranza predefinita (rtol = 1e-6).
    assert not [key for key in by if key[0] == 400]
    # Lo stocastico non compare mai; il segnale coincide da 150 barre in su.
    assert not [key for key in by if key[1] in {"sto", "signal", "position"}]

    # 150 barre: la differenza c'è, viene riportata e sta nei limiti misurati.
    assert by[150, "rsi"]["n_diff"] == 50
    assert 0.05 < by[150, "rsi"]["max_abs_diff"] < 0.1               # misurato 0,073 punti RSI
    assert 0.005 < by[150, "macd_z"]["max_abs_diff"] < 0.02          # misurato 0,013
    for col in ("buy_strength", "sell_strength"):
        assert by[150, col]["max_abs_diff"] < 0.005                  # misurato 0,0015 e 0,0028

    # 250 barre: l'RSI è già entro 1e-6, il MACD normalizzato non ancora.
    assert (250, "rsi") not in by
    assert 1e-6 < by[250, "macd_z"]["max_abs_diff"] < 1e-5           # misurato 3,5e-6
    assert by[250, "macd_z"]["max_rel_diff"] < 1e-4                  # misurato 7,3e-5
    # Con una tolleranza dichiarata di 1e-4 relativo, 250 barre bastano a tutti gli indicatori.
    assert check_recursive(strat, data, lengths=(250, 400), rtol=1e-4) == []

    # Storico troppo corto: il segnale stesso cambia (limite noto del segnale a stato).
    short = {row["column"]: row for row in check_recursive(strat, data, lengths=(100,))}
    assert short["signal"]["n_diff"] == 25                           # misurato: 25 barre su 50
    assert np.isinf(short["macd_z"]["max_abs_diff"])                 # NaN da un lato: ancora in riscaldamento


def test_recursive_rejects_unusable_lengths(data):
    strat = MovingAverageCrossover()
    with pytest.raises(ValueError):
        check_recursive(strat, data, lengths=(len(data), 5000))      # nessuna più corta della serie
    with pytest.raises(ValueError):
        check_recursive(strat, data, lengths=(30,), tail=50)         # più corta della coda
    with pytest.raises(ValueError):
        check_recursive(strat, data, lengths=(60,), tail=50, columns=["non_esiste"])
