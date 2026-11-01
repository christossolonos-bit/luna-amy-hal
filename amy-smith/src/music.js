import { readdirSync, readFileSync, writeFileSync, existsSync, mkdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { DATA_DIR } from './config.js';

// The music library: every audio file in data/music, whether she made it (generate_music) or it
// was dropped in by hand, plus named playlists. library.json keeps what's known about her own
// songs (title and the prompt they were made from) so she can pick tracks by mood, not just name.
export const MUSIC_DIR = path.join(DATA_DIR, 'music');
const INFO_FILE = path.join(MUSIC_DIR, 'library.json');
const PLAYLISTS_FILE = path.join(DATA_DIR, 'playlists.json');
const AUDIO = /\.(mp3|wav|ogg|opus|m4a|aac|flac|webm)$/i;

const readJson = (file, fallback) => {
  try {
    return existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) : fallback;
  } catch {
    return fallback;
  }
};

// Songs made before titles existed end in a timestamp ("...-mvp3dt7z"); leave that out of the title.
const titleFromFile = (file) =>
  path.basename(file, path.extname(file)).replace(/-[a-z0-9]{8}$/, '').replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').trim();

// [{ file, title, about, made }] sorted by title. `about` is the prompt for songs she made.
export function tracks() {
  mkdirSync(MUSIC_DIR, { recursive: true });
  const info = readJson(INFO_FILE, {});
  return readdirSync(MUSIC_DIR)
    .filter((f) => AUDIO.test(f) && statSync(path.join(MUSIC_DIR, f)).isFile())
    .map((file) => ({ file, title: info[file]?.title || titleFromFile(file), about: info[file]?.about || '', made: Boolean(info[file]) }))
    .sort((a, b) => a.title.localeCompare(b.title));
}

// Saves a song she made under a readable, unique file name. Returns the file name.
export function addSong(bytes, { title, about }) {
  mkdirSync(MUSIC_DIR, { recursive: true });
  const slug = String(title || 'song').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60) || 'song';
  let file = `${slug}.mp3`;
  for (let n = 2; existsSync(path.join(MUSIC_DIR, file)); n++) file = `${slug}-${n}.mp3`;
  writeFileSync(path.join(MUSIC_DIR, file), bytes);
  const info = readJson(INFO_FILE, {});
  info[file] = { title: title || titleFromFile(file), about, at: Date.now() };
  writeFileSync(INFO_FILE, JSON.stringify(info, null, 2));
  return file;
}

// Finds the best track for a spoken or typed title: exact, then contains, then most words in common.
export function findTrack(query, list = tracks()) {
  const q = String(query ?? '').toLowerCase().replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ').trim();
  if (!q) return null;
  const norm = (t) => t.title.toLowerCase().replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ').trim();
  const exact = list.find((t) => norm(t) === q || t.file.toLowerCase() === q);
  if (exact) return exact;
  // Several titles contain it: one that starts with it wins, then the closest in length.
  const contains = list.filter((t) => norm(t).includes(q) || q.includes(norm(t)));
  if (contains.length) {
    const rank = (t) => (norm(t).startsWith(q) ? 0 : 1000) + Math.abs(norm(t).length - q.length);
    return contains.sort((a, b) => rank(a) - rank(b))[0];
  }
  const words = q.split(' ').filter((w) => w.length > 2);
  let best = null, score = 0;
  for (const t of list) {
    const s = words.filter((w) => norm(t).includes(w) || t.about.toLowerCase().includes(w)).length;
    if (s > score) [best, score] = [t, s];
  }
  return score >= Math.max(1, Math.ceil(words.length / 2)) ? best : null;
}

// ---- Playlists: { name: [file, ...] } ----

export const playlists = () => readJson(PLAYLISTS_FILE, {});

export function findPlaylist(name) {
  const all = playlists();
  const q = String(name ?? '').toLowerCase().trim();
  const key = Object.keys(all).find((k) => k.toLowerCase() === q) ?? Object.keys(all).find((k) => k.toLowerCase().includes(q) || q.includes(k.toLowerCase()));
  if (!key) return null;
  const have = new Set(tracks().map((t) => t.file));
  return { name: key, files: all[key].filter((f) => have.has(f)) }; // skip songs deleted since
}

export function savePlaylist(name, files) {
  const all = playlists();
  const key = Object.keys(all).find((k) => k.toLowerCase() === name.toLowerCase()) ?? name;
  all[key] = files;
  writeFileSync(PLAYLISTS_FILE, JSON.stringify(all, null, 2));
  return key;
}

export function deletePlaylist(name) {
  const found = findPlaylist(name);
  if (!found) return null;
  const all = playlists();
  delete all[found.name];
  writeFileSync(PLAYLISTS_FILE, JSON.stringify(all, null, 2));
  return found.name;
}
