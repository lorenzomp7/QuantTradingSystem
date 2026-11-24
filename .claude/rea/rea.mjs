#!/usr/bin/env node
// Avvia REA (https://github.com/morluto/rea, licenza MIT) con la versione fissata e gli
// strumenti per gli APK (JADX headless, Apktool) installati da .claude/rea/installa.sh
// (Linux, macOS, sessioni cloud) o da .claude/rea/installa-windows.ps1 (Windows).
//
//   node .claude/rea/rea.mjs                     server MCP su stdio (lo usa .mcp.json)
//   node .claude/rea/rea.mjs <comando> [...]     un comando di REA da terminale,
//                                                es. inspect-android-package app.apk --detail summary --evidence_output out.json
//
// Funziona uguale su Linux, macOS e Windows; non serve `rea` installato globalmente.
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';

const REA_VERSION = '6.10.1';
const JADX_JAR = 'jadx-headless-mcp-0.7.1-all.jar';
const FAMILIES = 'android,apktool,artifact,adb,flutter';
const win = process.platform === 'win32';

export function toolDir() {
  if (process.env.REA_TOOLS_DIR) return process.env.REA_TOOLS_DIR;
  if (win) return join(process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local'), 'rea');
  return join(process.env.XDG_CACHE_HOME || join(homedir(), '.cache'), 'rea-tools');
}

export function reaEnv(base = process.env) {
  const dir = toolDir();
  const env = { ...base };
  // REA cerca il JAR solo quando uno strumento lo usa: il percorso può essere impostato
  // anche se l'installazione in background non è ancora finita.
  if (!env.REA_JADX_MCP_JAR) env.REA_JADX_MCP_JAR = join(dir, JADX_JAR);
  const apktool = join(dir, win ? 'apktool.bat' : 'apktool');
  const managed = existsSync(join(dir, '.installa-stato'));
  if (!env.REA_APKTOOL_COMMAND && (managed || existsSync(apktool))) env.REA_APKTOOL_COMMAND = apktool;
  if (!env.REA_MCP_TOOL_FAMILIES) env.REA_MCP_TOOL_FAMILIES = FAMILIES;
  return env;
}

function nodeOk(v = process.versions.node) {
  const [maj, min, pat] = v.split('.').map(Number);
  const ge = (a, b, c) => maj > a || (maj === a && (min > b || (min === b && pat >= c)));
  return (maj === 22 && ge(22, 23, 4)) || (maj === 24 && ge(24, 22, 1)) || ge(26, 12, 1);
}

function npxCommand() {
  const bin = dirname(process.execPath);
  const candidates = win
    ? [join(bin, 'node_modules', 'npm', 'bin', 'npx-cli.js')]
    : [join(bin, '..', 'lib', 'node_modules', 'npm', 'bin', 'npx-cli.js'),
       join(bin, 'node_modules', 'npm', 'bin', 'npx-cli.js')];
  const cli = candidates.find((p) => existsSync(p));
  if (cli) return { cmd: process.execPath, pre: [cli], shell: false };
  return { cmd: 'npx', pre: [], shell: win };
}

function main() {
  if (!nodeOk()) {
    process.stderr.write(`REA ${REA_VERSION} vuole Node ^22.23.4, ^24.22.1 o >=26.12.1; qui c'è ${process.versions.node}.\n`);
    process.exit(1);
  }
  const args = process.argv.slice(2);
  const reaArgs = args.length ? args : ['--mcp'];
  const { cmd, pre, shell } = npxCommand();
  const child = spawn(cmd, [...pre, '-y', `rea-agents@${REA_VERSION}`, ...reaArgs], {
    stdio: 'inherit',
    env: reaEnv(),
    shell,
  });
  for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP']) {
    process.on(sig, () => { try { child.kill(sig); } catch { /* già uscito */ } });
  }
  child.on('error', (e) => {
    process.stderr.write(`REA non parte: ${e.message}\n`);
    process.exit(1);
  });
  child.on('exit', (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith('rea.mjs')) main();
