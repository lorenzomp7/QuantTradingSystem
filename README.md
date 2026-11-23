# 📈 QuantTradingSystem

Sistema di trading quantitativo in Python 3.10+ con dashboard web (FastAPI),
strategia di esempio **Moving Average Crossover**, backtest vettorizzato
(Pandas/NumPy), paper broker e deploy automatico su **Render**.

> ⚠️ Progetto a scopo didattico. Il broker di default è **simulato** (paper):
> nessun ordine reale viene inviato finché non si implementa un broker reale.

---

## Dashboard e backtest

Il grafico del backtest mostra l’intero periodo simulato, confronta la strategia con il benchmark Buy & Hold e indica operazioni e periodi in liquidità. La configurazione predefinita del bot usa dati crypto intraday e dimensiona le posizioni in percentuale.

## Fase 1 — Struttura della directory

```
QuantTradingSystem/
├── main.py                  # Entry point: server FastAPI, endpoint API e dashboard
├── config.py                # Configurazione da variabili d'ambiente
├── core/
│   ├── __init__.py
│   └── engine.py            # Loop del bot: dati -> strategia -> broker
├── strategy/
│   ├── __init__.py
│   ├── quant_model.py       # Moving Average Crossover, backtest e metriche
│   ├── rsi_macd.py          # RSI + MACD + stocastico con regole fuzzy (long-only)
│   ├── indicators.py        # RSI di Wilder, istogramma MACD, stocastico %K
│   ├── optimization.py      # Ricerca dei parametri: griglia o algoritmo genetico
│   ├── metrics.py           # Barre/anno per mercato e intervallo, statistiche per trade
│   └── research.py          # Costi × slippage, walk-forward con periodo finale riservato
├── utils/
│   ├── __init__.py
│   ├── data_fetcher.py      # Dati OHLCV: Yahoo Finance + fallback mock (GBM), provenienza
│   ├── data_health.py       # Controllo della fonte dati con simboli canarino
│   └── logger.py            # Logging su stdout + buffer in memoria per /api/logs
├── execution/
│   ├── __init__.py
│   ├── broker.py            # PaperBroker + placeholder Alpaca / ccxt, stati dell'ordine
│   └── reconcile.py         # Ricalcolo indipendente del registro del PaperBroker
├── templates/
│   └── dashboard.html       # Dashboard web (stato, controlli, backtest, log)
├── tests/
│   ├── conftest.py
│   ├── test_api.py
│   └── test_quant_model.py
├── requirements.txt         # Dipendenze di runtime
├── requirements-dev.txt     # Dipendenze di test (pytest, httpx)
├── render.yaml              # Blueprint Render (Infrastructure as Code)
├── .env.example             # Template variabili d'ambiente
├── .gitignore
└── README.md
```

## Fase 2 — Componenti principali

| File | Ruolo |
|---|---|
| `main.py` | App FastAPI: dashboard `/`, `/health`, `/api/status`, `/api/bot/{start,stop,run-once}`, `/api/signals`, `/api/backtest`, `/api/data-health`, `/api/logs`, Swagger su `/docs` |
| `strategy/quant_model.py` | `MovingAverageCrossover` (SMA breve vs SMA lunga), backtest senza look-ahead bias, costi in bps, metriche (CAGR, Sharpe, Sortino, Max Drawdown, win rate) |
| `strategy/metrics.py` | Barre per anno per mercato (crypto 24/7, azioni, forex) e intervallo, oppure misurate dalle date; profit factor e altre statistiche per trade |
| `strategy/rsi_macd.py` | `RsiMacdFuzzy`: seconda strategia, stessa interfaccia di `MovingAverageCrossover`. Regole fuzzy su RSI, istogramma MACD (z-score mobile) e stocastico, dalla tesi THESIS-fuzzylogic-stocks (codice riscritto) |
| `strategy/optimization.py` | `grid_search` e `genetic_search` (torneo, incrocio uniforme, mutazione, elitismo; obiettivo Sharpe, rendimento o 0,7·guadagno + 0,3·Sharpe come nella tesi). Risultati in campione: per la scelta vera usare `walk_forward` |
| `strategy/research.py` | `cost_sensitivity` (commissioni ×1/×2/×3 e slippage 0/5 bps, `/api/backtest/costs` e tabella in dashboard) e `walk_forward` (parametri scelti solo sul training, test fuori campione, periodo finale riservato) |
| `tools/compare_external.py` | Confronto con Vibe-Trading sugli stessi dati, commissioni e prezzi di esecuzione |
| `utils/data_fetcher.py` | `MarketDataFetcher` con cache TTL; Yahoo Finance via `yfinance`, fallback su serie GBM deterministica, sempre dichiarato (`DataInfo`) |
| `utils/data_health.py` | `python -m utils.data_health`: prova BTC-USD e SPY su Yahoo senza ripiego, esce con 1 se una fonte non risponde o è vecchia |
| `core/engine.py` | `TradingEngine`: ciclo periodico asincrono che allinea la posizione al segnale |
| `execution/broker.py` | `PaperBroker` in memoria; `AlpacaBroker` e `CcxtBroker` pronti da completare. Stati dell'ordine (`new`, `open`, `partially_filled`, `filled`, `canceled`, `rejected`, `pending_cancel`); un ordine senza liquidità resta nel registro come `rejected` |
| `execution/reconcile.py` | `reconcile_broker(broker)`: rifà cassa, posizioni, costo medio e NAV in `Decimal` dagli ordini e segnala ogni campo del conto che non torna |

### La strategia in breve

```
SMA_short > SMA_long  ->  signal = 1 (long)
SMA_short <= SMA_long ->  signal = 0 (flat)
posizione detenuta(t) = signal(t-1)        # niente look-ahead
rendimento(t) = posizione(t) * r_asset(t) - |Δposizione(t)| * fee_bps / 10000
```

### Endpoint di controllo protetti

`POST /api/bot/*` richiede l'header `X-API-Key: <CONTROL_API_KEY>` quando la
variabile è impostata. Nella dashboard la chiave si inserisce in alto a destra.

```bash
curl -X POST -H "X-API-Key: $CONTROL_API_KEY" https://<tuo-servizio>.onrender.com/api/bot/start
```

### Esecuzione locale

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                   # poi modifica i valori
uvicorn main:app --reload --port 10000                 # http://localhost:10000
pytest -q                                              # test (dati mock, offline)
```

### Dati: provenienza e blocco degli ordini

Se Yahoo non risponde il fetcher usa la serie mock, ma lo dichiara: `/api/status`
(campo `data`) e `/api/data-health` riportano fonte richiesta, fonte usata,
ultima candela ed età. Con dati mock di ripiego o troppo vecchi il bot calcola
il segnale ma **non invia ordini** (`orders_blocked` nel segnale), nemmeno le vendite:
con dati vecchi una posizione aperta resta aperta finché i dati tornano validi, perché
un'uscita decisa su prezzi sbagliati non è più sicura di un'entrata. Un ordine rifiutato
(liquidità insufficiente) non viene ripetuto finché la cassa non cambia.

```bash
python -m utils.data_health                 # 0 = Yahoo sano, 1 = fonte bloccata o vecchia
curl -f https://<servizio>/api/data-health  # 503 quando il bot non invierebbe ordini
```

### Seconda strategia e ottimizzazione genetica

Sugli stessi dati mock (2 anni: 504 sedute per SPY, 730 giorni 24/7 per BTC; 10.000; 5 bps):

| Dati | Strategia | Rend. % | CAGR % | Sharpe | Max DD % | Trade | Buy & Hold % |
|---|---|---:|---:|---:|---:|---:|---:|
| SPY | SMA 20/50 | 0,69 | 0,34 | 0,09 | −14,65 | 9 | 1,08 |
| SPY | RSI/MACD fuzzy | 5,03 | 2,49 | 0,24 | −19,11 | 16 | 1,08 |
| BTC-USD | SMA 20/50 | 48,42 | 21,83 | 1,17 | −15,27 | 14 | 81,42 |
| BTC-USD | RSI/MACD fuzzy | 38,54 | 17,70 | 0,99 | −14,01 | 27 | 81,42 |

Sono dati sintetici: la tabella mostra che le due strategie funzionano con lo stesso
backtest, non che una renda più dell'altra sul mercato vero. Sul mock SPY la griglia SMA
(90 combinazioni) trova 30/71 con Sharpe 1,09; l'algoritmo genetico (seme 7, 117
valutazioni) trova 29/72 con Sharpe 1,14, in campione.

### Griglia di parametri: ciclo contro versione vettorizzata (punto 15)

`sma_grid_vectorized` calcola tutte le coppie SMA in un'unica matrice numpy e dà gli
stessi risultati di `grid_search` (un backtest per coppia). `tools/bench_grid.py` lo
verifica e misura i tempi (5 ripetizioni; ogni misura dura almeno 0,5 s):

| Mock SPY 5 anni, 1260 barre, 617 coppie | Tempo per griglia (mediana) | Scarto | Picco memoria |
|---|---:|---:|---:|
| Ciclo (`grid_search`) | 3,08 s | 2,7% | 0,8 MB |
| Vettorizzata (`sma_grid_vectorized`) | 0,032 s | 6,4% | 62,7 MB |

Circa 96× più veloce, al prezzo di più memoria (matrici coppie × barre): con griglie
molto grandi sulla VM conviene dividerle a blocchi. Profilo con
[flameox](https://github.com/morluto/flameox) e py-spy: il ciclo spende il tempo
nell'overhead di pandas di ogni singolo backtest.

```bash
python tools/bench_grid.py                                   # esce con 1 se i risultati differiscono
pip install flameox py-spy
flameox capture --provider py-spy --operation rank_cpu_hotspots -- python tools/bench_grid.py --mode loop --repeat 1
```

### Confronto con Vibe-Trading e studi fuori campione

[Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) (MIT) è un secondo motore di backtest
indipendente. Si installa in un ambiente separato (Python ≥ 3.11) e si confronta così:

```bash
python3.11 -m venv ~/.venvs/vibe && ~/.venvs/vibe/bin/pip install vibe-trading-ai
python tools/compare_external.py --vibe-python ~/.venvs/vibe/bin/python    # 0 = differenze in tolleranza
```

I dati del confronto hanno open(t) = close(t-1), così l'esecuzione al close della barra del
segnale (QTS) e all'open della barra dopo (Vibe-Trading) avvengono allo stesso prezzo; la
chiusura forzata di fine backtest di Vibe-Trading viene addebitata anche a QTS. Sul mock
BTC-USD 2 anni (SMA 20/50, 5 bps) l'equity finale differisce dello 0,0010%, sul mock SPY
5 anni (SMA 10/30, 10 bps) dello 0,0033%, con gli stessi trade.

```python
from strategy.research import walk_forward
walk_forward(MovingAverageCrossover, data, [{"short_window": 10, "long_window": 50}, …],
             train_bars=400, test_bars=150, holdout_bars=150)
```

### Prova della dashboard nel browser

`tools/smoke_dashboard.sh` usa [agent-browser](https://github.com/vercel-labs/agent-browser)
(Apache-2.0): avvia l'app con dati mock, apre la dashboard a 1280×800 e 390×844, controlla
metriche, grafico e assenza di errori nella pagina, prova Avvia/Ferma senza chiave (401) e
con chiave, salva gli screenshot. Con `BASE_URL=… API_KEY=…` prova una dashboard già online.
In CI gira come job facoltativo `smoke-dashboard`.

```bash
npm i -g agent-browser && agent-browser install --with-deps
bash tools/smoke_dashboard.sh          # 0 = tutto ok, 1 = primo controllo fallito
```

Le metriche del backtest sono annualizzate con il numero di barre l'anno del
mercato e dell'intervallo (`bars_per_year` nelle metriche): 365 per BTC
giornaliero, 252 per SPY, 365 × 288 per BTC a 5 minuti.

---

## Fase 3 — Istruzioni di deploy

### 1. GitHub

Crea un repository **vuoto** su GitHub (senza README/.gitignore) chiamato
`QuantTradingSystem`, poi dalla cartella del progetto:

```bash
cd QuantTradingSystem
git init
git add .
git commit -m "Initial commit: QuantTradingSystem"
git branch -M main
git remote add origin https://github.com/<TUO-UTENTE>/QuantTradingSystem.git
git push -u origin main
```

Verifica prima del push che `.env` **non** compaia in `git status`
(è escluso da `.gitignore`).

### 2. Render

**Opzione A — Blueprint (consigliata, usa `render.yaml`)**

1. Accedi a <https://dashboard.render.com> e collega il tuo account GitHub
   (*Account Settings → Git Providers*), autorizzando l'accesso al repo.
2. **New → Blueprint** e seleziona il repository `QuantTradingSystem`.
3. Render legge `render.yaml` e propone il servizio `quant-trading-system`.
   Ti chiederà i valori delle variabili con `sync: false` (chiavi broker):
   lasciale vuote se usi `BROKER=paper`.
4. **Apply**: parte la build (`pip install -r requirements.txt`) e poi lo start
   (`uvicorn main:app --host 0.0.0.0 --port $PORT`). Il deploy usa il branch `main`.
5. A deploy completato il servizio è pubblico su
   `https://quant-trading-system.onrender.com` (o simile).
6. Copia il valore generato di `CONTROL_API_KEY` da *Environment* e usalo
   nella dashboard per avviare/fermare il bot.

**Opzione B — Web Service manuale**

1. **New → Web Service** → seleziona il repo.
2. Runtime: *Python 3* · Branch: `main`
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Health Check Path: `/health`
6. Aggiungi le variabili d'ambiente della tabella sotto, poi **Create Web Service**.

### Variabili d'ambiente

| Variabile | Obbligatoria | Default | Descrizione |
|---|---|---|---|
| `PYTHON_VERSION` | consigliata | `3.11.9` | Versione Python usata da Render |
| `CONTROL_API_KEY` | **sì (prod)** | — | Protegge start/stop/run-once. Senza, chiunque può controllare il bot |
| `DATA_SOURCE` | no | `yahoo` | `yahoo` o `mock` |
| `SYMBOL` | no | `BTC-USD` | Ticker da tradare (es. `ETH-USD`, `SPY`, `AAPL`) |
| `DATA_PERIOD` / `DATA_INTERVAL` | no | `5d` / `5m` | Storico e durata candele. Intraday: Yahoo consente max `60d` (il periodo viene ridotto in automatico). Azioni giornaliere: `2y` / `1d` |
| `SHORT_WINDOW` / `LONG_WINDOW` | no | `20` / `50` | Finestre delle SMA |
| `INITIAL_CAPITAL` / `FEE_BPS` | no | `10000` / `5` | Capitale iniziale del paper broker e commissioni (bps) |
| `POSITION_SIZE_PCT` | no | `100` | % della liquidità investita a ogni BUY (quantità frazionaria) |
| `TRADE_QUANTITY` | no | `0` | Se > 0, quantità fissa per ogni BUY (ha la precedenza) |
| `LOOP_INTERVAL_SECONDS` | no | `60` | Frequenza del ciclo del bot |
| `AUTO_START_BOT` | no | `true` | Avvia il bot all'avvio del server e dopo ogni riavvio |
| `KEEP_ALIVE` / `KEEP_ALIVE_INTERVAL_SECONDS` | no | `true` / `600` | Auto-ping a `RENDER_EXTERNAL_URL/health` per evitare lo sleep del piano free |
| `KEEP_ALIVE_URL` | no | — | URL da pingare al posto di `RENDER_EXTERNAL_URL` |
| `BROKER` | no | `paper` | `paper`, `alpaca`, `ccxt` |
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | se `BROKER=alpaca` | — | 🔐 **Segreti**: solo su Render, mai nel repo |
| `ALPACA_BASE_URL` | no | paper endpoint | `https://paper-api.alpaca.markets` |
| `CCXT_EXCHANGE` / `CCXT_API_KEY` / `CCXT_SECRET` | se `BROKER=ccxt` | — | 🔐 **Segreti** dell'exchange |
| `CCXT_SANDBOX` | no | `true` | Usa la testnet dell'exchange |

### Note operative su Render

- **Piano free**: il servizio va in *sleep* dopo ~15 minuti senza traffico HTTP.
  Il keep-alive integrato (`KEEP_ALIVE=true`) pinga ogni 10 minuti l'URL
  pubblico del servizio; come rete di sicurezza configura anche un ping
  esterno gratuito (cron-job.org / UptimeRobot) su `https://<servizio>.onrender.com/health`.
  Il free include 750 ore/mese: bastano per un solo servizio acceso 24/7.
  Per un uso serio resta consigliato il piano *Starter*.
- **Stato in memoria**: posizioni del paper broker e log si azzerano a ogni
  deploy/riavvio. Per persistenza aggiungi un database (es. Render PostgreSQL).
- **Yahoo Finance** può limitare le richieste da IP cloud: in quel caso il
  sistema passa automaticamente ai dati mock e lo segnala nei log e in
  `last_data_source_used` dello stato.
- Un solo processo uvicorn (nessun `--workers N`): il bot vive nel processo
  web e più worker creerebbero più bot indipendenti.
