import { readdirSync, readFileSync, writeFileSync, mkdirSync, statSync, existsSync, renameSync, copyFileSync, cpSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DATA_DIR } from './config.js';

// The owner's files, for working on projects together: browse, read, create, edit precisely, move,
// copy. Deliberately missing: deleting anything and running commands. Everything lives under the
// home folder; secrets (keys, .env files, browser and app data) are never readable or writable.
// Before a file is changed its old version is saved to data/file-history, so any edit can be undone.

const HOME = os.homedir();
const ONEDRIVE = process.env.OneDrive && existsSync(process.env.OneDrive) ? process.env.OneDrive : null;
// "Desktop" etc. resolve to where Windows really keeps them (on this PC, inside OneDrive).
const pick = (...dirs) => dirs.find((d) => d && existsSync(d)) ?? dirs[dirs.length - 1];
const ALIASES = {
  desktop: pick(ONEDRIVE && path.join(ONEDRIVE, 'Desktop'), path.join(HOME, 'Desktop')),
  documents: pick(ONEDRIVE && path.join(ONEDRIVE, 'Documents'), path.join(HOME, 'Documents')),
  pictures: pick(ONEDRIVE && path.join(ONEDRIVE, 'Pictures'), path.join(HOME, 'Pictures')),
  downloads: path.join(HOME, 'Downloads'),
  music: path.join(HOME, 'Music'),
  videos: path.join(HOME, 'Videos'),
  home: HOME,
  '~': HOME,
};

const BLOCKED_DIRS = /[\\/](AppData|\.ssh|\.gnupg|\.aws|\.azure|\.kube|\.docker|\.git)([\\/]|$)/i;
const BLOCKED_FILES = /(^|[\\/])(\.env(\.[\w.-]+)?|id_[a-z0-9]+|[^\\/]*\.(pem|key|pfx|p12|kdbx)|credentials[^\\/]*|.*\.db(-wal|-shm)?)$/i;
const HISTORY = path.join(DATA_DIR, 'file-history');
const MAX_READ = 120_000;

// Turns "Desktop/my-app/index.js", "~/x" or an absolute path into a real path, refusing anything
// outside the home folder or on the blocklist.
export function resolve(p) {
  let raw = String(p ?? '').trim().replace(/^["']|["']$/g, '');
  if (!raw) throw new Error('No path given');
  const [first, ...rest] = raw.split(/[\\/]/);
  const alias = ALIASES[first.toLowerCase()];
  const full = path.resolve(alias ? path.join(alias, ...rest) : path.isAbsolute(raw) ? raw : path.join(HOME, raw));
  const rel = path.relative(HOME, full);
  if (rel.startsWith('..') || path.isAbsolute(rel)) throw new Error(`${full} is outside your home folder, so it's off limits`);
  if (BLOCKED_DIRS.test(full) || BLOCKED_FILES.test(full)) throw new Error(`${full} is private (keys, passwords or app data), so it's off limits`);
  return full;
}

// Friendly path for messages: "Desktop\my-app\index.js" instead of the full OneDrive path.
export function show(full) {
  for (const [name, dir] of Object.entries(ALIASES)) {
    if (name === '~' || name === 'home') continue;
    if (full === dir || full.startsWith(dir + path.sep)) return path.join(name[0].toUpperCase() + name.slice(1), path.relative(dir, full));
  }
  return full;
}

const size = (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);
const isText = (buf) => !buf.subarray(0, 8000).includes(0);

export function list(p = 'Desktop', depth = 1) {
  const dir = resolve(p);
  if (!statSync(dir).isDirectory()) throw new Error(`${show(dir)} is a file, not a folder`);
  const lines = [];
  const walk = (d, indent, level) => {
    let entries = readdirSync(d, { withFileTypes: true }).filter((e) => !BLOCKED_DIRS.test(path.join(d, e.name) + path.sep) && !BLOCKED_FILES.test(path.join(d, e.name)));
    entries.sort((a, b) => Number(b.isDirectory()) - Number(a.isDirectory()) || a.name.localeCompare(b.name));
    for (const e of entries) {
      if (lines.length >= 300) return;
      const full = path.join(d, e.name);
      if (e.isDirectory()) {
        lines.push(`${indent}${e.name}/`);
        if (level < depth && e.name !== 'node_modules') walk(full, indent + '  ', level + 1);
      } else {
        let s = '';
        try { s = size(statSync(full).size); } catch {}
        lines.push(`${indent}${e.name}  (${s})`);
      }
    }
  };
  walk(dir, '', 1);
  return `${show(dir)}:\n${lines.join('\n') || '(empty)'}${lines.length >= 300 ? '\n(list cut at 300 entries)' : ''}`;
}

export function read(p, startLine, endLine) {
  const file = resolve(p);
  const buf = readFileSync(file);
  if (!isText(buf)) return `${show(file)} is a binary file (${size(buf.length)}); it can't be shown as text.`;
  const all = buf.toString('utf8').split(/\r?\n/);
  const from = Math.max(1, Number(startLine) || 1), to = Math.min(all.length, Number(endLine) || all.length);
  let out = '', n = from;
  for (; n <= to && out.length < MAX_READ; n++) out += `${String(n).padStart(4)}| ${all[n - 1]}\n`;
  // Size up front so a big read can be planned; a cut-short read says so loudly, with where to go on.
  const header = `${show(file)} (${all.length} lines, ${size(buf.length)})`;
  if (n <= to) return `${header} TRUNCATED: showing lines ${from}-${n - 1}. Read the rest with start_line ${n}.\n${out}`;
  return `${header}, lines ${from}-${to}${to === all.length && from === 1 ? ' (whole file)' : ''}:\n${out}`;
}

const historyKey = (file) => path.relative(HOME, file).replace(/[\\/:]/g, '__');

// Keeps the version about to be replaced, so undo can bring it back.
function snapshot(file) {
  if (!existsSync(file)) return;
  mkdirSync(HISTORY, { recursive: true });
  copyFileSync(file, path.join(HISTORY, `${Date.now()}@@${historyKey(file)}`));
}

export function write(p, content, overwrite = false) {
  const file = resolve(p);
  const exists = existsSync(file);
  if (exists && !overwrite) throw new Error(`${show(file)} already exists. Use edit to change part of it, or overwrite to replace it all.`);
  if (exists && statSync(file).isDirectory()) throw new Error(`${show(file)} is a folder`);
  snapshot(file);
  mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(file, String(content ?? ''));
  return `${exists ? 'Replaced' : 'Created'} ${show(file)} (${String(content ?? '').split('\n').length} lines).`;
}

// A precise edit: swap one exact piece of text for another. It must match exactly once (unless
// all is set), so an edit never lands somewhere unexpected.
export function edit(p, oldText, newText, all = false) {
  const file = resolve(p);
  const text = readFileSync(file, 'utf8');
  if (typeof oldText !== 'string' || !oldText) throw new Error('Give the exact text to replace (old_text)');
  // Files with Windows line endings: match edits written with plain \n too.
  const crlf = text.includes('\r\n');
  const find = crlf && !oldText.includes('\r\n') ? oldText.replace(/\n/g, '\r\n') : oldText;
  const put = crlf && !String(newText).includes('\r\n') ? String(newText ?? '').replace(/\n/g, '\r\n') : String(newText ?? '');
  const count = text.split(find).length - 1;
  if (!count) throw new Error(`That text isn't in ${show(file)}. Read the file again and copy the exact lines, including spaces.`);
  if (count > 1 && !all) throw new Error(`That text appears ${count} times in ${show(file)}. Include more surrounding lines so it matches once, or set replace_all.`);
  snapshot(file);
  const after = all ? text.split(find).join(put) : text.replace(find, () => put);
  writeFileSync(file, after);
  // Show the edited region as it is now (with a little context), so it's clear the edit landed
  // where it was meant to, without a separate read.
  const line = text.slice(0, text.indexOf(find)).split('\n').length;
  const removed = find.split('\n').length, added = put.split('\n').length;
  const lines = after.split(/\r?\n/);
  const from = Math.max(1, line - 2), to = Math.min(lines.length, line + added + 1);
  const region = lines.slice(from - 1, to).map((l, i) => `${String(from + i).padStart(4)}| ${l}`).join('\n');
  return `Edited ${show(file)}: replaced ${all ? count : 1} spot${all && count > 1 ? 's' : ''} (-${removed} line${removed === 1 ? '' : 's'}, +${added}), starting at line ${line}${all && count > 1 ? ' (first one shown)' : ''}. Now:\n${region}`;
}

// Puts back the version from before the last change made here.
export function undo(p) {
  const file = resolve(p);
  const key = historyKey(file);
  const versions = existsSync(HISTORY) ? readdirSync(HISTORY).filter((n) => n.split('@@')[1] === key).sort() : [];
  if (!versions.length) throw new Error(`No earlier version of ${show(file)} to go back to.`);
  const last = path.join(HISTORY, versions[versions.length - 1]);
  writeFileSync(file, readFileSync(last));
  renameSync(last, last + '.restored'); // so a second undo goes one step further back
  return `Restored ${show(file)} to how it was before the last change.`;
}

export function makeFolder(p) {
  const dir = resolve(p);
  if (existsSync(dir)) return `${show(dir)} already exists.`;
  mkdirSync(dir, { recursive: true });
  return `Created folder ${show(dir)}.`;
}

export function move(from, to) {
  const src = resolve(from), dest = resolve(to);
  if (!existsSync(src)) throw new Error(`${show(src)} doesn't exist`);
  const target = existsSync(dest) && statSync(dest).isDirectory() ? path.join(dest, path.basename(src)) : dest;
  if (existsSync(target)) throw new Error(`${show(target)} already exists; nothing was moved.`);
  mkdirSync(path.dirname(target), { recursive: true });
  renameSync(src, target);
  return `Moved ${show(src)} to ${show(target)}.`;
}

export function copy(from, to) {
  const src = resolve(from), dest = resolve(to);
  if (!existsSync(src)) throw new Error(`${show(src)} doesn't exist`);
  const target = existsSync(dest) && statSync(dest).isDirectory() ? path.join(dest, path.basename(src)) : dest;
  if (existsSync(target)) throw new Error(`${show(target)} already exists; nothing was copied.`);
  mkdirSync(path.dirname(target), { recursive: true });
  cpSync(src, target, { recursive: true, errorOnExist: true, force: false });
  return `Copied ${show(src)} to ${show(target)}.`;
}

// Files whose name contains `name`, under a folder (a few levels deep).
export function find(name, under = 'home') {
  const root = resolve(under);
  const needle = String(name ?? '').toLowerCase();
  if (!needle) throw new Error('What should the name contain?');
  const hits = [];
  const walk = (d, level) => {
    if (hits.length >= 50 || level > 6) return;
    let entries;
    try { entries = readdirSync(d, { withFileTypes: true }); } catch { return; }
    for (const e of entries) {
      const full = path.join(d, e.name);
      if (BLOCKED_DIRS.test(full + path.sep) || BLOCKED_FILES.test(full) || e.name === 'node_modules' || e.name.startsWith('.')) continue;
      if (e.name.toLowerCase().includes(needle)) hits.push(show(full) + (e.isDirectory() ? '/' : ''));
      if (e.isDirectory()) walk(full, level + 1);
    }
  };
  walk(root, 0);
  return hits.length ? `Found:\n${hits.join('\n')}${hits.length >= 50 ? '\n(stopped at 50)' : ''}` : `Nothing named like "${name}" under ${show(root)}.`;
}

export function fileBytes(p) {
  const file = resolve(p);
  if (statSync(file).isDirectory()) throw new Error(`${show(file)} is a folder; zip it with send_project or send the files one by one`);
  return { name: path.basename(file), bytes: readFileSync(file) };
}
