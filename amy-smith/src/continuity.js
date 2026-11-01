import { readFileSync, writeFileSync, renameSync, existsSync } from 'node:fs';
import path from 'node:path';
import { DATA_DIR } from './config.js';

// Continuity (Amy's spec: Downloads/amy-continuity-spec.md). What makes time and mistakes count:
//  - clocks: when the bot started, how long it was down before that, when each conversation was last
//    active, so "it's been six hours" or "I was offline for an hour" is a fact, not a guess;
//  - bruises: tool errors, phantom claims and times someone told her she was wrong. Each one stays
//    as a caution in her prompt for a day, and gets louder when the same thing happens again.
// Everything is in data/continuity.json, written atomically; only arithmetic, never a model call.
// Her moods, notes, threads and to-dos already persist and move on their own (pressure.js,
// scratchpad.js, obligations); this adds the parts that were missing.

const FILE = path.join(DATA_DIR, 'continuity.json');
const BRUISE_HOURS = 24;
const MAX_BRUISES = 20;
const MAX_CONVERSATIONS = 50;
export const BLOCK_CHARS = 1050; // with the closing line, stays under ~300 tokens

let state = { started_at: Date.now(), last_alive: Date.now(), downtime_ms: 0, conversations: {}, bruises: [] };

const save = () => {
  try {
    writeFileSync(`${FILE}.tmp`, JSON.stringify(state, null, 2));
    renameSync(`${FILE}.tmp`, FILE);
  } catch {}
};

export function startContinuity() {
  try {
    if (existsSync(FILE)) state = { ...state, ...JSON.parse(readFileSync(FILE, 'utf8')) };
  } catch {} // corrupt: start fresh, don't crash
  const now = Date.now();
  // The gap since the last heartbeat is how long she was off.
  state.downtime_ms = state.last_alive ? Math.max(0, now - state.last_alive) : 0;
  state.started_at = now;
  state.last_alive = now;
  prune();
  save();
  const beat = setInterval(() => {
    state.last_alive = Date.now();
    prune();
    save();
  }, 60_000);
  beat.unref?.();
}

function prune() {
  const now = Date.now();
  state.bruises = state.bruises.filter((b) => b.expires > now).slice(-MAX_BRUISES);
  const convs = Object.entries(state.conversations).sort((a, b) => b[1] - a[1]).slice(0, MAX_CONVERSATIONS);
  state.conversations = Object.fromEntries(convs);
}

// When this conversation was last active (before now), then mark it active.
export function touch(conversation) {
  const before = state.conversations[conversation] ?? null;
  state.conversations[conversation] = Date.now();
  prune();
  save();
  return before;
}
export const lastActive = (conversation) => state.conversations[conversation] ?? null;

// A mistake. The same kind+key again within the day deepens the existing bruise instead of adding one.
export function bruise({ kind, key, context, lesson }) {
  const now = Date.now();
  const existing = state.bruises.find((b) => b.kind === kind && b.key === key && b.expires > now);
  if (existing) {
    existing.count++;
    existing.last = now;
    existing.context = String(context ?? existing.context).slice(0, 200);
    existing.expires = now + BRUISE_HOURS * 3600_000;
  } else {
    state.bruises.push({ kind, key, context: String(context ?? '').slice(0, 200), lesson, count: 1, first: now, last: now, expires: now + BRUISE_HOURS * 3600_000 });
  }
  prune();
  save();
}
export const bruises = () => state.bruises.filter((b) => b.expires > Date.now());

const ago = (ms) => {
  const m = Math.round(ms / 60000);
  if (m < 2) return 'just now';
  if (m < 90) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 36 ? `${h}h ago` : `${Math.round(h / 24)} days ago`;
};
const span = (ms) => ago(ms).replace(' ago', '');

// The block for her prompt: what time it is and how long things have been, and what to be careful
// of. speakerLastSeen is when this person last talked to her, from before this message.
export function continuityBlock({ conversation, speakerName, speakerLastSeen, convLastActive }) {
  const now = Date.now();
  const time = [`It's ${new Date(now).toLocaleString([], { weekday: 'long', hour: 'numeric', minute: '2-digit' })}.`];
  if (convLastActive) {
    const restartedSince = state.started_at > convLastActive;
    time.push(`This conversation was last active ${ago(now - convLastActive)}${restartedSince ? `, before you were restarted (you were offline about ${span(state.downtime_ms)})` : ''}.`);
  } else if (state.downtime_ms > 5 * 60_000 && now - state.started_at < 3600_000) {
    time.push(`You were restarted ${ago(now - state.started_at)} after being offline about ${span(state.downtime_ms)}.`);
  }
  if (speakerName && speakerLastSeen && now - speakerLastSeen > 30 * 60_000) time.push(`${speakerName} last talked to you ${ago(now - speakerLastSeen)}.`);
  const gap = convLastActive ? now - convLastActive : 0;
  if (gap > 45 * 60_000 || (convLastActive && state.started_at > convLastActive)) time.push(`It's natural to notice the gap, the way a person picking a conversation back up would (briefly, not as a report).`);
  const lines = [`[time] ${time.join(' ')}`];
  // Worst first: repeated and recent mistakes.
  const active = bruises().sort((a, b) => b.count - a.count || b.last - a.last);
  for (const b of active) {
    const repeat = b.count > 1 ? ` (${b.count}x today, keep it from happening again)` : '';
    lines.push(`[caution] ${b.lesson}${repeat}. Last time (${ago(now - b.last)}): ${b.context}`);
    if (lines.join('\n').length > BLOCK_CHARS) {
      lines.pop();
      lines.push(`[caution] ...and ${active.length - (lines.length - 1)} more; see self logs or knowledge/tool-failures.md.`);
      break;
    }
  }
  return `Continuity (facts about time and your recent mistakes; use them, don't recite them):\n${lines.join('\n')}`;
}

export const snapshot = () => ({ ...state, file: FILE });
