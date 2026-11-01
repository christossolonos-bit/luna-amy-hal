import { DatabaseSync } from 'node:sqlite';
import path from 'node:path';
import { DATA_DIR, config } from './config.js';
import { embed } from './llm.js';

const db = new DatabaseSync(path.join(DATA_DIR, 'companion.db'));

db.exec(`
  PRAGMA journal_mode = WAL;
  CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    conversation TEXT NOT NULL,
    role TEXT NOT NULL,
    user_id TEXT,
    name TEXT,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL
  );
  CREATE INDEX IF NOT EXISTS messages_conv ON messages (conversation, id);
  CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding TEXT NOT NULL,
    created_at INTEGER NOT NULL
  );
  CREATE INDEX IF NOT EXISTS memories_user ON memories (user_id);
  -- Consolidated history. Level 1: a paragraph covering one block of messages (up to last_message_id).
  -- Level 2: the running major summary that absorbs level-1 paragraphs (rolled_up = 1 once absorbed).
  CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY,
    conversation TEXT NOT NULL,
    level INTEGER NOT NULL,
    content TEXT NOT NULL,
    last_message_id INTEGER,
    rolled_up INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
  );
  CREATE INDEX IF NOT EXISTS summaries_conv ON summaries (conversation, level, id);
  -- Everyone the bot has talked to, with their tier (stranger | guest | friend). last_dm_at is set
  -- once they've DM'd it, which is what lets it DM them back.
  CREATE TABLE IF NOT EXISTS contacts (
    user_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    username TEXT,
    tier TEXT NOT NULL DEFAULT 'stranger',
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    last_dm_at INTEGER
  );
  -- People she has introduced herself to in voice, so each person only gets the "I'm an AI, I'm
  -- listening" hello once.
  CREATE TABLE IF NOT EXISTS greeted (
    user_id TEXT PRIMARY KEY,
    at INTEGER NOT NULL
  );
  -- Obligations: things she has committed to do, open until checked off. A target means "when this
  -- person shows up" (target_id once known, else matched by target_name); none means a standing priority.
  CREATE TABLE IF NOT EXISTS obligations (
    id INTEGER PRIMARY KEY,
    task TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'task',
    target_id TEXT,
    target_name TEXT,
    created_by TEXT,
    created_at INTEGER NOT NULL,
    done_at INTEGER,
    note TEXT
  );
  CREATE TABLE IF NOT EXISTS persona_history (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    persona TEXT NOT NULL,
    reason TEXT,
    changed_by TEXT,
    created_at INTEGER NOT NULL
  );
`);

// What tools a reply used ("guide files: ok"), so later turns know it was already done.
try {
  db.exec('ALTER TABLE messages ADD COLUMN tools TEXT');
} catch {} // already there

const q = {
  addMessage: db.prepare(
    'INSERT INTO messages (conversation, role, user_id, name, content, created_at, tools) VALUES (?, ?, ?, ?, ?, ?, ?)',
  ),
  recent: db.prepare(
    'SELECT role, user_id, name, content FROM messages WHERE conversation = ? ORDER BY id DESC LIMIT ?',
  ),
  clearConversation: db.prepare('DELETE FROM messages WHERE conversation = ?'),
  clearSummaries: db.prepare('DELETE FROM summaries WHERE conversation = ?'),
  summarizedUpTo: db.prepare(
    'SELECT COALESCE(MAX(last_message_id), 0) AS id FROM summaries WHERE conversation = ? AND level = 1',
  ),
  messagesAfter: db.prepare(
    'SELECT id, role, user_id, name, content, created_at FROM messages WHERE conversation = ? AND id > ? ORDER BY id LIMIT ?',
  ),
  countAfter: db.prepare('SELECT COUNT(*) AS n FROM messages WHERE conversation = ? AND id > ?'),
  recentWithIds: db.prepare(
    'SELECT id, role, user_id, name, content, tools FROM messages WHERE conversation = ? ORDER BY id DESC LIMIT ?',
  ),
  addSummary: db.prepare(
    'INSERT INTO summaries (conversation, level, content, last_message_id, created_at) VALUES (?, ?, ?, ?, ?)',
  ),
  openParagraphs: db.prepare(
    'SELECT id, content, created_at FROM summaries WHERE conversation = ? AND level = 1 AND rolled_up = 0 ORDER BY id',
  ),
  majorSummary: db.prepare(
    'SELECT id, content, created_at FROM summaries WHERE conversation = ? AND level = 2 ORDER BY id DESC LIMIT 1',
  ),
  markRolledUp: db.prepare('UPDATE summaries SET rolled_up = 1 WHERE conversation = ? AND level = 1 AND id <= ?'),
  allSummaries: db.prepare('SELECT * FROM summaries WHERE conversation = ? ORDER BY id DESC'),
  addMemory: db.prepare('INSERT INTO memories (user_id, content, embedding, created_at) VALUES (?, ?, ?, ?)'),
  userMemories: db.prepare('SELECT id, content, embedding FROM memories WHERE user_id = ?'),
  listMemories: db.prepare('SELECT id, content, created_at FROM memories WHERE user_id = ? ORDER BY id'),
  deleteMemory: db.prepare('DELETE FROM memories WHERE id = ? AND user_id = ?'),
  deleteAllMemories: db.prepare('DELETE FROM memories WHERE user_id = ?'),
  memoryUsers: db.prepare(
    `SELECT m.user_id, COUNT(*) AS count, MAX(m.created_at) AS last,
       (SELECT name FROM messages WHERE user_id = m.user_id AND name IS NOT NULL ORDER BY id DESC LIMIT 1) AS name
     FROM memories m GROUP BY m.user_id ORDER BY last DESC`,
  ),
  conversations: db.prepare(
    'SELECT conversation, COUNT(*) AS count, MAX(created_at) AS last FROM messages GROUP BY conversation ORDER BY last DESC',
  ),
  stats: db.prepare('SELECT (SELECT COUNT(*) FROM messages) AS messages, (SELECT COUNT(*) FROM memories) AS memories'),
  upsertContact: db.prepare(
    `INSERT INTO contacts (user_id, name, username, first_seen, last_seen, last_dm_at) VALUES (?, ?, ?, ?, ?, ?)
     ON CONFLICT (user_id) DO UPDATE SET name = excluded.name, username = COALESCE(excluded.username, username),
       last_seen = excluded.last_seen, last_dm_at = COALESCE(excluded.last_dm_at, last_dm_at)`,
  ),
  contacts: db.prepare('SELECT user_id, name, username, tier, last_seen, last_dm_at FROM contacts ORDER BY last_seen DESC'),
  contactTier: db.prepare('SELECT tier FROM contacts WHERE user_id = ?'),
  setContactTier: db.prepare('UPDATE contacts SET tier = ? WHERE user_id = ?'),
  addPersona: db.prepare(
    'INSERT INTO persona_history (name, persona, reason, changed_by, created_at) VALUES (?, ?, ?, ?, ?)',
  ),
  personaHistory: db.prepare('SELECT * FROM persona_history ORDER BY id DESC LIMIT ?'),
  personaVersion: db.prepare('SELECT * FROM persona_history WHERE id = ?'),
  personaCount: db.prepare('SELECT COUNT(*) AS n FROM persona_history'),
};

// ---- Persona versions (every change is kept so it can be rolled back) ----

export function savePersonaVersion({ name, persona, reason = null, changedBy = null }) {
  q.addPersona.run(name, persona, reason, changedBy, Date.now());
}

export function personaHistory(limit = 50) {
  return q.personaHistory.all(limit);
}

export function personaVersion(id) {
  return q.personaVersion.get(id);
}

export function hasPersonaHistory() {
  return q.personaCount.get().n > 0;
}

// ---- Contacts ----

// Records that the bot heard from someone (and whether it was a DM). New people start as strangers.
export function noteContact({ userId, name, username = null, dm = false }) {
  const now = Date.now();
  q.upsertContact.run(userId, name, username, now, now, dm ? now : null);
}

export function contactTier(userId) {
  return q.contactTier.get(userId)?.tier ?? 'stranger';
}

export function setContactTier(userId, tier) {
  return q.setContactTier.run(tier, userId).changes > 0;
}

export function listContacts() {
  return q.contacts.all();
}

// Finds a contact by ID, display name or username (exact match first, then partial).
// dmOnly limits it to people who have DM'd the bot.
export function findContact(who, { dmOnly = false } = {}) {
  const needle = String(who).trim().replace(/^@/, '').toLowerCase();
  if (!needle) return null;
  const all = q.contacts.all().filter((c) => !dmOnly || c.last_dm_at);
  const names = (c) => [c.user_id, c.name, c.username].filter(Boolean).map((n) => n.toLowerCase());
  return (
    all.find((c) => names(c).includes(needle)) ??
    all.find((c) => names(c).some((n) => n.includes(needle))) ??
    null
  );
}

// ---- Short-term: rolling conversation history (per channel, or per voice session) ----

export function addMessage(conversation, { role, userId = null, name = null, content, tools = null }) {
  q.addMessage.run(conversation, role, userId, name, content, Date.now(), tools);
}

export function recentMessages(conversation, limit = config.historyLimit) {
  return q.recent.all(conversation, limit).reverse();
}

export function clearConversation(conversation) {
  q.clearConversation.run(conversation);
  q.clearSummaries.run(conversation);
}

// ---- Consolidation: old messages are boiled down into paragraphs, and paragraphs into a major summary ----

// What the model sees as raw history: everything not yet summarized, plus the last few
// messages before that so a fresh summary doesn't leave it mid-sentence.
export function promptMessages(conversation, overlap = 10, limit = config.historyLimit) {
  const cutoff = q.summarizedUpTo.get(conversation).id;
  const rows = q.recentWithIds.all(conversation, limit).reverse();
  return rows.filter((m, i) => m.id > cutoff || i >= rows.length - overlap);
}

// The oldest block of `size` messages that hasn't been summarized yet, or null if there isn't a full one.
export function unsummarizedBlock(conversation, size) {
  const cutoff = q.summarizedUpTo.get(conversation).id;
  if (q.countAfter.get(conversation, cutoff).n < size) return null;
  return q.messagesAfter.all(conversation, cutoff, size);
}

export function addSummary(conversation, { level, content, lastMessageId = null }) {
  q.addSummary.run(conversation, level, content, lastMessageId, Date.now());
}

// Paragraphs written since the last major summary.
export function openParagraphs(conversation) {
  return q.openParagraphs.all(conversation);
}

export function majorSummary(conversation) {
  return q.majorSummary.get(conversation) ?? null;
}

// Saves a new major summary and marks the paragraphs it absorbed (up to id) as done.
export function rollUp(conversation, content, upToParagraphId) {
  db.exec('BEGIN');
  try {
    addSummary(conversation, { level: 2, content });
    q.markRolledUp.run(conversation, upToParagraphId);
    db.exec('COMMIT');
  } catch (err) {
    db.exec('ROLLBACK');
    throw err;
  }
}

export function summaries(conversation) {
  return q.allSummaries.all(conversation);
}

// ---- Long-term: facts about each user, retrieved by semantic similarity ----

export async function recall(userId, query) {
  const rows = q.userMemories.all(userId);
  if (!rows.length) return [];
  const [qv] = await embed([query]);
  return rows
    .map((r) => ({ id: r.id, content: r.content, score: cosine(qv, JSON.parse(r.embedding)) }))
    .filter((r) => r.score >= config.memory.minScore)
    .sort((a, b) => b.score - a.score)
    .slice(0, config.memory.topK);
}

// Stores facts, skipping any that are near-duplicates of what we already know.
export async function remember(userId, facts) {
  if (!facts.length) return [];
  const existing = q.userMemories.all(userId).map((r) => JSON.parse(r.embedding));
  const vectors = await embed(facts);
  const saved = [];
  facts.forEach((fact, i) => {
    const v = vectors[i];
    if (existing.some((e) => cosine(v, e) >= config.memory.dedupeScore)) return;
    q.addMemory.run(userId, fact, JSON.stringify(v), Date.now());
    existing.push(v);
    saved.push(fact);
  });
  return saved;
}

export function listMemories(userId) {
  return q.listMemories.all(userId);
}

export function forget(userId, id) {
  return id == null ? q.deleteAllMemories.run(userId).changes : q.deleteMemory.run(id, userId).changes;
}

// ---- Dashboard queries ----

export function memoryUsers() {
  return q.memoryUsers.all();
}

export function conversations() {
  return q.conversations.all();
}

export function stats() {
  return q.stats.get();
}

// People she already knew before greetings existed don't need the introduction.
if (!db.prepare('SELECT 1 FROM greeted LIMIT 1').get()) {
  db.exec('INSERT OR IGNORE INTO greeted (user_id, at) SELECT user_id, first_seen FROM contacts');
}

// Marks someone as greeted; returns false if they already were (so callers greet only once).
export function markGreeted(userId) {
  return db.prepare('INSERT OR IGNORE INTO greeted (user_id, at) VALUES (?, ?)').run(userId, Date.now()).changes > 0;
}

// ---- Check-in consent (their profile card) ----
// Whether she may reach out to someone on her own: asked once, in person; their answer, how often,
// and what they'd like to talk about. "no" is permanent: she never asks again.
for (const [col, type] of [['reach_out', 'TEXT'], ['reach_every_days', 'INTEGER'], ['reach_note', 'TEXT'], ['reach_asked_at', 'INTEGER'], ['reached_at', 'INTEGER']]) {
  try {
    db.exec(`ALTER TABLE contacts ADD COLUMN ${col} ${type}`);
  } catch {} // already there
}

export function reachCard(userId) {
  return db.prepare('SELECT user_id, name, tier, reach_out, reach_every_days, reach_note, reach_asked_at, reached_at FROM contacts WHERE user_id = ?').get(userId) ?? null;
}

export function setReach(userId, { answer, everyDays, note }) {
  const card = reachCard(userId);
  if (!card) return false;
  if (card.reach_out === 'no' && answer === 'yes') return false; // a no is final
  db.prepare('UPDATE contacts SET reach_out = ?, reach_every_days = ?, reach_note = ? WHERE user_id = ?').run(
    answer,
    answer === 'yes' ? Math.max(1, Math.round(everyDays || 7)) : null,
    note ?? card.reach_note,
    userId,
  );
  return true;
}

export const markAskedReach = (userId) => db.prepare('UPDATE contacts SET reach_asked_at = ? WHERE user_id = ?').run(Date.now(), userId);
export const markReached = (userId) => db.prepare('UPDATE contacts SET reached_at = ? WHERE user_id = ?').run(Date.now(), userId);

// Someone known under another name: "ILGAV3NG3R's real name is Brandon" in their memories means
// "Brandon" is ILGAV3NG3R. Returns their user id, or null.
export function personByRealName(name) {
  const n = String(name ?? '').trim();
  if (n.length < 2) return null;
  const row = db
    .prepare(`SELECT user_id FROM memories WHERE content LIKE ? OR content LIKE ? OR content LIKE ? OR content LIKE ? ORDER BY id DESC LIMIT 1`)
    .get(`%real name is ${n}%`, `%goes by ${n}%`, `%also called ${n}%`, `%nickname is ${n}%`);
  return row?.user_id ?? null;
}

// ---- Obligations ----

export function addObligation({ task, kind = 'task', targetId = null, targetName = null, createdBy = null }) {
  return Number(
    db.prepare('INSERT INTO obligations (task, kind, target_id, target_name, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)')
      .run(task, kind, targetId, targetName, createdBy, Date.now()).lastInsertRowid,
  );
}

export function openObligations() {
  return db.prepare('SELECT * FROM obligations WHERE done_at IS NULL ORDER BY id').all();
}

// Open items waiting for this person: by id, or by a name given before she'd met them
// ("when UltraLogic joins..."), matched against their display name or username.
export function obligationsFor(userId, names = []) {
  const lower = names.filter(Boolean).map((n) => n.toLowerCase());
  return openObligations().filter((o) => {
    if (o.target_id) return o.target_id === userId;
    if (!o.target_name) return false;
    const want = o.target_name.toLowerCase();
    return lower.some((n) => n === want || n.includes(want) || want.includes(n));
  });
}

export function completeObligation(id, note = null, userId = null) {
  return db.prepare('UPDATE obligations SET done_at = ?, note = ?, target_id = COALESCE(target_id, ?) WHERE id = ? AND done_at IS NULL')
    .run(Date.now(), note, userId, id).changes > 0;
}

export function cancelObligation(id) {
  return db.prepare("UPDATE obligations SET done_at = ?, note = 'cancelled' WHERE id = ? AND done_at IS NULL").run(Date.now(), id).changes > 0;
}

// A conversation's whole history in order, a page at a time (for rebuilding memories).
export function messagesPage(conversation, afterId = 0, size = 40) {
  return q.messagesAfter.all(conversation, afterId, size);
}

export function forgetEveryone() {
  return db.prepare('DELETE FROM memories').run().changes;
}

export async function addMemoryManually(userId, content) {
  const [v] = await embed([content]);
  q.addMemory.run(userId, content, JSON.stringify(v), Date.now());
}

function cosine(a, b) {
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    na += a[i] * a[i];
    nb += b[i] * b[i];
  }
  return dot / (Math.sqrt(na) * Math.sqrt(nb) || 1);
}
