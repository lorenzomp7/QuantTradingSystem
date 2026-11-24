# App analizzate

Una scheda per app di riferimento del Piano (integrazione QTS 7): cosa usano per
grafici, indicatori, notifiche e backtest dal telefono, collegato ai punti 19, 26,
27, 29 e 31 del foglio «Modifiche».

| App | Pacchetto | Scheda |
|---|---|---|
| TradingView | `com.tradingview.tradingviewapp` | da fare |
| BTS – Backtest Strategy | `com.backtest` | da fare |
| TradeTracer | `ai.tradetracer.app` | da fare |
| Replay Trader | `nl.buqe.chartreplay` | da fare |

## Come si fanno

Sul tuo computer, con il telefono collegato via USB (debug USB attivo) e le app
installate dal Play Store:

```bash
npm i -g rea-agents                     # REA, MIT: https://github.com/morluto/REA
export REA_JADX_MCP_JAR=/percorso/jadx-headless-mcp-0.7.1-all.jar   # SHA-256 6e5eacf5…ff75
export REA_APKTOOL_COMMAND=/percorso/apktool                        # Apktool >= 3.0.2
rea inspect-adb-client && rea inspect-apktool-client                # tutto pronto?
bash tools/analyze_apps.sh              # oppure: bash tools/analyze_apps.sh tradingview
```

Lo script copia ogni APK in `~/qts-apk/<app>/` (fuori dalla repo), lo analizza con REA
e scrive qui `<app>.md`. Costo: 0 € (REA, JADX, Apktool e adb sono gratuiti).

## Regole

- Gli APK e i risultati completi di REA **non entrano nella repo**: sono di terzi
  (`.gitignore` esclude `*.apk`).
- Nelle schede solo dichiarazioni (librerie, permessi, componenti) e conteggi, con
  versione e data. Nessun codice, testo o immagine copiati dall'app.
- Lo scopo è capire funzioni e interoperabilità per QTS. Niente aggiramento di
  protezioni, licenze o servizi (vedi il DISCLAIMER di REA).
- Le schede generate vanno rilette a mano: la sezione «Cosa portare in QTS» si
  compila dopo la rilettura.
