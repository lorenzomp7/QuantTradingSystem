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
| Salute della fonte dati | `python -m utils.data_health` (usa la rete; esce con 1 se Yahoo non risponde) |
| Prova della dashboard nel browser | `bash tools/smoke_dashboard.sh` (agent-browser; senza CDN: `ALLOW_NO_CHARTJS=1` o `CHARTJS_FILE=…/chart.umd.js`) |
| Confronto con Vibe-Trading | `python tools/compare_external.py --vibe-python <python con vibe-trading-ai>` (ambiente separato, Python ≥ 3.11) |
| Benchmark della griglia | `python tools/bench_grid.py` (ciclo contro vettorizzata; profilo: `flameox capture --provider py-spy --operation rank_cpu_hotspots -- python tools/bench_grid.py --mode loop --repeat 1`) |

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
- Per confrontare il backtest con librerie esterne (Vibe-Trading, backtesting.py, Backtrader)
  usa stessi dati, commissioni e regole di esecuzione, e riporta le differenze:
  `tools/compare_external.py` lo fa già per Vibe-Trading.
- Per scegliere parametri usa `strategy/research.py` (`walk_forward`): scelta solo sul
  training, test fuori campione e periodo finale riservato. Mai scegliere sul test.

## Procedure per gli agenti (`.claude/skills/`)

- **Verifiche nel browser**: usa `agent-browser` (non il Chrome personale); prima esegui
  `agent-browser skills get core` e usa una `--session` tua. Per la dashboard c'è già
  `tools/smoke_dashboard.sh`: lancialo dopo ogni modifica a `templates/dashboard.html`
  o alle API che la alimentano (punti 19, 20 e 26 del Piano).
- **Sicurezza** (`security-and-hardening`): leggila prima di toccare `execution/broker.py`
  (ordini, broker reali, chiavi API), l'autenticazione `X-API-Key`, CORS o `config.py`.
- **Osservabilità** (`observability-and-instrumentation`): per log, metriche e avvisi del bot
  (punti 23 e 31 del Piano) e ogni volta che un errore del ciclo o un blocco degli ordini
  potrebbe passare inosservato: il bot gira da solo, senza i log di una piattaforma gestita.
