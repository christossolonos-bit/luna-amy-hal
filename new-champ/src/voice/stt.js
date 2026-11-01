import { config } from '../config.js';
import { activity } from '../events.js';
import { transcribeLocal, clean } from './whisper.js';
import { transcribeGroq } from './groq.js';
import { transcribe as transcribeFish } from './fishAudio.js';

// Speech -> text for one utterance (48kHz mono 16-bit PCM from the voice receiver).
// Groq is used when configured; if it has no key or a request fails, local Whisper takes over.
export async function transcribeSpeech(pcm48k) {
  if (config.stt === 'fish') return clean(await transcribeFish(toWav(pcm48k, 48000)));
  if (config.stt === 'groq' && config.groq.apiKey) {
    try {
      return clean(await transcribeGroq(toWav(downsample16k(pcm48k), 16000)));
    } catch (err) {
      activity('error', { where: 'groq transcription (used local Whisper instead)', message: err.message });
    }
  }
  return transcribeLocal(pcm48k);
}

// Which engine will actually be used right now (for the dashboard).
export function sttEngine() {
  if (config.stt === 'fish') return 'Fish Audio';
  if (config.stt === 'groq' && config.groq.apiKey) return `Groq ${config.groq.model}`;
  return `Local ${config.whisper.model.split('/').pop()}`;
}

// 48kHz -> 16kHz 16-bit, averaging each group of 3 samples. Smaller upload, same accuracy for speech.
function downsample16k(pcm) {
  const samples = Math.floor(pcm.length / 6);
  const out = Buffer.alloc(samples * 2);
  for (let i = 0; i < samples; i++) {
    const o = i * 6;
    out.writeInt16LE(Math.round((pcm.readInt16LE(o) + pcm.readInt16LE(o + 2) + pcm.readInt16LE(o + 4)) / 3), i * 2);
  }
  return out;
}

export function toWav(pcm, sampleRate) {
  const header = Buffer.alloc(44);
  header.write('RIFF', 0);
  header.writeUInt32LE(36 + pcm.length, 4);
  header.write('WAVE', 8);
  header.write('fmt ', 12);
  header.writeUInt32LE(16, 16); // fmt chunk size
  header.writeUInt16LE(1, 20); // PCM
  header.writeUInt16LE(1, 22); // mono
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(sampleRate * 2, 28); // byte rate
  header.writeUInt16LE(2, 32); // block align
  header.writeUInt16LE(16, 34); // bits per sample
  header.write('data', 36);
  header.writeUInt32LE(pcm.length, 40);
  return Buffer.concat([header, pcm]);
}
