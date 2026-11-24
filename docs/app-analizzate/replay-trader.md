# Replay Trader – scheda da fonti pubbliche

- Pacchetto: `nl.buqe.chartreplay` (sviluppatore Daan Koeman)
- Data: 2026-11-24. **Fonti pubbliche, non l'APK**.

| Punto | Trovato | Fonte |
|---|---|---|
| 27 – replay | riproduce dati storici reali barra per barra «come se fossero live», per esercitarsi senza rischio | [mwm.ai, dalla scheda dello store](https://mwm.ai/apps/replay-trader/6778979705) |
| 26 – uso dal telefono | ambiente simulato: si aprono e chiudono posizioni mentre le barre avanzano | idem |
| Libreria dei grafici, dati | da APK | — |

App simili dello stesso genere aggiungono: scelta di data e ora di partenza da un calendario
([TradeSimuFx](https://apps.apple.com/jp/app/fxtester/id1516365841?l=en)) e replay dei propri
trade reali importati da file ([TradeReplay per Mac](https://apps.apple.com/app/id6766245495)).

## Cosa portare in QTS

- Punto 27: partenza del replay da una data scelta, poi barra per barra; a ogni passo solo i dati
  fino a t (stessa regola anti look-ahead del progetto).
- Punto 27, estensione: sovrapporre al replay i trade veri del bot (dal branch `bot-state`) per
  rivedere le decisioni prese.
