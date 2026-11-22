# 📈 QuantTradingSystem

Sistema di trading quantitativo in Python 3.10+ con dashboard web (FastAPI),
strategia di esempio **Moving Average Crossover**, backtest vettorizzato
(Pandas/NumPy), paper broker e deploy automatico gratuito: dashboard su **GitHub Pages**,
bot e API su una **VM Oracle Cloud Always Free** (0 €/mese, sempre accesa).

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

### 2. Deploy gratuito: GitHub Pages + VM Oracle

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

Dai push successivi su `main` i due workflow si aggiornano da soli.

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
