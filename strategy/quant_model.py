"""
strategy/quant_model.py
=======================
Logica matematica e di trading.

Strategia di esempio: **Moving Average Crossover** (long-only).

    SMA_short(t) = media dei close sugli ultimi `short_window` periodi
    SMA_long(t)  = media dei close sugli ultimi `long_window` periodi

    signal(t) = 1  se SMA_short(t) > SMA_long(t)   (trend rialzista -> long)
              = 0  altrimenti                       (flat / in cash)

    Golden cross: signal passa da 0 a 1  -> BUY
    Death cross : signal passa da 1 a 0  -> SELL

Il backtest è vettorizzato con Pandas/NumPy ed evita il *look-ahead bias*:
il segnale calcolato sulla chiusura della barra t diventa un ordine eseguito
all'**open della barra t+1** (posizione = signal.shift(1), `execution =
"next_open"`). Con `execution = "close"` l'ordine si esegue al close della
barra del segnale: è il «cheat-on-close» di Backtrader, lasciato solo per
confronto. I costi di transazione sono in basis point su ogni variazione di
posizione e cadono sulla barra d'esecuzione (vedi `position_returns`).

Per aggiungere nuove strategie basta estendere `BaseStrategy` e
implementare `generate_signals()`: backtest e metriche sono riutilizzati.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from strategy.metrics import DEFAULT_BARS_PER_YEAR, effective_bars_per_year, round_trip_stats

# Valore storico, lasciato per compatibilità: le metriche ora usano il numero
# di barre l'anno del mercato e dell'intervallo (vedi strategy/metrics.py).
TRADING_DAYS_PER_YEAR = DEFAULT_BARS_PER_YEAR

# Regole d'esecuzione degli ordini: all'open della barra dopo il segnale
# (predefinita) oppure al close della barra del segnale (solo per confronto).
EXECUTIONS = ("next_open", "close")


def check_execution(execution: str) -> str:
    """Valida la regola d'esecuzione (`ValueError` se non è tra `EXECUTIONS`)."""
    if execution not in EXECUTIONS:
        raise ValueError(f"execution deve essere uno di {list(EXECUTIONS)}, ricevuto {execution!r}.")
    return execution


# ============================================================== Parametri
@dataclass
class StrategyParams:
    short_window: int = 20
    long_window: int = 50
    initial_capital: float = 10_000.0
    fee_bps: float = 5.0            # costo per trade in basis point (5 bps = 0.05%)
    risk_free_rate: float = 0.0     # tasso risk-free annuo per lo Sharpe ratio
    execution: str = "next_open"    # "next_open" (open della barra dopo il segnale) o "close"

    def __post_init__(self) -> None:
        if self.short_window < 1 or self.long_window < 2:
            raise ValueError("Le finestre delle medie mobili devono essere positive.")
        if self.short_window >= self.long_window:
            raise ValueError("short_window deve essere strettamente minore di long_window.")
        if self.initial_capital <= 0:
            raise ValueError("initial_capital deve essere > 0.")
        if self.fee_bps < 0:
            raise ValueError("fee_bps non può essere negativo.")
        check_execution(self.execution)


@dataclass
class BacktestResult:
    metrics: dict
    equity_curve: pd.Series = field(repr=False)
    trades: pd.DataFrame = field(repr=False)
    signals: pd.DataFrame = field(repr=False)
    # Buy & Hold sullo stesso periodo, capitale e regola d'esecuzione della strategia.
    benchmark_curve: pd.Series | None = field(default=None, repr=False)

    def to_dict(self, tail: int | None = None) -> dict:
        """
        Serializzazione JSON-friendly (per le API).

        Ogni punto della curva include anche il benchmark Buy & Hold
        (`benchmark_curve`: stesso inizio della strategia, commissione
        d'ingresso compresa) e il flag `in_market` (posizione detenuta sulla
        barra): quando è 0 la strategia è in liquidità e l'equity resta
        piatta, cosa che la dashboard evidenzia. La curva parte dalla prima
        barra valutata: il riscaldamento degli indicatori non c'è.
        """
        eq = self.equity_curve if tail is None else self.equity_curve.tail(tail)
        benchmark = self.benchmark_curve
        if benchmark is None:                      # risultato costruito a mano, senza benchmark
            close = self.signals["close"].loc[self.equity_curve.index]
            benchmark = self.metrics["initial_capital"] * close / close.iloc[0]
        in_market = self.signals["in_market"]
        return {
            "metrics": self.metrics,
            "equity_curve": [
                {
                    "date": d.strftime("%Y-%m-%d"),
                    "equity": round(float(v), 2),
                    "benchmark": round(float(benchmark.loc[d]), 2),
                    "in_market": int(in_market.loc[d]),
                }
                for d, v in eq.items()
            ],
            "trades": [
                {
                    "date": d.strftime("%Y-%m-%d"),
                    "side": row["side"],
                    "price": round(float(row["price"]), 4),
                }
                for d, row in self.trades.iterrows()
            ],
        }


# ============================================================== Strategie
class BaseStrategy(ABC):
    """Interfaccia comune a tutte le strategie."""

    name: str = "base"

    def __init__(self, params: StrategyParams | None = None) -> None:
        self.params = params or StrategyParams()

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Deve restituire una copia di `data` con almeno le colonne:
            signal   -> posizione desiderata (0 = flat, 1 = long)
            position -> variazione di signal (+1 = BUY, -1 = SELL, 0 = hold)
        """

    def warmup_bars(self) -> int:
        """
        Barre iniziali in cui gli indicatori non sono ancora definiti e la
        strategia non può avere un segnale. Il backtest non le valuta: equity,
        metriche e Buy & Hold partono dalla barra con questo indice.
        """
        return 0

    # -------------------------------------------------------- Live signal
    def latest_signal(self, data: pd.DataFrame) -> dict:
        """Ultimo segnale disponibile, usato dal bot live a ogni ciclo."""
        sig = self.generate_signals(data)
        last = sig.iloc[-1]
        action = {1: "BUY", -1: "SELL"}.get(int(last["position"]), "HOLD")
        out = {
            "timestamp": sig.index[-1].isoformat(),
            "close": round(float(last["close"]), 4),
            "signal": int(last["signal"]),
            "action": action,
        }
        for col in ("sma_short", "sma_long"):
            if col in sig.columns and pd.notna(last[col]):
                out[col] = round(float(last[col]), 4)
        return out

    # ----------------------------------------------------------- Backtest
    def backtest(
        self,
        data: pd.DataFrame,
        bars_per_year: int | None = None,
        start: str | pd.Timestamp | None = None,
    ) -> BacktestResult:
        """
        Backtest vettorizzato. `bars_per_year` è il fattore di annualizzazione
        (es. `metrics.bars_per_year("5m", "crypto")`); se manca si misura
        dalle date della serie.

        Il segnale della barra t si esegue sulla barra t+1, al suo open
        (`execution="next_open"`) o al close di t (`"close"`). Un segnale che
        cambia sull'ultima barra non si esegue: resta in `pending_order`.

        La valutazione comincia dalla barra t0 = la più tarda tra il
        riscaldamento (`warmup_bars()`) e `start` (data: prima barra con
        indice >= start). Le barre prima servono solo agli indicatori: equity,
        metriche, curva e Buy & Hold partono da t0, senza posizione; il primo
        ordine possibile si esegue sulla barra t0+1. Il Buy & Hold compra su
        t0+1 con la stessa regola d'esecuzione e la stessa commissione, e non vende.
        """
        p = self.params
        execution = check_execution(getattr(p, "execution", "next_open"))
        sig = self.generate_signals(data)
        if execution == "next_open" and "open" not in sig.columns:
            raise ValueError(
                "Con execution='next_open' serve la colonna 'open'; senza open usa execution='close'."
            )

        # Inizio della valutazione: dopo il riscaldamento e non prima di `start`.
        n = len(sig)
        warmup = int(self.warmup_bars())
        first = 0 if start is None else int(sig.index.searchsorted(pd.Timestamp(start)))
        t0 = max(warmup, first)
        if t0 >= n:
            raise ValueError(
                f"Nessuna barra da valutare: {n} barre, riscaldamento {warmup}, inizio richiesto {start!r}."
            )

        # Posizione effettivamente detenuta: segnale della barra prima (no look-ahead),
        # nessuna posizione fino a t0 compresa.
        held_all = sig["signal"].shift(1).fillna(0.0).astype(float)
        held_all.iloc[: t0 + 1] = 0.0
        sig["in_market"] = held_all.astype(int)

        close = sig["close"].iloc[t0:]
        open_ = sig["open"].iloc[t0:] if "open" in sig.columns else None
        held = held_all.iloc[t0:]

        # Rendimenti, cambi di posizione e prezzo eseguito: commissione sulla barra d'esecuzione.
        strat_ret, turnover, price = position_returns(close, open_, held, p.fee_bps, execution)
        equity = p.initial_capital * (1 + strat_ret).cumprod()
        equity.name = "equity"

        # Buy & Hold sullo stesso periodo: compra sulla barra t0+1 come farebbe la strategia.
        bench_held = pd.Series(1.0, index=close.index)
        bench_held.iloc[0] = 0.0
        bench_ret, _, _ = position_returns(close, open_, bench_held, p.fee_bps, execution)
        benchmark = p.initial_capital * (1 + bench_ret).cumprod()
        benchmark.name = "benchmark"

        trades = _trades_from_positions(held, price, execution)

        # Segnale cambiato sull'ultima barra: ordine in attesa, non un trade.
        diff = int(sig["signal"].iloc[-1]) - int(held.iloc[-1])
        pending = "BUY" if diff > 0 else "SELL" if diff < 0 else None

        metrics = compute_metrics(
            strategy_returns=strat_ret,
            asset_returns=bench_ret,
            equity=equity,
            trades=trades,
            initial_capital=p.initial_capital,
            risk_free_rate=p.risk_free_rate,
            bars_per_year=bars_per_year,
        )
        metrics["warmup_bars"] = warmup
        metrics["data_start"] = sig.index[0].strftime("%Y-%m-%d")
        metrics["pending_order"] = pending
        metrics["open_missing_bars"] = (
            int(_reference_price(close.to_numpy(dtype=float), open_.to_numpy(dtype=float), execution)[1].sum())
            if execution == "next_open" else 0
        )
        metrics["strategy"] = self.name
        metrics["params"] = asdict(p)
        return BacktestResult(metrics=metrics, equity_curve=equity, trades=trades, signals=sig,
                              benchmark_curve=benchmark)


class MovingAverageCrossover(BaseStrategy):
    """Strategia trend-following basata sull'incrocio di due SMA."""

    name = "moving_average_crossover"

    def warmup_bars(self) -> int:
        """La SMA lunga è definita dalla barra `long_window − 1`."""
        return self.params.long_window - 1

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        if "close" not in data.columns:
            raise ValueError("Il DataFrame deve contenere la colonna 'close'.")
        if len(data) < self.params.long_window:
            raise ValueError(
                f"Dati insufficienti: servono almeno {self.params.long_window} barre, "
                f"ricevute {len(data)}."
            )

        df = data.copy()
        df["sma_short"] = df["close"].rolling(self.params.short_window, min_periods=self.params.short_window).mean()
        df["sma_long"] = df["close"].rolling(self.params.long_window, min_periods=self.params.long_window).mean()

        # Segnale valido solo quando entrambe le medie sono definite.
        valid = df["sma_long"].notna()
        df["signal"] = np.where(valid & (df["sma_short"] > df["sma_long"]), 1, 0)
        df["position"] = df["signal"].diff().fillna(0).astype(int)
        return df


# ============================================================= Esecuzione
def _reference_price(close: np.ndarray, open_: np.ndarray | None, execution: str) -> tuple[np.ndarray, np.ndarray]:
    """
    Prezzo di riferimento R_t dell'ordine eseguito sulla barra t e barre senza open valido.

    `next_open`: R_t = open(t); se l'open manca (NaN o <= 0) si ripiega su
    close(t-1). `close`: R_t = close(t-1). Sulla prima barra non c'è un close
    precedente e si usa il close della barra stessa.
    """
    prev_close = np.empty_like(close)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]
    if execution == "close":
        return prev_close, np.zeros(close.shape, dtype=bool)
    if open_ is None:
        raise ValueError("Con execution='next_open' serve la serie degli open; senza open usa execution='close'.")
    with np.errstate(invalid="ignore"):
        missing = ~(open_ > 0)                       # NaN o non positivo
    return np.where(missing, prev_close, open_), missing


def _returns_core(close: np.ndarray, ref: np.ndarray, held: np.ndarray, fee: float, slip: float):
    """
    Formula unica del backtest, in numpy. `close` e `ref` hanno una riga
    (barre); `held` può avere più righe (una per combinazione di parametri).

    Con h = posizione detenuta, Δ_t = h_t − h_{t-1} (h prima della serie = 0):
        P_t = R_t · (1 + slip · segno(Δ_t))            prezzo eseguito
        g_t = P_t / C_{t-1} − 1  (0 sulla prima barra)  dal close precedente all'esecuzione
        d_t = C_t / P_t − 1                             dall'esecuzione al close
        r_t = (1 + h_{t-1}·g_t) · (1 + h_t·d_t) · (1 − fee·|Δ_t|) − 1
    """
    prev_held = np.zeros_like(held)
    prev_held[..., 1:] = held[..., :-1]
    delta = held - prev_held
    price = ref * (1 + slip * np.sign(delta))
    prev_close = np.empty_like(close)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]
    gap = price / prev_close - 1
    gap[..., 0] = 0.0
    intrabar = close / price - 1
    turnover = np.abs(delta)
    returns = (1 + prev_held * gap) * (1 + held * intrabar) * (1 - fee * turnover) - 1
    return returns, turnover, price


def position_returns(
    close: pd.Series,
    open_: pd.Series | None,
    held: pd.Series,
    fee_bps: float,
    execution: str = "next_open",
    slippage_bps: float = 0.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Rendimenti per barra di una posizione `held` (h_t = segnale di t-1).

    Restituisce (rendimenti, turnover, prezzo d'esecuzione). L'ordine nato dal
    segnale di t-1 si esegue sulla barra t al prezzo di riferimento R_t:
    open(t) con `next_open`, close(t-1) con `close`; lo slippage lo sposta a
    sfavore (acquisto più caro, vendita più bassa). Quindi:

        barra d'ingresso  (C_t / P_t)·(1 − c) − 1
        barra d'uscita    (P_t / C_{t-1})·(1 − c) − 1
        in posizione      C_t / C_{t-1} − 1
        flat              0

    con c = fee_bps / 10000, pagata in forma moltiplicativa sulla barra
    d'esecuzione. Un open mancante (NaN o <= 0) è sostituito dal close
    precedente. È la stessa formula usata da `backtest`, da
    `research.window_returns` e, in matrice, da `optimization.sma_grid_vectorized`.
    """
    check_execution(execution)
    if fee_bps < 0 or slippage_bps < 0:
        raise ValueError("fee_bps e slippage_bps non possono essere negativi.")
    c = close.to_numpy(dtype=float)
    o = None if open_ is None else open_.to_numpy(dtype=float)
    ref, _ = _reference_price(c, o, execution)
    returns, turnover, price = _returns_core(c, ref, held.to_numpy(dtype=float), fee_bps / 10_000,
                                             slippage_bps / 10_000)
    idx = close.index
    return (pd.Series(returns, index=idx), pd.Series(turnover, index=idx), pd.Series(price, index=idx))


def _trades_from_positions(held: pd.Series, price: pd.Series, execution: str) -> pd.DataFrame:
    """
    Una riga per ogni cambio della posizione detenuta (BUY o SELL).

    `next_open`: data della barra d'esecuzione e prezzo eseguito (il suo open).
    `close`: data della barra del segnale (quella prima) e il suo close.
    """
    delta = held.diff().fillna(held).to_numpy()
    where = np.flatnonzero(delta != 0)
    when = where if execution == "next_open" else np.maximum(where - 1, 0)
    return pd.DataFrame(
        {"side": np.where(delta[where] > 0, "BUY", "SELL"), "price": price.to_numpy()[where]},
        index=held.index[when],
    )


# ================================================================ Metriche
def compute_metrics(
    strategy_returns: pd.Series,
    asset_returns: pd.Series,
    equity: pd.Series,
    trades: pd.DataFrame,
    initial_capital: float,
    risk_free_rate: float = 0.0,
    bars_per_year: int | None = None,
) -> dict:
    """
    Metriche di performance standard.

    Annualizzate con `bars_per_year` barre l'anno; se è None il fattore si
    misura dalle date (`effective_bars_per_year`), come fa Vibe-Trading per
    le serie di cui non si conosce il mercato. `asset_returns` sono i
    rendimenti per barra del benchmark sullo stesso periodo: il backtest passa
    quelli del Buy & Hold con commissione d'ingresso.
    """
    n = len(strategy_returns)
    bpy = int(bars_per_year) if bars_per_year else effective_bars_per_year(strategy_returns.index)
    years = max(n / bpy, 1e-9)

    final_equity = float(equity.iloc[-1]) if n else initial_capital
    total_return = final_equity / initial_capital - 1
    cagr = (final_equity / initial_capital) ** (1 / years) - 1 if final_equity > 0 else -1.0

    vol = float(strategy_returns.std(ddof=0) * np.sqrt(bpy))
    excess = strategy_returns - risk_free_rate / bpy
    std = excess.std(ddof=0)
    sharpe = float(excess.mean() / std * np.sqrt(bpy)) if std > 0 else 0.0

    downside = excess[excess < 0].std(ddof=0)
    sortino = (
        float(excess.mean() / downside * np.sqrt(bpy))
        if downside and downside > 0 else 0.0
    )

    running_max = equity.cummax()
    drawdown = equity / running_max - 1
    max_dd = float(drawdown.min()) if n else 0.0

    buy_hold = float((1 + asset_returns).prod() - 1)

    # Statistiche sui round-trip completi (BUY -> SELL), al lordo dei costi.
    rt = round_trip_stats(trades, equity.index)

    return {
        "start": strategy_returns.index[0].strftime("%Y-%m-%d") if n else None,
        "end": strategy_returns.index[-1].strftime("%Y-%m-%d") if n else None,
        "bars": n,
        "bars_per_year": bpy,
        "initial_capital": round(initial_capital, 2),
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "annual_volatility_pct": round(vol * 100, 2),
        "sharpe_ratio": round(sharpe, 3),
        "sortino_ratio": round(sortino, 3),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "buy_and_hold_return_pct": round(buy_hold * 100, 2),
        "num_trades": int(len(trades)),
        **rt,
    }
