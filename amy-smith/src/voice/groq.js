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
    const err = new Error(`Groq transcription failed (${res.status}): ${body.slice(0, 200)}`);
    // Rate limited: how long until it resets, from the header or "try again in 7m30.5s".
    if (res.status === 429) {
      const header = Number(res.headers.get('retry-after'));
      const m = body.match(/try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?/i);
      const fromText = m ? ((Number(m[1]) || 0) * 3600 + (Number(m[2]) || 0) * 60 + (Number(m[3]) || 0)) * 1000 : 0;
      err.retryAfterMs = Math.max(header > 0 ? header * 1000 : 0, fromText, 60_000);
    }
    throw err;
  }
  return (await res.json()).text ?? '';
}
