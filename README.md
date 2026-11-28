# 📈 QuantTradingSystem

Sistema di trading quantitativo in Python 3.10+ con dashboard web (FastAPI),
strategia di esempio **Moving Average Crossover**, backtest vettorizzato
(Pandas/NumPy), paper broker e deploy automatico gratuito (0 €/mese) in due modi:
- **senza server**: dashboard su **GitHub Pages**, bot eseguito ogni ora da **GitHub Actions**,
  backtest calcolato nel browser con Pyodide;
- **con VM**: dashboard su GitHub Pages, bot e API sempre accesi su una **VM Oracle Cloud Always Free**.

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
│   └── dashboard.html       # Dashboard web (stato, controlli, backtest, log); anche statica
├── web/
│   └── static_api.py        # /api/backtest e /api/backtest/costs per Pyodide (sito senza server)
├── tools/                   # bot_once (un ciclo, stato su file), data_snapshot, build_static_site,
│                            # prove nel browser, confronti esterni, benchmark
├── tests/
│   ├── conftest.py
│   ├── test_api.py
│   └── test_quant_model.py
├── requirements.txt         # Dipendenze di runtime
├── requirements-dev.txt     # Dipendenze di test (pytest, httpx)
├── Dockerfile               # Immagine di API + bot
├── deploy/                  # compose, Caddy e guida della VM Oracle (deploy/README.md)
├── .github/workflows/       # test, GitHub Pages, deploy sulla VM
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
h(t) = signal(t-1)                         # posizione detenuta: niente look-ahead
P(t) = open(t) · (1 ± slippage_bps/10000)  # prezzo eseguito (execution = "next_open"): + acquisto, − vendita
r(t) = (1 + h(t-1)·(P(t)/close(t-1) − 1)) · (1 + h(t)·(close(t)/P(t) − 1)) · (1 − c·|h(t) − h(t-1)|) − 1
       con c = fee_bps / 10000
```

L'ordine nato dal segnale della barra t-1 si esegue all'**open della barra t**: sulla barra
d'ingresso la strategia guadagna open→close, su quella d'uscita close precedente→open, e il
salto tra il close del segnale e l'open successivo non è suo. La commissione cade sulla barra
d'esecuzione. I trade hanno la data della barra d'esecuzione e il suo open come prezzo. Un
segnale che cambia sull'ultima barra non si esegue: compare in `pending_order`. Se l'open di
una barra manca si usa il close precedente (`open_missing_bars` le conta); senza colonna
`open` il backtest chiede `execution="close"`.

`slippage_bps` (predefinito 0, consigliato 5; in `StrategyParams`, `/api/backtest` e sito senza
server) sposta il prezzo eseguito a sfavore: si compra a open × (1 + s) e si vende a
open × (1 − s), e i trade riportano quel prezzo. La tabella dei costi (`cost_sensitivity`) usa
lo stesso meccanismo: lo slippage di ogni scenario si somma a quello di base sul prezzo, non
alla commissione. Il `PaperBroker` del bot esegue ancora al prezzo esatto (punto 10 del Piano,
parte broker, non fatta).

Le prime barre, in cui gli indicatori non sono definiti (`warmup_bars`), non si valutano:
equity, metriche (`start`, `bars`), curva e Buy & Hold partono dalla prima barra con indicatori
pronti (o da `start`, se più tardi). `data_start` è la prima barra dei dati. Il backtest non
scarica storico in più: con `period=1y` e SMA 50 le barre valutate sono circa 200.

`execution="close"` (parametro di `StrategyParams`, di `/api/backtest` e del sito senza
server) esegue al close della barra del segnale, P(t) = close(t-1): è il «cheat-on-close» di
Backtrader, tenuto solo per confronto con i risultati precedenti.

### Trade e metriche

`BacktestResult.round_trips` (e `round_trips` nella risposta di `/api/backtest`) è la tabella dei
trade: ingresso e uscita, prezzi eseguiti, barre, rendimento lordo e **netto**
`(P_out / P_in)·(1 − c)² − 1`, PnL e commissioni in valuta, quantità, MAE/MFE da minimi e massimi,
`status`. Il trade ancora aperto a fine periodo c'è, con `status = "open"`, valorizzato
all'ultimo close e con la sola commissione d'ingresso; non entra nelle statistiche
(`open_trades` lo conta). Win rate, guadagno e perdita medi, profit factor e `avg_trade_pct` si
calcolano sui netti: un trade a +0,05% lordo con 10 bps di commissione è una perdita di 0,15%.
Sempre sui netti (logica del riepilogo di TradeTracer): `expectancy_pct` = quota di vinti ×
guadagno medio − quota di persi × |perdita media|; `accuracy_magnitude_pct` = profitto lordo /
(profitto lordo + |perdita lorda|) e `accuracy_frequency_pct` = vinti / totale;
`max_consecutive_wins`, `best_trade_pct`, `worst_trade_pct`; `fragility_ratio` = (perdita di coda
massima / guadagno mediano dei vincenti) × (perdite di coda / trade), dove una perdita è di coda
se vale almeno 3 guadagni mediani (0 se non ce ne sono). Esempio: +2, +4, +6, −1, −15% →
expectancy −0,8%, accuracy 42,86% e 60%, fragility 0,75.
Altre metriche: `exposure_pct` (quota di barre in posizione), `calmar_ratio` (CAGR / |max
drawdown|), `max_drawdown_duration_bars` (barre consecutive sotto il massimo precedente) e il
Sortino con la downside deviation standard, `√(media(min(eccesso, 0)²))` su tutte le barre
(prima era la deviazione standard dei soli rendimenti negativi: sul mock SPY SMA 20/50 passa
da 0,13 a 0,16, sul mock BTC-USD da 1,897 a 1,899).

### Endpoint di controllo protetti

`POST /api/bot/*` richiede l'header `X-API-Key: <CONTROL_API_KEY>` quando la
variabile è impostata. Nella dashboard la chiave si inserisce in alto a destra.

```bash
curl -X POST -H "X-API-Key: $CONTROL_API_KEY" https://quant.<IP-con-i-trattini>.sslip.io/api/bot/start
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
| SPY | SMA 20/50 | 1,06 | 0,59 | 0,11 | −14,48 | 9 | 13,96 |
| SPY | RSI/MACD fuzzy | 6,25 | 3,62 | 0,30 | −19,11 | 16 | 17,43 |
| BTC-USD | SMA 20/50 | 46,94 | 22,91 | 1,18 | −15,26 | 14 | 68,10 |
| BTC-USD | RSI/MACD fuzzy | 36,09 | 18,70 | 0,99 | −13,70 | 27 | 82,75 |

Esecuzione all'open della barra dopo il segnale (`next_open`). Con `execution="close"`, la
regola usata fino alla rev. 48, le stesse righe davano rendimento 0,69 / 5,03 / 48,42 / 38,54.

Ogni riga è valutata dalla fine del riscaldamento dei propri indicatori (`warmup_bars`: 49
barre per SMA 20/50, 74 per RSI/MACD), e il Buy & Hold parte dalla stessa barra, compra con
la stessa regola e paga la commissione d'ingresso: per questo è diverso tra le due strategie
(455 e 430 barre valutate su SPY, 681 e 656 su BTC-USD). Prima il Buy & Hold partiva dalla
barra 0 (1,08% su SPY, 81,42% su BTC-USD), con 49 o 74 barre che la strategia non poteva usare.
Per confrontare strategie diverse sullo stesso periodo: `backtest(data, start="2025-06-01")`.

Sono dati sintetici: la tabella mostra che le due strategie funzionano con lo stesso
backtest, non che una renda più dell'altra sul mercato vero. Sul mock SPY la griglia SMA
(90 combinazioni) trova 30/71 con Sharpe 1,17; l'algoritmo genetico (seme 7, 113
valutazioni) trova 30/70 con Sharpe 1,25, in campione. Senza un `start` comune ogni coppia è
valutata dal proprio riscaldamento, quindi su un periodo diverso: `grid_search(…, start=…)`
e `sma_grid_vectorized(…, start=…)` le mettono sulla stessa barra d'inizio.

### Griglia di parametri: ciclo contro versione vettorizzata (punto 15)

`sma_grid_vectorized` calcola tutte le coppie SMA in un'unica matrice numpy e dà gli
stessi risultati di `grid_search` (un backtest per coppia). `tools/bench_grid.py` lo
verifica e misura i tempi (5 ripetizioni; ogni misura dura almeno 0,5 s):

| Mock SPY 5 anni, 1260 barre, 617 coppie | Tempo per griglia (mediana) | Scarto | Picco memoria |
|---|---:|---:|---:|
| Ciclo (`grid_search`) | 1,89 s | 7,3% | 0,8 MB |
| Vettorizzata (`sma_grid_vectorized`) | 0,033 s | 2,1% | 82,2 MB |

Circa 57× più veloce, al prezzo di più memoria (matrici coppie × barre): con griglie
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

QTS e Vibe-Trading eseguono entrambi all'open della barra dopo il segnale; i dati del
confronto hanno in più open(t) = close(t-1), così il prezzo è lo stesso anche con
`execution="close"`. La chiusura forzata di fine backtest di Vibe-Trading viene addebitata
anche a QTS. **Confronto da rieseguire**: i numeri che seguono sono della rev. 48, quando QTS
eseguiva al close del segnale e sottraeva la commissione invece di moltiplicarla; con la nuova
formula non sono stati ricalcolati. Sul mock BTC-USD 2 anni (SMA 20/50, 5 bps) l'equity finale
differiva dello 0,0010%, sul mock SPY 5 anni (SMA 10/30, 10 bps) dello 0,0033%, con gli stessi trade.

```python
from strategy.research import walk_forward
walk_forward(MovingAverageCrossover, data, [{"short_window": 10, "long_window": 50}, …],
             train_bars=400, test_bars=150, holdout_bars=150)
```

### App di riferimento analizzate con REA

`tools/analyze_apps.sh` usa [REA](https://github.com/morluto/REA) (MIT) sul tuo computer, con il
telefono collegato via adb: copia gli APK di TradingView, BTS, TradeTracer e Replay Trader fuori
dalla repo, ne legge manifest, librerie e stringhe, e `tools/app_report.py` scrive una scheda per
app in `docs/app-analizzate/` (grafici → punti 19 e 27, indicatori → 29, notifiche → 31, backtest
e replay → 26 e 27). Nelle schede solo dichiarazioni e conteggi, niente codice copiato.

### Prova della dashboard nel browser

`tools/smoke_dashboard.sh` usa [agent-browser](https://github.com/vercel-labs/agent-browser)
(Apache-2.0): avvia l'app con dati mock, apre la dashboard a 1280×800 e 390×844, controlla
metriche, grafico e assenza di errori nella pagina, prova Avvia/Ferma senza chiave (401) e
con chiave, salva gli screenshot. Con `PAGES=1` serve la pagina come la pubblica
`pages.yml` (con `config.js`) da un'altra origine e controlla anche CORS: origine della
dashboard autorizzata, preflight di `X-API-Key`, origine estranea rifiutata. Con
`BASE_URL=https://lorenzomp7.github.io/QuantTradingSystem/ API_KEY=…` prova la dashboard
pubblicata contro l'API indicata dal suo `config.js` (o da `API_URL`) senza cambiare lo
stato del bot: Avvia si prova solo se è già acceso, Ferma mai; `ALLOW_CONTROL=1` dà il
consenso ad avviarlo e fermarlo. In CI gira come job facoltativo `smoke-dashboard`,
nelle due modalità.

La chiave API inserita nella dashboard resta solo nella scheda (`sessionStorage`):
l'origine `lorenzomp7.github.io` è condivisa da tutte le pagine GitHub Pages dell'account,
che con `localStorage` potrebbero leggerla. Per lo stesso motivo CORS autorizza l'intera
origine, non il solo progetto: un dominio dedicato per Pages la renderebbe separata.

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

### 2a. Deploy gratuito senza server: GitHub Pages + GitHub Actions

È la modalità attiva finché nel repo non ci sono le variabili `VM_HOST` o `API_HOST`.
Costo: 0 € (repo pubblico: minuti di Actions e Pages gratuiti).

- **Bot** → ogni ora (minuto 17) il workflow `pages.yml` esegue un ciclo (`tools/bot_once.py`,
  broker paper, `BTC-USD` a candele 1h su 60 giorni). Conto, log e storia dell'equity
  restano nel branch `bot-state` (un solo commit, riscritto a ogni esecuzione).
- **Dashboard** → <https://lorenzomp7.github.io/QuantTradingSystem/>: stato e log dai file del
  bot; backtest e tabella dei costi calcolati nel browser con [Pyodide](https://pyodide.org)
  dallo stesso codice Python dell'API, sui prezzi giornalieri di Yahoo aggiornati una volta al
  giorno (`tools/data_snapshot.py`, tutta la storia per SPY, QQQ, AAPL, MSFT, BTC-USD, ETH-USD). La prima
  apertura scarica circa 20 MB (Python, NumPy, pandas), poi il browser li tiene in cache.

Attivazione, una volta sola:

1. Unisci il lavoro su `main`: le esecuzioni pianificate partono solo dal branch predefinito.
2. **Settings → Pages → Source: GitHub Actions**.
3. **Actions → GitHub Pages → Run workflow**: primo ciclo e prima pubblicazione.

| Variabile del repo (facoltativa) | Default | Effetto |
|---|---|---|
| `BOT_ENABLED` | `true` | `false` = cicli saltati, la pagina resta |
| `BOT_SYMBOL` | `BTC-USD` | simbolo del bot |
| `SNAPSHOT_SYMBOLS` | `SPY,QQQ,AAPL,MSFT,BTC-USD,ETH-USD` | simboli con i prezzi per il backtest; gli altri usano la serie mock |

Limiti rispetto alla VM: un ciclo all'ora invece che al minuto, e GitHub può ritardare o saltare
un'esecuzione pianificata; dopo 60 giorni senza commit sul repo GitHub sospende le esecuzioni
pianificate (si riattivano da Actions). Niente pulsanti Avvia/Ferma: si usa `BOT_ENABLED` o
*Run workflow*. Per ripartire con un conto nuovo cancella il branch `bot-state`.
Avvisi: un ciclo fallito rende rossa l'esecuzione (GitHub manda la notifica), gli ordini sospesi
per dati mock o vecchi compaiono come avviso nella run, e la pagina mostra «IN RITARDO» se lo
stato ha più di 3 ore. Uno stato salvato illeggibile o alterato non viene mai sostituito da un
conto nuovo: il ciclo si ferma con l'errore in pagina. Solo `main` aggiorna il conto: *Run
workflow* da un altro branch ricostruisce la pagina senza toccare `bot-state`.
Prova in locale: `PYODIDE_DIR=… bash tools/smoke_static.sh` (vedi l'intestazione dello script).

### 2b. Deploy gratuito con VM: GitHub Pages + VM Oracle

- **Dashboard** → GitHub Pages, <https://lorenzomp7.github.io/QuantTradingSystem/> (workflow `pages.yml`).
- **API + bot** → container Docker su una VM Oracle Cloud *Always Free* condivisa con gli altri progetti,
  dietro Caddy con HTTPS automatico (workflow `deploy-backend.yml`). La VM non va mai in sleep: niente keep-alive.

1. Crea la VM una volta sola seguendo [deploy/README.md](deploy/README.md).
2. In **Settings → Secrets and variables → Actions** del repo:

   | Tipo | Nome | Valore |
   |---|---|---|
   | Variable | `VM_HOST` | IP pubblico della VM |
   | Secret | `VM_SSH_KEY` | chiave privata SSH della VM |
   | Secret | `CONTROL_API_KEY` | chiave per start/stop/run-once, es. `openssl rand -hex 24` |
   | Variable | `SYMBOL`, `DATA_INTERVAL`, `DATA_PERIOD` | facoltative (di base `BTC-USD`, `5m`, `5d`) |
   | Variable | `API_HOST` | facoltativa: dominio tuo per l'API |
   | Variable | `CORS_ORIGINS` | facoltativa, di base `https://lorenzomp7.github.io` |

3. **Settings → Pages → Source: GitHub Actions**.
4. Da **Actions** lancia *Deploy backend* e poi *GitHub Pages* (*Run workflow*).
   L'API risponde su `https://quant.<IP con i trattini>.sslip.io` (Swagger su `/docs`) e la dashboard
   su Pages la usa in automatico; la dashboard resta disponibile anche all'indirizzo dell'API.
5. Nella dashboard inserisci `CONTROL_API_KEY` in alto a destra per avviare/fermare il bot.

Dai push successivi su `main` i due workflow si aggiornano da soli. Con `VM_HOST` impostata
`pages.yml` collega la pagina alla VM e non esegue più il bot su Actions.

### Variabili d'ambiente

| Variabile | Obbligatoria | Default | Descrizione |
|---|---|---|---|
| `CONTROL_API_KEY` | **sì (prod)** | — | Protegge start/stop/run-once. Senza, chiunque può controllare il bot |
| `CORS_ORIGINS` | no | `https://lorenzomp7.github.io` | Origini (separate da virgola) da cui il browser può chiamare l'API |
| `DATA_SOURCE` | no | `yahoo` | `yahoo` o `mock` |
| `SYMBOL` | no | `BTC-USD` | Ticker da tradare (es. `ETH-USD`, `SPY`, `AAPL`) |
| `DATA_PERIOD` / `DATA_INTERVAL` | no | `5d` / `5m` | Storico e durata candele. Intraday: Yahoo consente max `60d` (il periodo viene ridotto in automatico). Azioni giornaliere: `2y` / `1d` |
| `SHORT_WINDOW` / `LONG_WINDOW` | no | `20` / `50` | Finestre delle SMA |
| `INITIAL_CAPITAL` / `FEE_BPS` | no | `10000` / `5` | Capitale iniziale del paper broker e commissioni (bps) |
| `POSITION_SIZE_PCT` | no | `100` | % della liquidità investita a ogni BUY (quantità frazionaria) |
| `TRADE_QUANTITY` | no | `0` | Se > 0, quantità fissa per ogni BUY (ha la precedenza) |
| `LOOP_INTERVAL_SECONDS` | no | `60` | Frequenza del ciclo del bot |
| `AUTO_START_BOT` | no | `true` | Avvia il bot all'avvio del server e dopo ogni riavvio |
| `BROKER` | no | `paper` | `paper`, `alpaca`, `ccxt` (sulla VM è fissato a `paper` in `deploy/compose.yml`) |
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | se `BROKER=alpaca` | — | 🔐 **Segreti**: solo come secret, mai nel repo |
| `ALPACA_BASE_URL` | no | paper endpoint | `https://paper-api.alpaca.markets` |
| `CCXT_EXCHANGE` / `CCXT_API_KEY` / `CCXT_SECRET` | se `BROKER=ccxt` | — | 🔐 **Segreti** dell'exchange |
| `CCXT_SANDBOX` | no | `true` | Usa la testnet dell'exchange |

### Note operative

- **Sempre attivo**: sulla VM il bot gira 24/7, senza sleep e senza ping esterni.
- **Stato in memoria**: posizioni del paper broker e log si azzerano a ogni
  deploy/riavvio del container. Per persistenza aggiungi un database (es. un
  container PostgreSQL in `deploy/compose.yml`).
- **Log**: sulla VM `sudo docker logs -f quant-trading-system`, oppure `/api/logs`.
- **Yahoo Finance** può limitare le richieste da IP cloud: in quel caso il
  sistema passa automaticamente ai dati mock e lo segnala nei log e in
  `last_data_source_used` dello stato.
- Un solo processo uvicorn (nessun `--workers N`): il bot vive nel processo
  web e più worker creerebbero più bot indipendenti.
