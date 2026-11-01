import { config } from './config.js';
import { chat } from './llm.js';
import { unsummarizedBlock, addSummary, openParagraphs, majorSummary, rollUp } from './memory.js';
import { activity } from './events.js';

// Every `historyLimit` messages become one paragraph. Once a day's worth of paragraphs piles up
// (24 of them, or the oldest is 24 hours old) they're folded into the major summary.
const PARAGRAPHS_PER_ROLLUP = 24;
const ROLLUP_AGE_MS = 24 * 60 * 60 * 1000;

const PARAGRAPH_TASK = (botName) =>
  `Summarize this stretch of a Discord conversation involving ${botName} in ONE paragraph (about 80-150 words). Keep who said or did what, topics discussed, decisions, plans, jokes or running bits worth recalling, and how people were feeling. Use people's names. Write in past tense, third person. Reply with the paragraph only.`;

const ROLLUP_TASK = (botName) =>
  `You keep ${botName}'s long-term memory of a Discord conversation. Merge the earlier summary (if any) and the newer paragraphs below into ONE updated summary of about 200-350 words. Keep what matters for future conversations: people, relationships, ongoing plans, recurring topics, important events, in-jokes. Drop trivia. Mention roughly when things happened (e.g. "on Oct 22"). Reply with the summary only.`;

// One consolidation at a time, in the background, never delaying a reply.
let chain = Promise.resolve();
export function queueConsolidation(conversation) {
  chain = chain
    .then(() => consolidate(conversation))
    .catch((err) => activity('error', { where: 'memory consolidation', message: err.message }));
  return chain;
}

async function consolidate(conversation) {
  let block;
  while ((block = unsummarizedBlock(conversation, config.historyLimit))) {
    const paragraph = await summarize(PARAGRAPH_TASK(config.botName), transcript(block));
    if (!paragraph) return; // try again after the next message rather than skipping the block
    addSummary(conversation, { level: 1, content: paragraph, lastMessageId: block.at(-1).id });
    activity('summarized', { conversation, messages: block.length, text: paragraph });
  }

  const paragraphs = openParagraphs(conversation);
  const due =
    paragraphs.length >= PARAGRAPHS_PER_ROLLUP ||
    (paragraphs.length > 1 && Date.now() - paragraphs[0].created_at >= ROLLUP_AGE_MS);
  if (!due) return;

  const previous = majorSummary(conversation);
  const input =
    (previous ? `Earlier summary:\n${previous.content}\n\n` : '') +
    `Newer paragraphs (oldest first):\n` +
    paragraphs.map((p) => `[${stamp(p.created_at)}] ${p.content}`).join('\n\n');
  const summary = await summarize(ROLLUP_TASK(config.botName), input);
  if (!summary) return;
  rollUp(conversation, summary, paragraphs.at(-1).id);
  activity('summarized', { conversation, paragraphs: paragraphs.length, major: true, text: summary });
}

async function summarize(task, input) {
  const text = await chat([
    { role: 'system', content: task },
    { role: 'user', content: input },
  ]);
  return text.trim();
}

function transcript(messages) {
  return messages
    .map((m) => `[${stamp(m.created_at)}] ${m.role === 'assistant' ? m.name ?? config.botName : m.name ?? 'Someone'}: ${m.content}`)
    .join('\n');
}

function stamp(ms) {
  return new Date(ms).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

// The summaries to put in the system prompt: the major summary, then the paragraphs since.
export function summaryContext(conversation) {
  const major = majorSummary(conversation);
  const paragraphs = openParagraphs(conversation);
  if (!major && !paragraphs.length) return '';
  return [
    `What happened earlier in this conversation (your own memory of it; use naturally, don't recite):`,
    major && `Long-term summary: ${major.content}`,
    ...paragraphs.map((p) => `[${stamp(p.created_at)}] ${p.content}`),
  ]
    .filter(Boolean)
    .join('\n\n');
}
