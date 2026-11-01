// HalMini Discord sidecar.
// Owns the Discord connection: gateway, text commands, joining voice (DAVE E2EE handled by
// @discordjs/voice + @snazzah/davey), per-speaker receive, Opus decode, and playback.
// It does no language work: utterances go to the Python engine as PCM; replies come back as text + PCM.

import { Client, Events, GatewayIntentBits, Partials } from "discord.js";
import {
  AudioPlayerStatus, EndBehaviorType, NoSubscriberBehavior, StreamType, VoiceConnectionStatus,
  createAudioPlayer, createAudioResource, entersState, joinVoiceChannel,
} from "@discordjs/voice";
import prism from "prism-media";
import readline from "node:readline";
import { Readable } from "node:stream";
import { chunkMessage, downsampleForStt, log, send } from "./protocol.js";

const env = process.env;
const PREFIX = env.HALMINI_PREFIX ?? "!";
const SILENCE_MS = Number(env.HALMINI_SILENCE_MS ?? 900);        // end of utterance
const MIN_UTTERANCE_MS = Number(env.HALMINI_MIN_UTTERANCE_MS ?? 400);
const MAX_UTTERANCE_MS = Number(env.HALMINI_MAX_UTTERANCE_MS ?? 15000);
const BARGE_IN = (env.HALMINI_BARGE_IN ?? "false") === "true";   // talking over the bot stops it (off by default)
const LISTEN_TO_BOTS = (env.HALMINI_LISTEN_TO_BOTS ?? "true") === "true";
const TEXT_CHANNELS = new Set((env.HALMINI_TEXT_CHANNELS ?? "").split(",").filter(Boolean));
const BYTES_PER_MS = (48000 * 2 * 2) / 1000;                     // 48 kHz, stereo, 16-bit

const NOTICE =
  "🎙️ I'm in the voice channel and **transcribing speech locally** so I can answer. " +
  "Audio is not stored; transcripts are kept in my memory like typed messages. " +
  `Say \`${PREFIX}leave\` to make me go, or \`${PREFIX}listen off\` to stop listening.`;

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds, GatewayIntentBits.GuildVoiceStates, GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent, GatewayIntentBits.DirectMessages,
  ],
  partials: [Partials.Channel],
});

/** guildId -> { connection, player, textChannelId, voiceChannelId, listening, capturing:Set, queue:[] } */
const sessions = new Map();

// Play replies one after another. Nothing replaces a reply that is already being spoken, so every
// reply is heard in full; see BARGE_IN above for the opt-in exception.
function playNext(session) {
  const next = session.queue.shift();
  if (!next) return;
  session.player.play(next.resource);
  send({ type: "playback", guild: next.guild, state: "started" });
}

function enqueue(session, guild, pcm) {
  const resource = createAudioResource(Readable.from([pcm]), {
    inputType: StreamType.Raw,  // 48 kHz stereo s16le, encoded to Opus by @discordjs/voice
  });
  session.queue.push({ guild, resource });
  if (session.player.state.status === AudioPlayerStatus.Idle) playNext(session);
}

async function displayName(guild, userId) {
  try {
    const member = await guild.members.fetch(userId);
    return { name: member.displayName, bot: member.user.bot };
  } catch {
    return { name: `user-${userId}`, bot: false };
  }
}

function capture(session, guild, userId) {
  if (session.capturing.has(userId)) return;
  session.capturing.add(userId);
  const opus = session.connection.receiver.subscribe(userId, {
    end: { behavior: EndBehaviorType.AfterSilence, duration: SILENCE_MS },
  });
  const decoder = new prism.opus.Decoder({ rate: 48000, channels: 2, frameSize: 960 });
  const chunks = [];
  let bytes = 0;
  const maxBytes = MAX_UTTERANCE_MS * BYTES_PER_MS;
  const started = Date.now();
  let finished = false;

  const finish = async () => {
    if (finished) return;
    finished = true;
    session.capturing.delete(userId);
    const durationMs = Math.round(bytes / BYTES_PER_MS);
    if (durationMs < MIN_UTTERANCE_MS || !session.listening) return;
    const who = await displayName(guild, userId);
    if (who.bot && !LISTEN_TO_BOTS) return;
    const pcm = downsampleForStt(Buffer.concat(chunks));
    send({
      type: "utterance", guild: guild.id, textChannel: session.textChannelId,
      user: { id: userId, name: who.name, bot: who.bot },
      durationMs, startedAt: started, pcm: pcm.toString("base64"),
    });
  };

  opus.pipe(decoder);
  decoder.on("data", (chunk) => {
    const remaining = Math.max(0, maxBytes - bytes);
    const bounded = chunk.subarray(0, remaining);
    chunks.push(bounded);
    bytes += bounded.length;
    if (bytes >= maxBytes) {
      finish();
      opus.destroy();
      decoder.destroy();
    }
  });
  decoder.on("end", finish);
  decoder.on("close", finish);
  opus.on("error", (err) => { log("receive error", err.message); finish(); });
  decoder.on("error", (err) => { log("decode error", err.message); finish(); });
}

async function join(guild, voiceChannel, textChannel) {
  leave(guild.id, false);
  const connection = joinVoiceChannel({
    channelId: voiceChannel.id, guildId: guild.id, adapterCreator: guild.voiceAdapterCreator,
    selfDeaf: false, selfMute: false,
  });
  await entersState(connection, VoiceConnectionStatus.Ready, 20_000);
  const player = createAudioPlayer({ behaviors: { noSubscriber: NoSubscriberBehavior.Pause } });
  connection.subscribe(player);
  const session = {
    connection, player, textChannelId: textChannel?.id ?? null, voiceChannelId: voiceChannel.id,
    listening: true, capturing: new Set(), queue: [],
  };
  sessions.set(guild.id, session);

  player.on("stateChange", (before, after) => {
    if (after.status !== AudioPlayerStatus.Idle) return;
    if (session.queue.length) return playNext(session);  // keep speaking, uninterrupted
    if (before.status !== AudioPlayerStatus.Idle) {
      send({ type: "playback", guild: guild.id, state: "finished" });
    }
  });
  player.on("error", (err) => log("player error", err.message));

  connection.receiver.speaking.on("start", (userId) => {
    if (userId === client.user.id) return;  // never transcribe our own voice
    if (BARGE_IN && session.player.state.status === AudioPlayerStatus.Playing) {
      session.queue.length = 0;
      session.player.stop(true);
      send({ type: "playback", guild: guild.id, state: "interrupted", by: userId });
    }
    if (session.listening) capture(session, guild, userId);
  });
  connection.on(VoiceConnectionStatus.Disconnected, async () => {
    try {  // a brief disconnect while Discord moves us between servers is normal
      await Promise.race([
        entersState(connection, VoiceConnectionStatus.Signalling, 5_000),
        entersState(connection, VoiceConnectionStatus.Connecting, 5_000),
      ]);
    } catch {
      leave(guild.id);
    }
  });

  if (textChannel) await textChannel.send(NOTICE);
  send({ type: "joined", guild: guild.id, voiceChannel: voiceChannel.id, textChannel: session.textChannelId });
}

function leave(guildId, announce = true) {
  const session = sessions.get(guildId);
  if (!session) return false;
  session.queue.length = 0;
  session.player.stop(true);
  session.connection.destroy();
  sessions.delete(guildId);
  if (announce) send({ type: "left", guild: guildId });
  return true;
}

async function handleCommand(message, command, arg) {
  const guild = message.guild;
  if (command === "join") {
    const voice = message.member?.voice?.channel;
    if (!guild || !voice) return message.reply("Join a voice channel first, then ask me again.");
    try {
      await join(guild, voice, message.channel);
    } catch (err) {
      log("join failed", err);
      await message.reply("I couldn't connect to that voice channel.");
    }
    return;
  }
  if (command === "leave") {
    return message.reply(guild && leave(guild.id) ? "Left the voice channel." : "I'm not in a voice channel.");
  }
  if (command === "listen") {
    const session = guild && sessions.get(guild.id);
    if (!session) return message.reply("I'm not in a voice channel.");
    session.listening = arg !== "off";
    return message.reply(session.listening ? "Listening." : "Not listening; I'll still speak replies.");
  }
  if (command === "stop") {
    const session = guild && sessions.get(guild.id);
    if (session) {
      session.queue.length = 0;
      session.player.stop(true);
    }
    send({ type: "command", guild: guild?.id ?? null, channel: message.channel.id,
           author: { id: message.author.id, name: message.member?.displayName ?? message.author.username },
           command, arg });
    return;
  }
  // Anything else (e.g. !mode) is the engine's business.
  send({ type: "command", guild: guild?.id ?? null, channel: message.channel.id,
         author: { id: message.author.id, name: message.member?.displayName ?? message.author.username },
         command, arg });
}

client.on(Events.MessageCreate, async (message) => {
  if (message.author.id === client.user.id) return;
  const content = message.content.trim();
  if (content.startsWith(PREFIX)) {
    const [command, ...rest] = content.slice(PREFIX.length).trim().split(/\s+/);
    return handleCommand(message, command.toLowerCase(), rest.join(" ").toLowerCase());
  }
  const mentioned = message.mentions.users.has(client.user.id);
  const myNames = [env.HALMINI_NAME ?? "Adam", client.user.username, message.guild?.members.me?.displayName]
    .filter(Boolean).map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const named = new RegExp(`\\b(?:${myNames.join("|")})\\b`, "i").test(content);
  const addressed = mentioned || named || !message.guild || TEXT_CHANNELS.has(message.channel.id);
  if (!addressed || (message.author.bot && !LISTEN_TO_BOTS)) return;
  const text = content.replace(new RegExp(`<@!?${client.user.id}>`, "g"), "").trim();
  if (!text) return;
  send({
    type: "text", guild: message.guild?.id ?? null, channel: message.channel.id, messageId: message.id,
    author: { id: message.author.id, name: message.member?.displayName ?? message.author.username, bot: message.author.bot },
    content: text,
    // An @mention, a DM or his own channel is always for him; his name in a message is checked in Python
    // the same way as by voice, so "I'm Amy, not Adam" isn't mistaken for being spoken to.
    direct: mentioned || !message.guild || TEXT_CHANNELS.has(message.channel.id),
  });
});

async function onEngineMessage(msg) {
  if (msg.type === "say") {
    const channel = await client.channels.fetch(msg.channel).catch(() => null);
    if (!channel) return log("unknown channel", msg.channel);
    const parts = chunkMessage(msg.content);
    const original = msg.replyTo ? await channel.messages.fetch(msg.replyTo).catch(() => null) : null;
    for (const [i, part] of parts.entries()) {
      if (i === 0 && original) await original.reply(part);  // a reply pings the author (e.g. Amy) so she answers
      else await channel.send(part);
    }
  } else if (msg.type === "play") {
    const session = sessions.get(msg.guild);
    if (!session) return;
    enqueue(session, msg.guild, Buffer.from(msg.pcm, "base64"));
  } else if (msg.type === "stop") {
    const session = sessions.get(msg.guild);
    if (session) {
      session.queue.length = 0;
      session.player.stop(true);
    }
  } else if (msg.type === "leave") {
    leave(msg.guild);
  }
}

readline.createInterface({ input: process.stdin }).on("line", (line) => {
  if (!line.trim()) return;
  let msg;
  try { msg = JSON.parse(line); } catch { return log("bad line from engine", line.slice(0, 80)); }
  onEngineMessage(msg).catch((err) => log("engine message failed", err));
}).on("close", shutdown);

function shutdown() {
  for (const guildId of [...sessions.keys()]) leave(guildId, false);
  client.destroy().finally(() => process.exit(0));
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

client.once(Events.ClientReady, (c) => {
  log(`logged in as ${c.user.tag}`);
  send({ type: "ready", user: { id: c.user.id, name: c.user.username } });
});

if (!env.DISCORD_TOKEN) {
  send({ type: "error", message: "DISCORD_TOKEN is not set" });
  process.exit(1);
}
client.login(env.DISCORD_TOKEN);
