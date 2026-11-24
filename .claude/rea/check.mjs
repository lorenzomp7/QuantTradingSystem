#!/usr/bin/env node
// Controlla che REA funzioni come lo usa Claude: avvia il server MCP di questa repo
// (.claude/rea/rea.mjs), chiede l'elenco degli strumenti e, se passi un APK, ne legge il
// manifest con inspect_android_package (serve JADX).
//
//   node .claude/rea/check.mjs              server e strumenti
//   node .claude/rea/check.mjs app.apk      anche un'analisi vera
//
// Esce con 1 se qualcosa non va.
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { reaEnv, toolDir } from './rea.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const apk = process.argv[2] ? resolve(process.argv[2]) : null;
const env = reaEnv();
console.log(`strumenti in: ${toolDir()}`);
console.log(`  JADX:    ${existsSync(env.REA_JADX_MCP_JAR) ? 'sì' : 'NO'}  ${env.REA_JADX_MCP_JAR}`);
console.log(`  Apktool: ${env.REA_APKTOOL_COMMAND && existsSync(env.REA_APKTOOL_COMMAND) ? 'sì' : 'NO'}  ${env.REA_APKTOOL_COMMAND ?? '(dal PATH)'}`);

const child = spawn(process.execPath, [join(here, 'rea.mjs')], { stdio: ['pipe', 'pipe', 'pipe'] });
let buf = '';
let stderr = '';
let nextId = 1;
const waiting = new Map();
child.stderr.on('data', (d) => { stderr += d; });
child.stdout.on('data', (d) => {
  buf += d;
  let i;
  while ((i = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, i).trim();
    buf = buf.slice(i + 1);
    let msg;
    try { msg = JSON.parse(line); } catch { continue; }
    if (msg.id != null && waiting.has(msg.id)) {
      waiting.get(msg.id)(msg);
      waiting.delete(msg.id);
    }
  }
});

function send(obj) { child.stdin.write(JSON.stringify(obj) + '\n'); }
function call(method, params, ms) {
  const id = nextId++;
  send({ jsonrpc: '2.0', id, method, params });
  return new Promise((ok, ko) => {
    const t = setTimeout(() => ko(new Error(`${method}: nessuna risposta in ${ms / 1000} s`)), ms);
    waiting.set(id, (m) => { clearTimeout(t); ok(m); });
  });
}

let failed = false;
try {
  // Il primo avvio scarica REA con npx: può richiedere un minuto.
  const init = await call('initialize', {
    protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'rea-check', version: '1' },
  }, 180000);
  if (init.error) throw new Error('initialize: ' + JSON.stringify(init.error));
  send({ jsonrpc: '2.0', method: 'notifications/initialized' });
  console.log(`server MCP: ${init.result.serverInfo?.name} ${init.result.serverInfo?.version}`);

  const list = await call('tools/list', {}, 60000);
  const names = (list.result?.tools ?? []).map((t) => t.name);
  console.log(`strumenti MCP: ${names.length}`);
  for (const n of ['inspect_android_package', 'inspect_apk_signature', 'decode_android_resources',
    'identify_flutter_build', 'pull_adb_package']) {
    const ok = names.includes(n);
    if (!ok) failed = true;
    console.log(`  ${ok ? 'sì' : 'NO'}  ${n}`);
  }

  if (apk) {
    const res = await call('tools/call', {
      name: 'inspect_android_package', arguments: { path: apk, detail: 'summary' },
    }, 600000);
    const text = JSON.stringify(res.result ?? res.error);
    const pkg = text.match(/"package_name":"([^"]+)"/);
    const ver = text.match(/"version_name":"([^"]+)"/);
    if (res.error || res.result?.isError || !pkg) {
      failed = true;
      console.log('inspect_android_package: ERRORE');
      console.log(text.slice(0, 1200));
    } else {
      console.log(`inspect_android_package: ${pkg[1]} ${ver ? ver[1] : ''} (JADX funziona)`);
    }
  }
} catch (e) {
  failed = true;
  console.log('ERRORE: ' + e.message);
  const tail = stderr.split('\n').filter((l) => l && !l.startsWith('Picked up')).slice(-15).join('\n');
  if (tail) console.log(tail);
} finally {
  child.kill();
}
console.log(failed ? 'REA: NON pronto' : 'REA: pronto');
process.exit(failed ? 1 : 0);
