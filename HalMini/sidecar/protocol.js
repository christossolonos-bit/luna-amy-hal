// Wire protocol with the Python engine: one JSON object per line.
//   sidecar -> engine (stdout):  ready, joined, left, utterance, text, playback, error
//   engine -> sidecar (stdin):   say, play, stop, join, leave
// stdout carries ONLY protocol lines; logs go to stderr.

export function send(message) {
  process.stdout.write(JSON.stringify(message) + "\n");
}

export function log(...parts) {
  console.error("[sidecar]", ...parts);
}

// 48 kHz stereo s16le (what Discord decodes to) -> 16 kHz mono s16le (what Whisper wants).
export function downsampleForStt(pcm48kStereo) {
  const frames = Math.floor(pcm48kStereo.length / 4);
  const outFrames = Math.floor(frames / 3);
  const out = Buffer.alloc(outFrames * 2);
  for (let i = 0; i < outFrames; i++) {
    let sum = 0;
    for (let k = 0; k < 3; k++) {
      const offset = (i * 3 + k) * 4;
      sum += pcm48kStereo.readInt16LE(offset) + pcm48kStereo.readInt16LE(offset + 2);
    }
    out.writeInt16LE(Math.max(-32768, Math.min(32767, Math.round(sum / 6))), i * 2);
  }
  return out;
}

// Split long replies to fit Discord's 2000-character message limit, preferring sentence breaks.
export function chunkMessage(text, limit = 1900) {
  const chunks = [];
  let rest = text;
  while (rest.length > limit) {
    let cut = rest.lastIndexOf(". ", limit);
    if (cut < limit / 2) cut = rest.lastIndexOf(" ", limit);
    if (cut < 1) cut = limit;
    chunks.push(rest.slice(0, cut + 1).trim());
    rest = rest.slice(cut + 1);
  }
  if (rest.trim()) chunks.push(rest.trim());
  return chunks;
}
