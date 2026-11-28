---
name: strategy-change
description: Procedura per aggiungere o modificare una strategia, il backtest o le metriche di QuantTradingSystem senza introdurre look-ahead, costi sbagliati o confronti falsati. Usala per ogni modifica a strategy/quant_model.py, core/engine.py o all'endpoint /api/backtest.
---

# Modificare strategie e backtest

## 1. Prima di scrivere codice
- Leggi `strategy/quant_model.py` (`BaseStrategy`, `MovingAverageCrossover`, `compute_metrics`)
  e `tests/test_quant_model.py`, in particolare `test_backtest_has_no_lookahead`.
- Scrivi in una frase la regola della strategia: quando è long, quando è flat, con quali parametri.

## 2. Nuova strategia
- Una sottoclasse di `BaseStrategy` con `name` univoco e `generate_signals(data)` che
  restituisce una copia di `data` con almeno:
  - `signal`: posizione desiderata alla chiusura della barra (0 flat, 1 long)
  - `position`: `signal.diff()` (+1 BUY, -1 SELL, 0 hold)
- Il segnale della barra t usa solo dati fino a t compreso: niente `shift(-1)`, niente
  medie centrate, niente indicatori calcolati sull'intera serie e poi riusati indietro.
- Errori chiari (`ValueError`) per colonne mancanti o dati insufficienti, come la strategia esistente.
- Nuovi parametri in `StrategyParams` con validazione in `__post_init__`.
- `warmup_bars()`: indice della prima barra con tutti gli indicatori definiti (0 se non servono).
  Il backtest valuta strategia e Buy & Hold solo da lì.
- Non toccare `BaseStrategy.backtest`: applica già il ritardo di una barra e i costi.

## 3. Regole che non si cambiano
- La posizione detenuta in t è il segnale di t-1 (`shift(1)` in `backtest`).
- L'ordine si esegue all'open della barra t (`execution="next_open"`, predefinito): la barra
  d'ingresso rende open→close, quella d'uscita close precedente→open. `execution="close"`
  (close della barra del segnale) serve solo per confronto: non usarlo per presentare risultati.
- Rendimenti, costi e prezzi dei trade escono da una sola funzione, `position_returns`
  (`strategy/quant_model.py`), usata anche da `research.window_returns` e, in matrice, da
  `optimization.sma_grid_vectorized`: una modifica alla formula va fatta lì.
- I costi sono `fee_bps` a ogni cambio di posizione, sulla barra d'esecuzione, in forma
  moltiplicativa: `(1 − fee_bps/10000)`.
- Un segnale che cambia sull'ultima barra non è un trade: va in `metrics["pending_order"]`.
- Il bot live (`core/engine.py`) in intraday usa solo barre chiuse: l'ultima barra in
  formazione si scarta prima di `latest_signal`.
- Il broker predefinito resta `paper`.

## 4. Test obbligatori (tests/test_quant_model.py)
- Segnali uguali alla definizione su dati costruiti a mano (come `test_signals_match_sma_definition`).
- Look-ahead: su una serie sempre crescente la prima barra con `signal == 1` non guadagna.
- Esecuzione (`tests/test_execution.py`): su dati costruiti a mano il salto tra il close del
  segnale e l'open successivo non è guadagnato e i trade hanno data e open della barra d'esecuzione.
- Costi: con `fee_bps > 0` il rendimento è più basso che con `fee_bps = 0`.
- Parametri non validi e dati insufficienti sollevano `ValueError`.
- Solo dati mock o costruiti: nessuna chiamata di rete.

## 5. Verifica e resoconto
- `python -m pytest -q` (parte anche dall'hook a fine turno).
- Riporta in tabella, sugli stessi dati mock e stesso capitale: rendimento totale, CAGR,
  Sharpe, max drawdown e numero di trade della strategia, accanto al Buy & Hold
  (`buy_and_hold_return_pct`: stesso inizio dopo il riscaldamento, stessa esecuzione, commissione
  d'ingresso). Per confrontare strategie con riscaldamenti diversi passa lo stesso `start`.
- Se confronti con backtesting.py o Backtrader, usa stessi dati, commissioni e
  regola di esecuzione (ordine deciso alla chiusura di t, eseguito all'open di t+1: `next_open`
  di QTS, il predefinito di Backtrader e backtesting.py) e spiega le differenze.
- Dichiara sempre la regola d'esecuzione usata (`metrics["params"]["execution"]`) e, se ci sono,
  `pending_order` e `open_missing_bars`.
- Non presentare un backtest come previsione di rendimenti futuri.
