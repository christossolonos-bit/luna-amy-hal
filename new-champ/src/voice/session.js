import {
  joinVoiceChannel,
  createAudioPlayer,
  createAudioResource,
  entersState,
  AudioPlayerStatus,
  VoiceConnectionStatus,
  EndBehaviorType,
  NoSubscriberBehavior,
  StreamType,
} from '@discordjs/voice';
import OpusScript from 'opusscript';
import { config } from '../config.js';
import { respond } from '../companion.js';
import { addMessage } from '../memory.js';
import { activity } from '../events.js';
import { synthesize, audioStream } from './fishAudio.js';
import { transcribeSpeech } from './stt.js';

const SAMPLE_RATE = 48000;
const FRAME_MS = 20; // Discord sends one Opus packet per 20ms

/** guildId -> VoiceSession */
export const sessions = new Map();

export async function joinChannel(voiceChannel, textChannel) {
  const existing = sessions.get(voiceChannel.guild.id);
  if (existing?.channelId === voiceChannel.id) return existing;
  existing?.destroy();

  const connection = joinVoiceChannel({
    channelId: voiceChannel.id,
    guildId: voiceChannel.guild.id,
    adapterCreator: voiceChannel.guild.voiceAdapterCreator,
    selfDeaf: false, // must be undeafened to hear people
    selfMute: false,
  });
  try {
    await entersState(connection, VoiceConnectionStatus.Ready, 20_000);
  } catch (err) {
    connection.destroy();
    throw new Error('Could not connect to the voice channel within 20s');
  }

  const session = new VoiceSession(connection, voiceChannel, textChannel);
  sessions.set(voiceChannel.guild.id, session);
  activity('voice-join', { guild: voiceChannel.guild.name, channel: voiceChannel.name });
  return session;
}

export function leaveChannel(guildId) {
  const session = sessions.get(guildId);
  if (!session) return false;
  session.destroy();
  return true;
}

class VoiceSession {
  constructor(connection, voiceChannel, textChannel) {
    this.connection = connection;
    this.guild = voiceChannel.guild;
    this.channelId = voiceChannel.id;
    this.channelName = voiceChannel.name;
    this.textChannel = textChannel;
    this.conversation = `voice:${this.guild.id}`;
    this.startedAt = Date.now();

    this.listening = new Set(); // userIds with an open audio stream
    this.speech = []; // queued { turn, resource: Promise<AudioResource> }
    this.turn = 0; // bumped on barge-in so stale sentences are dropped
    this.pumping = false;
    this.replyChain = Promise.resolve();

    this.player = createAudioPlayer({ behaviors: { noSubscriber: NoSubscriberBehavior.Pause } });
    this.player.on('error', (err) => activity('error', { where: 'voice playback', message: err.message }));
    connection.subscribe(this.player);

    connection.receiver.speaking.on('start', (userId) => this.listen(userId));

    connection.on(VoiceConnectionStatus.Disconnected, async () => {
      try {
        // Moving channels looks like a disconnect followed by a reconnect.
        await Promise.race([
          entersState(connection, VoiceConnectionStatus.Signalling, 5_000),
          entersState(connection, VoiceConnectionStatus.Connecting, 5_000),
        ]);
      } catch {
        this.destroy();
      }
    });
  }

  get speaking() {
    return this.player.state.status !== AudioPlayerStatus.Idle;
  }

  listen(userId) {
    if (this.listening.has(userId)) return;
    const member = this.guild.members.cache.get(userId);
    if (member?.user.bot) return;
    this.listening.add(userId);

    const stream = this.connection.receiver.subscribe(userId, {
      end: { behavior: EndBehaviorType.AfterSilence, duration: config.voice.silenceMs },
    });
    const decoder = new OpusScript(SAMPLE_RATE, 2, OpusScript.Application.VOIP);
    const pcm = [];
    let frames = 0;
    const bargeInFrames = Math.ceil(config.voice.minSpeechMs / FRAME_MS);

    stream.on('data', (packet) => {
      try {
        pcm.push(toMono(decoder.decode(packet)));
      } catch {
        return; // skip a corrupt packet rather than dropping the utterance
      }
      // Only interrupt once they've clearly started talking, not on a cough.
      if (++frames === bargeInFrames && config.voice.bargeIn && this.speaking) this.interrupt();
    });

    stream.once('end', () => {
      this.listening.delete(userId);
      decoder.delete();
      if (frames * FRAME_MS < config.voice.minSpeechMs) return;
      this.handleUtterance(userId, Buffer.concat(pcm)).catch((err) =>
        activity('error', { where: 'voice reply', message: err.message }),
      );
    });
    stream.once('error', () => this.listening.delete(userId));
  }

  async handleUtterance(userId, pcm) {
    const member = this.guild.members.cache.get(userId) ?? (await this.guild.members.fetch(userId).catch(() => null));
    const speaker = { id: userId, name: member?.displayName ?? 'Someone' };

    const text = await transcribeSpeech(pcm);
    if (!text) return;
    if (config.voice.postTranscripts) this.post(`🎙️ **${speaker.name}:** ${text}`);

    if (config.voice.wakeWord && !text.toLowerCase().includes(config.voice.wakeWord)) {
      // Not addressed to us, but keep it as context for the next time we are.
      addMessage(this.conversation, { role: 'user', userId, name: speaker.name, content: text });
      activity('heard', { conversation: this.conversation, voice: true, speaker: speaker.name, userId, text, ignored: true });
      return;
    }

    // One reply at a time, in the order people spoke.
    this.replyChain = this.replyChain.then(async () => {
      const turn = ++this.turn;
      const reply = await respond({
        conversation: this.conversation,
        speaker,
        text,
        voice: true,
        onSentence: (sentence) => {
          if (turn === this.turn) this.say(sentence, turn);
        },
      });
      if (reply && config.voice.postTranscripts) this.post(`🔊 ${reply}`);
    }).catch((err) => activity('error', { where: 'voice reply', message: err.message }));
  }

  // Queues a line of speech. TTS downloads right away so the next sentence is ready when this one ends.
  say(text, turn = this.turn) {
    const clip = synthesize(text);
    clip.catch(() => {}); // surfaced in pump()
    this.speech.push({ turn, clip });
    this.pump();
  }

  async pump() {
    if (this.pumping) return;
    this.pumping = true;
    try {
      while (this.speech.length) {
        const item = this.speech.shift();
        if (item.turn !== this.turn) continue;
        try {
          // Build the audio resource only now, from the downloaded clip, so it's fresh when it plays.
          const clip = await item.clip;
          if (!isOggOpus48k(clip)) throw new Error('Fish Audio returned audio in an unexpected format');
          if (item.turn !== this.turn) continue;
          this.player.play(createAudioResource(audioStream(clip), { inputType: StreamType.OggOpus }));
        } catch (err) {
          activity('error', { where: 'text-to-speech', message: err.message });
          continue;
        }
        await entersState(this.player, AudioPlayerStatus.Idle, 5 * 60_000).catch(() => this.player.stop(true));
      }
    } finally {
      this.pumping = false;
    }
  }

  // Stop talking and drop anything still queued (someone started speaking over us).
  interrupt({ quiet = false } = {}) {
    this.turn++;
    this.speech.length = 0;
    this.player.stop(true);
    if (!quiet) activity('barge-in', { guild: this.guild.name });
  }

  post(content) {
    this.textChannel?.send({ content: content.slice(0, 2000), allowedMentions: { parse: [] } }).catch(() => {});
  }

  destroy() {
    if (sessions.get(this.guild.id) === this) sessions.delete(this.guild.id);
    this.interrupt({ quiet: true });
    this.player.removeAllListeners();
    if (this.connection.state.status !== VoiceConnectionStatus.Destroyed) this.connection.destroy();
    activity('voice-leave', { guild: this.guild.name, channel: this.channelName });
  }

  summary() {
    const channel = this.guild.channels.cache.get(this.channelId);
    return {
      guildId: this.guild.id,
      guild: this.guild.name,
      channel: this.channelName,
      startedAt: this.startedAt,
      speaking: this.speaking,
      listeningTo: [...this.listening].map((id) => this.guild.members.cache.get(id)?.displayName ?? id),
      members: channel?.members?.filter((m) => !m.user.bot).map((m) => m.displayName) ?? [],
    };
  }
}

// Checks a clip is Ogg Opus at 48kHz. Fish sends mono, which Discord plays fine on its stereo stream.
function isOggOpus48k(clip) {
  const head = clip.indexOf('OpusHead');
  if (clip.toString('latin1', 0, 4) !== 'OggS' || head < 0 || clip.length < head + 16) return false;
  const channels = clip.readUInt8(head + 9);
  return (channels === 1 || channels === 2) && clip.readUInt32LE(head + 12) === SAMPLE_RATE;
}

// Interleaved 16-bit stereo -> mono.
function toMono(stereo) {
  const out = Buffer.alloc(stereo.length / 2);
  for (let i = 0, o = 0; i < stereo.length; i += 4, o += 2) {
    out.writeInt16LE((stereo.readInt16LE(i) + stereo.readInt16LE(i + 2)) >> 1, o);
  }
  return out;
}
