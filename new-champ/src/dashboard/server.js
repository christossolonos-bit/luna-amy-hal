import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { timingSafeEqual } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ChannelType } from 'discord.js';
import { config, getSettings, updateSettings } from '../config.js';
import { bus, recentActivity, activity } from '../events.js';
import { listModels } from '../llm.js';
import { respond } from '../companion.js';
import { setPersona } from '../persona.js';
import * as memory from '../memory.js';
import { sessions, leaveChannel, joinChannel } from '../voice/session.js';
import { sttEngine, transcribeSpeech } from '../voice/stt.js';
import { synthesize } from '../voice/fishAudio.js';
import * as emotion from '../emotion.js';

const PUBLIC = path.join(path.dirname(fileURLToPath(import.meta.url)), 'public');
const startedAt = Date.now();

export function startDashboard(bot) {
  const routes = buildRoutes(bot);

  const server = http.createServer(async (req, res) => {
    try {
      if (!authorized(req)) {
        res.writeHead(401, { 'WWW-Authenticate': 'Basic realm="Companion dashboard"' });
        return res.end('Password required');
      }
      // Block other websites from driving the API through your browser.
      if (req.method !== 'GET' && req.headers.origin && new URL(req.headers.origin).host !== req.headers.host) {
        return send(res, 403, { error: 'Cross-origin request blocked' });
      }

      const url = new URL(req.url, `http://${req.headers.host}`);
      if (!url.pathname.startsWith('/api/')) return serveStatic(res, url.pathname);

      // Decode so IDs like "voice:123" match even when the browser sends "voice%3A123".
      const pathname = decodeURIComponent(url.pathname);
      for (const [method, pattern, handler] of routes) {
        const match = method === req.method && pathname.match(pattern);
        if (match) {
          // Binary uploads (mic audio) are read by their own handler.
          const binary = req.headers['content-type'] === 'application/octet-stream';
          const body = ['POST', 'PUT'].includes(req.method) && !binary ? await readJson(req) : {};
          const result = await handler({ req, res, url, params: match.groups ?? {}, body });
          if (result !== undefined) send(res, 200, result);
          return;
        }
      }
      send(res, 404, { error: 'Not found' });
    } catch (err) {
      send(res, err.status ?? 500, { error: err.message });
    }
  });

  server.listen(config.dashboard.port, config.dashboard.host, () => {
    const shown = config.dashboard.host === '0.0.0.0' ? 'localhost' : config.dashboard.host;
    activity('ready', { message: `Dashboard at http://${shown}:${config.dashboard.port}` });
  });
  server.on('error', (err) => activity('error', { where: 'dashboard', message: err.message }));
  return server;
}

function buildRoutes(bot) {
  const r = (method, pattern, handler) => [method, new RegExp(`^${pattern}$`), handler];
  // The Discord client while the bot is online, else null.
  const online = () => (bot.client?.isReady() ? bot.client : null);
  const need = () => {
    const client = online();
    if (!client) throw Object.assign(new Error('The bot is offline. Turn it on first.'), { status: 503 });
    return client;
  };

  return [
    r('GET', '/api/status', () => {
      const client = online();
      return {
        power: { status: bot.status, since: bot.since, error: bot.error, hasToken: Boolean(config.discordToken) },
        bot: client && { tag: client.user.tag, avatar: client.user.displayAvatarURL({ size: 128 }) },
        ping: client?.ws.ping ?? null,
        uptime: Date.now() - startedAt,
        guilds: client
          ? client.guilds.cache.map((g) => ({ id: g.id, name: g.name, members: g.memberCount, icon: g.iconURL({ size: 64 }) }))
          : [],
        voice: [...sessions.values()].map((s) => s.summary()),
      model: config.ollama.model,
      embedModel: config.ollama.embedModel,
      fishReady: Boolean(config.fish.apiKey),
        fishVoiceId: config.fish.voiceId || null,
        searchReady: Boolean(config.tavily.apiKey),
        stt: sttEngine(),
        stats: memory.stats(),
      };
    }),

    r('POST', '/api/power/(?<action>on|off)', async ({ params }) => {
      if (params.action === 'on') {
        if (!config.discordToken) throw Object.assign(new Error('DISCORD_TOKEN is empty in .env'), { status: 400 });
        await bot.start();
      } else {
        await bot.stop();
      }
      return { status: bot.status };
    }),

    // Every voice channel the bot can see, with who's in it and whether it's allowed for /join.
    r('GET', '/api/voice-channels', () => {
      const client = online();
      if (!client) return [];
      const allowed = config.voice.channelIds;
      return [...client.guilds.cache.values()].flatMap((g) =>
        g.channels.cache
          .filter((c) => c.type === ChannelType.GuildVoice || c.type === ChannelType.GuildStageVoice)
          .sort((a, b) => a.rawPosition - b.rawPosition)
          .map((c) => ({
            id: c.id,
            name: c.name,
            guildId: g.id,
            guild: g.name,
            members: c.members.filter((m) => !m.user.bot).map((m) => m.displayName),
            allowed: !allowed.length || allowed.includes(c.id),
            joined: sessions.get(g.id)?.channelId === c.id,
          })),
      );
    }),

    r('POST', '/api/voice/join', async ({ body }) => {
      const client = need();
      const channel = client.channels.cache.get(String(body.channelId ?? ''));
      if (!channel?.isVoiceBased()) throw Object.assign(new Error('Unknown voice channel'), { status: 404 });
      if (!config.fish.apiKey) throw Object.assign(new Error('Voice needs FISH_API_KEY in .env'), { status: 400 });
      // Transcripts go to the voice channel's built-in text chat.
      await joinChannel(channel, channel);
      return { ok: true };
    }),

    // Live activity feed (Server-Sent Events).
    r('GET', '/api/events', ({ req, res }) => {
      res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' });
      for (const e of recentActivity) res.write(`data: ${JSON.stringify(e)}\n\n`);
      const onEvent = (e) => res.write(`data: ${JSON.stringify(e)}\n\n`);
      const keepAlive = setInterval(() => res.write(': ping\n\n'), 25_000);
      bus.on('activity', onEvent);
      req.on('close', () => {
        clearInterval(keepAlive);
        bus.off('activity', onEvent);
      });
      return undefined;
    }),

    r('GET', '/api/settings', () => getSettings()),
    r('PUT', '/api/settings', ({ body }) => {
      const { persona, botName, ...rest } = body;
      setPersona({ name: botName, persona, reason: 'Edited in dashboard', changedBy: 'dashboard' });
      if (Object.keys(rest).length) {
        updateSettings(rest);
        activity('settings', { message: `Updated ${Object.keys(rest).join(', ')}` });
      }
      return getSettings();
    }),
    r('GET', '/api/persona/history', () => memory.personaHistory()),
    r('POST', '/api/persona/history/(?<id>\\d+)/restore', ({ params }) => {
      const version = memory.personaVersion(Number(params.id));
      if (!version) throw Object.assign(new Error('No such version'), { status: 404 });
      setPersona({
        name: version.name,
        persona: version.persona,
        reason: `Restored version from ${new Date(version.created_at).toLocaleString()}`,
        changedBy: 'dashboard',
      });
      return getSettings();
    }),
    r('GET', '/api/models', () => listModels()),
    r('GET', '/api/channels', () =>
      [...(online()?.guilds.cache.values() ?? [])]
        .flatMap((g) =>
          g.channels.cache
            .filter((c) => c.type === ChannelType.GuildText)
            .map((c) => ({ id: c.id, name: c.name, guild: g.name, position: c.rawPosition })),
        )
        .sort((a, b) => a.guild.localeCompare(b.guild) || a.position - b.position),
    ),

    r('GET', '/api/memories/users', () => memory.memoryUsers()),
    r('GET', '/api/memories/(?<userId>\\w+)', ({ params }) => memory.listMemories(params.userId)),
    r('POST', '/api/memories/(?<userId>\\w+)', async ({ params, body }) => {
      if (!body.content?.trim()) throw Object.assign(new Error('content is required'), { status: 400 });
      await memory.addMemoryManually(params.userId, body.content.trim());
      return memory.listMemories(params.userId);
    }),
    r('DELETE', '/api/memories/(?<userId>\\w+)/(?<id>\\d+)', ({ params }) => ({
      removed: memory.forget(params.userId, Number(params.id)),
    })),
    r('DELETE', '/api/memories/(?<userId>\\w+)', ({ params }) => ({ removed: memory.forget(params.userId) })),

    r('GET', '/api/conversations', () =>
      memory.conversations().map((c) => ({ ...c, label: conversationLabel(online(), c.conversation) })),
    ),
    r('GET', '/api/conversations/(?<id>[\\w:]+)', ({ params }) => memory.recentMessages(params.id, 200)),
    r('DELETE', '/api/conversations/(?<id>[\\w:]+)', ({ params }) => {
      memory.clearConversation(params.id);
      return { ok: true };
    }),

    r('POST', '/api/voice/(?<guildId>\\d+)/leave', ({ params }) => ({ left: leaveChannel(params.guildId) })),
    r('POST', '/api/voice/(?<guildId>\\d+)/say', ({ params, body }) => {
      const session = sessions.get(params.guildId);
      if (!session) throw Object.assign(new Error('Not in voice in that server'), { status: 404 });
      if (!body.text?.trim()) throw Object.assign(new Error('text is required'), { status: 400 });
      session.say(body.text.trim());
      activity('replied', { conversation: session.conversation, voice: true, text: body.text.trim(), manual: true });
      return { ok: true };
    }),

    // Talk to the companion straight from the dashboard (uses the same brain and memory).
    r('POST', '/api/chat', async ({ body }) => {
      if (!body.text?.trim()) throw Object.assign(new Error('text is required'), { status: 400 });
      const reply = await respond({
        conversation: 'dashboard',
        speaker: { id: 'dashboard', name: body.name?.trim() || 'Dashboard' },
        text: body.text.trim(),
      });
      return { reply };
    }),

    // ---- Avatar studio (/avatar.html) ----

    r('GET', '/api/avatar/model', async ({ res }) => {
      try {
        const data = await readFile(config.avatar.path);
        res.writeHead(200, { 'Content-Type': 'model/gltf-binary', 'Cache-Control': 'no-cache' });
        res.end(data);
      } catch {
        send(res, 404, { error: `No avatar at ${config.avatar.path}. Set AVATAR_PATH in .env.` });
      }
    }),

    r('GET', '/api/avatar/status', () => ({
      botName: config.botName,
      model: config.ollama.model,
      fishReady: Boolean(config.fish.apiKey),
      fishVoiceId: config.fish.voiceId || null,
      stt: sttEngine(),
    })),

    // Her current feelings. POST { feelings: { name: 0..1 } } nudges them, { reset: true } calms her.
    r('GET', '/api/avatar/emotion', () => ({ feelings: emotion.current('avatar'), emotions: emotion.EMOTIONS, gestures: emotion.GESTURES })),
    r('POST', '/api/avatar/emotion', ({ body }) => {
      if (body.reset) return { feelings: emotion.reset('avatar') };
      const feelings = {};
      for (const [name, v] of Object.entries(body.feelings ?? {})) {
        if (emotion.EMOTIONS[name]) feelings[name] = Math.max(0, Math.min(1, Number(v) || 0));
      }
      return { feelings: emotion.nudge('avatar', feelings) };
    }),

    // Push-to-talk: raw 48kHz mono 16-bit PCM in, transcript out.
    r('POST', '/api/avatar/listen', async ({ req }) => {
      const chunks = [];
      let size = 0;
      for await (const chunk of req) {
        size += chunk.length;
        if (size > 12e6) throw Object.assign(new Error('Recording too long'), { status: 413 });
        chunks.push(chunk);
      }
      const pcm = Buffer.concat(chunks);
      if (pcm.length < 9600) return { text: '' }; // under 0.1s
      return { text: (await transcribeSpeech(pcm)) || '' };
    }),

    // Streams the reply as NDJSON, one line per sentence with its Fish Audio clip (base64 mp3),
    // so the avatar starts talking while the rest is still being generated.
    r('POST', '/api/avatar/chat', async ({ req, res, body }) => {
      if (!body.text?.trim()) throw Object.assign(new Error('text is required'), { status: 400 });
      res.writeHead(200, { 'Content-Type': 'application/x-ndjson', 'Cache-Control': 'no-cache' });
      let closed = false;
      res.on('close', () => (closed = true));
      const write = (obj) => !closed && res.write(JSON.stringify(obj) + '\n');

      const speak = body.speak !== false && Boolean(config.fish.apiKey);
      const text = body.text.trim();

      // Her first reaction, shown on her face while she thinks.
      const first = emotion.guessFromUser(text);
      write({ type: 'state', feelings: first ? emotion.nudge('avatar', first) : emotion.current('avatar') });

      // Each sentence: strip its feeling tag, fold it into her state, then fetch the clip.
      // Clips are fetched in parallel but sent in sentence order.
      let queue = Promise.resolve();
      const onSentence = (raw) => {
        const parsed = emotion.parseSentence(raw);
        const named = parsed.feelings ?? (parsed.text && emotion.guessFromReply(parsed.text));
        const feelings = parsed.feelings
          ? emotion.feel('avatar', named)
          : named ? emotion.nudge('avatar', named) : emotion.current('avatar');
        const clip = speak && !closed && parsed.text
          ? synthesize(parsed.text, { format: 'mp3' }).catch((err) => {
              write({ type: 'error', message: err.message });
              return null;
            })
          : null;
        queue = queue.then(async () => {
          const audio = await clip;
          write({ type: 'sentence', text: parsed.text, audio: audio ? audio.toString('base64') : null, feelings, gestures: parsed.gestures });
        });
      };

      try {
        const reply = await respond({
          conversation: 'avatar',
          speaker: { id: 'dashboard', name: body.name?.trim() || 'Dashboard' },
          text,
          voice: true,
          onSentence,
          systemExtra: emotion.emotionPrompt('avatar'),
        });
        await queue;
        write({ type: 'done', reply });
      } catch (err) {
        await queue;
        write({ type: 'error', message: err.message });
      }
      res.end();
      return undefined;
    }),
  ];
}

function conversationLabel(client, id) {
  if (id === 'dashboard') return 'Dashboard playground';
  if (id === 'avatar') return 'Avatar studio';
  const [kind, ref] = id.split(':');
  if (kind === 'voice') return `🔊 ${client?.guilds.cache.get(ref)?.name ?? ref}`;
  const channel = client?.channels.cache.get(ref);
  if (!channel) return `# ${ref}`;
  if (channel.type === ChannelType.DM) return `DM · ${channel.recipient?.displayName ?? 'user'}`;
  return `#${channel.name} · ${channel.guild?.name ?? ''}`;
}

function authorized(req) {
  const password = config.dashboard.password;
  if (!password) return true;
  const header = req.headers.authorization ?? '';
  if (!header.startsWith('Basic ')) return false;
  const given = Buffer.from(header.slice(6), 'base64').toString().split(':').slice(1).join(':');
  const a = Buffer.from(given);
  const b = Buffer.from(password);
  return a.length === b.length && timingSafeEqual(a, b);
}

const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' };

async function serveStatic(res, pathname) {
  const file = path.normalize(path.join(PUBLIC, pathname === '/' ? 'index.html' : pathname));
  if (!file.startsWith(PUBLIC)) return send(res, 403, { error: 'Forbidden' });
  try {
    const data = await readFile(file);
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] ?? 'application/octet-stream' });
    res.end(data);
  } catch {
    send(res, 404, { error: 'Not found' });
  }
}

function send(res, status, data) {
  if (res.headersSent) return;
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(data));
}

async function readJson(req) {
  let raw = '';
  for await (const chunk of req) {
    raw += chunk;
    if (raw.length > 1e6) throw Object.assign(new Error('Body too large'), { status: 413 });
  }
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch {
    throw Object.assign(new Error('Invalid JSON'), { status: 400 });
  }
}
