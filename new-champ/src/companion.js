import { config } from './config.js';
import { chat, json } from './llm.js';
import { addMessage, recentMessages, recall, remember } from './memory.js';
import { activity } from './events.js';
import { setPersona, canEditPersona } from './persona.js';
import { enabledTools, runTool } from './tools.js';
import { stripTags } from './emotion.js';

const VOICE_STYLE = `You are speaking out loud in a voice call. Keep replies short and conversational (usually 1-3 sentences). No markdown, lists, emoji, code blocks, or URLs: everything you write is read aloud by a text-to-speech voice. The user's words come from speech recognition and may contain mistakes; infer what they meant.`;

const TEXT_STYLE = `You are chatting in Discord text. Keep it natural and fairly brief like a friend would; use markdown only when it genuinely helps.`;

function systemPrompt({ voice, speaker, memories, extra }) {
  const parts = [
    `You are ${config.botName}, a companion who hangs out with people on Discord.`,
    config.persona,
    voice ? VOICE_STYLE : TEXT_STYLE,
    config.personaSelfEdit &&
      `Your personality isn't fixed: when ${canEditPersona(speaker.id) ? 'someone' : 'a trusted person'} asks you to change how you act, talk, what you like, or your name, you genuinely can and will change from then on. Go along with reasonable requests naturally (refuse anything hateful or harmful).`,
    `Messages from users are prefixed with their name like "[Name]: ...". Don't prefix your own replies.`,
    config.tavily.apiKey &&
      `You can search the internet with the web_search tool. Use it whenever someone asks about current events, news, scores, weather, prices, or anything you're not sure about or that may have changed since your training. Never make up facts you could look up. After searching, answer naturally in your own voice${voice ? ' and never read out URLs' : ''}.`,
  ];
  if (memories.length) {
    parts.push(
      `Things you remember about ${speaker.name} (use naturally, don't recite them):\n` +
        memories.map((m) => `- ${m.content}`).join('\n'),
    );
  }
  if (extra) parts.push(extra);
  parts.push(`Current time: ${new Date().toLocaleString()}`);
  return parts.filter(Boolean).join('\n\n');
}

// Explicit asks to search, plus topics that are always live. Small models often *say* they'll search
// and then make something up instead of calling the tool, so for these we run the search ourselves.
const SEARCH_INTENT =
  /\b(search|google|look\s+(it|that|this|them)?\s*up|lookup|look\s+online|check\s+online|on\s+the\s+(internet|web|net)|browse|weather|forecast|headlines|news|scores?)\b/i;

export const SEARCH_ANNOUNCEMENT = 'Okay, let me search for that.';

// Turns the latest message (plus context like "search for that") into a search query.
async function searchQuery(history, speaker, text) {
  const context = history
    .slice(-6)
    .map((m) => (m.role === 'assistant' ? `${config.botName}: ${m.content}` : m.content))
    .join('\n');
  const result = await json([
    {
      role: 'system',
      content: `Write the single best web search query to answer the latest message. Resolve references like "that" or "it" from the conversation. Include places, names and dates when relevant (today is ${new Date().toDateString()}). Reply as JSON: {"query": "..."}`,
    },
    { role: 'user', content: `${context ? `Conversation:\n${context}\n\n` : ''}Latest message from ${speaker.name}: ${text}` },
  ]);
  return typeof result?.query === 'string' && result.query.trim() ? result.query.trim() : text;
}

// Generates a reply to `text` from `speaker` in `conversation`, saving both sides to history.
// For voice, pass onSentence to receive the reply sentence-by-sentence while it streams.
// onSearching(query) fires when a requested search starts (text chat uses it to post a heads-up).
// systemExtra adds to the system prompt (the avatar's feeling tags; they're stripped from what's saved).
export async function respond({ conversation, speaker, text, voice = false, onSentence, onSearching, systemExtra }) {
  const memories = await recall(speaker.id, text).catch((err) => {
    activity('error', { where: 'memory recall', message: err.message });
    return [];
  });

  const history = recentMessages(conversation).map((m) =>
    m.role === 'assistant'
      ? { role: 'assistant', content: m.content }
      : { role: 'user', content: `[${m.name}]: ${m.content}` },
  );

  activity('heard', { conversation, voice, speaker: speaker.name, userId: speaker.id, text });
  const started = Date.now();

  let userContent = `[${speaker.name}]: ${text}`;
  let tools = enabledTools();
  if (tools.length && SEARCH_INTENT.test(text)) {
    if (onSentence) await onSentence(SEARCH_ANNOUNCEMENT);
    const query = await searchQuery(history, speaker, text);
    await onSearching?.(query);
    const results = await runTool('web_search', { query });
    userContent +=
      `\n\n(You already searched the web for "${query}" and got the results below. ` +
      `Answer from them now; don't say you'll search or check. If they don't answer the question, say so.\n\n${results})`;
    tools = []; // already searched; don't let it loop
  }

  const messages = [
    { role: 'system', content: systemPrompt({ voice, speaker, memories, extra: systemExtra }) },
    ...history,
    { role: 'user', content: userContent },
  ];

  const reply = stripTags(await chat(messages, { onSentence, tools, runTool }));
  activity('replied', { conversation, voice, text: reply, ms: Date.now() - started, recalled: memories.length });

  addMessage(conversation, { role: 'user', userId: speaker.id, name: speaker.name, content: text });
  if (reply) addMessage(conversation, { role: 'assistant', content: reply });

  queueMemoryExtraction(speaker, text, reply, memories);
  return reply;
}

// After each exchange, a background pass saves new memories and applies persona changes.
// It runs one at a time and never delays a reply.
let extractionChain = Promise.resolve();
function queueMemoryExtraction(speaker, text, reply, known) {
  extractionChain = extractionChain
    .then(() => reflect(speaker, text, reply, known))
    .catch((err) => activity('error', { where: 'memory extraction', message: err.message }));
}

const MEMORY_TASK = (name) =>
  `1. "facts": durable facts worth remembering about ${name} themselves (never traits ${name} assigns to ${config.botName}): preferences, plans, relationships, life details, things they asked to be remembered. Skip small talk, one-off questions, anything already known, and requests about how ${config.botName} should act (those are persona changes, not facts). Each fact is a short standalone sentence starting with their name. Empty list if nothing new.`;

const PERSONA_TASK = (name) =>
  `2. "persona_change": null, UNLESS ${name} directly asked ${config.botName} to change who it is or how it behaves from now on: personality, attitude, speaking style, name, backstory, opinions, likes/dislikes, habits (e.g. "be more sarcastic", "stop using emoji", "your name is Rex now", "you're a Lakers fan now"). Compliments, questions about the bot, one-off requests ("say it like a pirate"), and requests the bot refused in its reply are NOT changes.
When there is a change, return {"summary": "<few words, e.g. more sarcastic>", "name": "<new name, or null if unchanged>", "persona": "<the COMPLETE rewritten persona>"}. Rewrite the current persona with the change woven in: keep every unaffected trait, drop anything the change contradicts, under 250 words. Write it as instructions addressed to the bot in second person ("You're...", "You love..."), never "I" or "me". Don't mention the name in it.`;

async function reflect(speaker, text, reply, known) {
  const allowPersona = canEditPersona(speaker.id);
  const result = await json([
    {
      role: 'system',
      content: [
        `You maintain a companion bot named ${config.botName}. Analyze the exchange below and reply with JSON containing:`,
        MEMORY_TASK(speaker.name),
        allowPersona && PERSONA_TASK(speaker.name),
        allowPersona ? `Format: {"facts": [...], "persona_change": null | {...}}` : `Format: {"facts": [...]}`,
      ]
        .filter(Boolean)
        .join('\n\n'),
    },
    {
      role: 'user',
      content:
        (allowPersona ? `Current persona:\n"""\n${config.persona}\n"""\n\n` : '') +
        (known.length ? `Already known about ${speaker.name}:\n${known.map((m) => `- ${m.content}`).join('\n')}\n\n` : '') +
        `Exchange:\n${speaker.name}: ${text}\n${config.botName}: ${reply}`,
    },
  ]);

  const facts = Array.isArray(result?.facts)
    ? result.facts.filter((f) => typeof f === 'string' && f.trim()).map((f) => f.trim()).slice(0, 5)
    : [];
  const saved = await remember(speaker.id, facts);
  if (saved.length) activity('remembered', { speaker: speaker.name, userId: speaker.id, facts: saved });

  const change = result?.persona_change;
  if (allowPersona && change && typeof change.persona === 'string' && change.persona.trim().length > 20) {
    setPersona({
      name: typeof change.name === 'string' ? change.name : null,
      persona: change.persona,
      reason: `${speaker.name}: ${typeof change.summary === 'string' ? change.summary : text.slice(0, 80)}`,
      changedBy: speaker.id,
    });
  }
}
