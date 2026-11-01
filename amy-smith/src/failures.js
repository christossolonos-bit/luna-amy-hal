import { appendFileSync, readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { DATA_DIR, ROOT } from './config.js';
import { activity } from './events.js';
import { bruise } from './continuity.js';

// The failure log (Amy's spec: Downloads/amy-tool-gating-spec.md). Every tool error, cut-short read,
// mid-turn tool request and phantom success (a claimed action with no successful tool behind it)
// is one JSON line in data/tool-failures.jsonl. knowledge/tool-failures.md is the rollup, grouped by
// tool and code and sorted by count: the list to triage and fix.

const LOG = path.join(DATA_DIR, 'tool-failures.jsonl');
const ROLLUP = path.join(ROOT, 'knowledge', 'tool-failures.md');

// kind: hard_fail | truncated | phantom_success | tool_request
export function logFailure({ conversation, kind, tool, code, args, note }) {
  const entry = { ts: new Date().toISOString(), conversation, kind, tool, error_code: code, args_summary: args ? String(args).slice(0, 200) : undefined, note: note ? String(note).slice(0, 300) : undefined };
  try {
    appendFileSync(LOG, JSON.stringify(entry) + '\n');
    writeRollup();
  } catch (err) {
    activity('error', { where: 'failure log', message: err.message });
  }
  activity('failure', entry);
  // Real mistakes leave a mark she sees in her next replies (continuity.js); repeats deepen it.
  if (kind === 'hard_fail' || kind === 'phantom_success') {
    bruise({
      kind,
      key: `${tool}|${code}`,
      context: note || args || '',
      lesson:
        kind === 'phantom_success'
          ? `You said a ${tool} action was done when no tool result backed it. Only say it's done after an ok result`
          : `${tool} failed with ${code}. ${LESSONS[code] ?? 'Check the guide for it before calling again'}`,
    });
  }
}

const LESSONS = {
  missing_field: 'Fill in every field before calling (unused ones as "", 0, false or [])',
  not_found: 'Check the name or path first (list, find, or the suggestion in the error)',
  not_available: "Ask for the tool with request_tool instead of describing a call you can't make",
  not_allowed: "That person's access doesn't allow it; say so plainly instead of trying",
  forbidden: "That's never allowed; don't try it again",
  conflict: 'Make the call more specific or do the missing step first',
  unavailable: "An outside service was down or rate-limited; don't loop, say so",
  too_large: 'Keep it smaller',
};

export function readFailures() {
  if (!existsSync(LOG)) return [];
  return readFileSync(LOG, 'utf8').split('\n').filter(Boolean).map((l) => {
    try {
      return JSON.parse(l);
    } catch {
      return null;
    }
  }).filter(Boolean);
}

function writeRollup() {
  const groups = new Map();
  for (const e of readFailures()) {
    const key = `${e.kind}|${e.tool ?? '-'}|${e.error_code ?? '-'}`;
    const g = groups.get(key) ?? { kind: e.kind, tool: e.tool ?? '-', code: e.error_code ?? '-', count: 0, first: e.ts, last: e.ts, example: '' };
    g.count++;
    g.last = e.ts;
    g.example = e.note || e.args_summary || g.example;
    groups.set(key, g);
  }
  const rows = [...groups.values()].sort((a, b) => b.count - a.count);
  const day = (ts) => ts.slice(0, 16).replace('T', ' ');
  const requests = rows.filter((r) => r.kind === 'tool_request').reduce((n, r) => n + r.count, 0);
  mkdirSync(path.dirname(ROLLUP), { recursive: true });
  writeFileSync(
    ROLLUP,
    `# Tool failures (rollup)\n\nGenerated from data/tool-failures.jsonl; most frequent first. Each row needs an owner or a fix.\n` +
      `Mid-turn tool requests so far: ${requests} (if this climbs, the planner is missing intents and needs work).\n\n` +
      `| count | kind | tool | code | first seen | last seen | latest example |\n|---|---|---|---|---|---|---|\n` +
      rows.map((r) => `| ${r.count} | ${r.kind} | ${r.tool} | ${r.code} | ${day(r.first)} | ${day(r.last)} | ${String(r.example).replace(/\|/g, '/').replace(/\s+/g, ' ').slice(0, 120)} |`).join('\n') +
      '\n',
  );
}
