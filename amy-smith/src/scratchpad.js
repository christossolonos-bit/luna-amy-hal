import { DatabaseSync } from 'node:sqlite';
import path from 'node:path';
import { DATA_DIR } from './config.js';

// Amy's working state, as she asked for it (Downloads/scratchpad-request.md):
//  - a scratchpad per conversation: short notes on moods, threads, commitments, open questions and
//    callbacks that she reads every turn and edits as things change, cleared after a day idle;
//  - at most five active threads across all conversations ("sticky notes"), dropped after 14 days.
// Neither feeds long-term memory. Both are capped so the prompt cost stays fixed.

const db = new DatabaseSync(path.join(DATA_DIR, 'companion.db'));
db.exec(`
  CREATE TABLE IF NOT EXISTS scratchpad (
    id INTEGER PRIMARY KEY,
    conversation TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    people TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    expires_at INTEGER
  );
  CREATE INDEX IF NOT EXISTS scratchpad_conv ON scratchpad (conversation, id);
  CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY,
    label TEXT NOT NULL,
    state TEXT NOT NULL,
    people TEXT NOT NULL DEFAULT '[]',
    last_touched INTEGER NOT NULL
  );
`);

export const KINDS = ['thread', 'mood', 'commitment', 'open_question', 'callback'];
const PROTECTED = ['commitment', 'open_question']; // never evicted until resolved
export const MAX_ENTRIES = 40;
export const MAX_THREADS = 5;
const MAX_TEXT = 300; // one to three sentences
// The injected block is held to ~2000 tokens (about 8000 characters) for the scratchpad, plus
// up to five short threads; that's the fixed budget the request asks for.
export const BLOCK_BUDGET_CHARS = 8000;
const IDLE_TTL_MS = Number(process.env.SCRATCHPAD_TTL_HOURS || 24) * 3600_000;
const THREAD_DECAY_MS = 14 * 24 * 3600_000;

const clip = (s, n = MAX_TEXT) => String(s ?? '').trim().replace(/\s+/g, ' ').slice(0, n);
const names = (people) => JSON.stringify((Array.isArray(people) ? people : people ? [people] : []).map((p) => clip(p, 40)).filter(Boolean).slice(0, 8));
const parse = (row) => row && { ...row, people: JSON.parse(row.people || '[]') };

// ---- Scratchpad ----

// Drops expired entries, and the whole pad once the conversation has been idle past the TTL.
function tidy(conversation) {
  const now = Date.now();
  db.prepare('DELETE FROM scratchpad WHERE conversation = ? AND expires_at IS NOT NULL AND expires_at < ?').run(conversation, now);
  const last = db.prepare('SELECT MAX(updated_at) AS t FROM scratchpad WHERE conversation = ?').get(conversation).t;
  if (last && now - last > IDLE_TTL_MS) db.prepare('DELETE FROM scratchpad WHERE conversation = ?').run(conversation);
}

export function entries(conversation) {
  tidy(conversation);
  return db.prepare('SELECT * FROM scratchpad WHERE conversation = ? ORDER BY id').all(conversation).map(parse);
}

// Keeps the pad inside its caps: oldest unprotected entries go first. If only protected ones are
// left over the cap, they stay; a commitment is never dropped before it's resolved.
function evict(conversation) {
  for (;;) {
    const all = db.prepare('SELECT id, kind, text FROM scratchpad WHERE conversation = ? ORDER BY updated_at, id').all(conversation);
    const size = all.reduce((n, e) => n + e.text.length + 40, 0);
    if (all.length <= MAX_ENTRIES && size <= BLOCK_BUDGET_CHARS) return;
    const victim = all.find((e) => !PROTECTED.includes(e.kind));
    if (!victim) return;
    db.prepare('DELETE FROM scratchpad WHERE id = ?').run(victim.id);
  }
}

export function write(conversation, { kind, text, people, expiresInHours }) {
  if (!KINDS.includes(kind)) throw new Error(`kind must be one of ${KINDS.join(', ')}`);
  if (!clip(text)) throw new Error('A note needs text');
  tidy(conversation);
  const now = Date.now();
  const expires = Number(expiresInHours) > 0 ? now + Number(expiresInHours) * 3600_000 : null;
  const id = Number(
    db.prepare('INSERT INTO scratchpad (conversation, kind, text, people, created_at, updated_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)')
      .run(conversation, kind, clip(text), names(people), now, now, expires).lastInsertRowid,
  );
  evict(conversation);
  return id;
}

export function update(conversation, id, { text, kind, people }) {
  const row = db.prepare('SELECT * FROM scratchpad WHERE id = ? AND conversation = ?').get(id, conversation);
  if (!row) return false;
  if (kind && !KINDS.includes(kind)) throw new Error(`kind must be one of ${KINDS.join(', ')}`);
  db.prepare('UPDATE scratchpad SET text = ?, kind = ?, people = ?, updated_at = ? WHERE id = ?')
    .run(text ? clip(text) : row.text, kind || row.kind, people ? names(people) : row.people, Date.now(), id);
  evict(conversation);
  return true;
}

// Resolved notes are simply gone: the pad holds open state only.
export function resolve(conversation, id) {
  return db.prepare('DELETE FROM scratchpad WHERE id = ? AND conversation = ?').run(id, conversation).changes > 0;
}

export function clear(conversation) {
  return db.prepare('DELETE FROM scratchpad WHERE conversation = ?').run(conversation).changes;
}

// ---- Active threads (shared by every conversation) ----

export function threads() {
  db.prepare('DELETE FROM threads WHERE last_touched < ?').run(Date.now() - THREAD_DECAY_MS);
  return db.prepare('SELECT * FROM threads ORDER BY last_touched DESC').all().map(parse);
}

// Creates or overwrites a thread. With five already open, a new one is refused until one retires.
export function setThread({ id, label, state, people }) {
  const now = Date.now();
  if (id) {
    const row = db.prepare('SELECT * FROM threads WHERE id = ?').get(id);
    if (!row) throw new Error(`No thread #${id}`);
    db.prepare('UPDATE threads SET label = ?, state = ?, people = ?, last_touched = ? WHERE id = ?')
      .run(label ? clip(label, 80) : row.label, state ? clip(state) : row.state, people ? names(people) : row.people, now, id);
    return Number(id);
  }
  if (!clip(label) || !clip(state)) throw new Error('A new thread needs a label and a state');
  const open = threads();
  const same = open.find((t) => t.label.toLowerCase() === clip(label, 80).toLowerCase());
  if (same) return setThread({ id: same.id, state, people });
  if (open.length >= MAX_THREADS) throw new Error(`Already ${MAX_THREADS} threads open (${open.map((t) => `#${t.id} ${t.label}`).join('; ')}). Retire one first.`);
  return Number(db.prepare('INSERT INTO threads (label, state, people, last_touched) VALUES (?, ?, ?, ?)').run(clip(label, 80), clip(state), names(people), now).lastInsertRowid);
}

export function retireThread(id) {
  return db.prepare('DELETE FROM threads WHERE id = ?').run(id).changes > 0;
}

export function clearThreads() {
  return db.prepare('DELETE FROM threads').run().changes;
}

// How old a note is, and whether an open one has gone stale (it then nags until resolved).
const age = (t) => {
  const m = Math.round((Date.now() - t) / 60000);
  return m < 60 ? `${m}m old` : m < 2880 ? `${Math.round(m / 60)}h old` : `${Math.round(m / 1440)}d old`;
};
const stale = (e) => ['commitment', 'open_question'].includes(e.kind) && Date.now() - e.created_at > 24 * 3600_000;

// The compact block added to her system prompt every turn.
export function promptBlock(conversation) {
  const pad = entries(conversation);
  const open = threads();
  const who = (p) => (p.length ? ` (${p.join(', ')})` : '');
  const parts = [];
  parts.push(
    pad.length
      ? `Your scratchpad for this conversation (private working notes; never read them out, just use them):\n` +
          pad.map((e) => `- #${e.id} [${e.kind}]${who(e.people)} ${e.text} (${age(e.created_at)}${stale(e) ? '; still open after a day: resolve it or bring it up' : ''})`).join('\n')
      : 'Your scratchpad for this conversation is empty.',
  );
  if (open.length) parts.push(`Active threads across all conversations:\n${open.map((t) => `- #${t.id} ${t.label}${who(t.people)}: ${t.state}`).join('\n')}`);
  parts.push(
    `Keep these current (note to add, scratchpad to update or resolve, thread for an ongoing situation) when something worth tracking happens (someone's mood shifts, you promise something, a question is left open, a topic to come back to); resolve notes once they're settled. Don't note small talk. If you tell someone you'll remember something, keep track of it or check in on them, write the note in that same reply; never claim a note you didn't write.`,
  );
  return parts.join('\n');
}
