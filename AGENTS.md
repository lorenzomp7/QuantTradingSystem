# QuantTradingSystem

Sistema didattico di trading quantitativo in Python ≥ 3.10: strategia Moving
Average Crossover, backtest vettorizzato (pandas/NumPy), paper broker e dashboard
FastAPI, con deploy su Render. Lavora su `main_claude` salvo indicazione diversa.

## Struttura

- `strategy/quant_model.py`: segnali, backtest e metriche. È il cuore del progetto.
- `core/engine.py`: ciclo asincrono dati → strategia → broker.
- `execution/broker.py`: `PaperBroker` (predefinito); Alpaca e ccxt sono segnaposto.
- `utils/data_fetcher.py`: Yahoo Finance con cache e serie mock deterministica (GBM).
- `main.py`: API e dashboard (`templates/dashboard.html`); `config.py`: variabili d'ambiente.
- `tests/`: pytest; `conftest.py` forza dati mock e nessuna chiamata di rete.

## Comandi

| Scopo | Comando |
|---|---|
| Dipendenze | `python -m pip install -r requirements-dev.txt` |
| **Verifica standard** | `python -m pytest -q` (circa 3 s, gira anche in CI) |
| Avvio locale | `cp .env.example .env` poi `uvicorn main:app --reload --port 10000` |
| Dati senza rete | `DATA_SOURCE=mock` |

Esegui la verifica standard prima di dichiarare finito un cambio. Riporta i
comandi davvero eseguiti e i loro esiti.

## Regole del progetto

- **Niente look-ahead**: la posizione al tempo t usa solo il segnale di t-1.
  Ogni modifica a segnali o backtest deve mantenerlo, con un test che lo provi.
- I costi di transazione restano in punti base (`fee_bps`) e vanno applicati
  a ogni cambio di posizione.
- Le metriche (CAGR, Sharpe, Sortino, drawdown, win rate) vanno confrontate con
  il benchmark Buy & Hold sullo stesso capitale e periodo.
- Il broker predefinito è e resta `paper`: non abilitare ordini reali, non
  aggiungere chiavi API ai file e non cambiare `render.yaml` senza richiesta.
- I test non devono usare la rete: usa i dati mock.
- Per confrontare il backtest con librerie esterne (backtesting.py, Backtrader)
  usa stessi dati, commissioni e regole di esecuzione, e riporta le differenze.
