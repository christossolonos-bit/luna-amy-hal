import { config } from '../config.js';

// Groq-hosted Whisper (https://console.groq.com). Takes a WAV buffer, returns the raw transcript.
export async function transcribeGroq(wav) {
  const form = new FormData();
  form.append('file', new Blob([wav], { type: 'audio/wav' }), 'speech.wav');
  form.append('model', config.groq.model);
  form.append('response_format', 'json');
  form.append('temperature', '0');
  if (config.fish.language) form.append('language', config.fish.language);

  const res = await fetch('https://api.groq.com/openai/v1/audio/transcriptions', {
    method: 'POST',
    headers: { Authorization: `Bearer ${config.groq.apiKey}` },
    body: form,
    signal: AbortSignal.timeout(20_000),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`Groq transcription failed (${res.status}): ${body.slice(0, 200)}`);
  }
  return (await res.json()).text ?? '';
}
