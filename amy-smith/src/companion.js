import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import { config, DATA_DIR } from './config.js';
import { chat, quickJson } from './llm.js';
import { addMessage, promptMessages, recall, noteContact, listContacts, obligationsFor, openObligations, completeObligation } from './memory.js';
import { permissions } from './contacts.js';
import { queueConsolidation, summaryContext } from './summaries.js';
import { capabilitiesBlock } from './capabilities.js';
import { activity } from './events.js';
import { setPersona, canEditPersona } from './persona.js';
import { enabledTools, runTool, toolFailed } from './tools.js';
import { logFailure } from './failures.js';
import { stripTags } from './emotion.js';
import { update as updateCharacter } from './characters.js';
import { MEMORY_TASK, fileFacts } from './extract.js';
import * as scratch from './scratchpad.js';
import * as pressure from './pressure.js';
import * as continuity from './continuity.js';

const VOICE_STYLE = `You are speaking out loud in a voice call. Keep replies short and conversational (usually 1-3 sentences). No markdown, lists, emoji, code blocks, or URLs: everything you write is read aloud by a text-to-speech voice. The user's words come from speech recognition and may contain mistakes; infer what they meant.`;

const TEXT_STYLE = `You are chatting in Discord text. Keep it natural and fairly brief like a friend would; use markdown only when it genuinely helps.`;

// The bot itself, unless a character from the avatar studio's cast is speaking.
const mainCharacter = () => ({ id: 'main', main: true, name: config.botName, persona: config.persona });

function systemPrompt({ voice, speaker, perms, memories, others, earlier, extra, character, tools }) {
  const parts = [
    character.main
      ? `You are ${character.name}, a companion who hangs out with people on Discord.`
      : `You are ${character.name}, a companion who hangs out with people.`,
    character.persona,
    voice ? VOICE_STYLE : TEXT_STYLE,
    `Messages from users are prefixed with their name like "[Name]: ...". Don't prefix your own replies.`,
    capabilitiesBlock({ voice, speaker, perms, character, tools: tools.map((t) => t.function.name) }),
  ];
  if (memories.length) {
    parts.push(
      `Things you remember about ${speaker.name} (use naturally, don't recite them):\n` +
        memories.map((m) => `- ${m.content}`).join('\n'),
    );
  }
  if (others?.length) {
    parts.push(
      `Things you remember about the other people here (each fact is about the person it's listed under; don't mix them up):\n` +
        others.map((o) => `${o.name}:\n${o.memories.map((m) => `- ${m.content}`).join('\n')}`).join('\n'),
    );
  }
  if (earlier) parts.push(earlier);
  if (extra) parts.push(extra);
  parts.push(`Current time: ${new Date().toLocaleString()}`);
  return parts.filter(Boolean).join('\n\n');
}

export const SEARCH_ANNOUNCEMENT = 'Okay, let me search for that.';

// Shot one of every reply: a quick look at the conversation that decides which actions, if any, the
// latest message really asks for. Only those tools are offered to the reply (shot two), so she can't
// fire off files or images nobody asked for. A planned web search is run before the reply.
// Returns { search: query | null, tools: [names], reason }. If planning fails, no actions.
const DELIVERABLES = ['send_project', 'send_file', 'generate_image', 'generate_music'];
const MULTI_STEP = ['files', 'read_source', 'git', 'self'];
// Her own note-keeping: offered every turn (outside the planner), since she decides when to use them.
const ALWAYS = ['note', 'scratchpad', 'thread', 'threads', 'guide'];
export async function planTurn(history, speaker, text, tools) {
  const context = history
    .slice(-8)
    .map((m) => (m.role === 'assistant' ? `${config.botName}: ${m.content}` : m.content))
    .join('\n');
  const menu = tools.map((t) => `- ${t.function.name}: ${t.function.description}`).join('\n');
  const result = await quickJson([
    {
      role: 'system',
      content:
        `You decide whether ${config.botName}'s next reply needs any actions. Available actions:\n${menu}\n\n` +
        `Rules:\n` +
        `- Pick an action only if the latest message clearly asks for it now, or clearly says yes to something ${config.botName} just offered to do.\n` +
        `- Normal chat, opinions, feelings, jokes, and questions ${config.botName} can answer from general knowledge need no actions.\n` +
        `- web_search only for current or changing facts (news, weather, scores, prices, recent events) or when asked to look something up. Give a precise query; resolve "that" or "it" from the conversation. Today is ${new Date().toDateString()}.\n` +
        `- At most one of send_file, send_project, generate_image and generate_music. More than one file means send_project.\n` +
        `- obligations when asked to do or remember to do something later ("when X joins, tell him...", "remind me to... when Y is on", "make sure you..."), to list her to-dos, or to check one off or cancel it.\n` +
        `- files for anything on the owner's computer: looking in folders, reading, creating or editing files and scripts, moving, copying, finding a file, or sending one from the computer. Iterating on a script ("change the timeout to 30", "add error handling") is files.\n` +
        `- checkin_consent when the person answers ${config.botName}'s question about whether she may check in on them on her own (yes/no, how often, what to talk about), changes their mind, or says not to message or check in on them.\n` +
        `- self when asked how she works, why something happened, where her files, settings or memory live, or to check her logs or errors. Explaining her own behaviour or a problem with her usually needs self or read_source.\n` +
        `- avatar_mode when asked to turn avatar mode or video replies on or off, to start or stop sending videos of herself, or whether it's on.\n` +
        `- git when asked about a project's git status or changes, to stage, commit or push, or when the owner confirms a push she just previewed ("yes, push it").\n` +
        `- list_models when asked which models are best for something, or to show or list models. switch_brain when asked to switch, use or pick a model or provider, including picking a number from a list just shown ("go with 2").\n` +
        `- background_task when asked to do something in the background, hand it off, have a helper or sub-agent work on it, or to check on or cancel such a task.\n` +
        `- music for playing songs: local library, playlists, or YouTube (URL or search via the youtube field), plus queue, pause, resume, skip, stop, or what's playing.\n` +
        `- generate_music only when the conversation already says what the song should be about (topic, mood or style) AND how long (a 30-second clip or a full song). If either is missing, pick nothing so ${config.botName} can ask.\n` +
        `- When unsure, pick nothing.\n` +
        `Reply as JSON only: {"actions": [{"tool": "<name>", "query": "<web_search only>"}], "reason": "<a few words>"}. Use {"actions": []} when nothing is needed.`,
    },
    { role: 'user', content: `${context ? `Conversation so far:\n${context}\n\n` : ''}Latest message from ${speaker.name}: ${text}` },
  ]).catch((err) => {
    activity('error', { where: 'planning', message: err.message });
    return null;
  });
  const offered = new Set(tools.map((t) => t.function.name));
  const plan = { search: null, tools: [], reason: typeof result?.reason === 'string' ? result.reason : '' };
  for (const a of Array.isArray(result?.actions) ? result.actions : []) {
    const name = typeof a?.tool === 'string' ? a.tool.trim() : '';
    if (!offered.has(name)) continue;
    if (name === 'web_search') plan.search = typeof a.query === 'string' && a.query.trim() ? a.query.trim() : text;
    else if (!plan.tools.includes(name)) plan.tools.push(name);
  }
  // If the planner skipped an obvious ask (common on weak local models), add it from the text.
  const low = String(text || '').toLowerCase();
  const ensure = (name) => {
    if (offered.has(name) && !plan.tools.includes(name) && (name !== 'web_search' || !plan.search)) {
      if (name === 'web_search') plan.search = text;
      else plan.tools.push(name);
      if (!plan.reason) plan.reason = 'heuristic';
    }
  };
  if (/\b(search|look up|google|internet|web)\b/.test(low)) ensure('web_search');
  if (/\b(desktop|create|make|write|save)\b/.test(low) && /\b(file|document|txt|folder|note)\b/.test(low)) ensure('files');
  if (/\b(read|open|edit|copy|move)\b/.test(low) && /\b(file|folder|script|\.env|code)\b/.test(low)) ensure('files');
  if (/\b(send|dm)\b/.test(low) && /\b(file|document)\b/.test(low)) ensure('send_file');
  if (/\b(youtube|youtu\.be|play (me )?(a |some )?(song|music|track)|put on (some )?music)\b/.test(low)) ensure('music');
  // One deliverable per turn, whatever the planner said.
  const first = plan.tools.find((n) => DELIVERABLES.includes(n));
  plan.tools = plan.tools.filter((n) => !DELIVERABLES.includes(n) || n === first);
  return plan;
}

// Generates a reply to `text` from `speaker` in `conversation`, saving both sides to history.
// For voice, pass onSentence to receive the reply sentence-by-sentence while it streams.
// onSearching(query) fires when a requested search starts (text chat uses it to post a heads-up).
// systemExtra adds to the system prompt (the avatar's feeling tags; they're stripped from what's saved).
// character picks who answers in the avatar studio (default: the bot). In a shared conversation the
// other characters' lines are shown to it as "[Name]: ...", like the people's.
// speaker: { id, name, username?, dm? }. Their contact tier decides what's remembered and which tools are offered.
// discord: { client, guild, sendHere(attachment) } when the chat is on Discord, so tools like send_file
// know where "here" is. sendHere either attaches to the reply or posts straight away; that's up to the caller.
// cue: a stage direction instead of a message from `speaker` (the cast talking among themselves).
// Nothing is said by a person, so only the character's line is saved and nothing is memorised.
// due: obligations to fulfil in this reply (the voice session passes them when someone joins);
// otherwise any waiting on the speaker are picked up here. Either way they're checked off after.
export async function respond({ conversation, speaker, text, voice = false, onSentence, onSearching, systemExtra, character = mainCharacter(), cue, discord = {}, due }) {
  // Continuity: when this person and this conversation were last active (read before updating).
  const speakerLastSeen = listContacts().find((c) => c.user_id === speaker.id)?.last_seen ?? null;
  const convLastActive = continuity.touch(conversation);
  if (!cue && character.main && CORRECTED.test(text)) {
    const last = promptMessages(conversation).filter((m) => m.role === 'assistant').at(-1)?.content;
    if (last) continuity.bruise({ kind: 'corrected', key: `${conversation}|${String(last).slice(0, 40)}`, context: `${speaker.name} said "${text.slice(0, 80)}" after you said "${last.slice(0, 100)}"`, lesson: `${speaker.name} corrected you. Read the actual file, log or source before stating causes, and label claims verified, inferred or guess` });
  }
  if (!cue && speaker.id !== 'dashboard') {
    noteContact({ userId: speaker.id, name: speaker.name, username: speaker.username, dm: speaker.dm });
  }
  const perms = permissions(speaker.id);
  // Introductions are only done out loud, when someone joins voice.
  // Introductions and the check-in question are asked in person, in voice, only.
  due ??= cue || !character.main ? [] : obligationsFor(speaker.id, [speaker.name, speaker.username]).filter((o) => voice || !['intro', 'consent'].includes(o.kind));
  const priorities = perms.owner && character.main ? openObligations().filter((o) => !o.target_id && !o.target_name) : [];
  systemExtra = [
    systemExtra,
    character.main && continuity.continuityBlock({ conversation, speakerName: cue ? null : speaker.name, speakerLastSeen, convLastActive }),
    obligationsPrompt(speaker, due, priorities),
    scratch.promptBlock(conversation),
    character.main && pressure.moodLine(speaker.id),
  ].filter(Boolean).join('\n\n');
  // turn and userText let a tool tell this turn apart from an earlier one, and see what the person
  // actually said (git push only goes ahead on the owner's own "yes" in a later message).
  const toolCtx = { ...discord, perms, conversation, turn: `${conversation}:${Date.now()}`, userText: cue ? '' : text, speaker };

  const recallFailed = (err) => {
    activity('error', { where: 'memory recall', message: err.message });
    return [];
  };
  const memories = cue || !perms.remember ? [] : await recall(speaker.id, text).catch(recallFailed);

  // Everyone else in the conversation (and anyone named in the message) gets their memories
  // pulled too, so in a group she knows about all of them, not just whoever is talking.
  const rows = promptMessages(conversation);
  const people = cue ? [] : peopleIn(rows, speaker, text);
  const others = [];
  for (const p of people) {
    if (!permissions(p.id).remember) continue;
    const found = (await recall(p.id, text).catch(recallFailed)).slice(0, 3);
    if (found.length) others.push({ name: p.name, memories: found });
  }

  // Assistant lines saved before there was a cast have no name; they were the bot's.
  const history = rows.map((m) => {
    const author = m.role === 'assistant' ? m.name ?? config.botName : m.name;
    return m.role === 'assistant' && author === character.name
      ? { role: 'assistant', content: m.content }
      : { role: 'user', content: `[${author}]: ${m.content}` };
  });

  if (!cue) activity('heard', { conversation, voice, speaker: speaker.name, userId: speaker.id, text });
  const started = Date.now();

  let userContent = cue ? `(${cue})` : `[${speaker.name}]: ${text}`;
  // Don't loop: she's shown what she just said and told to answer what was said now instead.
  const recentMine = rows.filter((m) => m.role === 'assistant' && (m.name ? m.name === character.name : character.main)).slice(-3).map((m) => m.content);
  // Tool calls and their results aren't part of the history, only what she said. Without this, every
  // turn she'd look the same thing up again (the guide, a file) and announce it as news again.
  const done = rows.filter((m) => m.role === 'assistant' && m.tools).slice(-4).map((m) => m.tools);
  if (!cue && done.length) {
    userContent += `\n\n(What you already did in your recent replies, oldest first: ${done.join('; ')}. That's done and already told; don't look it up again or report it again unless someone asks.)`;
  }
  if (!process.env.NO_ANTIREPEAT && !cue && recentMine.length) {
    userContent += `\n\n(Your last reply was: "${recentMine.at(-1).replace(/\s+/g, ' ').slice(0, 200)}". Don't say that again or anything close to it. Respond to what ${speaker.name} just said, in this message.)`;
  }
  const available = enabledTools(toolCtx);
  // Shot one: decide what, if anything, to do. Banter cues never take actions.
  const actions = available.filter((t) => !ALWAYS.includes(t.function.name));
  const plan = cue || !actions.length ? { search: null, tools: [] } : await planTurn(history, speaker, text, actions);
  if (plan.search || plan.tools.length) {
    activity('plan', { conversation, search: plan.search, tools: plan.tools, reason: plan.reason, ms: Date.now() - started });
  }
  if (plan.search) {
    if (onSentence) await onSentence(SEARCH_ANNOUNCEMENT);
    await onSearching?.(plan.search);
    const results = await runTool('web_search', { query: plan.search }, toolCtx);
    userContent +=
      `\n\n(You already searched the web for "${plan.search}" and got the results below. ` +
      `Answer from them now; don't say you'll search or check. If they don't answer the question, say so.\n\n${results})`;
  }
  // Shot two: the reply, offered the tools the plan chose, plus any still pending from the last few
  // turns (sticky), plus her note tools. A tool she's allowed but wasn't handed can be asked for
  // mid-turn with request_tool.
  const stuck = cue ? [] : stickyFor(conversation, available);
  // The guide comes with real tools, as help using them. Handed over alone on a chat turn it's the only
  // "action" she has, so pushed to act she'd look it up and announce it, turn after turn.
  const working = plan.tools.length > 0 || stuck.length > 0;
  const tools = available.filter((t) => plan.tools.includes(t.function.name) || stuck.includes(t.function.name) || (ALWAYS.includes(t.function.name) && (working || t.function.name !== 'guide')));
  const requestable = available.filter((t) => !tools.includes(t)).map((t) => t.function.name);
  if (!cue && requestable.length) tools.push(requestToolDef(requestable));
  if (plan.tools.length) userContent += `\n\n(For this reply, use ${plan.tools.join(' and ')} once, then answer.)`;
  // So she never narrates a call she couldn't make: what's actually in hand this turn.
  if (!cue) {
    userContent +=
      `\n\n(Tools in hand this turn: ${tools.map((t) => t.function.name).join(', ') || 'none'}. ` +
      `If this needs one that isn't listed, ask for it with request_tool${requestable.length ? '' : ' (nothing else is available to you right now)'}; never describe a call you didn't make, and never say something was saved, sent, played or done unless a tool result says ok. Never say a tool can't do something without trying the call (or checking the guide) first. ` +
      `Every tool result starts with [tool: ok] or [tool: error code] plus a hint. An error means it did not happen: follow the hint (or the guide for that tool), fix the call and retry once, or say plainly that it didn't work.)`;
  }

  // Every call this turn, for the phantom check and sticky tools: tool -> succeeded at least once.
  const succeeded = new Map();
  let requests = 0;
  const calls = []; // every tool call this turn and what came back, for the turn log
  const runner = async (name, args) => {
    if (name === 'request_tool') {
      let want = '', reason = '';
      try { ({ tool: want = '', reason = '' } = typeof args === 'string' ? JSON.parse(args || '{}') : args ?? {}); } catch {}
      const def = available.find((t) => t.function.name === want);
      const already = tools.some((t) => t.function.name === want);
      logFailure({ conversation, kind: 'tool_request', tool: want || '(none)', code: def ? 'granted' : 'refused', note: reason });
      if (already) return `[request_tool: ok]\n${want} is already in your hands; call it now.`;
      if (!def) return `[request_tool: error not_allowed] ${want || 'That tool'} isn't something you can use for ${speaker.name}.\nhint: Say plainly that you can't do it for them, and why if you know.`;
      if (++requests > 2) return `[request_tool: error conflict] No more tool requests this reply.\nhint: Say what you couldn't do, and that they can ask again.`;
      tools.push(def);
      return `[request_tool: ok]\n${want} is now in your hands for this reply. Call it.`;
    }
    const result = await runTool(name, args, toolCtx);
    calls.push({ name, args: typeof args === 'string' ? args : JSON.stringify(args), result: String(result).slice(0, 1500) });
    if (!toolFailed(result)) succeeded.set(name, true);
    else if (!succeeded.has(name)) succeeded.set(name, false);
    return result;
  };

  const messages = [
    // Her abilities are described from everything she's allowed (`available`), not just this turn's
    // tools, or on plain chat turns she'd believe she can't make files at all.
    { role: 'system', content: systemPrompt({ voice, speaker, perms, memories, others, earlier: summaryContext(conversation), extra: systemExtra, character, tools: available }) },
    ...history,
    { role: 'user', content: userContent },
  ];

  // Working on files takes several steps (look, read, edit, check), so those turns get more rounds.
  const multiStep = plan.tools.some((n) => MULTI_STEP.includes(n));
  const chatOpts = {
    onSentence,
    tools,
    runTool: runner,
    maxRounds: multiStep ? 12 : undefined, // undefined: the default (TOOL_ROUNDS, 4)
    repeatable: [...MULTI_STEP, ...ALWAYS],
    free: [...ALWAYS, 'request_tool'],
    voice,
  };
  let reply = stripTags(await chat(messages, chatOpts));

  // Phantom success: she said something was done, but no tool for it succeeded this turn.
  const phantom = !cue && phantomClaim(reply, succeeded);
  if (phantom) {
    logFailure({ conversation, kind: 'phantom_success', tool: phantom.tool, code: 'no_tool_result', note: `claimed "${phantom.claim}": ${reply.slice(0, 200)}` });
    const allowed = available.find((t) => t.function.name === phantom.tool);
    if (allowed && !tools.includes(allowed)) tools.push(allowed);
    if (voice) {
      // Already spoken: correct it out loud, and keep the tool in hand for the next turns.
      await onSentence?.(`Actually, correction: that didn't go through, nothing was ${phantom.claim}. Say it again and I'll do it properly.`);
      reply += ` (Correction: that didn't go through; nothing was ${phantom.claim}.)`;
    } else {
      // Text: one more go, told plainly what happened, before anything is sent.
      const retry = stripTags(await chat([
        ...messages,
        { role: 'assistant', content: reply },
        { role: 'user', content: `(You said "${phantom.claim}", but no ${phantom.tool} call succeeded in this reply, so it did not happen. ${allowed ? `Do it now with ${phantom.tool}, then answer` : `You can't do that here, so correct yourself plainly`}. Don't claim it again without an ok tool result. Reply to them fresh; they haven't seen your previous attempt.)` },
      ], { ...chatOpts, onSentence: undefined }));
      reply = phantomClaim(retry, succeeded) ? `${retry}\n\n*(Correction: that didn't actually go through; nothing was ${phantom.claim}. Ask again and I'll do it properly.)*` : retry;
    }
  }
  if (!cue) updateSticky(conversation, plan.tools, phantom?.tool, succeeded);

  // Still a near-copy of something she just said: in text, one more go before it's sent. (In voice it
  // has already been spoken; the reminder above is what prevents it there.)
  const repeated = recentMine.find((prev) => similarity(prev, reply) > 0.6);
  if (repeated) {
    logFailure({ conversation, kind: 'repeat', tool: '-', code: 'repeated_reply', note: reply.slice(0, 200) });
    if (!voice) {
      reply = stripTags(await chat([
        ...messages,
        { role: 'assistant', content: reply },
        { role: 'user', content: `(That's nearly word for word what you already said. Drop it entirely and answer what ${speaker.name} actually just said: "${text.slice(0, 300)}". Reply to them fresh; they haven't seen the repeat.)` },
      ], { ...chatOpts, onSentence: undefined }));
    }
  }
  logTurn({ conversation, speaker: speaker.name, voice, messages, tools: tools.map((t) => t.function.name), calls, reply, brain: config.brain });
  activity('replied', {
    conversation,
    voice,
    text: reply,
    ms: Date.now() - started,
    brain: config.brain,
    recalled: memories.length + others.reduce((n, o) => n + o.memories.length, 0),
  });

  if (!cue) addMessage(conversation, { role: 'user', userId: speaker.id, name: speaker.name, content: text });
  const used = calls.map((c) => `${c.name} ${toolTarget(c.args)}: ${toolFailed(c.result) ? 'failed' : 'ok'}`.replace(/  +/g, ' '));
  if (reply) addMessage(conversation, { role: 'assistant', name: character.main ? null : character.name, content: reply, tools: used.length ? used.join(', ') : null });

  // She's said it, so those are done.
  if (reply) for (const o of due) completeObligation(o.id, voice ? 'said in voice' : 'said in chat', speaker.id);
  if (due.length && reply) activity('obligation', { done: due.map((o) => `#${o.id} ${o.task}`), to: speaker.name });

  // Her pressure buckets react to the exchange (novelty, names, friction, being needed, contact).
  if (!cue && character.main) {
    try {
      pressure.onTurn({ speaker, text, voice, conversation, actions: plan.tools, made: plan.tools.some((n) => DELIVERABLES.includes(n)) });
    } catch (err) {
      activity('error', { where: 'pressure', message: err.message });
    }
  }

  queueConsolidation(conversation);
  // Facts can be about anyone in the conversation, so extraction runs whenever someone in it can be
  // remembered; each fact is filed under the person it's about.
  if (!cue && (perms.remember || people.some((p) => permissions(p.id).remember))) {
    const context = rows.slice(-10).map((m) => `${m.role === 'assistant' ? m.name ?? config.botName : m.name}: ${m.content}`).join('\n');
    queueMemoryExtraction({ speaker, text, reply, known: memories, character, context, people: [{ id: speaker.id, name: speaker.name }, ...people] });
  }
  return reply;
}

// Exactly what she was sent and did on her last few turns (data/turn-log.jsonl, newest last), so when
// she says something odd you can see why instead of guessing.
const TURN_LOG = path.join(DATA_DIR, 'turn-log.jsonl');
function logTurn(entry) {
  try {
    const lines = existsSync(TURN_LOG) ? readFileSync(TURN_LOG, 'utf8').split('\n').filter(Boolean) : [];
    lines.push(JSON.stringify({ ts: new Date().toISOString(), ...entry }));
    writeFileSync(TURN_LOG, lines.slice(-30).join('\n') + '\n');
  } catch (err) {
    activity('error', { where: 'turn log', message: err.message });
  }
}

// The gist of a tool call's arguments for the history ("files" for a guide lookup, "copy knowledge").
function toolTarget(args) {
  let a = {};
  try { a = JSON.parse(args || '{}'); } catch {}
  return [a.action, a.topic, a.path, a.query, a.title, a.model, a.task].filter((v) => typeof v === 'string' && v.trim()).join(' ').slice(0, 80);
}

// How alike two replies are: the share of their words in common (0 = nothing, 1 = same words).
function similarity(a, b) {
  const words = (s) => new Set(String(s).toLowerCase().match(/[a-z']{3,}/g) ?? []);
  const x = words(a), y = words(b);
  if (x.size < 5 || y.size < 5) return 0;
  let common = 0;
  for (const w of x) if (y.has(w)) common++;
  return common / Math.min(x.size, y.size);
}

// Someone telling her she got it wrong (continuity.js keeps it as a caution for a day).
const CORRECTED = /\b(that'?s (not right|wrong|not true|not how)|you('re| are) wrong|you got (it|that) wrong|no,? (that'?s|it'?s) not|incorrect|wrong again|that'?s false|you'?re making (it|that|stuff) up|not what i (said|asked))\b/i;

// ---- tool gating (Amy's spec: Downloads/amy-tool-gating-spec.md) ----

// Sticky tools: one the planner chose (or she claimed) that didn't succeed stays in hand for the
// next few turns of the conversation, so "write me a file" then "yeah, do it" still has the tool.
const STICKY_TURNS = 3;
const sticky = new Map(); // conversation -> Map(tool -> turns left)
function stickyFor(conversation, available) {
  const m = sticky.get(conversation);
  if (!m) return [];
  return [...m.keys()].filter((name) => available.some((t) => t.function.name === name));
}
function updateSticky(conversation, planned, claimed, succeeded) {
  const m = sticky.get(conversation) ?? new Map();
  for (const [name, left] of m) {
    if (succeeded.get(name) || left <= 1) m.delete(name);
    else m.set(name, left - 1);
  }
  for (const name of [...planned, claimed].filter(Boolean)) {
    if (!succeeded.get(name) && !m.has(name)) m.set(name, STICKY_TURNS);
  }
  if (m.size) sticky.set(conversation, m);
  else sticky.delete(conversation);
}

// Asking for a tool mid-turn. Offered only when there's something she could ask for.
function requestToolDef(names) {
  return {
    type: 'function',
    function: {
      name: 'request_tool',
      description: 'Ask for a tool you need for this request but were not handed this turn. If you are allowed it, it is added right away and you can call it in your next step.',
      parameters: {
        type: 'object',
        properties: {
          tool: { type: 'string', enum: names, description: 'The tool you need' },
          reason: { type: 'string', description: 'What they asked for that needs it' },
        },
        required: ['tool', 'reason'],
      },
    },
  };
}

// Action claims, and the tool that has to have succeeded for each to be true. Override or extend
// in data/phantom-claims.json ({"tool": "regex"}); false alarms are cheaper than misses.
const CLAIMS = {
  send_file: /\b(i('ve| have)? sent|sent (it|that|you)|it'?s attached|attached (it|the file)|here'?s the file)\b/i,
  send_project: /\b(zipped (it|them|up)|sent (you )?the (project|zip))\b/i,
  files: /\b(i('ve| have)? (saved|written|wrote|created|edited|updated|changed|moved|renamed|copied)\b.{0,40}\b(file|folder|line|script|code|config)|(file|folder|script)\b.{0,25}\b(is |has been )?(saved|created|updated|edited))\b/i,
  obligations: /\b(on (my|the) (list|to-?do)|added (it|that|this) to (my|the) list|it'?s on my list|i'?ve (set|added) (a |the )?reminder)\b/i,
  checkin_consent: /\b(saved (it )?(to|on) your card|on your card|got it saved|noted (it )?on your (card|profile))\b/i,
  music: /\b(it'?s playing|now playing|playing (it )?now|started playing|i'?ve stopped|stopped (the )?(song|music|it)|paused (it|the song|the music))\b/i,
  generate_image: /\b(here'?s (your|the) (picture|image|drawing)|(image|picture) is ready|i drew)\b/i,
  generate_music: /\b(song('s| is) (done|ready|finished)|i (made|wrote) (you )?a (song|track))\b/i,
  switch_brain: /\b(switched (over )?to|i'?m now (on|running)|now running on)\b/i,
  git: /\b(i('ve| have)? (pushed|committed)|(pushed|committed) (it|them|the changes))\b/i,
  avatar_mode: /\bavatar mode (is )?(now )?(on|off)\b/i,
  rebuild_memories: /\b(started|kicked off) (the )?(memory )?rebuild\b/i,
};
let claimOverrides = null;
function phantomClaim(reply, succeeded) {
  if (claimOverrides === null) {
    try {
      claimOverrides = Object.fromEntries(Object.entries(JSON.parse(readFileSync(path.join(DATA_DIR, 'phantom-claims.json'), 'utf8'))).map(([k, v]) => [k, new RegExp(v, 'i')]));
    } catch {
      claimOverrides = {};
    }
  }
  for (const [tool, re] of Object.entries({ ...CLAIMS, ...claimOverrides })) {
    const m = String(reply).match(re);
    if (m && !succeeded.get(tool)) return { tool, claim: m[0].trim() };
  }
  return null;
}

// The owner's name for "take it up with ...", from the contact list (or a fallback).
export function ownerName() {
  const owner = listContacts().find((c) => config.ownerIds.includes(c.user_id));
  return owner?.name ?? 'the owner';
}

function obligationsPrompt(speaker, due, priorities) {
  const parts = [];
  if (due.length) {
    parts.push(
      `You have obligations to fulfil in this reply, now that ${speaker.name} is here. Do every one of them, naturally and in your own words, as part of what you say:\n` +
        due.map((o) => `- ${o.task}`).join('\n'),
    );
  }
  if (priorities.length) {
    parts.push(
      `Your open priorities (things you committed to; keep them in mind, bring them up when it fits, and check one off with the obligations tool once it's done):\n` +
        priorities.map((o) => `- #${o.id} ${o.task}`).join('\n'),
    );
  }
  return parts.join('\n\n');
}

// Who else is in this conversation: everyone who spoke in the recent history, plus contacts named
// in the message ("tell Chris..."). The speaker isn't included. Returns [{ id, name }].
function peopleIn(rows, speaker, text) {
  const found = new Map();
  for (const m of rows.slice(-40)) {
    if (m.role === 'user' && m.user_id && m.user_id !== speaker.id && m.user_id !== 'dashboard') found.set(m.user_id, m.name ?? 'Someone');
  }
  for (const c of listContacts()) {
    if (c.user_id === speaker.id || found.has(c.user_id)) continue;
    const named = [c.name, c.username].filter((n) => n && n.length > 2).some((n) => new RegExp(`\\b${n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'i').test(text));
    if (named) found.set(c.user_id, c.name);
  }
  return [...found].map(([id, name]) => ({ id, name })).slice(0, 8);
}

// After each exchange, a background pass saves new memories and applies persona changes.
// It runs one at a time and never delays a reply.
let extractionChain = Promise.resolve();
function queueMemoryExtraction(job) {
  extractionChain = extractionChain
    .then(() => reflect(job))
    .catch((err) => activity('error', { where: 'memory extraction', message: err.message }));
}

const PERSONA_TASK = (name, botName) =>
  `2. "persona_change": null, UNLESS ${name} directly asked ${botName} to change who it is or how it behaves from now on: personality, attitude, speaking style, name, backstory, opinions, likes/dislikes, habits (e.g. "be more sarcastic", "stop using emoji", "your name is Rex now", "you're a Lakers fan now"). Compliments, questions about the bot, one-off requests ("say it like a pirate"), and requests the bot refused in its reply are NOT changes.
When there is a change, return {"summary": "<few words, e.g. more sarcastic>", "name": "<new name, or null if unchanged>", "persona": "<the COMPLETE rewritten persona>"}. Rewrite the current persona with the change woven in: keep every unaffected trait, drop anything the change contradicts, under 250 words. Write it as instructions addressed to the bot in second person ("You're...", "You love..."), never "I" or "me". Don't mention the name in it.`;

async function reflect({ speaker, text, reply, known, character = mainCharacter(), context = '', people = [{ id: speaker.id, name: speaker.name }] }) {
  const allowPersona = canEditPersona(speaker.id);
  // On the active brain: OpenRouter models pick out facts and people far more reliably than the
  // small local model (quickJson falls back to Ollama when that's the brain).
  const result = await quickJson([
    {
      role: 'system',
      content: [
        `You maintain a companion bot named ${character.name}. Analyze the latest exchange below (the earlier lines are context, already handled) and reply with JSON containing:`,
        `1. ${MEMORY_TASK(people.map((p) => p.name), character.name)}`,
        allowPersona && PERSONA_TASK(speaker.name, character.name),
        allowPersona ? `Format: {"facts": [...], "persona_change": null | {...}}` : `Format: {"facts": [...]}`,
      ]
        .filter(Boolean)
        .join('\n\n'),
    },
    {
      role: 'user',
      content:
        (allowPersona ? `Current persona:\n"""\n${character.persona || '(none yet)'}\n"""\n\n` : '') +
        (known.length ? `Already known about ${speaker.name}:\n${known.map((m) => `- ${m.content}`).join('\n')}\n\n` : '') +
        (context ? `Earlier in the conversation:\n${context}\n\n` : '') +
        `Latest exchange:\n${speaker.name}: ${text}\n${character.name}: ${reply}`,
    },
  ]);

  await fileFacts(result?.facts, people, speaker.name);

  const change = result?.persona_change;
  if (allowPersona && change && typeof change.persona === 'string' && change.persona.trim().length > 20) {
    const name = typeof change.name === 'string' ? change.name : null;
    if (character.main) {
      setPersona({
        name,
        persona: change.persona,
        reason: `${speaker.name}: ${typeof change.summary === 'string' ? change.summary : text.slice(0, 80)}`,
        changedBy: speaker.id,
      });
    } else {
      updateCharacter(character.id, { name: name ?? undefined, persona: change.persona }, speaker.id);
    }
  }
}
