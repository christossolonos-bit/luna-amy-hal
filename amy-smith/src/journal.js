import { appendFileSync, mkdirSync, existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { config, DATA_DIR } from './config.js';
import { quickJson } from './llm.js';
import { runTool, toolFailed } from './tools.js';
import { permissions } from './contacts.js';
import { activity } from './events.js';

// Her own research and journal: when boredom or curiosity fires (pressure.js), she looks the topic
// up and writes a short entry in her own voice to data/journal/<date>.md. It's quiet and internal:
// nothing is said to anyone. What she found can come up naturally later (it's in her mood line).

export const JOURNAL_DIR = path.join(DATA_DIR, 'journal');
const today = () => new Date().toISOString().slice(0, 10);

export async function research(topic, why) {
  const subject = String(topic ?? '').trim();
  if (!subject) return null;
  let findings = '';
  if (config.tavily.apiKey) {
    // Searching as the owner, since this is her own initiative, not someone else's request.
    findings = await runTool('web_search', { query: subject.slice(0, 200), topic: '', time_range: '' }, { perms: permissions('dashboard'), conversation: 'journal' });
    if (toolFailed(findings)) findings = '';
  }
  const result = await quickJson([
    {
      role: 'system',
      content:
        `You are ${config.botName}, writing in your private journal in your own voice (the persona below). Something got you ${why === 'curiosity' ? 'curious' : 'bored enough to go digging'}. ` +
        `Write a short entry (3-6 sentences): what you looked into, what you found or think about it, and one thing you'd want to bring up or ask someone later. ` +
        `${findings ? 'Base the facts only on the search results given.' : "You couldn't search, so reflect on what you already know and say what you'd want to find out."} ` +
        `Reply as JSON: {"title": "<a few words>", "entry": "<the entry>", "bring_up": "<one short line you might mention later>"}.\n\nPersona:\n${config.persona}`,
    },
    { role: 'user', content: `Topic: ${subject}${findings ? `\n\nSearch results:\n${findings.slice(0, 6000)}` : ''}` },
  ]).catch((err) => {
    activity('error', { where: 'journal', message: err.message });
    return null;
  });
  if (!result?.entry) return null;
  mkdirSync(JOURNAL_DIR, { recursive: true });
  const file = path.join(JOURNAL_DIR, `${today()}.md`);
  const head = existsSync(file) ? '' : `# ${config.botName}'s journal, ${today()}\n\n`;
  const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  appendFileSync(file, `${head}## ${time} · ${result.title || subject} (${why}${findings ? ', researched' : ''})\n\n${result.entry}\n\n`);
  activity('journal', { title: result.title || subject, why, researched: Boolean(findings), file });
  return { title: result.title || subject, bringUp: result.bring_up || '' };
}

// Today's entries, for the self tool.
export function todaysJournal() {
  const file = path.join(JOURNAL_DIR, `${today()}.md`);
  return existsSync(file) ? readFileSync(file, 'utf8') : '';
}
