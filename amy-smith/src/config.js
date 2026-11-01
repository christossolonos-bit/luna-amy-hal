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
  // Discord user IDs of the owner: always top access, and the only one who can set contacts' tiers.
  ownerIds: list(env.OWNER_IDS),
  // Channels where the bot replies to every message (it always replies to DMs and @mentions).
  chatChannelIds: list(env.CHAT_CHANNEL_IDS),
  historyLimit: num(env.HISTORY_LIMIT, 100),

  ollama: {
    host: env.OLLAMA_HOST || 'http://127.0.0.1:11434',
    model: env.OLLAMA_MODEL || 'gemma4:12b',
    embedModel: env.OLLAMA_EMBED_MODEL || 'bge-m3',
    think: bool(env.OLLAMA_THINK, false),
    // Context window in tokens. Ollama defaults to 4096, which silently cuts off long histories.
    numCtx: num(env.OLLAMA_NUM_CTX, 32768),
  },

  // Main answerer is always HalMini (see companion.js). This setting is only the *teacher*
  // used when HalMini cannot answer, then the reply is taught back into HalMini.
  // 'nvidia' (default) | 'ollama' | 'openrouter'. Embeddings stay on Ollama.
  brain: (env.BRAIN || 'nvidia').toLowerCase(),
  // HalMini fact brain — tried first on every normal turn (disable with HALMINI_ENABLED=0).
  halmini: {
    enabled: !/^(0|false|off|no)$/i.test(env.HALMINI_ENABLED || '1'),
  },
  // NVIDIA NIM (OpenAI-compatible). Default teacher when HalMini misses.
  nvidia: {
    apiKey: env.NVIDIA_API_KEY,
    baseUrl: (env.NVIDIA_BASE_URL || 'https://integrate.api.nvidia.com/v1').replace(/\/$/, ''),
    model: env.NVIDIA_MODEL || 'nvidia/nemotron-3.5-super-vl-120b-a12b',
    maxTokens: num(env.NVIDIA_MAX_TOKENS, 256),
  },
  // OpenRouter (https://openrouter.ai/keys). Also powers image generation, whichever brain is on.
  openrouter: {
    apiKey: env.OPENROUTER_API_KEY,
    model: env.OPENROUTER_MODEL || 'deepseek/deepseek-v4.1-flash-1017',
    // How hard reasoning models think before answering: none, low, medium or high.
    reasoning: (env.OPENROUTER_REASONING || 'medium').toLowerCase(),
    // Spoken replies: reasoning costs seconds before the first word, so it's off unless set.
    voiceReasoning: (env.OPENROUTER_VOICE_REASONING || 'none').toLowerCase(),
    imageModel: env.OPENROUTER_IMAGE_MODEL || 'google/gemini-3.1-flash-image',
    // Google Lyria 3 music: 30-second clips ($0.04 each) and full-length songs ($0.08 each).
    clipModel: env.OPENROUTER_CLIP_MODEL || 'google/lyria-3-clip-preview',
    songModel: env.OPENROUTER_SONG_MODEL || 'google/lyria-3-pro-preview',
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
    minSpeechMs: num(env.VOICE_MIN_SPEECH_MS, 400), // talking this long interrupts the bot
    // Sounds shorter than this are ignored, so coughs and clicks don't become "thank you".
    minUtteranceMs: num(env.VOICE_MIN_UTTERANCE_MS, 1500),
    bargeIn: bool(env.VOICE_BARGE_IN, true),
    postTranscripts: bool(env.VOICE_POST_TRANSCRIPTS, true),
    // If set, the bot only answers voice utterances containing this word.
    wakeWord: (env.VOICE_WAKE_WORD || '').toLowerCase(),
  },

  // Video replies: with the recorder page open (/avatar.html?recorder=1), Discord text replies
  // also get a clip of her face saying them. AVATAR_VIDEO=off switches the whole thing off.
  avatarVideo: !/^(off|false|0|no)$/i.test(env.AVATAR_VIDEO || ''),

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
  ollamaModel: [() => config.ollama.model, (v) => (config.ollama.model = String(v))],
  ollamaThink: [() => config.ollama.think, (v) => (config.ollama.think = Boolean(v))],
  avatarVideo: [() => config.avatarVideo, (v) => (config.avatarVideo = Boolean(v))],
  brain: [() => config.brain, (v) => {
    const b = String(v || '').toLowerCase().trim();
    config.brain = b === 'openrouter' ? 'openrouter' : b === 'nvidia' || b === 'nim' ? 'nvidia' : 'ollama';
  }],
  nvidiaModel: [() => config.nvidia.model, (v) => (config.nvidia.model = String(v).trim() || config.nvidia.model)],
  openrouterModel: [() => config.openrouter.model, (v) => (config.openrouter.model = String(v).trim() || config.openrouter.model)],
  openrouterVoiceReasoning: [() => config.openrouter.voiceReasoning, (v) => (config.openrouter.voiceReasoning = ['none', 'low', 'medium', 'high'].includes(v) ? v : 'none')],
  openrouterReasoning: [() => config.openrouter.reasoning, (v) => (config.openrouter.reasoning = ['none', 'low', 'medium', 'high'].includes(v) ? v : 'medium')],
  openrouterImageModel: [() => config.openrouter.imageModel, (v) => (config.openrouter.imageModel = String(v).trim() || config.openrouter.imageModel)],
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
    openrouterKey: Boolean(config.openrouter.apiKey), // whether one is set, never the key itself
    nvidiaKey: Boolean(config.nvidia.apiKey),
    halminiEnabled: config.halmini.enabled,
    teacher: config.brain, // alias: LLM only runs after a HalMini miss
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
