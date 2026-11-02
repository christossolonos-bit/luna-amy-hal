/** YouTube → local audio via yt-dlp (or `ytp` if that's on PATH). */
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { MUSIC_DIR } from './music.js';

export const YT_DIR = path.join(MUSIC_DIR, 'yt');
const YT_URL = /^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.be)\//i;

function pickBin() {
  // Prefer explicit override, then `ytp` if present, else yt-dlp (installed on this machine).
  return (process.env.YOUTUBE_DLP || process.env.YTP || 'yt-dlp').trim() || 'yt-dlp';
}

function run(bin, args, { timeoutMs = 180_000 } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(bin, args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '';
    let err = '';
    const t = setTimeout(() => {
      child.kill();
      reject(new Error(`${bin} timed out after ${Math.round(timeoutMs / 1000)}s`));
    }, timeoutMs);
    child.stdout.on('data', (d) => (out += d));
    child.stderr.on('data', (d) => (err += d));
    child.on('error', (e) => {
      clearTimeout(t);
      reject(e);
    });
    child.on('close', (code) => {
      clearTimeout(t);
      if (code === 0) resolve({ out, err });
      else reject(new Error(`${bin} failed (${code}): ${(err || out).slice(-400)}`));
    });
  });
}

function targetFor(query) {
  const q = String(query || '').trim();
  if (!q) throw new Error('empty YouTube query');
  if (YT_URL.test(q) || q.includes('youtube.com/') || q.includes('youtu.be/')) return q;
  return `ytsearch1:${q}`;
}

/** Return cached relative path under MUSIC_DIR if this id was downloaded before. */
function cached(id) {
  if (!id || !existsSync(YT_DIR)) return null;
  const hit = readdirSync(YT_DIR).find((f) => f === `${id}.mp3` || f.startsWith(`${id}.`));
  return hit ? path.join('yt', hit).replace(/\\/g, '/') : null;
}

/**
 * Resolve a YouTube URL or search phrase to a library-relative audio file.
 * Returns { file, title, id }.
 */
export async function fetchYoutube(query) {
  mkdirSync(YT_DIR, { recursive: true });
  const bin = pickBin();
  const target = targetFor(query);

  // Metadata first (cheap); reuse cache when possible.
  let id = '';
  let title = String(query).trim();
  try {
    const meta = await run(bin, ['--no-playlist', '--print', '%(id)s|||%(title)s', target], { timeoutMs: 60_000 });
    const line = meta.out.trim().split(/\r?\n/).filter(Boolean).at(-1) || '';
    const [mid, mtitle] = line.split('|||');
    if (mid) id = mid.trim();
    if (mtitle) title = mtitle.trim();
  } catch (err) {
    // continue — download may still work
    if (!YT_URL.test(query)) throw err;
  }

  const existing = cached(id);
  if (existing) return { file: existing, title, id };

  const outTmpl = path.join(YT_DIR, '%(id)s.%(ext)s');
  await run(bin, [
    '--no-playlist',
    '-f', 'bestaudio/best',
    '-x',
    '--audio-format', 'mp3',
    '--audio-quality', '5',
    '-o', outTmpl,
    '--print', 'after_move:filepath',
    target,
  ]);

  const file = cached(id) || (() => {
    // Fallback: newest file in yt/
    const files = readdirSync(YT_DIR).filter((f) => /\.(mp3|m4a|opus|webm|ogg)$/i.test(f));
    if (!files.length) throw new Error('yt-dlp finished but no audio file appeared');
    files.sort();
    return path.join('yt', files.at(-1)).replace(/\\/g, '/');
  })();

  return { file, title, id };
}

export function isYoutubeRequest(text) {
  const t = String(text || '').toLowerCase();
  return YT_URL.test(t) || /\b(youtube|yt\b|play .+ on youtube|from youtube)\b/.test(t);
}
