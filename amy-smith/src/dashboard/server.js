import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { timingSafeEqual } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ChannelType } from 'discord.js';
import { config, getSettings, updateSettings } from '../config.js';
import { bus, recentActivity, activity } from '../events.js';
import { listModels, openRouterModels } from '../llm.js';
import { respond } from '../companion.js';
import { setPersona } from '../persona.js';
import * as memory from '../memory.js';
import { sessions, leaveChannel, joinChannel } from '../voice/session.js';
import { sttEngine, transcribeSpeech } from '../voice/stt.js';
import { synthesize } from '../voice/fishAudio.js';
import * as emotion from '../emotion.js';
import * as characters from '../characters.js';
import * as music from '../music.js';
import * as video from '../avatarVideo.js';
import * as pressure from '../pressure.js';

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
    r('GET', '/api/openrouter/models', () => openRouterModels()),
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

    // The cast: everyone who can appear in the studio, with their name, persona, voice and mood.
    r('GET', '/api/avatar/characters', () =>
      characters.list().map(({ modelPath, ...c }) => ({ ...c, feelings: emotion.current(characters.emotionKey(c.id)) })),
    ),
    r('PUT', '/api/avatar/characters/(?<id>[\\w-]+)', ({ params, body }) => {
      const updated = characters.update(params.id, body);
      if (!updated) throw Object.assign(new Error('No such character'), { status: 404 });
      return updated;
    }),

    r('GET', '/api/avatar/model(?:/(?<id>[\\w-]+))?', async ({ res, params }) => {
      const c = characters.get(params.id ?? 'main');
      if (!c) return send(res, 404, { error: 'No such character' });
      try {
        const data = await readFile(c.modelPath);
        res.writeHead(200, { 'Content-Type': 'model/gltf-binary', 'Cache-Control': 'no-cache' });
        res.end(data);
      } catch {
        send(res, 404, { error: `No model for ${c.name} at ${c.modelPath}` });
      }
    }),

    // Songs from the music library, for the studio's player. Only plain file names in data/music.
    r('GET', '/api/music/(?<file>[^/]+)', async ({ res, params }) => {
      const file = decodeURIComponent(params.file);
      if (file.includes('..') || /[\\/]/.test(file) || !music.tracks().some((t) => t.file === file)) return send(res, 404, { error: 'No such song' });
      const type = { mp3: 'audio/mpeg', wav: 'audio/wav', ogg: 'audio/ogg', opus: 'audio/ogg', m4a: 'audio/mp4', aac: 'audio/aac', flac: 'audio/flac', webm: 'audio/webm' };
      res.writeHead(200, { 'Content-Type': type[path.extname(file).slice(1).toLowerCase()] ?? 'application/octet-stream' });
      res.end(await readFile(path.join(music.MUSIC_DIR, file)));
    }),
    r('GET', '/api/music', () => ({ tracks: music.tracks(), playlists: music.playlists() })),
    r('GET', '/api/pressure', () => pressure.snapshot()),

    // Video replies: the recorder page's event stream, its "recording started", and the clip itself.
    r('GET', '/api/avatar/recorder', ({ req, res }) => {
      video.attachRecorder(req, res);
      return undefined;
    }),
    // Records a test line through the recorder page and returns the clip, to check it works.
    r('POST', '/api/avatar/recorder/test', async ({ res, body }) => {
      if (!video.recorderConnected()) throw Object.assign(new Error('No recorder page is open (/avatar.html?recorder=1)'), { status: 409 });
      const clip = await video.requestClip(String(body.text || "Hey, it's me. This is a test of my video replies."));
      if (!clip) throw Object.assign(new Error('No clip came back; see the activity feed for why'), { status: 502 });
      res.writeHead(200, { 'Content-Type': 'video/webm', 'Content-Length': clip.length });
      res.end(clip);
      return undefined;
    }),
    r('POST', '/api/avatar/recorder/(?<id>\\d+)/ack', ({ params }) => ({ ok: video.ack(params.id) })),
    r('POST', '/api/avatar/recorder/(?<id>\\d+)/failed', ({ params, body }) => ({ ok: video.finish(params.id, null, String(body.reason ?? 'the page reported a failure').slice(0, 200)) })),
    r('POST', '/api/avatar/recorder/(?<id>\\d+)', async ({ req, params }) => {
      const chunks = [];
      let size = 0;
      for await (const chunk of req) {
        size += chunk.length;
        if (size > video.MAX_CLIP_BYTES + 1) break; // finish() turns oversized clips into text-only
        chunks.push(chunk);
      }
      return { ok: video.finish(params.id, Buffer.concat(chunks)) };
    }),

    r('GET', '/api/avatar/status', () => ({
      botName: config.botName,
      model: config.ollama.model,
      fishReady: Boolean(config.fish.apiKey),
      fishVoiceId: config.fish.voiceId || null,
      stt: sttEngine(),
    })),

    // A character's current feelings (?character=id, default the main one).
    // POST { character, feelings: { name: 0..1 } } nudges them, { character, reset: true } calms them.
    r('GET', '/api/avatar/emotion', ({ url }) => ({
      feelings: emotion.current(characters.emotionKey(url.searchParams.get('character') || 'main')),
      emotions: emotion.EMOTIONS,
      gestures: emotion.GESTURES,
    })),
    r('POST', '/api/avatar/emotion', ({ body }) => {
      const key = characters.emotionKey(characters.get(body.character)?.id ?? 'main');
      if (body.reset) return { feelings: emotion.reset(key) };
      const feelings = {};
      for (const [name, v] of Object.entries(body.feelings ?? {})) {
        if (emotion.EMOTIONS[name]) feelings[name] = Math.max(0, Math.min(1, Number(v) || 0));
      }
      return { feelings: emotion.nudge(key, feelings) };
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
      // auto: banter mode. Nobody spoke; body.character takes the next turn in the cast's conversation.
      const auto = Boolean(body.auto);
      if (!auto && !body.text?.trim()) throw Object.assign(new Error('text is required'), { status: 400 });
      if (auto && !characters.get(body.character)) throw Object.assign(new Error('Unknown character'), { status: 400 });
      res.writeHead(200, { 'Content-Type': 'application/x-ndjson', 'Cache-Control': 'no-cache' });
      let closed = false;
      res.on('close', () => (closed = true));
      const write = (obj) => !closed && res.write(JSON.stringify(obj) + '\n');

      const text = auto ? '' : body.text.trim();
      const person = body.name?.trim() || 'Dashboard';

      // Whoever is named answers ("Leo, ..."); otherwise whoever the person was talking to.
      const character = characters.get(auto ? body.character : characters.addressee(text, body.character));
      const key = characters.emotionKey(character.id);
      const others = characters.list().filter((c) => c.id !== character.id).map((c) => c.name);
      write({ type: 'speaker', character: character.id });

      // Only the main character may fall back to Fish's default voice; without a voice of their
      // own, the others would get a random voice for every sentence, so they stay silent instead.
      const voiceId = character.voiceId || (character.main ? undefined : null);
      const speak = body.speak !== false && Boolean(config.fish.apiKey) && voiceId !== null;
      if (body.speak !== false && voiceId === null) {
        write({ type: 'notice', message: `${character.name} has no voice yet. Set a Fish voice ID for them in Settings.` });
      }

      // In banter, the cue says whose line to answer; nobody's words are invented for the person.
      let cue;
      if (auto) {
        const last = memory.recentMessages('avatar', 1)[0];
        const lastName = last ? (last.role === 'assistant' ? last.name ?? config.botName : last.name) : null;
        cue =
          lastName && lastName !== character.name
            ? `${person} is listening quietly. It's your turn: reply to what ${lastName} just said, in character, talking with ${others.join(' and ')}. ` +
              `Keep it to one to three sentences. Don't repeat yourself or restate what was said; push the conversation forward, and now and then change the subject.`
            : `${person} is listening quietly. Start a conversation with ${others.join(' and ')} about anything that interests you, in character, in one to three sentences.`;
      }

      // Their first reaction, shown on their face while they think.
      const first = auto ? null : emotion.guessFromUser(text);
      write({ type: 'state', feelings: first ? emotion.nudge(key, first) : emotion.current(key) });

      // Each sentence: strip its feeling tag, fold it into her state, then fetch the clip.
      // Clips are fetched in parallel but sent in sentence order.
      let queue = Promise.resolve();
      const onSentence = (raw) => {
        const parsed = emotion.parseSentence(raw);
        const named = parsed.feelings ?? (parsed.text && emotion.guessFromReply(parsed.text));
        const feelings = parsed.feelings
          ? emotion.feel(key, named)
          : named ? emotion.nudge(key, named) : emotion.current(key);
        const clip = speak && !closed && parsed.text
          ? synthesize(parsed.text, { format: 'mp3', voiceId }).catch((err) => {
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
          speaker: { id: 'dashboard', name: person },
          text,
          voice: true,
          onSentence,
          character,
          cue,
          // The studio is "here": files and pictures appear in the chat. With the bot online,
          // she can also send files to Discord channels or DMs, since the dashboard is the owner.
          discord: {
            client: online(),
            showImage: (image) => write({ type: 'image', ...image }),
            showAudio: (track) => write({ type: 'audio', ...track }),
            studioMusic: (command) => write({ type: 'music', ...command }),
            sendHere: (file) => write({ type: 'file', name: file.name, base64: Buffer.from(file.attachment).toString('base64') }),
          },
          systemExtra: [
            others.length &&
              `${others.join(' and ')} ${others.length > 1 ? 'are' : 'is'} here too, in the same conversation. Their lines appear as "[Name]: ...". ` +
                `You can talk to them, react to them or tease them, but speak only as yourself, ${character.name}: never write their lines.`,
            emotion.emotionPrompt(key),
          ].filter(Boolean).join('\n\n'),
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
