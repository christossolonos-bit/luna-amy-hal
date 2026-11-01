import 'dotenv/config';
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const DATA_DIR = path.join(ROOT, 'data');
mkdirSync(DATA_DIR, { recursive: true });

const env = process.env;
const list = (v) => (v ? v.split(',').map((s) => s.trim()).filter(Boolean) : []);
const num = (v, d) => (v === undefined || v === '' ? d : Number(v));
const bool = (v, d) => (v === undefined || v === '' ? d : /^(1|true|yes|on)$/i.test(v));

const personaPath = path.join(ROOT, 'persona.md');
const settingsPath = path.join(DATA_DIR, 'settings.json');

export const config = {
  discordToken: env.DISCORD_TOKEN,
  clientId: env.CLIENT_ID,
  guildId: env.GUILD_ID,

  botName: env.BOT_NAME || 'Champ',
  // On/off switch from the dashboard; remembered across restarts.
  botEnabled: true,
  persona: existsSync(personaPath) ? readFileSync(personaPath, 'utf8').trim() : '',
  // Lets people reshape the bot's persona just by asking in conversation.
  personaSelfEdit: bool(env.PERSONA_SELF_EDIT, true),
  // Discord user IDs allowed to do that. Empty = anyone.
  personaEditors: list(env.PERSONA_EDITORS),
  // Channels where the bot replies to every message (it always replies to DMs and @mentions).
  chatChannelIds: list(env.CHAT_CHANNEL_IDS),
  historyLimit: num(env.HISTORY_LIMIT, 100),

  // Chat brain: 'nvidia' = free NVIDIA NIM (OpenAI-compatible, same as Luna), 'ollama' = local.
  llmBackend: (env.LLM_BACKEND || (env.NVIDIA_API_KEY ? 'nvidia' : 'ollama')).toLowerCase(),
  nvidia: {
    apiKey: env.NVIDIA_API_KEY || '',
    baseUrl: (env.NVIDIA_BASE_URL || 'https://integrate.api.nvidia.com/v1').replace(/\/$/, ''),
    model: env.NVIDIA_MODEL || 'nvidia/nemotron-3.5-super-vl-120b-a12b',
    maxTokens: num(env.NVIDIA_MAX_TOKENS, 256),
  },

  ollama: {
    host: env.OLLAMA_HOST || 'http://127.0.0.1:11434',
    model: env.OLLAMA_MODEL || 'gemma4:12b',
    embedModel: env.OLLAMA_EMBED_MODEL || 'bge-m3',
    think: bool(env.OLLAMA_THINK, false),
    // Context window in tokens. Ollama defaults to 4096, which silently cuts off long histories.
    numCtx: num(env.OLLAMA_NUM_CTX, 32768),
  },

  memory: {
    topK: num(env.MEMORY_TOP_K, 6),
    minScore: num(env.MEMORY_MIN_SCORE, 0.35),
    dedupeScore: num(env.MEMORY_DEDUPE_SCORE, 0.9),
  },

  fish: {
    apiKey: env.FISH_API_KEY,
    voiceId: env.FISH_VOICE_ID,
    ttsModel: env.FISH_TTS_MODEL || 's2.1-pro',
    asrModel: env.FISH_ASR_MODEL || 'transcribe-1-pro',
    language: env.FISH_LANGUAGE || 'en',
  },

  // Web search (https://tavily.com). Leave the key blank to turn search off.
  tavily: {
    apiKey: env.TAVILY_API_KEY,
    depth: env.TAVILY_DEPTH || 'basic', // basic = 1 credit, advanced = 2
    maxResults: num(env.TAVILY_MAX_RESULTS, 5),
  },

  // Speech-to-text: 'groq' (Groq cloud Whisper, falls back to local), 'local' (Whisper on this PC),
  // or 'fish' (Fish Audio API, needs credit).
  stt: (env.STT_PROVIDER || 'local').toLowerCase(),
  groq: {
    apiKey: env.GROQ_API_KEY,
    model: env.GROQ_STT_MODEL || 'whisper-large-v3-turbo',
  },
  whisper: {
    model: env.WHISPER_MODEL || 'Xenova/whisper-base.en',
    device: env.WHISPER_DEVICE || 'auto', // auto (GPU via DirectML, else CPU) | dml | cpu
  },

  voice: {
    // Voice channels the bot may join. Empty = any.
    channelIds: list(env.VOICE_CHANNEL_IDS),
    silenceMs: num(env.VOICE_SILENCE_MS, 800),
    minSpeechMs: num(env.VOICE_MIN_SPEECH_MS, 400),
    bargeIn: bool(env.VOICE_BARGE_IN, true),
    postTranscripts: bool(env.VOICE_POST_TRANSCRIPTS, true),
    // If set, the bot only answers voice utterances containing this word.
    wakeWord: (env.VOICE_WAKE_WORD || '').toLowerCase(),
  },

  // The 3D avatar shown at /avatar.html.
  avatar: {
    path: env.AVATAR_PATH || path.join(DATA_DIR, 'avatar.glb'),
  },

  dashboard: {
    port: num(env.DASHBOARD_PORT, 3000),
    host: env.DASHBOARD_HOST || '127.0.0.1',
    password: env.DASHBOARD_PASSWORD || '',
  },
};

// Settings the dashboard can change at runtime. Saved to data/settings.json, which overrides .env.
// Each entry maps a flat settings key to [get, set] on the config object.
const EDITABLE = {
  botName: [() => config.botName, (v) => (config.botName = String(v).trim() || 'Champ')],
  botEnabled: [() => config.botEnabled, (v) => (config.botEnabled = Boolean(v))],
  voiceChannelIds: [() => config.voice.channelIds, (v) => (config.voice.channelIds = Array.isArray(v) ? v.map(String) : list(v))],
  chatChannelIds: [() => config.chatChannelIds, (v) => (config.chatChannelIds = Array.isArray(v) ? v.map(String) : list(v))],
  historyLimit: [() => config.historyLimit, (v) => (config.historyLimit = Math.max(1, Math.min(500, Math.round(Number(v)) || 100)))],
  personaSelfEdit: [() => config.personaSelfEdit, (v) => (config.personaSelfEdit = Boolean(v))],
  personaEditors: [() => config.personaEditors, (v) => (config.personaEditors = Array.isArray(v) ? v.map(String) : list(v))],
  ollamaModel: [() => config.ollama.model, (v) => (config.ollama.model = String(v))],
  ollamaThink: [() => config.ollama.think, (v) => (config.ollama.think = Boolean(v))],
  // Blank falls back to .env: with no voice ID, Fish picks a random voice for every sentence.
  fishVoiceId: [() => config.fish.voiceId ?? '', (v) => (config.fish.voiceId = String(v ?? '').trim() || env.FISH_VOICE_ID || '')],
  fishTtsModel: [() => config.fish.ttsModel, (v) => (config.fish.ttsModel = String(v))],
  fishLanguage: [() => config.fish.language, (v) => (config.fish.language = String(v).trim().toLowerCase())],
  voiceSilenceMs: [() => config.voice.silenceMs, (v) => (config.voice.silenceMs = Number(v) || 800)],
  voiceBargeIn: [() => config.voice.bargeIn, (v) => (config.voice.bargeIn = Boolean(v))],
  voicePostTranscripts: [() => config.voice.postTranscripts, (v) => (config.voice.postTranscripts = Boolean(v))],
  voiceWakeWord: [() => config.voice.wakeWord, (v) => (config.voice.wakeWord = String(v).trim().toLowerCase())],
};

let saved = {};
if (existsSync(settingsPath)) {
  try {
    saved = JSON.parse(readFileSync(settingsPath, 'utf8'));
    for (const [key, value] of Object.entries(saved)) EDITABLE[key]?.[1](value);
  } catch (err) {
    console.warn('Ignoring unreadable data/settings.json:', err.message);
  }
}

export function getSettings() {
  return {
    ...Object.fromEntries(Object.entries(EDITABLE).map(([k, [get]]) => [k, get()])),
    persona: config.persona,
  };
}

export function updateSettings(patch) {
  for (const [key, value] of Object.entries(patch)) {
    if (key === 'persona') {
      config.persona = String(value).trim();
      writeFileSync(personaPath, config.persona + '\n');
    } else if (EDITABLE[key]) {
      EDITABLE[key][1](value);
      saved[key] = EDITABLE[key][0]();
    }
  }
  writeFileSync(settingsPath, JSON.stringify(saved, null, 2));
  return getSettings();
}
