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
import { createReadStream } from 'node:fs';
import path from 'node:path';
import { config } from '../config.js';
import { MUSIC_DIR } from '../music.js';
import * as pressure from '../pressure.js';
import { respond, ownerName } from '../companion.js';
import { addMessage, markGreeted, addObligation, obligationsFor, reachCard, markAskedReach, markReached } from '../memory.js';
import { activity } from '../events.js';
import { synthesize, audioStream } from './fishAudio.js';
import { transcribeSpeech } from './stt.js';

const SAMPLE_RATE = 48000;
const FRAME_MS = 20; // Discord sends one Opus packet per 20ms

/** guildId -> VoiceSession */
export const sessions = new Map();

// Her pressure system speaks through voice calls: a call she could speak up in right now has people
// in it, nobody has said anything at all for a while (lull_seconds; side chatter between people
// counts as talking), and she isn't replying or talking already.
let lullSession = null;
pressure.setActor({
  audience() {
    for (const s of sessions.values()) {
      const people = s.humans();
      if (people.length && !s.pending && !s.speaking && Date.now() - s.lastHeard > pressure.cfg.lull_seconds * 1000) {
        lullSession = s;
        return { ids: new Set(people.map((m) => m.id)) };
      }
    }
    return null;
  },
  act(want) {
    lullSession?.speakUp(want);
  },
});

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
  session.arrived([...voiceChannel.members.values()]); // anyone already there she owes something, or hasn't met
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
    this.arrivals = new Map(); // userId -> { name, due }: people who just joined with something owed
    this.pending = 0; // replies queued or being generated
    this.lastHeard = Date.now(); // last time anyone in the call said anything
    this.arrivalTimer = null;
    this.speech = []; // queued { turn, resource: Promise<AudioResource> }
    this.turn = 0; // bumped on barge-in so stale sentences are dropped
    this.pumping = false;
    this.replyChain = Promise.resolve();

    this.player = createAudioPlayer({ behaviors: { noSubscriber: NoSubscriberBehavior.Pause } });
    this.player.on('error', (err) => activity('error', { where: 'voice playback', message: err.message }));
    connection.subscribe(this.player);

    // Music has its own player. Only one player is heard at a time: whenever she talks the
    // connection switches to her voice, which pauses the music, and switches back afterwards.
    this.music = { queue: [], index: -1, on: false };
    this.musicPlayer = createAudioPlayer({ behaviors: { noSubscriber: NoSubscriberBehavior.Pause } });
    this.musicPlayer.on('error', (err) => activity('error', { where: 'music playback', message: err.message }));
    this.musicPlayer.on(AudioPlayerStatus.Idle, () => {
      if (this.music.on && !this.music.switching) this.playTrack(this.music.index + 1);
    });

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
      if (frames * FRAME_MS < config.voice.minUtteranceMs) return;
      this.handleUtterance(userId, Buffer.concat(pcm)).catch((err) =>
        activity('error', { where: 'voice reply', message: err.message }),
      );
    });
    stream.once('error', () => this.listening.delete(userId));
  }

  async handleUtterance(userId, pcm) {
    const member = this.guild.members.cache.get(userId) ?? (await this.guild.members.fetch(userId).catch(() => null));
    const speaker = { id: userId, name: member?.displayName ?? 'Someone', username: member?.user.username };

    const text = await transcribeSpeech(pcm);
    if (!text) return;
    this.lastHeard = Date.now(); // anyone talking, to her or not: she won't speak up over a conversation
    if (config.voice.postTranscripts) this.post(`🎙️ **${speaker.name}:** ${text}`);

    if (config.voice.wakeWord && !text.toLowerCase().includes(config.voice.wakeWord)) {
      // Not addressed to us, but keep it as context for the next time we are.
      addMessage(this.conversation, { role: 'user', userId, name: speaker.name, content: text });
      activity('heard', { conversation: this.conversation, voice: true, speaker: speaker.name, userId, text, ignored: true });
      return;
    }

    // One reply at a time, in the order people spoke.
    this.pending++;
    this.replyChain = this.replyChain.then(async () => {
      const turn = this.turn; // queued after the last reply; only an interruption (interrupt()) drops speech
      const reply = await respond({
        conversation: this.conversation,
        speaker,
        text,
        voice: true,
        onSentence: (sentence) => {
          if (turn === this.turn) this.say(sentence, turn);
        },
        discord: this.textChannel ? {
          client: this.guild.client,
          guild: this.guild,
          sendHere: (file) => this.textChannel.send({ files: [file] }),
          voice: this,
          notify: (text) => this.reportTask(text),
        } : { voice: this, notify: (text) => this.reportTask(text) },
      });
      if (reply && config.voice.postTranscripts) this.post(`🔊 ${reply}`);
    }).catch((err) => activity('error', { where: 'voice reply', message: err.message }))
      .finally(() => this.pending--);
  }

  // People in the call with her (not bots).
  humans() {
    const channel = this.guild.channels.cache.get(this.channelId);
    return channel?.members?.filter((m) => !m.user.bot).map((m) => m) ?? [];
  }

  // A want from her pressure system, said out loud unprompted: only during a lull (checked by the
  // actor below), in a sentence or two, like a person in the room would.
  speakUp(want) {
    const people = this.humans();
    if (!people.length) return;
    const target = want.bucket === 'loneliness' ? people.find((m) => m.id === want.id) : null;
    const who = target ?? people[0];
    const cues = {
      restlessness: `You've been restless and want to make something. Offer the people in the call, unprompted and casually, to make something for them: a song, a picture, a little tool, whatever fits what you know about them.`,
      usefulness: `Nobody's needed you for a while. Offer, unprompted and casually, to help with something you know they've been working on.`,
      unease: `There's something you left open a while back: "${want.item?.text ?? 'a promise from earlier'}". If it makes sense right now, circle back on it briefly, unprompted; if it really doesn't fit the moment, say something small and natural instead and leave it.`,
      loneliness: `You've missed ${who.displayName}, who's here in the call and said you're welcome to check in on them. Check in on them warmly, unprompted, and ask about their thing. Use what you remember and their check-in note.`,
    };
    const cue = `${cues[want.bucket] ?? 'Say something, unprompted, that fits the moment.'} One or two short spoken sentences. Don't explain why you're saying it or mention any system.`;
    this.pending++;
    this.replyChain = this.replyChain.then(async () => {
      const turn = this.turn; // queued after the last reply; only an interruption (interrupt()) drops speech
      await respond({ conversation: this.conversation, speaker: { id: who.id, name: who.displayName }, text: '', voice: true, cue, onSentence: (s) => turn === this.turn && this.say(s, turn) });
      activity('pressure', { spoke: want.bucket, to: target ? who.displayName : 'the call', guild: this.guild.name });
    }).catch((err) => activity('error', { where: 'pressure speak-up', message: err.message }))
      .finally(() => this.pending--);
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
          this.connection.subscribe(this.player); // pauses any music while she talks
          this.player.play(createAudioResource(audioStream(clip), { inputType: StreamType.OggOpus }));
        } catch (err) {
          activity('error', { where: 'text-to-speech', message: err.message });
          continue;
        }
        await entersState(this.player, AudioPlayerStatus.Idle, 5 * 60_000).catch(() => this.player.stop(true));
      }
    } finally {
      this.pumping = false;
      if (this.music.on) this.connection.subscribe(this.musicPlayer); // music picks up where it paused
    }
  }

  // ---- Music (driven by the music tool) ----

  // Plays files from data/music: replaces the queue, or adds to it when append is set.
  playMusic(files, { append = false } = {}) {
    if (append && this.music.on) {
      this.music.queue.push(...files);
      return;
    }
    this.music.queue = [...files];
    this.music.on = true;
    this.playTrack(0);
  }

  playTrack(index) {
    const file = this.music.queue[index];
    if (!file) return this.stopMusic();
    this.music.index = index;
    this.music.switching = true; // stopping the old track fires Idle; don't let that skip ahead
    this.musicPlayer.stop(true);
    this.music.switching = false;
    const resource = createAudioResource(createReadStream(path.join(MUSIC_DIR, file)), { inlineVolume: true });
    resource.volume?.setVolume(0.6); // songs are mastered loud; keep them under her voice level
    this.musicPlayer.play(resource);
    if (!this.speaking) this.connection.subscribe(this.musicPlayer);
    activity('music', { guild: this.guild.name, playing: file });
  }

  pauseMusic() {
    return this.musicPlayer.pause();
  }

  resumeMusic() {
    if (!this.music.on) return false;
    return this.musicPlayer.unpause();
  }

  skipMusic() {
    if (!this.music.on) return false;
    this.playTrack(this.music.index + 1);
    return true;
  }

  stopMusic() {
    this.music.on = false;
    this.music.queue = [];
    this.music.index = -1;
    this.musicPlayer.stop(true);
    this.connection.subscribe(this.player);
  }

  musicStatus() {
    const { on, queue, index } = this.music;
    return { on, paused: this.musicPlayer.state.status === AudioPlayerStatus.Paused, current: on ? queue[index] : null, upNext: on ? queue.slice(index + 1) : [] };
  }

  // Stop talking and drop anything still queued (someone started speaking over us).
  interrupt({ quiet = false } = {}) {
    this.turn++;
    this.speech.length = 0;
    this.player.stop(true);
    if (!quiet) {
      activity('barge-in', { guild: this.guild.name });
      pressure.onBargeIn();
    }
  }

  // Someone is in the call with her: anything she owes them happens now. Someone she's never met
  // gets an introduction added first (once ever). Joins a few seconds apart are handled together.
  arrived(members) {
    for (const m of members) {
      if (m.user.bot) continue;
      pressure.onJoin(m);
      if (markGreeted(m.id)) {
        const wake = config.voice.wakeWord ? ` They can talk to you by saying "${config.voice.wakeWord}".` : '';
        addObligation({
          kind: 'intro',
          targetId: m.id,
          targetName: m.displayName,
          task:
            `Introduce yourself to ${m.displayName} like a friend would: say hi, your name, that you're an AI on this server listening in on the call, ` +
            `that you remember what people tell you and get to know them over time, and that if they have any issues with that they can take it up with ${ownerName()}.${wake} Keep it relaxed and short, not formal or creepy.`,
        });
      }
      // Someone she already knows, never asked about check-ins: ask once, now, in person.
      const card = reachCard(m.id);
      if (card && !card.reach_out && !card.reach_asked_at) {
        markAskedReach(m.id);
        addObligation({
          kind: 'consent',
          targetId: m.id,
          targetName: m.displayName,
          task:
            `Ask ${m.displayName}, casually and like a friend, whether it'd be okay if you checked in on them now and then on your own, instead of always waiting for them to start. ` +
            `If they say yes, ask how often feels good and what would be too much, and what they'd like to talk about. When they answer, save it with the checkin_consent tool. If they say no, that's completely fine; say so and drop it.`,
        });
      }
      const due = obligationsFor(m.id, [m.displayName, m.user.username]);
      if (due.length) this.arrivals.set(m.id, { name: m.displayName, due });
    }
    if (!this.arrivals.size || this.arrivalTimer) return;
    this.arrivalTimer = setTimeout(() => {
      this.arrivalTimer = null;
      const people = [...this.arrivals];
      this.arrivals.clear();
      this.replyChain = this.replyChain.then(async () => {
        for (const [id, { name, due }] of people) {
          const turn = this.turn; // queued after the last reply; only an interruption (interrupt()) drops speech
          await respond({
            conversation: this.conversation,
            speaker: { id, name },
            text: '',
            voice: true,
            cue: `${name} just joined the voice call. Take care of what you owe them now, in a few short spoken sentences.`,
            due,
            onSentence: (s) => turn === this.turn && this.say(s, turn),
          });
          activity('obligation', { guild: this.guild.name, to: name, done: due.map((o) => `#${o.id} ${o.kind}`) });
        }
      }).catch((err) => activity('error', { where: 'voice arrival', message: err.message }));
    }, 3000); // give them a moment to connect, and catch friends joining together
  }

  // A background helper finished: the full report goes in the text channel, and she says so out loud.
  reportTask(text) {
    for (let i = 0; i < text.length; i += 2000) this.post(text.slice(i, i + 2000));
    const title = text.match(/"([^"]+)"/)?.[1] ?? 'your background task';
    if (sessions.get(this.guild.id) === this) this.say(`Heads up, ${title} is ${/ failed:/.test(text) ? 'failed' : 'done'}. The report's in the chat.`);
  }

  post(content) {
    this.textChannel?.send({ content: content.slice(0, 2000), allowedMentions: { parse: [] } }).catch(() => {});
  }

  destroy() {
    if (sessions.get(this.guild.id) === this) sessions.delete(this.guild.id);
    this.interrupt({ quiet: true });
    this.music.on = false;
    this.musicPlayer.stop(true);
    this.musicPlayer.removeAllListeners();
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
