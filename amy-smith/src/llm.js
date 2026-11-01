import { Ollama } from 'ollama';
import { config } from './config.js';

const ollama = new Ollama({ host: config.ollama.host });
const KEEP_ALIVE = '30m';

// Tool rounds per reply (TOOL_ROUNDS in .env): enough to look something up and then act on it. The
// planner already limits which tools are offered; file and code work get more (see companion.js).
const MAX_TOOL_ROUNDS = Math.max(1, Number(process.env.TOOL_ROUNDS) || 4);
// A tool result that failed or came back incomplete (see runTool's result header in tools.js).
const SETBACK = /^\[[\w-]+: error|^Error|TRUNCATED|NOT READ/;
const LOOKUP_FILLERS = ['Hang on, let me look that up.', 'One sec, checking.', 'Let me look into that real quick.'];

// Streams a chat reply, calling onSentence for each complete sentence as it arrives
// (so voice can start speaking before the whole reply is generated). Returns the full text.
// If tools are given, the model may call them; results are fed back and it keeps going.
// maxRounds: how many times the model may call tools before it must answer. repeatable: tools it
// may call more than once (working on files takes read, edit, read again); every other tool runs
// at most once per reply.
// free: bookkeeping tools (her notes); a round that only uses those doesn't count against
// maxRounds, up to two such rounds, so taking a note never costs her the reply.
export async function chat(messages, { onSentence, tools = [], runTool, maxRounds = MAX_TOOL_ROUNDS, repeatable = [], free = [], voice = false } = {}) {
  messages = [...messages];
  let full = '';
  let spoke = false;
  const used = new Set();
  let paid = 0, freeRounds = 0, nudged = false, retried = false;
  const outcomes = []; // "files read: ok", for the notice when rounds run out
  const say = async (sentence) => {
    spoke = true;
    await onSentence(sentence);
  };

  for (let round = 0; ; round++) {
    const offerTools = tools.length > 0 && paid < maxRounds;
    const stream = await startChat(messages, offerTools ? tools : null, voice);

    let content = '';
    let pending = '';
    let calls = [];
    for await (const part of stream) {
      if (part.message?.tool_calls?.length) calls.push(...part.message.tool_calls);
      const chunk = part.message?.content ?? '';
      content += chunk;
      if (!onSentence) continue;
      pending += chunk;
      let cut;
      while ((cut = findSentenceEnd(pending)) !== -1) {
        const sentence = pending.slice(0, cut).trim();
        pending = pending.slice(cut);
        if (sentence) await say(sentence);
      }
    }
    if (onSentence && pending.trim()) await say(pending.trim());
    if (content.trim()) full += (full ? ' ' : '') + content.trim();
    // Each tool at most once per reply (unless repeatable), even if the model asks for it again.
    calls = calls.filter((c) => repeatable.includes(c.function.name) || (!used.has(c.function.name) && used.add(c.function.name)));
    if (!calls.length) {
      // Tools ran but nothing was said: ask once more, without tools, for the actual reply.
      if (!full.trim() && round > 0 && !nudged) {
        nudged = true;
        paid = maxRounds;
        messages.push({ role: 'user', content: '(Now reply to them. Say your answer out loud; no tools.)' });
        continue;
      }
      return full;
    }
    if (calls.every((c) => free.includes(c.function.name)) && freeRounds < 2) freeRounds++;
    else paid++;

    // Avoid dead air in voice while a tool runs.
    if (onSentence && !spoke && calls.some((c) => c.function.name === 'web_search')) await say(LOOKUP_FILLERS[Math.floor(Math.random() * LOOKUP_FILLERS.length)]);
    messages.push({ role: 'assistant', content, tool_calls: calls });
    let setback = false;
    for (const call of calls) {
      const result = await runTool(call.function.name, call.function.arguments);
      const bad = SETBACK.test(result);
      if (bad) {
        setback = true;
        used.delete(call.function.name); // a call that failed didn't happen, so it may be made again
      }
      if (!free.includes(call.function.name)) {
        let action = '';
        try { action = JSON.parse(call.function.arguments || '{}').action ?? ''; } catch {}
        outcomes.push(`${call.function.name}${action ? ` ${action}` : ''}: ${bad ? (/TRUNCATED|NOT READ/.test(result) ? 'incomplete' : 'failed') : 'ok'}`);
      }
      messages.push({ role: 'tool', content: result, tool_name: call.function.name, tool_call_id: call.id });
    }
    // An error or a cut-short read gets one more round to fix the call or read on, even when the
    // turn's rounds are used up, instead of answering on half the facts. Once per reply.
    if (setback && paid >= maxRounds && !retried) {
      retried = true;
      paid = maxRounds - 1;
    }
    // Out of rounds: say so, with what was done, so the answer is honest about what isn't.
    if (paid >= maxRounds && tools.length) {
      messages.push({
        role: 'user',
        content: `(No more tool rounds this reply. Calls made: ${outcomes.join('; ') || 'none'}. Answer now; be clear about anything that failed or isn't finished, and that it can be continued next message.)`,
      });
    }
  }
}

async function startChat(messages, tools, voice = false) {
  if (config.brain === 'openrouter') return openRouterChat(messages, tools, voice);
  if (config.brain === 'nvidia') return nvidiaChat(messages, tools);
  const request = {
    model: config.ollama.model,
    messages,
    stream: true,
    think: config.ollama.think,
    keep_alive: KEEP_ALIVE,
    options: { num_ctx: config.ollama.numCtx },
  };
  if (!tools) return ollama.chat(request);
  try {
    return await ollama.chat({ ...request, tools });
  } catch (err) {
    // Models without tool support: just answer without tools.
    if (/does not support tools/i.test(err.message)) return ollama.chat(request);
    throw err;
  }
}

// ---- NVIDIA NIM / OpenRouter: OpenAI-style chat completions (SSE) ----

const OPENROUTER = 'https://openrouter.ai/api/v1';
export const openRouterHeaders = () => ({
  Authorization: `Bearer ${config.openrouter.apiKey}`,
  'Content-Type': 'application/json',
  'HTTP-Referer': 'http://127.0.0.1', // shows up as the app in OpenRouter's activity log
  'X-Title': config.botName,
});

const nvidiaHeaders = () => ({
  Authorization: `Bearer ${config.nvidia.apiKey}`,
  'Content-Type': 'application/json',
  Accept: 'application/json',
});

// Shared SSE reader → Ollama-shaped chunks for chat().
async function* streamOpenAIChat(res, label) {
  const calls = [];
  const dec = new TextDecoder();
  let buf = '';
  for await (const bytes of res.body) {
    buf += dec.decode(bytes, { stream: true });
    let nl;
    while ((nl = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line.startsWith('data:')) continue;
      const data = line.slice(5).trim();
      if (data === '[DONE]') continue;
      const chunk = JSON.parse(data);
      if (chunk.error) throw new Error(`${label}: ${chunk.error.message ?? JSON.stringify(chunk.error)}`);
      const delta = chunk.choices?.[0]?.delta ?? {};
      for (const tc of delta.tool_calls ?? []) {
        const c = (calls[tc.index ?? 0] ??= { id: '', function: { name: '', arguments: '' } });
        if (tc.id) c.id = tc.id;
        if (tc.function?.name) c.function.name += tc.function.name;
        if (tc.function?.arguments) c.function.arguments += tc.function.arguments;
      }
      if (delta.content) yield { message: { content: delta.content } };
    }
  }
  if (calls.length) yield { message: { content: '', tool_calls: calls.filter(Boolean) } };
}

// NVIDIA NIM teacher — used when HalMini misses.
// Nemotron burns the token budget on reasoning unless enable_thinking is false.
async function* nvidiaChat(messages, tools) {
  if (!config.nvidia.apiKey) throw new Error('NVIDIA is the teacher, but NVIDIA_API_KEY is empty in .env');
  const body = {
    model: config.nvidia.model,
    stream: true,
    max_tokens: config.nvidia.maxTokens,
    temperature: 0.6,
    top_p: 0.95,
    messages: messages.map(toOpenAI),
    chat_template_kwargs: { enable_thinking: false },
  };
  if (tools) body.tools = tools;
  const res = await fetch(`${config.nvidia.baseUrl}/chat/completions`, {
    method: 'POST',
    headers: nvidiaHeaders(),
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`NVIDIA (${config.nvidia.model}) failed (${res.status}): ${(await res.text()).slice(0, 300)}`);
  yield* streamOpenAIChat(res, 'NVIDIA');
}

// Yields the same shape as Ollama's stream ({ message: { content, tool_calls } }) so chat()
// handles both. Reasoning tokens are dropped: only the answer is spoken.
async function* openRouterChat(messages, tools, voice = false) {
  // Spoken replies use their own (default: no) reasoning: on Grok 4.20 medium reasoning alone added
  // about 10 seconds before the first word. Text and tool work keep the main setting.
  const effort = voice ? config.openrouter.voiceReasoning : config.openrouter.reasoning;
  if (!config.openrouter.apiKey) throw new Error('OpenRouter is the brain, but OPENROUTER_API_KEY is empty in .env');
  const body = {
    model: config.openrouter.model,
    stream: true,
    messages: messages.map(toOpenAI),
    ...(effort === 'none' ? { reasoning: { enabled: false } } : { reasoning: { effort, exclude: true } }),
  };
  if (tools) body.tools = tools;
  const res = await fetch(`${OPENROUTER}/chat/completions`, { method: 'POST', headers: openRouterHeaders(), body: JSON.stringify(body) });
  if (!res.ok) throw new Error(`OpenRouter (${config.openrouter.model}) failed (${res.status}): ${(await res.text()).slice(0, 300)}`);
  yield* streamOpenAIChat(res, 'OpenRouter');
}

// Ollama-style history to OpenAI style: tool calls need ids and string arguments.
function toOpenAI(m) {
  if (m.role === 'tool') return { role: 'tool', tool_call_id: m.tool_call_id ?? m.tool_name, content: m.content };
  if (!m.tool_calls) return { role: m.role, content: m.content };
  return {
    role: 'assistant',
    content: m.content || null,
    tool_calls: m.tool_calls.map((c) => ({
      id: c.id ?? c.function.name,
      type: 'function',
      function: { name: c.function.name, arguments: typeof c.function.arguments === 'string' ? c.function.arguments : JSON.stringify(c.function.arguments ?? {}) },
    })),
  };
}

// OpenRouter's catalogue, for the dashboard's model pickers.
export async function openRouterModels() {
  const res = await fetch(`${OPENROUTER}/models`, { signal: AbortSignal.timeout(15_000) });
  if (!res.ok) throw new Error(`OpenRouter model list failed (${res.status})`);
  const { data } = await res.json();
  return data.map((m) => ({
    id: m.id,
    name: m.name,
    reasoning: m.supported_parameters?.includes('reasoning') ?? false,
    tools: m.supported_parameters?.includes('tools') ?? false,
    image: m.architecture?.output_modalities?.includes('image') ?? false,
    price: Number(m.pricing?.prompt) * 1e6, // dollars per million input tokens
    outPrice: Number(m.pricing?.completion) * 1e6,
    context: m.context_length ?? 0,
    created: m.created ?? 0,
  }));
}

// The strongest families for each kind of work, best first. OpenRouter doesn't rate models, so this
// is the judgment; within a family the newest model wins. Only models that can use tools qualify.
const FAMILIES = {
  coding: [/anthropic\/claude-opus/, /anthropic\/claude-sonnet/, /openai\/gpt-[\d.]+-?(sol|codex)?(-pro)?$/, /google\/gemini-[\d.]+-(argon|pro)/, /moonshotai\/kimi/, /deepseek\/deepseek/, /qwen\/qwen[\d.]*-coder/, /z-ai\/glm/, /mistralai\/(devstral|codestral)/, /minimax\/minimax/],
  reasoning: [/openai\/gpt-[\d.]+-?(sol)?-pro/, /anthropic\/claude-opus/, /google\/gemini-[\d.]+-(pro|argon)/, /openai\/gpt-[\d.]+/, /deepseek\/deepseek/, /moonshotai\/kimi/, /qwen\/qwen/, /x-ai\/grok/],
  general: [/anthropic\/claude-(sonnet|opus)/, /openai\/gpt-[\d.]+/, /google\/gemini-[\d.]+-(pro|flash)/, /deepseek\/deepseek/, /moonshotai\/kimi/, /meta\/muse/, /mistralai\/mistral-large/],
  cheap: [/deepseek\/deepseek.*flash/, /google\/gemini-[\d.]+-flash/, /anthropic\/claude-haiku/, /minimax\/minimax.*flash/, /qwen\/qwen/, /mistralai\/mistral/, /inception\/mercury/],
};

// Up to `count` models for a purpose, best first, one per family (its newest).
export async function rankModels(purpose = 'coding', count = 5) {
  const families = FAMILIES[purpose] ?? FAMILIES.general;
  // Skip aliases ("~…-latest"), previews, dated snapshots, and free/batch variants.
  const usable = (await openRouterModels()).filter(
    (m) => m.tools && !m.image && !/^~|preview|customtools|:(free|batch|beta)$|-\d{4}$/.test(m.id) && m.price >= 0 && (purpose !== 'cheap' || m.price <= 0.5),
  );
  const picks = [];
  for (const family of families) {
    const best = usable.filter((m) => family.test(m.id) && !picks.includes(m)).sort((a, b) => b.created - a.created)[0];
    if (best) picks.push(best);
    if (picks.length >= count) break;
  }
  return picks;
}

// Music with Google's Lyria 3: a 30-second clip or a full-length song, as MP3 bytes. OpenRouter
// only returns audio when streamed; it arrives as base64 pieces in delta.audio.data.
export async function generateMusic(prompt, length = 'clip') {
  if (!config.openrouter.apiKey) throw new Error('OPENROUTER_API_KEY is empty in .env');
  const model = length === 'full' ? config.openrouter.songModel : config.openrouter.clipModel;
  const res = await fetch(`${OPENROUTER}/chat/completions`, {
    method: 'POST',
    headers: openRouterHeaders(),
    body: JSON.stringify({ model, stream: true, modalities: ['text', 'audio'], audio: { format: 'mp3' }, messages: [{ role: 'user', content: prompt }] }),
    signal: AbortSignal.timeout(300_000),
  });
  if (!res.ok) throw new Error(`Music generation (${model}) failed (${res.status}): ${(await res.text()).slice(0, 300)}`);
  const dec = new TextDecoder();
  let buf = '', audio = '', text = '';
  for await (const bytes of res.body) {
    buf += dec.decode(bytes, { stream: true });
    let nl;
    while ((nl = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line.startsWith('data:') || line.endsWith('[DONE]')) continue;
      const chunk = JSON.parse(line.slice(5));
      if (chunk.error) throw new Error(`Music generation: ${chunk.error.message ?? JSON.stringify(chunk.error)}`);
      const delta = chunk.choices?.[0]?.delta ?? {};
      if (delta.audio?.data) audio += delta.audio.data;
      if (delta.content) text += delta.content;
    }
  }
  if (!audio) throw new Error(`${model} returned no audio${text ? `: ${text.slice(0, 200)}` : ''}`);
  // Lyria times its lyrics ("[12.5:] line"); the timestamps aren't for reading.
  return { bytes: Buffer.from(audio, 'base64'), lyrics: text.replace(/\[\d+(\.\d+)?:?\]\s*/g, '').trim() };
}

// One-shot image generation. Returns PNG/JPEG bytes and any text the model added.
export async function generateImage(prompt) {
  if (!config.openrouter.apiKey) throw new Error('OPENROUTER_API_KEY is empty in .env');
  const res = await fetch(`${OPENROUTER}/chat/completions`, {
    method: 'POST',
    headers: openRouterHeaders(),
    body: JSON.stringify({ model: config.openrouter.imageModel, modalities: ['image', 'text'], messages: [{ role: 'user', content: prompt }] }),
    signal: AbortSignal.timeout(120_000),
  });
  if (!res.ok) throw new Error(`Image generation (${config.openrouter.imageModel}) failed (${res.status}): ${(await res.text()).slice(0, 300)}`);
  const message = (await res.json()).choices?.[0]?.message ?? {};
  const url = message.images?.[0]?.image_url?.url;
  const m = url?.match(/^data:(image\/[\w+.-]+);base64,(.+)$/);
  if (!m) throw new Error(`${config.openrouter.imageModel} returned no image${message.content ? `: ${String(message.content).slice(0, 200)}` : ''}`);
  return { mime: m[1], bytes: Buffer.from(m[2], 'base64'), text: message.content || '' };
}

// One non-streamed step for a background helper: the model's message, with any tool calls.
export async function agentStep(messages, tools, model) {
  if (!config.openrouter.apiKey) throw new Error('Background helpers need OPENROUTER_API_KEY in .env');
  const res = await fetch(`${OPENROUTER}/chat/completions`, {
    method: 'POST',
    headers: openRouterHeaders(),
    body: JSON.stringify({ model, messages: messages.map(toOpenAI), tools, reasoning: { effort: 'medium', exclude: true } }),
    signal: AbortSignal.timeout(300_000),
  });
  if (!res.ok) throw new Error(`OpenRouter (${model}) failed (${res.status}): ${(await res.text()).slice(0, 300)}`);
  const data = await res.json();
  if (data.error) throw new Error(`OpenRouter: ${data.error.message ?? JSON.stringify(data.error)}`);
  const m = data.choices?.[0]?.message ?? {};
  return { content: m.content ?? '', tool_calls: m.tool_calls ?? [] };
}

// A fast JSON answer from whichever brain is on (no reasoning), for planning each reply.
export async function quickJson(messages) {
  if (config.brain === 'ollama') return json(messages);
  if (config.brain === 'nvidia') {
    if (!config.nvidia.apiKey) throw new Error('NVIDIA is the teacher, but NVIDIA_API_KEY is empty in .env');
    const res = await fetch(`${config.nvidia.baseUrl}/chat/completions`, {
      method: 'POST',
      headers: nvidiaHeaders(),
      body: JSON.stringify({
        model: config.nvidia.model,
        messages: messages.map(toOpenAI),
        temperature: 0,
        max_tokens: Math.max(256, config.nvidia.maxTokens || 256),
        chat_template_kwargs: { enable_thinking: false },
        response_format: { type: 'json_object' },
      }),
      signal: AbortSignal.timeout(20_000),
    });
    if (!res.ok) throw new Error(`NVIDIA planning failed (${res.status}): ${(await res.text()).slice(0, 200)}`);
    const text = (await res.json()).choices?.[0]?.message?.content ?? '';
    try {
      return JSON.parse(text.replace(/^```(?:json)?\s*|\s*```$/g, ''));
    } catch {
      return null;
    }
  }
  if (!config.openrouter.apiKey) throw new Error('OpenRouter is the brain, but OPENROUTER_API_KEY is empty in .env');
  const res = await fetch(`${OPENROUTER}/chat/completions`, {
    method: 'POST',
    headers: openRouterHeaders(),
    body: JSON.stringify({ model: config.openrouter.model, messages, response_format: { type: 'json_object' }, reasoning: { enabled: false }, temperature: 0 }),
    signal: AbortSignal.timeout(20_000),
  });
  if (!res.ok) throw new Error(`OpenRouter planning failed (${res.status}): ${(await res.text()).slice(0, 200)}`);
  const text = (await res.json()).choices?.[0]?.message?.content ?? '';
  try {
    return JSON.parse(text.replace(/^```(?:json)?\s*|\s*```$/g, ''));
  } catch {
    return null;
  }
}

// One-shot JSON completion on local Ollama, used for background memory extraction.
export async function json(messages) {
  const res = await ollama.chat({
    model: config.ollama.model,
    messages,
    format: 'json',
    think: false,
    keep_alive: KEEP_ALIVE,
    options: { num_ctx: config.ollama.numCtx }, // same as chat, or Ollama reloads the model
  });
  try {
    return JSON.parse(res.message.content);
  } catch {
    return null;
  }
}

export async function embed(texts) {
  const res = await ollama.embed({ model: config.ollama.embedModel, input: texts, keep_alive: KEEP_ALIVE });
  return res.embeddings;
}

export async function listModels() {
  const { models } = await ollama.list();
  return models.map((m) => ({ name: m.name, size: m.size, family: m.details?.family, capabilities: m.capabilities ?? [] }));
}

export async function checkOllama() {
  const { models } = await ollama.list();
  const names = new Set(models.map((m) => m.name));
  const has = (n) => names.has(n) || names.has(`${n}:latest`);
  for (const m of [config.ollama.model, config.ollama.embedModel]) {
    if (!has(m)) throw new Error(`Ollama model "${m}" not found. Run: ollama pull ${m}`);
  }
}

// Index just past the first sentence terminator followed by whitespace, or -1.
// Requires a few characters so "Mr." or "1." at the very start don't split.
function findSentenceEnd(text) {
  const re = /[.!?…]+["')\]]*\s|\n+/g;
  let m;
  while ((m = re.exec(text))) {
    if (m.index >= 12 || m[0].startsWith('\n')) return m.index + m[0].length;
  }
  return -1;
}
