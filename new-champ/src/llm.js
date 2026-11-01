import { Ollama } from 'ollama';
import { config } from './config.js';

const ollama = new Ollama({ host: config.ollama.host });
const KEEP_ALIVE = '30m';
const useNvidia = () => config.llmBackend === 'nvidia';

const MAX_TOOL_ROUNDS = 3;
const LOOKUP_FILLERS = ['Hang on, let me look that up.', 'One sec, checking.', 'Let me look into that real quick.'];

// Streams a chat reply, calling onSentence for each complete sentence as it arrives
// (so voice can start speaking before the whole reply is generated). Returns the full text.
// If tools are given, the model may call them; results are fed back and it keeps going.
export async function chat(messages, { onSentence, tools = [], runTool } = {}) {
  messages = [...messages];
  let full = '';
  let spoke = false;
  const say = async (sentence) => {
    spoke = true;
    await onSentence(sentence);
  };

  for (let round = 0; ; round++) {
    const offerTools = tools.length > 0 && round < MAX_TOOL_ROUNDS;
    const stream = await startChat(messages, offerTools ? tools : null);

    let content = '';
    let pending = '';
    const calls = [];
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
    if (!calls.length) return full;

    // Avoid dead air in voice while a tool runs.
    if (onSentence && !spoke) await say(LOOKUP_FILLERS[Math.floor(Math.random() * LOOKUP_FILLERS.length)]);
    messages.push({ role: 'assistant', content, tool_calls: calls });
    for (const call of calls) {
      const result = await runTool(call.function.name, call.function.arguments);
      messages.push(
        useNvidia()
          ? { role: 'tool', content: result, tool_call_id: call.id || call.function.name }
          : { role: 'tool', content: result, tool_name: call.function.name },
      );
    }
  }
}

async function startChat(messages, tools) {
  if (useNvidia()) return startNvidiaChat(messages, tools);

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

async function* startNvidiaChat(messages, tools) {
  const body = {
    model: config.nvidia.model,
    messages: toOpenAiMessages(messages),
    stream: true,
    max_tokens: config.nvidia.maxTokens,
    temperature: 0.7,
  };
  if (tools?.length) {
    body.tools = tools.map(toOpenAiTool);
    body.tool_choice = 'auto';
  }

  let res;
  try {
    res = await fetch(`${config.nvidia.baseUrl}/chat/completions`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${config.nvidia.apiKey}`,
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      },
      body: JSON.stringify(body),
    });
  } catch (err) {
    throw new Error(`NVIDIA chat failed: ${err.message}`);
  }

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    // Retry once without tools if the endpoint rejects them.
    if (tools?.length && /tool/i.test(text)) {
      yield* startNvidiaChat(messages, null);
      return;
    }
    throw new Error(`NVIDIA chat ${res.status}: ${text.slice(0, 300)}`);
  }

  const toolAcc = new Map();
  let sawTool = false;
  for await (const event of readSse(res.body)) {
    if (event === '[DONE]') break;
    let data;
    try {
      data = JSON.parse(event);
    } catch {
      continue;
    }
    const choice = data.choices?.[0];
    if (!choice) continue;
    const delta = choice.delta || {};
    if (delta.content) yield { message: { content: delta.content } };
    if (delta.tool_calls?.length) {
      sawTool = true;
      for (const tc of delta.tool_calls) {
        const idx = tc.index ?? 0;
        const cur = toolAcc.get(idx) || { id: '', type: 'function', function: { name: '', arguments: '' } };
        if (tc.id) cur.id = tc.id;
        if (tc.function?.name) cur.function.name += tc.function.name;
        if (tc.function?.arguments) cur.function.arguments += tc.function.arguments;
        toolAcc.set(idx, cur);
      }
    }
  }
  if (sawTool && toolAcc.size) {
    yield { message: { content: '', tool_calls: [...toolAcc.values()] } };
  }
}

function toOpenAiMessages(messages) {
  return messages.map((m) => {
    if (m.role === 'tool') {
      return { role: 'tool', content: String(m.content ?? ''), tool_call_id: m.tool_call_id || m.tool_name || 'tool' };
    }
    if (m.tool_calls?.length) {
      return {
        role: 'assistant',
        content: m.content || null,
        tool_calls: m.tool_calls.map((c, i) => ({
          id: c.id || `call_${i}`,
          type: 'function',
          function: {
            name: c.function.name,
            arguments: typeof c.function.arguments === 'string' ? c.function.arguments : JSON.stringify(c.function.arguments ?? {}),
          },
        })),
      };
    }
    return { role: m.role, content: m.content ?? '' };
  });
}

function toOpenAiTool(def) {
  return {
    type: 'function',
    function: {
      name: def.function.name,
      description: def.function.description,
      parameters: def.function.parameters,
    },
  };
}

async function* readSse(body) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf('\n')) !== -1) {
      let line = buf.slice(0, idx);
      buf = buf.slice(idx + 1);
      if (line.endsWith('\r')) line = line.slice(0, -1);
      if (!line.startsWith('data:')) continue;
      const data = line.slice(5).trim();
      if (data) yield data;
    }
  }
}

// One-shot JSON completion, used for background memory extraction.
export async function json(messages) {
  if (useNvidia()) {
    const res = await fetch(`${config.nvidia.baseUrl}/chat/completions`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${config.nvidia.apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: config.nvidia.model,
        messages: [
          ...toOpenAiMessages(messages),
          { role: 'user', content: 'Respond with valid JSON only. No markdown.' },
        ],
        stream: false,
        max_tokens: Math.max(config.nvidia.maxTokens, 512),
        temperature: 0.2,
      }),
    });
    if (!res.ok) throw new Error(`NVIDIA json ${res.status}: ${(await res.text()).slice(0, 200)}`);
    const data = await res.json();
    const content = data.choices?.[0]?.message?.content ?? '';
    try {
      const start = content.indexOf('{');
      const end = content.lastIndexOf('}');
      return JSON.parse(start >= 0 ? content.slice(start, end + 1) : content);
    } catch {
      return null;
    }
  }

  const res = await ollama.chat({
    model: config.ollama.model,
    messages,
    format: 'json',
    think: false,
    keep_alive: KEEP_ALIVE,
    options: { num_ctx: config.ollama.numCtx },
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
  if (useNvidia()) {
    return [{ name: config.nvidia.model, size: 0, family: 'nvidia', capabilities: ['chat'] }];
  }
  const { models } = await ollama.list();
  return models.map((m) => ({ name: m.name, size: m.size, family: m.details?.family, capabilities: m.capabilities ?? [] }));
}

export async function checkLlm() {
  if (useNvidia()) {
    if (!config.nvidia.apiKey) throw new Error('NVIDIA_API_KEY is empty. Copy it from Luna riko combine .env');
    const res = await fetch(`${config.nvidia.baseUrl}/models`, {
      headers: { Authorization: `Bearer ${config.nvidia.apiKey}` },
    });
    if (!res.ok) throw new Error(`NVIDIA API check failed (${res.status}). Is the free NIM key valid?`);
  } else {
    const { models } = await ollama.list();
    const names = new Set(models.map((m) => m.name));
    const has = (n) => names.has(n) || names.has(`${n}:latest`);
    if (!has(config.ollama.model)) throw new Error(`Ollama model "${config.ollama.model}" not found. Run: ollama pull ${config.ollama.model}`);
  }

  // Memory embeddings always use local Ollama.
  const { models } = await ollama.list();
  const names = new Set(models.map((m) => m.name));
  const has = (n) => names.has(n) || names.has(`${n}:latest`);
  if (!has(config.ollama.embedModel)) {
    throw new Error(`Ollama embed model "${config.ollama.embedModel}" not found. Run: ollama pull ${config.ollama.embedModel}`);
  }
}

/** @deprecated use checkLlm */
export const checkOllama = checkLlm;

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
