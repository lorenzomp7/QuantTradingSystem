"""Regola d'esecuzione del backtest: ordine all'open della barra successiva (punto 2 del Piano)."""

import numpy as np
import pandas as pd
import pytest

from strategy.quant_model import BaseStrategy, MovingAverageCrossover, StrategyParams, position_returns
from strategy.research import _window_trades, window_returns
from strategy.rsi_macd import RsiMacdParams

OPEN = [10, 10, 10, 11, 13, 12, 9, 9.5]
CLOSE = [10, 10, 11, 12, 12.5, 10, 9, 10]
SIGNAL = [0, 0, 1, 1, 0, 0, 1, 1]


class FixedSignal(BaseStrategy):
    """Strategia di prova: il segnale è già nella colonna `sig` dei dati."""

    name = "fixed_signal"

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()
        df["signal"] = df["sig"].astype(int)
        df["position"] = df["signal"].diff().fillna(0).astype(int)
        return df


def _frame(open_, close, signal) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(close), freq="D")
    return pd.DataFrame({"open": np.asarray(open_, dtype=float), "close": np.asarray(close, dtype=float),
                         "sig": signal}, index=idx)


def _run(open_=OPEN, close=CLOSE, signal=SIGNAL, **params):
    params.setdefault("fee_bps", 0)
    data = _frame(open_, close, signal)
    return FixedSignal(StrategyParams(short_window=1, long_window=2, **params)).backtest(data, bars_per_year=252)


# ------------------------------------------------------- Controllo a mano
def test_hand_check_next_open():
    res = _run()
    expected = [10000, 10000, 10000, 10909.09, 11363.64, 10909.09, 10909.09, 11483.25]
    np.testing.assert_allclose(res.equity_curve.to_numpy(), expected, atol=0.005)
    idx = res.equity_curve.index
    assert list(res.trades.index) == [idx[3], idx[5], idx[7]]
    assert list(res.trades["side"]) == ["BUY", "SELL", "BUY"]
    assert list(res.trades["price"]) == [11.0, 12.0, 9.5]
    assert list(res.signals["in_market"]) == [0, 0, 0, 1, 1, 0, 0, 1]
    assert res.metrics["num_trades"] == 3 and res.metrics["pending_order"] is None
    assert res.metrics["params"]["execution"] == "next_open"


def test_next_open_entry_bar_earns_open_to_close():
    res = _run()
    eq = res.equity_curve
    assert eq.iloc[3] / eq.iloc[2] == pytest.approx(12 / 11)          # close(3) / open(3)
    assert eq.iloc[7] / eq.iloc[6] == pytest.approx(10 / 9.5)


def test_next_open_exit_bar_earns_prev_close_to_open():
    res = _run()
    eq = res.equity_curve
    assert eq.iloc[5] / eq.iloc[4] == pytest.approx(12 / 12.5)        # open(5) / close(4)
    assert eq.iloc[6] == pytest.approx(eq.iloc[5])                    # poi flat


def test_gap_between_signal_close_and_next_open_is_not_earned():
    # Segnale alla barra 2 (close 11), open della barra dopo a 13: il salto 11 -> 13 non è della strategia.
    res = _run(open_=[10, 10, 10, 13, 13], close=[10, 10, 11, 13, 13], signal=[0, 0, 1, 1, 1])
    assert res.equity_curve.iloc[-1] == pytest.approx(10_000.0)
    cheat = _run(open_=[10, 10, 10, 13, 13], close=[10, 10, 11, 13, 13], signal=[0, 0, 1, 1, 1], execution="close")
    assert cheat.equity_curve.iloc[-1] == pytest.approx(10_000.0 * 13 / 11)


def test_trades_recorded_at_execution_bar_open():
    res = _run()
    data = _frame(OPEN, CLOSE, SIGNAL)
    for ts, row in res.trades.iterrows():
        assert row["price"] == data.loc[ts, "open"]
        assert res.signals.loc[ts, "in_market"] == (1 if row["side"] == "BUY" else 0)
    # `position` e `signal` restano sulla barra del segnale (una barra prima dell'esecuzione).
    assert list(res.signals.index[res.signals["position"] != 0]) == [data.index[2], data.index[4], data.index[6]]


# ----------------------------------------------------------- Modo close
def test_close_mode_matches_legacy_formula():
    close = pd.Series(CLOSE, dtype=float)
    held = pd.Series(SIGNAL, dtype=float).shift(1).fillna(0.0)
    ret = close.pct_change().fillna(0.0)
    turnover = held.diff().abs().fillna(held.abs())
    # Senza commissioni la formula coincide con quella storica: held · ret.
    res0 = _run(execution="close")
    np.testing.assert_allclose(res0.equity_curve.pct_change().fillna(0.0).to_numpy(), (held * ret).to_numpy(),
                               atol=1e-12)
    # Con commissioni cambia solo la forma (moltiplicativa invece di sottratta): differenza c·|ret| sul cambio.
    c = 5 / 10_000
    res = _run(execution="close", fee_bps=5)
    new = res.equity_curve.pct_change().fillna(0.0).to_numpy()
    legacy = (held * ret - turnover * c).to_numpy()
    assert np.all(np.abs(new - legacy) <= (turnover * c * ret.abs()).to_numpy() + 1e-12)
    np.testing.assert_allclose(new, legacy, atol=1e-4)
    # Trade sulla barra del segnale, al suo close (come prima).
    idx = res.equity_curve.index
    assert list(res.trades.index) == [idx[2], idx[4], idx[6]]
    assert list(res.trades["price"]) == [11.0, 12.5, 9.0]


def test_open_equal_prev_close_makes_modes_identical():
    close = np.array([10, 10.5, 11, 10.8, 11.5, 12, 11.2, 11.9])
    open_ = np.r_[close[0], close[:-1]]
    a = _run(open_=open_, close=close, fee_bps=7)
    b = _run(open_=open_, close=close, fee_bps=7, execution="close")
    np.testing.assert_allclose(a.equity_curve.to_numpy(), b.equity_curve.to_numpy(), rtol=1e-12)
    assert list(a.trades["price"]) == list(b.trades["price"])


# ----------------------------------------------------------- Commissioni
def test_fee_charged_on_execution_bar():
    c = 10 / 10_000
    res = _run(fee_bps=10)
    r = res.equity_curve.pct_change().fillna(0.0)
    assert r.iloc[2] == 0.0                                           # barra del segnale: nessun costo
    assert r.iloc[3] == pytest.approx(12 / 11 * (1 - c) - 1)          # ingresso
    assert r.iloc[4] == pytest.approx(12.5 / 12 - 1)                  # in posizione: nessun costo
    assert r.iloc[5] == pytest.approx(12 / 12.5 * (1 - c) - 1)        # uscita
    assert res.equity_curve.iloc[-1] < _run().equity_curve.iloc[-1]


def test_position_returns_slippage_moves_fill_price():
    close = pd.Series(CLOSE, dtype=float)
    open_ = pd.Series(OPEN, dtype=float)
    held = pd.Series(SIGNAL, dtype=float).shift(1).fillna(0.0)
    r0, turnover, p0 = position_returns(close, open_, held, fee_bps=0)
    r, _, p = position_returns(close, open_, held, fee_bps=0, slippage_bps=20)
    assert list(turnover) == [0, 0, 0, 1, 0, 1, 0, 1]
    assert p.iloc[3] == pytest.approx(11 * 1.002) and p.iloc[5] == pytest.approx(12 * 0.998)
    assert p0.iloc[3] == 11 and p0.iloc[5] == 12
    assert r.iloc[3] < r0.iloc[3] and r.iloc[5] < r0.iloc[5] and r.iloc[4] == pytest.approx(r0.iloc[4])


# ---------------------------------------------------------- Ultima barra
def test_last_bar_signal_is_pending_not_traded():
    for mode in ("next_open", "close"):
        buy = _run(signal=[0, 0, 1, 1, 0, 0, 0, 1], execution=mode)
        assert buy.metrics["pending_order"] == "BUY"
        assert buy.metrics["num_trades"] == 2 and list(buy.trades["side"]) == ["BUY", "SELL"]
        assert buy.signals["in_market"].iloc[-1] == 0
        sell = _run(signal=[0, 0, 1, 1, 1, 1, 1, 0], execution=mode)
        assert sell.metrics["pending_order"] == "SELL"
        assert list(sell.trades["side"]) == ["BUY"]
    # Posizione aperta a fine serie: valorizzata all'ultimo close, senza commissione d'uscita.
    c = 10 / 10_000
    held_to_end = _run(signal=[0, 0, 1, 1, 1, 1, 1, 1], fee_bps=10)
    assert held_to_end.equity_curve.iloc[-1] == pytest.approx(10_000 * (CLOSE[-1] / OPEN[3]) * (1 - c))
    assert held_to_end.metrics["pending_order"] is None


# -------------------------------------------------------- Open mancante
def test_missing_open_column_raises():
    data = _frame(OPEN, CLOSE, SIGNAL).drop(columns=["open"])
    strat = FixedSignal(StrategyParams(short_window=1, long_window=2))
    with pytest.raises(ValueError, match="execution='close'"):
        strat.backtest(data)
    ok = FixedSignal(StrategyParams(short_window=1, long_window=2, execution="close")).backtest(data)
    assert ok.metrics["open_missing_bars"] == 0


def test_nan_open_falls_back_to_prev_close():
    open_ = list(OPEN)
    open_[3] = np.nan            # barra d'ingresso senza open: si esegue al close precedente (11)
    open_[5] = 0.0               # barra d'uscita con open non valido: close precedente (12,5)
    res = _run(open_=open_)
    assert res.metrics["open_missing_bars"] == 2
    assert list(res.trades["price"]) == [11.0, 12.5, 9.5]
    eq = res.equity_curve
    assert eq.iloc[3] / eq.iloc[2] == pytest.approx(12 / 11)
    assert eq.iloc[5] == pytest.approx(eq.iloc[4])
    assert np.isfinite(eq.to_numpy()).all()
    assert _run().metrics["open_missing_bars"] == 0


# ------------------------------------------------------------ Parametri
def test_invalid_execution_raises():
    with pytest.raises(ValueError):
        StrategyParams(execution="vwap")
    with pytest.raises(ValueError):
        RsiMacdParams(execution="open")
    with pytest.raises(ValueError):
        position_returns(pd.Series(CLOSE), pd.Series(OPEN), pd.Series(SIGNAL), 0, execution="nope")
    assert StrategyParams().execution == "next_open" and RsiMacdParams().execution == "next_open"
    assert StrategyParams(execution="close").execution == "close"


# -------------------------------------------------------------- Finestre
def test_window_returns_next_open():
    c = 10 / 10_000
    data = _frame(OPEN, CLOSE, [0, 0, 1, 1, 0, 0, 1, 1])
    strat = FixedSignal(StrategyParams(short_window=1, long_window=2, fee_bps=10))
    w = window_returns(strat, data, 1, 8)
    assert list(w.positions) == [0, 0, 1, 1, 0, 0, 1]
    assert w.returns.iloc[2] == pytest.approx(12 / 11 * (1 - c) - 1)           # ingresso all'open della barra 3
    assert w.returns.iloc[4] == pytest.approx(12 / 12.5 * (1 - c) - 1)         # uscita all'open della barra 5
    # Ultima barra: ingresso all'open (9,5) e liquidazione al close (10), due commissioni.
    assert w.returns.iloc[-1] == pytest.approx(10 / 9.5 * (1 - c) ** 2 - 1)
    assert w.turnover.iloc[-1] == 2
    trades = _window_trades(w)
    assert list(trades["side"]) == ["BUY", "SELL", "BUY", "SELL"]
    assert list(trades["price"]) == [11.0, 12.0, 9.5, 10.0]
    assert list(trades.index) == [data.index[3], data.index[5], data.index[7], data.index[7]]
    # La finestra non usa barre oltre la sua fine.
    cut = window_returns(strat, data.iloc[:6], 1, 6)
    pd.testing.assert_series_equal(cut.returns.iloc[:-1], w.returns.iloc[:4])


def test_sma_backtest_uses_open_of_next_bar():
    # Stessa serie di close, open più alto del close precedente: l'ingresso costa di più che in modo close.
    idx = pd.date_range("2024-01-01", periods=60, freq="B")
    close = pd.Series(np.linspace(100, 160, 60), index=idx)
    df = pd.DataFrame({"open": close.shift(1).fillna(close.iloc[0]) * 1.01, "high": close * 1.02, "low": close * 0.98,
                       "close": close, "volume": 1.0})
    nxt = MovingAverageCrossover(StrategyParams(short_window=3, long_window=10, fee_bps=0)).backtest(df)
    cls = MovingAverageCrossover(StrategyParams(short_window=3, long_window=10, fee_bps=0, execution="close")).backtest(df)
    assert nxt.equity_curve.iloc[-1] == pytest.approx(cls.equity_curve.iloc[-1] / 1.01)
    first = nxt.trades.index[0]
    assert nxt.trades["price"].iloc[0] == df.loc[first, "open"]
