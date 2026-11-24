@AGENTS.md

## Istruzioni specifiche per Claude Code

AGENTS.md (importato sopra) vale per tutti gli agenti; qui solo ciò che riguarda Claude Code.

### Metodo di lavoro

- Un compito per sessione. Tra un compito e il successivo usa `/clear`.
- Modifiche su più file o di architettura: prima plan mode, fai approvare il piano, poi implementa.
- Prima di modificare leggi i file coinvolti e i test esistenti. Niente refactor non richiesti.
- Ogni cambio di comportamento va con un test; esegui la verifica standard di AGENTS.md.
- Alla fine riporta: cosa è cambiato, comandi eseguiti con esito, cosa resta da fare.
- Subagenti solo se l'utente lo chiede o per la revisione con `reviewer` prima di chiudere un compito grande.

### Ambiente (sessioni cloud)

- All'avvio di ogni sessione cloud `.claude/hooks/session_start.sh` installa `requirements-dev.txt`. Il messaggio iniziale della sessione riporta l'esito.
- Se qualcosa manca, i dettagli sono in `.claude/hooks/.state/session-setup.log`; per ripetere la preparazione: `CLAUDE_SETUP_FORCE=1 bash .claude/hooks/session_start.sh`.

### Hook automatici (`.claude/settings.json`)

- Gli hook partono tramite `.claude/hooks/run.sh`, che usa il primo Python 3.9+ disponibile (`python3`, `python` o `py`). Se Python manca vengono saltati senza bloccare.
- Dopo ogni modifica un controllo rapido del file (sintassi, nomi non definiti, JSON/YAML validi). Se segnala un errore, correggilo subito.
- Prima di chiudere il turno, se hai modificato codice, partono `python -m pytest -q -x`. Se falliscono correggi, oppure spiega perché il problema non dipende dalle tue modifiche. Per disattivarli in una sessione: `CLAUDE_SKIP_STOP_TESTS=1`.

### Skill

- Di progetto: `strategy-change`, da usare per ogni modifica a strategie, backtest o metriche.
- Generali, da `addyosmani/agent-skills` (versione fissata, fonte in `.claude/references/README.md`): pianificazione (`planning-and-task-breakdown`), piccoli passi (`incremental-implementation`), test prima del codice (`test-driven-development`), revisione (`code-review-and-quality`), debug (`debugging-and-error-recovery`), sicurezza (`security-and-hardening`), osservabilità (`observability-and-instrumentation`).

### Subagenti

- `reviewer` (`.claude/agents/reviewer.md`): revisione indipendente del diff con i controlli specifici di questo progetto. Usalo quando l'utente lo chiede o prima di chiudere un compito grande.

### Server MCP

- `rea` ([REA](https://github.com/morluto/rea) di morluto, MIT, 6.10.1) è dichiarato in `.mcp.json`
  (`node .claude/rea/rea.mjs`, uguale su Linux, macOS e Windows) e attivo in `.claude/settings.json`:
  in ogni sessione Claude Code su questa repo, nel cloud e sul PC, ci sono i suoi 61 strumenti per gli
  APK (famiglie `android,apktool,artifact,adb,flutter`: manifest, firma, risorse, classi, Flutter, adb).
  JADX e Apktool li installa `.claude/rea/installa.sh` (nel cloud parte da solo all'avvio della
  sessione; prima della prima analisi `bash .claude/rea/installa.sh --attendi`) o, sul PC Windows,
  `.claude\rea\installa-windows.ps1`, che registra REA anche in Claude Desktop. Come usarlo: skill
  `rea`. Controllo: `node .claude/rea/check.mjs [file.apk]`.
- GitHub è già disponibile tramite `gh`.
