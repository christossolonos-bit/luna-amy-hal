/** HalMini = main fact brain. Ask first; NVIDIA (or other teacher) only on miss, then teach-back. */
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { existsSync } from 'node:fs';
import { activity } from './events.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SHARE = path.resolve(ROOT, '..');
const ASK = path.join(ROOT, 'tools', 'halmini_ask.py');
const PY_CANDIDATES = [
  path.join(SHARE, 'Luna', '.venv', 'Scripts', 'python.exe'),
  path.join(SHARE, 'Amy', '.venv', 'Scripts', 'python.exe'),
  'python',
];

function pythonBin() {
  for (const p of PY_CANDIDATES) {
    if (p === 'python' || existsSync(p)) return p;
  }
  return 'python';
}

function runAsk(args, envExtra = {}, timeoutMs = 25000) {
  return new Promise((resolve) => {
    const py = pythonBin();
    const child = spawn(py, [ASK, ...args], {
      cwd: path.join(SHARE, 'Luna'),
      env: {
        ...process.env,
        HALMINI_ENABLED: '1',
        HALMINI_ROOT: path.join(SHARE, 'Amy'),
        HALMINI_DB: path.join(SHARE, 'Luna', 'data', 'luna_amy.db'),
        HALMINI_SELF_NAME: 'Luna',
        HALMINI_PERSONA: 'amy',
        RIKO_CHARACTER: 'luna',
        ...envExtra,
      },
      windowsHide: true,
    });
    let out = '';
    let err = '';
    const t = setTimeout(() => {
      child.kill();
      resolve({ ok: false, text: '', reason: 'timeout' });
    }, timeoutMs);
    child.stdout.on('data', (d) => (out += d));
    child.stderr.on('data', (d) => (err += d));
    child.on('close', () => {
      clearTimeout(t);
      try {
        const line = out.trim().split(/\r?\n/).filter(Boolean).at(-1) || '{}';
        resolve(JSON.parse(line));
      } catch {
        resolve({ ok: false, text: '', reason: `parse:${err.slice(0, 120) || out.slice(0, 120)}` });
      }
    });
  });
}

export async function tryHalMini(text, speaker = {}) {
  if (!existsSync(ASK)) return { ok: false, text: '', reason: 'missing-ask-script' };
  const body = (text || '').trim();
  if (!body) return { ok: false, text: '', reason: 'empty' };
  const result = await runAsk([body], {
    HALMINI_SPEAKER: speaker.name || 'user',
    HALMINI_SPEAKER_ID: speaker.id || '',
    HALMINI_MODE: 'ask',
  });
  if (result?.ok && result.text?.trim()) {
    activity('halmini', { ok: true, reason: result.reason, preview: result.text.slice(0, 80) });
  } else {
    activity('halmini', { ok: false, reason: result?.reason || 'miss' });
  }
  return result || { ok: false, text: '', reason: 'empty-result' };
}

/** Teach HalMini from an LLM reply (background). */
export function teachHalMini(assistantText, speaker = {}) {
  const body = (assistantText || '').trim();
  if (!body || !existsSync(ASK)) return;
  runAsk([body], {
    HALMINI_SPEAKER: speaker.name || 'TutorBot',
    HALMINI_SPEAKER_ID: speaker.id || '',
    HALMINI_MODE: 'teach',
  }).catch(() => {});
}
