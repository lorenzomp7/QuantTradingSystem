# TradingView – scheda da fonti pubbliche

- Pacchetto: `com.tradingview.tradingviewapp`
- Data: 2026-11-24. **Fonti pubbliche, non l'APK**; le righe «da APK» si completano con
  `bash tools/analyze_apps.sh tradingview`.

## Grafici e replay (punti 19 e 27)

| Cosa | Trovato | Fonte |
|---|---|---|
| App Android | nativa dal 2018, con gli stessi strumenti di disegno e indicatori del sito | [blog TradingView](https://www.tradingview.com/blog/en/meet-the-tradingview-app-for-android-10038) |
| Libreria dei grafici | per chi integra la loro libreria (Advanced Charts) non esistono wrapper nativi: si usa una WebView con un ponte nativo ↔ JavaScript; in mobile una sola scala dei prezzi e pulsanti di zoom/scorrimento al tocco | [documentazione Charting Library](https://www.tradingview.com/charting-library-docs/latest/mobile_specifics/) |
| Bar Replay | riavvolge il grafico e lo riproduce alla velocità e risoluzione scelte | [App Store](https://apps.apple.com/KW/app/id1205990992) |
| Legenda in mobile | chiusura e variazione %; OHLC e valori degli indicatori solo in «tracking mode» (pressione lunga sulla barra) | [documentazione](https://www.tradingview.com/charting-library-docs/v29/mobile_specifics) |
| Libreria usata dall'app stessa (WebView o nativa) | da APK | — |

## Avvisi (punto 31)

| Cosa | Trovato | Fonte |
|---|---|---|
| Canali | avvisi nel cloud, notificati via email, app, desktop o **webhook** | [App Store](https://apps.apple.com/kz/app/id1205990992) |
| Problemi | utenti segnalano avvisi non sincronizzati tra telefono e tablet e un registro in cui ogni avviso va aperto per segnarlo letto | [App Store](https://apps.apple.com/PL/app/id1205990992), [App Store](https://apps.apple.com/IN/app/id1205990992) |
| FCM, canali di notifica | da APK | — |

## Dal telefono (punto 26)

| Cosa | Trovato | Fonte |
|---|---|---|
| Paper trading | conto simulato integrato nel grafico | [App Store](https://apps.apple.com/KW/app/id1205990992) |
| Richiesta utenti | aprire direttamente il grafico invece della watchlist | [App Store](https://apps.apple.com/PL/app/id1205990992) |
| Timeframe | cambiare l'intervallo su un grafico non lo cambia sugli altri aperti (lamentela) | [App Store](https://apps.apple.com/QA/app/id1205990992) |

## Cosa portare in QTS

- Punto 27: replay con controllo di velocità e passo; un solo grafico guida il cursore e gli
  altri (drawdown, equity) lo seguono.
- Punto 19: in mobile mostrare chiusura e variazione e il resto solo al tocco lungo.
- Punto 31: il webhook generico già previsto (WEBHOOK_URL) è lo stesso meccanismo di
  TradingView; mantenere un solo stato «letto» per avviso, non per dispositivo.
- Punto 26: aprire la dashboard direttamente sul grafico dell'ultimo backtest.
