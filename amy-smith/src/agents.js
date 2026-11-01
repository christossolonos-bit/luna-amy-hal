import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { config, DATA_DIR } from './config.js';
import { agentStep } from './llm.js';
import { activity } from './events.js';

// Background helpers: Amy hands a task (write a script, research something, build a project) to a
// helper that works through it on its own with file access and web search, while she keeps talking.
// When it finishes it reports back where the task was started, and its report is kept in data/tasks.

const MAX_STEPS = 40;
const MAX_RESULT_CHARS = 20_000;
const tasks = new Map(); // id -> task
let nextId = 1;

// run(name, args) runs one of her tools; tools are their definitions; notify(text) reports back.
export function startTask({ title, instructions, model, tools, run, notify }) {
  const id = nextId++;
  const task = { id, title: String(title || 'Task').slice(0, 80), status: 'running', model: model || config.openrouter.model, steps: 0, actions: [], started: Date.now(), result: '', cancel: false };
  tasks.set(id, task);
  activity('task', { id, title: task.title, status: 'started', model: task.model });

  (async () => {
    const messages = [
      {
        role: 'system',
        content:
          `You are a background helper for ${config.botName}, working on a task for her owner. Work through it on your own with your tools, step by step: ` +
          `look before you change things, make precise edits rather than rewriting whole files, and check your work by reading it back. ` +
          `Paths can start with Desktop, Documents, Downloads, Pictures, Music or Videos. You can't delete files or run commands; if the task needs that, say so in your report. ` +
          `When you're done, reply with a short report: what you did, where things are, and anything left for the owner to do.`,
      },
      { role: 'user', content: String(instructions) },
    ];
    for (; task.steps < MAX_STEPS && !task.cancel; task.steps++) {
      const step = await agentStep(messages, tools, task.model);
      if (!step.tool_calls.length) {
        task.result = step.content.trim() || '(The helper finished without a report.)';
        break;
      }
      messages.push({ role: 'assistant', content: step.content, tool_calls: step.tool_calls });
      for (const call of step.tool_calls) {
        if (task.cancel) break;
        const out = String(await run(call.function.name, call.function.arguments));
        task.actions.push(`${call.function.name} ${String(call.function.arguments).slice(0, 160)}`);
        messages.push({ role: 'tool', tool_call_id: call.id, content: out.length > MAX_RESULT_CHARS ? `${out.slice(0, MAX_RESULT_CHARS)}\n(cut)` : out });
      }
    }
    if (task.cancel) task.status = 'cancelled';
    else if (!task.result) {
      task.status = 'stopped';
      task.result = `Stopped after ${MAX_STEPS} steps without finishing. Last actions: ${task.actions.slice(-5).join('; ')}`;
    } else task.status = 'done';
  })()
    .catch((err) => {
      task.status = 'failed';
      task.result = err.message;
    })
    .finally(() => {
      task.finished = Date.now();
      save(task);
      activity('task', { id, title: task.title, status: task.status, steps: task.steps });
      if (task.status !== 'cancelled') notify?.(`Background task #${id} "${task.title}" ${task.status}:\n${task.result}`);
    });
  return task;
}

function save(task) {
  const dir = path.join(DATA_DIR, 'tasks');
  mkdirSync(dir, { recursive: true });
  const when = new Date(task.started).toISOString().slice(0, 16).replace(/[:T]/g, '-');
  writeFileSync(
    path.join(dir, `${when}-task-${task.id}.md`),
    `# Task ${task.id}: ${task.title}\n\nStatus: ${task.status}, ${task.steps} steps, model ${task.model}\n\n## Report\n\n${task.result}\n\n## Actions\n\n${task.actions.map((a) => `- ${a}`).join('\n')}\n`,
  );
}

export function listTasks() {
  return [...tasks.values()].map((t) => ({
    id: t.id,
    title: t.title,
    status: t.status,
    steps: t.steps,
    minutes: Math.round(((t.finished ?? Date.now()) - t.started) / 60000),
    lastAction: t.actions.at(-1) ?? null,
    result: t.status === 'running' ? null : t.result.slice(0, 1500),
  }));
}

export function cancelTask(id) {
  const t = tasks.get(Number(id));
  if (!t || t.status !== 'running') return false;
  t.cancel = true;
  return true;
}
