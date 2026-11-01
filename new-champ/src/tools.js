import { config } from './config.js';
import { activity } from './events.js';

// Tools the model can call mid-conversation. Each has an Ollama tool definition and a run(args) function.
const TOOLS = {
  web_search: {
    enabled: () => Boolean(config.tavily.apiKey),
    definition: {
      type: 'function',
      function: {
        name: 'web_search',
        description:
          'Search the internet. Use for current events, news, sports scores, weather, prices, release dates, or any fact you are unsure of or that may have changed recently. Do not use for casual chat.',
        parameters: {
          type: 'object',
          properties: {
            query: { type: 'string', description: 'What to search for, phrased like a search engine query' },
            topic: { type: 'string', enum: ['general', 'news'], description: 'Use "news" for recent events and headlines' },
            time_range: { type: 'string', enum: ['day', 'week', 'month', 'year'], description: 'Only include results from this recent period' },
          },
          required: ['query'],
        },
      },
    },
    run: tavilySearch,
  },
};

export function enabledTools() {
  return Object.values(TOOLS)
    .filter((t) => t.enabled())
    .map((t) => t.definition);
}

export async function runTool(name, args = {}) {
  const tool = TOOLS[name];
  if (!tool?.enabled()) return `Error: unknown tool "${name}"`;
  try {
    return await tool.run(typeof args === 'string' ? JSON.parse(args) : args);
  } catch (err) {
    activity('error', { where: name, message: err.message });
    return `Error: ${err.message}`;
  }
}

async function tavilySearch({ query, topic = 'general', time_range }) {
  if (!query?.trim()) return 'Error: empty search query';
  const body = {
    query: query.trim(),
    topic: topic === 'news' ? 'news' : 'general',
    search_depth: config.tavily.depth,
    max_results: config.tavily.maxResults,
    include_answer: 'basic',
  };
  if (['day', 'week', 'month', 'year'].includes(time_range)) body.time_range = time_range;

  const started = Date.now();
  const res = await fetch('https://api.tavily.com/search', {
    method: 'POST',
    headers: { Authorization: `Bearer ${config.tavily.apiKey}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(20_000),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Tavily search failed (${res.status}): ${text.slice(0, 200)}`);
  }
  const data = await res.json();
  const results = data.results ?? [];
  activity('search', {
    query: body.query,
    results: results.slice(0, 3).map((r) => ({ title: r.title, url: r.url })),
    ms: Date.now() - started,
  });

  if (!results.length && !data.answer) return `No results found for "${body.query}".`;
  return [
    data.answer && `Summary: ${data.answer}`,
    ...results.map(
      (r, i) =>
        `[${i + 1}] ${r.title}${r.published_date ? ` (${r.published_date})` : ''}\n${r.url}\n${(r.content ?? '').slice(0, 600)}`,
    ),
  ]
    .filter(Boolean)
    .join('\n\n');
}
