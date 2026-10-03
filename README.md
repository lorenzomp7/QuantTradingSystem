# 📈 QuantTradingSystem

Sistema di trading quantitativo in Python 3.10+ con dashboard web (FastAPI),
strategia di esempio **Moving Average Crossover**, backtest vettorizzato
(Pandas/NumPy), paper broker e deploy automatico su **Render**.

> ⚠️ Progetto a scopo didattico. Il broker di default è **simulato** (paper):
> nessun ordine reale viene inviato finché non si implementa un broker reale.

---

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
│   └── quant_model.py       # Moving Average Crossover, backtest e metriche
├── utils/
│   ├── __init__.py
│   ├── data_fetcher.py      # Dati OHLCV: Yahoo Finance + fallback mock (GBM)
│   └── logger.py            # Logging su stdout + buffer in memoria per /api/logs
├── execution/
│   ├── __init__.py
│   └── broker.py            # PaperBroker + placeholder Alpaca / ccxt
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
| `main.py` | App FastAPI: dashboard `/`, `/health`, `/api/status`, `/api/bot/{start,stop,run-once}`, `/api/signals`, `/api/backtest`, `/api/logs`, Swagger su `/docs` |
| `strategy/quant_model.py` | `MovingAverageCrossover` (SMA breve vs SMA lunga), backtest senza look-ahead bias, costi in bps, metriche (CAGR, Sharpe, Sortino, Max Drawdown, win rate) |
| `utils/data_fetcher.py` | `MarketDataFetcher` con cache TTL; Yahoo Finance via `yfinance`, fallback automatico su serie GBM deterministica |
| `core/engine.py` | `TradingEngine`: ciclo periodico asincrono che allinea la posizione al segnale |
| `execution/broker.py` | `PaperBroker` in memoria; `AlpacaBroker` e `CcxtBroker` pronti da completare |

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
   (`uvicorn main:app --host 0.0.0.0 --port $PORT`). Il deploy usa il branch di default del repo GitHub.
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
| `SYMBOL` | no | `SPY` | Ticker da tradare (es. `AAPL`, `BTC-USD`) |
| `DATA_PERIOD` / `DATA_INTERVAL` | no | `2y` / `1d` | Storico scaricato |
| `SHORT_WINDOW` / `LONG_WINDOW` | no | `20` / `50` | Finestre delle SMA |
| `INITIAL_CAPITAL` / `FEE_BPS` / `TRADE_QUANTITY` | no | `10000` / `5` / `10` | Parametri di capitale e costi |
| `LOOP_INTERVAL_SECONDS` | no | `300` | Frequenza del ciclo del bot |
| `AUTO_START_BOT` | no | `false` | Avvia il bot all'avvio del server |
| `BROKER` | no | `paper` | `paper`, `alpaca`, `ccxt` |
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | se `BROKER=alpaca` | — | 🔐 **Segreti**: solo su Render, mai nel repo |
| `ALPACA_BASE_URL` | no | paper endpoint | `https://paper-api.alpaca.markets` |
| `CCXT_EXCHANGE` / `CCXT_API_KEY` / `CCXT_SECRET` | se `BROKER=ccxt` | — | 🔐 **Segreti** dell'exchange |
| `CCXT_SANDBOX` | no | `true` | Usa la testnet dell'exchange |

### Note operative su Render

- **Piano free**: il servizio va in *sleep* dopo ~15 minuti senza traffico HTTP,
  quindi il loop del bot si ferma. Per un bot sempre attivo usa almeno il
  piano *Starter* (o un ping esterno, sconsigliato per uso serio).
- **Stato in memoria**: posizioni del paper broker e log si azzerano a ogni
  deploy/riavvio. Per persistenza aggiungi un database (es. Render PostgreSQL).
- **Yahoo Finance** può limitare le richieste da IP cloud: in quel caso il
  sistema passa automaticamente ai dati mock e lo segnala nei log e in
  `last_data_source_used` dello stato.
- Un solo processo uvicorn (nessun `--workers N`): il bot vive nel processo
  web e più worker creerebbero più bot indipendenti.
