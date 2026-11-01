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
  CREATE TABLE IF NOT EXISTS persona_history (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    persona TEXT NOT NULL,
    reason TEXT,
    changed_by TEXT,
    created_at INTEGER NOT NULL
  );
`);

const q = {
  addMessage: db.prepare(
    'INSERT INTO messages (conversation, role, user_id, name, content, created_at) VALUES (?, ?, ?, ?, ?, ?)',
  ),
  recent: db.prepare(
    'SELECT role, user_id, name, content FROM messages WHERE conversation = ? ORDER BY id DESC LIMIT ?',
  ),
  clearConversation: db.prepare('DELETE FROM messages WHERE conversation = ?'),
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

// ---- Short-term: rolling conversation history (per channel, or per voice session) ----

export function addMessage(conversation, { role, userId = null, name = null, content }) {
  q.addMessage.run(conversation, role, userId, name, content, Date.now());
}

export function recentMessages(conversation, limit = config.historyLimit) {
  return q.recent.all(conversation, limit).reverse();
}

export function clearConversation(conversation) {
  q.clearConversation.run(conversation);
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
