---
name: rea
description: Analizza app Android dai loro APK con REA (morluto/rea) direttamente dalla chat - manifest, permessi, componenti, firma, risorse, librerie, Flutter, classi - e, con il telefono collegato, preleva gli APK via adb. Usala quando l'utente manda o indica un APK (.apk, .apks, .xapk, .apkm), chiede di usare REA, di studiare un'app di riferimento dal suo APK, di prendere un'app dal telefono o di compilare le schede delle app analizzate.
---

# REA dalla chat

L'utente fa tutto dalla chat: ti dà gli APK (o il telefono collegato) e tu esegui ogni
comando, senza chiedergli di lanciare niente. Costo: 0 € (REA, JADX, Apktool e adb sono gratuiti).

## 1. Prepara REA

- **Claude Code nel cloud**: l'hook SessionStart ha già avviato l'installazione in background.
  Prima della prima analisi: `bash .claude/rea/installa.sh --attendi`.
- **Claude Code su Linux o macOS**: `bash .claude/rea/installa.sh` (una volta per macchina).
- **Claude Code o Claude Desktop sul PC Windows**: se `node .claude/rea/check.mjs` dice che JADX
  manca, esegui `powershell -ExecutionPolicy Bypass -File .claude\rea\installa-windows.ps1 -SenzaDomande`
  (una volta per PC; registra REA anche in Claude Desktop) e chiedi all'utente di riavviare Claude.
- **Chat Claude con un computer Linux ma senza Claude Code** (nessun server MCP): clona la repo,
  `bash .claude/rea/installa.sh`, poi usa i comandi da terminale del punto 3.
- Controllo: `node .claude/rea/check.mjs [file.apk]` (server MCP, strumenti, e con un APK
  un'analisi vera). Se fallisce, riporta l'errore all'utente invece di aggirarlo.

Gli strumenti stanno in `~/.cache/rea-tools` (Windows: `%LOCALAPPDATA%\rea`), condivisi da tutte
le repo; nella repo non si scrive nulla.

## 2. Trova gli APK

- Allegati in chat: `/mnt/user-data/uploads/` o il percorso che indica l'utente.
- Cartella sul PC: quella che dice l'utente.
- Telefono via USB (debug USB attivo, adb installato): `list_adb_devices`, `resolve_adb_packages`
  (cerca per nome), `pull_adb_package` (copia tutto l'APK con gli SHA-256).
- `.xapk`, `.apkm`, `.apks` sono ZIP: estrai e analizza il `base.apk` (gli split di lingua o
  densità di solito non servono). Un `.apkm` cifrato non si apre: dillo all'utente.
- Copia gli APK **fuori dalla repo** (es. `~/apk/<app>/`): non vanno mai committati.

## 3. Analizza

Con il server MCP `rea` (dichiarato in `.mcp.json`, attivo in `.claude/settings.json`) chiama
direttamente gli strumenti. Senza MCP usa gli stessi comandi da terminale, con i trattini:
`node .claude/rea/rea.mjs <comando> ...` (nel cloud anche `rea <comando> ...`).

| Cosa serve | Strumento MCP | Da terminale |
|---|---|---|
| Pacchetto, versione, SDK, permessi, componenti | `inspect_android_package` (`detail: "summary"`) | `inspect-android-package app.apk --detail summary --evidence_output out.json` |
| Firma e certificato | `inspect_apk_signature` | `inspect-apk-signature app.apk` |
| Manifest leggibile, stringhe, risorse | `decode_android_resources` | `decode-android-resources app.apk` |
| App Flutter? Versione del motore | `identify_flutter_build` | `identify-flutter-build app.apk` |
| Classi e librerie (SDK di mappe, meteo, ML…) | `search_android_classes` (query vuota = inventario) | `search-android-classes app.apk <testo>` |
| Una classe o un metodo | `inspect_android_class`, `inspect_android_method` | `inspect-android-class`, `inspect-android-method` |
| Chi chiama cosa | `trace_android_references` | `trace-android-references` |
| Contenuto dell'archivio | `inspect_artifact` | `inspect-artifact app.apk` |

- `--evidence_output` non sovrascrive mai: usa un file nuovo fuori dalla repo.
- Le risposte complete sono lunghe: preferisci `detail: "summary"` e leggi solo quello che serve.
- Se uno strumento risponde che JADX o Apktool mancano, torna al punto 1.

## 4. Regole

- Analisi statica per capire funzioni e interoperabilità, mai per aggirare protezioni, licenze o
  servizi (vedi il DISCLAIMER di REA). Analizza solo APK che l'utente ti dà o che sono sul suo telefono.
- Nella repo vanno solo dichiarazioni (librerie, permessi, componenti, domini) e conteggi, con
  versione dell'app, data e motore usato. Nessun codice, testo o immagine copiati dall'app.
- Non scrivere come provato sul telefono ciò che l'utente non ha visto succedere.
- Commit secondo AGENTS.md (autore, eventuale `[skip ci]`), push sul branch di lavoro (`main_claude`).

## 5. In questa repo

- App di trading del Piano (integrazione QTS 7): TradingView `com.tradingview.tradingviewapp`,
  BTS – Backtest Strategy `com.backtest`, TradeTracer `ai.tradetracer.app`, Replay Trader
  `nl.buqe.chartreplay`. Tabella e regole in `docs/app-analizzate/README.md`.
- Con il telefono collegato `bash tools/analyze_apps.sh [app]` (usa `rea` dal PATH). Con gli APK
  dati in chat usa gli strumenti MCP e scrivi `docs/app-analizzate/<app>.md` nello stesso formato;
  aggiorna la colonna «Scheda».
