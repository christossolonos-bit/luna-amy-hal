import { execFile } from 'node:child_process';
import path from 'node:path';
import { resolve, show } from './files.js';

// Git for the owner's projects, without a shell: git is run directly with a fixed set of
// subcommands and arguments built here, never text passed through from the model. Only status,
// diff, add, commit and push exist. Push is two-step: preparePush() shows what would go up, and
// push() runs only after the owner confirms (see tools.js).

const run = (cwd, args, timeout = 30_000) =>
  new Promise((ok, fail) => {
    execFile('git', args, { cwd, timeout, windowsHide: true, maxBuffer: 4 * 1024 * 1024, env: { ...process.env, GIT_TERMINAL_PROMPT: '0' } }, (err, stdout, stderr) => {
      if (err) fail(new Error((stderr || err.message).trim().split('\n').slice(0, 6).join('\n')));
      else ok(stdout.trimEnd());
    });
  });

// The repository a path belongs to (any folder or file inside it).
export async function repoOf(p) {
  const dir = resolve(p);
  const top = await run(dir, ['rev-parse', '--show-toplevel']).catch(() => run(path.dirname(dir), ['rev-parse', '--show-toplevel'])).catch(() => {
    throw new Error(`${show(dir)} isn't inside a git repository`);
  });
  return resolve(path.normalize(top)); // the repo itself must be an allowed place too
}

// A path given relative to the repo (or as a full/Desktop path inside it), checked to stay inside.
function inRepo(repo, p) {
  const raw = String(p ?? '').trim();
  if (!raw || raw.startsWith('-')) throw new Error(`"${raw}" isn't a file path`);
  let full;
  try {
    full = resolve(raw);
    if (!full.startsWith(repo)) full = path.resolve(repo, raw);
  } catch {
    full = path.resolve(repo, raw);
  }
  const rel = path.relative(repo, full);
  if (rel.startsWith('..') || path.isAbsolute(rel)) throw new Error(`${raw} is outside the repository`);
  resolve(full); // same blocklist as the files tool
  return rel || '.';
}

export async function status(repo) {
  const branch = await run(repo, ['status', '--short', '--branch']);
  return `${show(repo)}:\n${branch || '(clean)'}`;
}

export async function diff(repo, { file, staged } = {}) {
  const args = ['diff', '--stat', '--patch', '--no-color', ...(staged ? ['--cached'] : [])];
  if (file) args.push('--', inRepo(repo, file));
  const out = await run(repo, args);
  if (!out) return staged ? 'Nothing staged.' : 'No unstaged changes.';
  return out.length > 30_000 ? `${out.slice(0, 30_000)}\n(diff cut at 30,000 characters; diff one file at a time for the rest)` : out;
}

export async function add(repo, files) {
  const list = Array.isArray(files) ? files : [files];
  const args = list.length === 1 && /^(all|\.|-A|everything)$/i.test(String(list[0]).trim()) ? ['add', '--all'] : ['add', '--', ...list.map((f) => inRepo(repo, f))];
  await run(repo, args);
  return `Staged. Now:\n${await run(repo, ['status', '--short'])}`;
}

export async function commit(repo, message) {
  const msg = String(message ?? '').trim();
  if (!msg) throw new Error('A commit needs a message');
  const staged = await run(repo, ['diff', '--cached', '--name-only']);
  if (!staged) throw new Error('Nothing is staged; add files first');
  await run(repo, ['commit', '-m', msg]);
  return `Committed: ${await run(repo, ['log', '-1', '--format=%h %s'])}`;
}

// What a push would send, without sending it.
export async function preparePush(repo) {
  const branch = await run(repo, ['branch', '--show-current']);
  if (!branch) throw new Error('Not on a branch (detached HEAD), so there is nothing to push');
  const upstream = await run(repo, ['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}']).catch(() => null);
  const remote = upstream ? upstream.split('/')[0] : 'origin';
  const url = await run(repo, ['remote', 'get-url', remote]).catch(() => null);
  if (!url) throw new Error(`There's no "${remote}" remote to push to`);
  const commits = upstream ? await run(repo, ['log', '--format=%h %s', `${upstream}..HEAD`]) : await run(repo, ['log', '--format=%h %s', '-10']);
  if (upstream && !commits) throw new Error(`Nothing to push: ${branch} is up to date with ${upstream}`);
  return { repo, branch, remote, url, upstream, commits };
}

export async function push(plan) {
  const args = plan.upstream ? ['push', plan.remote, plan.branch] : ['push', '--set-upstream', plan.remote, plan.branch];
  await run(plan.repo, args, 120_000);
  return `Pushed ${plan.branch} to ${plan.remote} (${plan.url}).`;
}
