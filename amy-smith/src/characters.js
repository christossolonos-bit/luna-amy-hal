import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import { config, DATA_DIR, updateSettings } from './config.js';
import { setPersona } from './persona.js';
import { activity } from './events.js';

// The avatar studio's cast. "main" is the bot itself: its name, persona and voice are the normal
// settings, so Discord and the dashboard stay in sync with it. Every other character lives in
// data/characters.json with its own name, persona, Fish voice and model (relative to data/).
const file = path.join(DATA_DIR, 'characters.json');
const DEFAULT_EXTRAS = [{ id: 'leo', name: 'Leo', persona: '', voiceId: '', model: 'characters/leo.glb' }];

let extras = DEFAULT_EXTRAS;
try {
  if (existsSync(file)) extras = JSON.parse(readFileSync(file, 'utf8'));
} catch (err) {
  console.warn('Ignoring unreadable data/characters.json:', err.message);
}
const saveExtras = () => writeFileSync(file, JSON.stringify(extras, null, 2));

export function list() {
  return [
    { id: 'main', main: true, name: config.botName, persona: config.persona, voiceId: config.fish.voiceId || '', modelPath: config.avatar.path },
    ...extras.map((c) => ({ ...c, main: false, modelPath: path.join(DATA_DIR, c.model) })),
  ];
}

export const get = (id) => list().find((c) => c.id === id) ?? null;

export function update(id, { name, persona, voiceId }, changedBy = 'dashboard') {
  if (id === 'main') {
    setPersona({ name, persona, reason: 'Edited in avatar studio', changedBy });
    if (voiceId !== undefined) updateSettings({ fishVoiceId: voiceId });
    return get('main');
  }
  const c = extras.find((x) => x.id === id);
  if (!c) return null;
  if (typeof name === 'string' && name.trim()) c.name = name.trim();
  if (typeof persona === 'string') c.persona = persona.trim();
  if (typeof voiceId === 'string') c.voiceId = voiceId.trim();
  saveExtras();
  activity('persona', { reason: `${c.name} edited`, changedBy, name: c.name });
  return get(id);
}

// Who a message is for: the character named earliest in it ("Leo, what do you think?"),
// otherwise whoever the person was already talking to.
export function addressee(text, fallbackId) {
  let best = null;
  for (const c of list()) {
    const name = c.name?.trim();
    if (!name || c.solo) continue; // solo characters are only talked to on their own page
    const m = new RegExp(`\\b${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'i').exec(text);
    if (m && (!best || m.index < best.index)) best = { id: c.id, index: m.index };
  }
  return best?.id ?? (get(fallbackId) ? fallbackId : 'main');
}

// Each character's emotional state is kept separately; "avatar" is the main one's (from before
// there was a cast) so her existing mood carries over.
export const emotionKey = (id) => (id === 'main' ? 'avatar' : `avatar:${id}`);
