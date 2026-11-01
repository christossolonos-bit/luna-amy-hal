import { Readable } from 'node:stream';
import { config } from '../config.js';

const API = 'https://api.fish.audio/v1';

function headers(model, extra = {}) {
  if (!config.fish.apiKey) throw new Error('FISH_API_KEY is not set in .env');
  return { Authorization: `Bearer ${config.fish.apiKey}`, model, ...extra };
}

async function fail(res, what) {
  const body = await res.text().catch(() => '');
  throw new Error(`Fish Audio ${what} failed (${res.status}): ${body.slice(0, 300)}`);
}

// Speech -> text. Takes a WAV buffer, returns the transcript ('' if nothing was said).
export async function transcribe(wav) {
  const form = new FormData();
  form.append('audio', new Blob([wav], { type: 'audio/wav' }), 'speech.wav');
  if (config.fish.language) form.append('language', config.fish.language); // empty = auto-detect
  form.append('ignore_timestamps', 'true');

  const res = await fetch(`${API}/asr`, {
    method: 'POST',
    headers: headers(config.fish.asrModel),
    body: form,
    signal: AbortSignal.timeout(60_000),
  });
  if (!res.ok) await fail(res, 'transcription');
  const data = await res.json();
  return (data.text ?? '').replace(/<\|speaker:\d+\|>/g, '').trim();
}

// At most this many TTS requests in flight, so long replies don't trip Fish's rate limits.
const MAX_CONCURRENT_TTS = 2;
let active = 0;
const waiting = [];

async function withSlot(fn) {
  if (active >= MAX_CONCURRENT_TTS) await new Promise((resolve) => waiting.push(resolve));
  active++;
  try {
    return await fn();
  } finally {
    active--;
    waiting.shift()?.();
  }
}

// Text -> speech. Downloads the whole clip (Ogg Opus) into memory, so it can sit in the
// speech queue for as long as needed without the connection timing out.
// The avatar page asks for mp3, which every browser can decode, and passes each character's own voice.
export function synthesize(text, { format = 'opus', voiceId = config.fish.voiceId } = {}) {
  return withSlot(async () => {
    const body = { text, format, latency: 'balanced' };
    if (voiceId) body.reference_id = voiceId;

    for (let attempt = 1; ; attempt++) {
      try {
        const res = await fetch(`${API}/tts`, {
          method: 'POST',
          headers: headers(config.fish.ttsModel, { 'Content-Type': 'application/json' }),
          body: JSON.stringify(body),
          signal: AbortSignal.timeout(45_000),
        });
        if ((res.status === 429 || res.status >= 500) && attempt < 3) {
          await new Promise((r) => setTimeout(r, 800 * attempt));
          continue;
        }
        if (!res.ok) await fail(res, 'text-to-speech');
        return Buffer.from(await res.arrayBuffer());
      } catch (err) {
        if (attempt >= 3 || err.message.startsWith('Fish Audio')) throw err;
        await new Promise((r) => setTimeout(r, 800 * attempt)); // network hiccup or timeout: retry
      }
    }
  });
}

// A byte stream over a downloaded clip (Readable.from would be object mode, which the player rejects).
export function audioStream(clip) {
  return Readable.from([clip], { objectMode: false });
}
