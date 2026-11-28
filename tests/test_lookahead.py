"""Controllo automatico del look-ahead per troncamento dei dati (Piano, voce 5)."""

import importlib
import inspect
import pkgutil

import numpy as np
import pandas as pd
import pytest

import strategy
from strategy import lookahead
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

    def _causal(self, data: pd.DataFrame) -> pd.DataFrame:
        """Segnale corretto (chiusura sopra quella di ieri), per le strategie che sbagliano altrove."""
        df = data.copy()
        return self._finish(df, df["close"] > df["close"].shift(1))


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


def _assert_caught(strat, data, column):
    """Il controllo trova il look-ahead nella colonna attesa e lo descrive per intero."""
    found = check_lookahead(strat, data)
    assert found, "look-ahead non rilevato"
    assert {row["column"] for row in found} >= {column}
    for row in found:
        assert set(row) == {"t", "date", "column", "truncated", "full"}
        assert 0 <= row["t"] < len(data) - 1
        assert row["date"] == data.index[row["t"]]
        # La differenza riportata è vera: ricalcolata qui a mano.
        part = strat.generate_signals(data.iloc[: row["t"] + 1].copy())[row["column"]].iloc[-1]
        whole = strat.generate_signals(data.copy())[row["column"]].iloc[row["t"]]
        assert (pd.isna(part) and pd.isna(row["truncated"])) or part == row["truncated"]
        assert (pd.isna(whole) and pd.isna(row["full"])) or whole == row["full"]
        assert pd.isna(part) != pd.isna(whole) or part != whole
    with pytest.raises(AssertionError) as err:
        assert_no_lookahead(strat, data)
    assert strat.name in str(err.value) and column in str(err.value)
    # Controllo: la stessa strategia senza dati futuri passa.
    clean = type(strat)(future=False)
    assert check_lookahead(clean, data) == []
    assert_no_lookahead(clean, data)


# --------------------------------------------------------------- Look-ahead
def test_ma_crossover_passes(data):
    strat = MovingAverageCrossover(StrategyParams(short_window=20, long_window=50))
    assert check_lookahead(strat, data) == []
    assert check_lookahead(strat, data, samples=10, seed=5, columns=["signal", "sma_long"]) == []
    assert_no_lookahead(strat, data)


def test_rsi_macd_passes(data):
    strat = RsiMacdFuzzy(RsiMacdParams())
    # Anche le colonne degli indicatori, non solo il segnale.
    assert {"rsi", "macd_z", "sto", "buy_strength", "sell_strength"} <= set(strat.generate_signals(data).columns)
    assert check_lookahead(strat, data) == []
    assert check_lookahead(strat, data, samples=20, seed=3) == []
    assert_no_lookahead(strat, data)


def _concrete_strategies() -> list:
    """Ogni sottoclasse concreta di `BaseStrategy` definita nel pacchetto `strategy`."""
    for mod in pkgutil.iter_modules(strategy.__path__):
        importlib.import_module(f"strategy.{mod.name}")
    found, todo = [], [BaseStrategy]
    while todo:
        for sub in todo.pop().__subclasses__():
            todo.append(sub)
            if sub.__module__.startswith("strategy.") and not inspect.isabstract(sub) and sub not in found:
                found.append(sub)
    return sorted(found, key=lambda cls: cls.__name__)


def test_strategy_discovery_finds_the_known_ones():
    assert {MovingAverageCrossover, RsiMacdFuzzy} <= set(_concrete_strategies())


@pytest.mark.parametrize("cls", _concrete_strategies(), ids=lambda cls: cls.__name__)
def test_every_strategy_has_no_lookahead(cls, data):
    # Test obbligatorio: una strategia nuova in `strategy/` entra qui da sola,
    # con i parametri predefiniti e su tutte le barre utili.
    before = data.copy()
    assert_no_lookahead(cls(), data, samples=len(data))
    pd.testing.assert_frame_equal(data, before)


def test_shift_minus_one_is_caught(data):
    _assert_caught(ShiftMinusOne(), data, "other_close")
    # Si vede anche guardando il solo segnale (accettato anche come stringa singola).
    only_signal = check_lookahead(ShiftMinusOne(), data, columns=["signal"])
    assert only_signal and {row["column"] for row in only_signal} == {"signal"}
    assert repr(check_lookahead(ShiftMinusOne(), data, columns="signal")) == repr(only_signal)


def test_centered_mean_is_caught(data):
    _assert_caught(CenteredMean(), data, "mean")


def test_full_series_normalisation_is_caught(data):
    _assert_caught(FullSeriesNormalisation(), data, "z")


# ---------------------------------------- Strategie che toccano i dati ricevuti
class InPlacePeek(_Fake):
    """Aggiunge `peek = close.shift(-1)` direttamente sul DataFrame ricevuto."""

    name = "fake_in_place_peek"

    def __init__(self, only_if_missing: bool = False) -> None:
        super().__init__()
        self.only_if_missing = only_if_missing

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        if not (self.only_if_missing and "peek" in data.columns):
            data["peek"] = data["close"].shift(-1)
        return self._finish(data, data["peek"] > data["close"])


@pytest.mark.parametrize("only_if_missing", [False, True])
def test_in_place_mutation_is_caught_and_caller_data_untouched(data, only_if_missing):
    before = data.copy()
    strat = InPlacePeek(only_if_missing)
    found = check_lookahead(strat, data)
    assert {"peek", "signal"} <= {row["column"] for row in found}
    with pytest.raises(AssertionError):
        assert_no_lookahead(strat, data)
    check_recursive(strat, data)
    min_bars(strat, data)
    # La strategia ha lavorato su copie: i dati di chi chiama sono quelli di prima.
    assert list(data.columns) == list(before.columns)
    pd.testing.assert_frame_equal(data, before)


class OverwriteClose(_Fake):
    """Sovrascrive una colonna d'ingresso con il suo valore futuro."""

    name = "fake_overwrite_close"

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = self._causal(data)
        df["close"] = df["close"].shift(-1).fillna(df["close"])
        return df


class ScaleVolume(_Fake):
    """Normalizza il volume con il massimo dell'intera serie."""

    name = "fake_scale_volume"

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = self._causal(data)
        df["volume"] /= df["volume"].max()
        return df


@pytest.mark.parametrize("cls, column", [(OverwriteClose, "close"), (ScaleVolume, "volume")])
def test_overwritten_input_column_is_caught(data, cls, column):
    found = check_lookahead(cls(), data)
    assert found and {row["column"] for row in found} == {column}
    with pytest.raises(AssertionError, match=column):
        assert_no_lookahead(cls(), data)


class InfiniteFlag(_Fake):
    """Il dato futuro compare come infinito: non deve passare per «uguale»."""

    name = "fake_infinite_flag"

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = self._causal(data)
        df["flag"] = np.where(df["close"].shift(-1) > df["close"], np.inf, 1.0)
        return df


def test_infinities_are_compared_exactly(data):
    same = lookahead._same
    inf = float("inf")
    assert same(inf, inf, 1e-9, 1e-12) and same(-inf, -inf, 1e-9, 1e-12)
    assert not same(5.0, inf, 1e-9, 1e-12)
    assert not same(inf, 5.0, 1e-9, 1e-12)
    assert not same(-inf, inf, 1e-9, 1e-12)
    assert not same(inf, -inf, 1e-9, 1e-12)
    assert not same(np.float64(1e300), np.float64(np.inf), 0.5, 1.0)
    assert not same(inf, float("nan"), 1e-9, 1e-12)
    found = check_lookahead(InfiniteFlag(), data)
    assert found and {row["column"] for row in found} == {"flag"}
    assert all(row["truncated"] == 1.0 and row["full"] == inf for row in found)
    # La coda non dipende dallo storico: nessuna differenza tra storici diversi.
    assert check_recursive(InfiniteFlag(), data) == []


class OnlyPositionWrong(_Fake):
    """`signal` è corretto, `position` anticipa il cambio di domani."""

    name = "fake_only_position_wrong"

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = self._causal(data)
        df["position"] = (df["signal"].shift(-1) - df["signal"]).fillna(0).astype(int)
        return df


def test_position_alone_is_checked(data):
    found = check_lookahead(OnlyPositionWrong(), data)
    assert found and {row["column"] for row in found} == {"position"}


class PeekFromDate(_Fake):
    """Look-ahead solo dalle barre con data >= `start` (e, se dato, <= `end`)."""

    name = "fake_peek_from_date"

    def __init__(self, start, end=None, min_len: int = 1) -> None:
        super().__init__()
        self.start, self.end, self.min_len = start, end, min_len

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        if len(data) < self.min_len:
            raise ValueError(f"Dati insufficienti: servono almeno {self.min_len} barre.")
        df = self._causal(data)
        late = df.index >= self.start
        if self.end is not None:
            late &= df.index <= self.end
        df["peek"] = df["close"].shift(-1).where(late, df["close"])
        return df


def test_every_bar_is_checked_up_to_the_second_last(data):
    n = len(data)
    # Look-ahead solo nelle ultime 10 barre: con samples=len(data) si trovano tutte
    # tranne l'ultima, dove troncato e completo coincidono per costruzione.
    tail_only = check_lookahead(PeekFromDate(data.index[n - 10]), data, samples=n)
    assert {row["t"] for row in tail_only} == set(range(n - 10, n - 1))
    # Look-ahead ovunque: tutte le barre da 0 alla penultima.
    everywhere = check_lookahead(ShiftMinusOne(), data, samples=n, columns=["other_close"])
    assert [row["t"] for row in everywhere] == list(range(n - 1))
    # samples maggiore delle barre disponibili non è un errore.
    assert len(check_lookahead(ShiftMinusOne(), data.head(60), samples=1000, columns=["other_close"])) == 59


def test_first_checked_bar_is_the_first_the_strategy_accepts(data):
    # La strategia rifiuta meno di 30 barre e guarda avanti solo sulla barra 29,
    # la prima calcolabile: con samples=1 si controlla proprio quella.
    strat = PeekFromDate(data.index[29], end=data.index[29], min_len=30)
    assert min_bars(strat, data) == 30
    found = check_lookahead(strat, data, samples=1)
    assert [(row["t"], row["column"]) for row in found] == [(29, "peek")]
    # I t partono dalla prima barra per cui la strategia non solleva ValueError.
    ma = MovingAverageCrossover(StrategyParams(short_window=20, long_window=50))
    assert min_bars(ma, data) == 50
    assert min_bars(RsiMacdFuzzy(), data) == RsiMacdFuzzy().warmup_bars() + 2
    with pytest.raises(ValueError):
        check_lookahead(ma, data.head(50))                           # nessuna barra da confrontare
    with pytest.raises(ValueError):
        check_lookahead(ma, data.head(49))                           # la strategia rifiuta la serie
    with pytest.raises(ValueError):
        check_lookahead(ma, data, columns=["non_esiste"])
    with pytest.raises(ValueError):
        check_lookahead(ma, data, samples=0)


def test_sample_is_reproducible(data):
    strat = ShiftMinusOne()
    a = check_lookahead(strat, data, samples=10, seed=1)
    b = check_lookahead(strat, data, samples=10, seed=1)
    assert repr(a) == repr(b)                                        # repr: NaN non è uguale a se stesso
    assert len({row["t"] for row in a}) == 10                        # shift(-1) sbaglia a ogni barra
    assert {row["t"] for row in a} != {row["t"] for row in check_lookahead(strat, data, samples=10, seed=2)}
    # Predefiniti: 50 barre, seed 0.
    default = check_lookahead(strat, data, columns=["other_close"])
    assert len(default) == 50
    assert repr(default) == repr(check_lookahead(strat, data, samples=50, seed=0, columns=["other_close"]))


class LengthNoise(_Fake):
    """Colonne che dipendono (di pochissimo) dalla lunghezza della serie: rumore, non look-ahead."""

    name = "fake_length_noise"

    def __init__(self, rel: float = 0.0, abs_: float = 0.0) -> None:
        super().__init__()
        self.rel, self.abs = rel, abs_

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = self._causal(data)
        df["noisy"] = df["close"] * (1 + self.rel * len(df))        # errore relativo <= rel * len
        df["tiny"] = self.abs * len(df)                              # vicino a zero: errore assoluto <= abs * len
        df["always_nan"] = np.nan
        return df


def test_default_tolerances_are_fixed(data):
    assert len(data) == 504
    # Predefiniti dichiarati.
    look = inspect.signature(check_lookahead).parameters
    rec = inspect.signature(check_recursive).parameters
    assert (look["samples"].default, look["seed"].default) == (50, 0)
    assert (look["rtol"].default, look["atol"].default) == (1e-9, 1e-12)
    assert (rec["tail"].default, rec["rtol"].default, rec["atol"].default) == (50, 1e-6, 1e-12)
    # E usati davvero. La barra 0, sempre nel campione, è la peggiore: tra 1 e 504 barre
    # l'errore relativo è rel * 503. Con 1e-12 fa 5e-10 e passa, con 2e-11 fa 1e-8 e no.
    # NaN = NaN.
    assert check_lookahead(LengthNoise(rel=1e-12), data) == []
    over = check_lookahead(LengthNoise(rel=2e-11), data)
    assert over and {row["column"] for row in over} == {"noisy"}
    # Assoluto: 1e-15 * 504 = 5e-13 passa, 1e-13 * 504 = 5e-11 no (il relativo qui non aiuta).
    assert check_lookahead(LengthNoise(abs_=1e-15), data) == []
    over = check_lookahead(LengthNoise(abs_=1e-13), data)
    assert over and {row["column"] for row in over} == {"tiny"}
    # Tolleranze esplicite più strette trovano anche il rumore ammesso prima.
    strict = check_lookahead(LengthNoise(rel=1e-12), data, rtol=1e-13, atol=0.0)
    assert strict and {row["column"] for row in strict} == {"noisy"}


class Notes(_Fake):
    """Colonna di testo con `None` legittimi; `late` esiste solo con almeno `late_from` barre."""

    name = "fake_notes"

    def __init__(self, late_from: int | None = None) -> None:
        super().__init__()
        self.late_from = late_from

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = self._causal(data)
        df["note"] = pd.Series([("long" if s else None) for s in df["signal"]], index=df.index, dtype=object)
        df["empty"] = pd.Series([None] * len(df), index=df.index, dtype=object)
        if self.late_from is not None and len(df) >= self.late_from:
            df["late"] = 1.0
        return df


def test_none_values_are_not_missing_columns(data):
    # `None` in una colonna di oggetti è un valore come NaN, non una colonna mancante.
    out = Notes().generate_signals(data)
    assert out["note"].isna().any() and out["note"].notna().any()
    assert check_lookahead(Notes(), data) == []
    assert check_recursive(Notes(), data) == []


def test_column_missing_on_truncated_data_is_reported(data):
    short = data.head(80)
    found = check_lookahead(Notes(late_from=40), short, samples=len(short))
    assert {row["column"] for row in found} == {"late"}
    assert {row["t"] for row in found} == set(range(0, 39))          # con t >= 39 la colonna c'è
    assert all(row["truncated"] is lookahead._MISSING and row["full"] == 1.0 for row in found)
    with pytest.raises(AssertionError, match="colonna mancante"):
        assert_no_lookahead(Notes(late_from=300), data)
    # Lo stesso tra storici di lunghezza diversa.
    rec = check_recursive(Notes(late_from=300), data, lengths=(150, 400))
    assert [(row["length"], row["column"], row["n_diff"]) for row in rec] == [(150, "late", 50)]
    assert rec[0]["short"] is lookahead._MISSING and rec[0]["full"] == 1.0


class Breaks(_Fake):
    """Solleva un errore che non è `ValueError` con una lunghezza precisa."""

    name = "fake_breaks"

    def __init__(self, at: int) -> None:
        super().__init__()
        self.at = at

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        if len(data) == self.at:
            raise ZeroDivisionError("divisione per zero nell'indicatore")
        return self._causal(data)


def test_other_exceptions_name_the_bar(data):
    with pytest.raises(RuntimeError, match=r"fake_breaks.*t=200\b.*ZeroDivisionError") as err:
        check_lookahead(Breaks(at=201), data, samples=len(data))
    assert isinstance(err.value.__cause__, ZeroDivisionError)
    with pytest.raises(RuntimeError, match=r"prime 126 barre.*ZeroDivisionError"):
        min_bars(Breaks(at=126), data)                         # 126: una lunghezza che la ricerca binaria prova
    with pytest.raises(RuntimeError, match=r"150 barre.*ZeroDivisionError"):
        check_recursive(Breaks(at=150), data)


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


class SingleEma(_Fake):
    """Una sola EMA ricorsiva, che parte dalla prima chiusura dello storico."""

    name = "fake_single_ema"

    def __init__(self, alpha: float) -> None:
        super().__init__()
        self.alpha = alpha

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = self._causal(data)
        df["ema"] = df["close"].ewm(alpha=self.alpha, adjust=False).mean()
        return df


@pytest.mark.parametrize("alpha, length", [(0.05, 100), (0.05, 200), (0.1, 150), (2 / 27, 250)])
def test_recursive_measures_the_analytic_ema_error(data, alpha, length):
    # EMA(t) = α·close(t) + (1−α)·EMA(t−1), con EMA = close sulla prima barra.
    # Partendo L barre prima della fine, l'errore iniziale e0 = close − EMA_completa
    # su quella barra si riduce di (1−α) a ogni passo: alla prima barra della coda
    # (L − tail passi dopo) vale esattamente |e0|·(1−α)^(L−tail), poi cala ancora.
    tail, n = 50, len(data)
    strat = SingleEma(alpha)
    assert check_lookahead(strat, data) == []                        # ricorsiva ma non guarda avanti
    full = strat.generate_signals(data)
    e0 = abs(data["close"].iloc[n - length] - full["ema"].iloc[n - length])
    expected = e0 * (1 - alpha) ** (length - tail)
    rows = check_recursive(strat, data, lengths=(length,), tail=tail, rtol=1e-12, atol=0.0)
    assert [row["column"] for row in rows] == ["ema"]
    row = rows[0]
    assert set(row) == {"length", "column", "n_diff", "max_abs_diff", "max_rel_diff", "date", "short", "full"}
    assert row["length"] == length and row["n_diff"] == tail
    assert row["max_abs_diff"] == pytest.approx(expected, rel=1e-6)
    assert row["date"] == data.index[n - tail]                       # la prima barra della coda è la peggiore
    assert abs(row["short"] - row["full"]) == pytest.approx(expected, rel=1e-6)
    assert row["full"] == full["ema"].iloc[n - tail]
    assert row["max_rel_diff"] >= row["max_abs_diff"] / full["ema"].iloc[-tail:].max()
    # La tolleranza decide: appena sopra il limite non c'è nulla da segnalare, sotto sì.
    worst_rel = row["max_rel_diff"]
    assert check_recursive(strat, data, lengths=(length,), tail=tail, rtol=worst_rel * 1.01, atol=0.0) == []
    assert check_recursive(strat, data, lengths=(length,), tail=tail, rtol=worst_rel * 0.5, atol=0.0)


def test_recursive_fast_ema_forgets_its_start(data):
    # α = 0,5: dopo 100 barre l'errore è e0·2^−100, sotto ogni tolleranza.
    assert check_recursive(SingleEma(0.5), data, lengths=(150,)) == []


@pytest.mark.parametrize("symbol", ["FUZZY", "TEST", "SPY", "AAPL", "BTC-USD", "ENI.MI"])
def test_recursive_rsi_macd_indicators_within_tolerance(symbol):
    """
    Limite noto di RsiMacdFuzzy: gli indicatori dipendono dall'inizio dello storico.

    RSI di Wilder e MACD sono medie esponenziali: ricordano la prima barra e la
    dimenticano in modo geometrico. Il test fissa solo ciò che vale per ogni
    serie; i numeri qui sotto sono misure, e cambiano con la serie.

    Differenza massima sulla coda di 50 barre rispetto alla serie intera, con i
    parametri predefiniti. Serie mock FUZZY (504 barre), assoluta (relativa):

        storico   rsi (punti)        macd_z (dev. std)     buy/sell_strength
        150       0,073 (1,3e-3)     0,013 (0,27)          0,0028 (4,9e-2)
        250       sotto 1e-6 rel.    3,5e-6 (7,3e-5)       7,5e-7 (1,3e-5)
        400       nessuna differenza oltre rtol = 1e-6 su tutte le colonne

    Su 16 serie mock (2 anni): a 150 barre rsi tra 0,003 e 0,10 punti e macd_z
    tra 1,2e-4 e 0,014; a 250 barre macd_z fino a 2,1e-5 e rsi entro 1e-6 in 15
    casi su 16 (1,2e-4 nell'altro); a 400 barre nessuna differenza in nessuna.

    Lo stocastico è una finestra finita e non dipende dallo storico. Il segnale
    è a stato (resta com'era tra una soglia e l'altra): nelle 16 serie coincide
    da 150 barre in su; con 100 barre (26 utili prima della coda, dopo il
    riscaldamento di 74) è diverso in 13 serie su 16, per esempio in 25 barre
    su 50 su FUZZY. Regola pratica ricavata da queste misure: almeno 250 barre
    di storico, 400 per riprodurre il backtest entro 1e-6. Vale per le serie
    mock e i parametri predefiniti, non è una garanzia.
    """
    data = generate_mock_ohlcv(symbol, period="2y")
    strat = RsiMacdFuzzy(RsiMacdParams())
    by = {(row["length"], row["column"]): row
          for row in check_recursive(strat, data, lengths=(100, 150, 250, 400))}

    # 400 barre: tutto entro la tolleranza predefinita (rtol = 1e-6).
    assert not [key for key in by if key[0] == 400]
    # Lo stocastico e i dati d'ingresso non compaiono mai.
    assert not [key for key in by if key[1] in {"sto", "open", "high", "low", "close", "volume"}]
    # 150 barre: la dipendenza c'è e viene riportata, non nascosta dalla tolleranza.
    for col in ("rsi", "macd_z"):
        assert by[150, col]["n_diff"] > 0
        assert 0 < by[150, col]["max_abs_diff"] < np.inf
        # Con più storico la differenza cala (0 = non più segnalata).
        longer = by.get((250, col), {"max_abs_diff": 0.0})["max_abs_diff"]
        assert longer < by[150, col]["max_abs_diff"]
    # 100 barre: la coda comincia dentro il riscaldamento, macd_z manca da un lato.
    assert by[100, "macd_z"]["n_diff"] == 50
    assert np.isinf(by[100, "macd_z"]["max_abs_diff"])
    if symbol == "FUZZY":
        # Il segnale a stato cambia davvero (misurato: 25 barre su 50). Non vale per
        # ogni serie: se nel tratto mancante la strategia era comunque flat, coincide.
        assert by[100, "signal"]["n_diff"] > 0


def test_recursive_rejects_unusable_lengths(data):
    strat = MovingAverageCrossover()
    with pytest.raises(ValueError):
        check_recursive(strat, data, lengths=(len(data), 5000))      # nessuna più corta della serie
    with pytest.raises(ValueError):
        check_recursive(strat, data, lengths=(30,), tail=50)         # più corta della coda
    with pytest.raises(ValueError):
        check_recursive(strat, data, lengths=(60,), tail=50, columns=["non_esiste"])
    with pytest.raises(ValueError):
        check_recursive(strat, data, tail=0)
    # Le lunghezze non minori della serie si saltano, le altre si usano.
    rows = check_recursive(SingleEma(0.05), data, lengths=(100, len(data), 9999), rtol=1e-12, columns="ema")
    assert [row["length"] for row in rows] == [100]
