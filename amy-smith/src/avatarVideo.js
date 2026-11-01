import { config } from './config.js';
import { synthesize } from './voice/fishAudio.js';
import { activity } from './events.js';

// Video replies (Amy's spec, Downloads/amy-avatar-video-spec): a reply in a Discord text channel
// can also arrive as a clip of her avatar's face saying it. The avatar page, opened in recorder
// mode (/avatar.html?recorder=1) and left open, records it live: it keeps an event stream to us,
// we send it the reply's lines with their voice, it plays and records them, and uploads a .webm.
// Nothing here ever holds up the text reply; if anything fails, there's simply no clip.

export const MAX_CLIP_BYTES = 20 * 1024 * 1024; // over this, text only (spec guard)
const MAX_TEXT = 900; // ~45 s of speech; longer replies stay text-only
const ACK_MS = 8_000; // the page must start recording within this
const CLIP_MS = 120_000; // and finish within this

const recorders = new Set(); // open event streams from recorder pages
const jobs = new Map(); // id -> { resolve, timer }
let nextId = 1;

export const recorderConnected = () => recorders.size > 0;

// The recorder page's event stream. While it's open, the page is alive (that's the heartbeat).
export function attachRecorder(req, res) {
  res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' });
  res.write(': connected\n\n');
  recorders.add(res);
  activity('video', { message: 'Avatar video recorder connected' });
  const keepAlive = setInterval(() => res.write(': ping\n\n'), 20_000);
  req.on('close', () => {
    clearInterval(keepAlive);
    recorders.delete(res);
    activity('video', { message: 'Avatar video recorder disconnected' });
    // Its unfinished clips won't come now; give up on them straight away instead of timing out.
    for (const [id, job] of jobs) if (job.page === res) finish(id, null, 'the recorder page closed');
  });
}

const sentences = (text) => text.replace(/\s+/g, ' ').match(/[^.!?…]+[.!?…]*["')\]]*\s*/g)?.map((s) => s.trim()).filter(Boolean) ?? [];

// Asks the recorder for a clip of `text`. Resolves to a Buffer, or null when there's no clip
// (switched off, no recorder open, too long, or any failure); never throws.
export async function requestClip(text) {
  if (!config.avatarVideo || !recorders.size || !config.fish.apiKey) return null;
  const clean = String(text ?? '').replace(/```[\s\S]*?```/g, ' ').replace(/[*_`#>|]/g, '').trim(); // speak prose, not markdown
  if (!clean) return null;
  if (clean.length > MAX_TEXT) {
    activity('video', { message: `No clip: reply is ${clean.length} characters (limit ${MAX_TEXT})` });
    return null;
  }
  try {
    const lines = sentences(clean);
    const audio = await Promise.all(lines.map((s) => synthesize(s, { format: 'mp3' })));
    const id = nextId++;
    const [page] = recorders;
    const done = new Promise((resolve) => {
      const giveUp = (why) => () => finish(id, null, why);
      jobs.set(id, { resolve, page, timer: setTimeout(giveUp('the recorder did not start in time'), ACK_MS) });
    });
    page.write(`data: ${JSON.stringify({ type: 'job', id, lines: lines.map((t, i) => ({ text: t, audio: audio[i].toString('base64') })) })}\n\n`);
    return await done;
  } catch (err) {
    activity('error', { where: 'avatar video', message: err.message });
    return null;
  }
}

// The page started recording: switch to the longer deadline.
export function ack(id) {
  const job = jobs.get(Number(id));
  if (!job) return false;
  clearTimeout(job.timer);
  job.timer = setTimeout(() => finish(Number(id), null, 'the recording took too long'), CLIP_MS);
  return true;
}

export function finish(id, clip, why) {
  const job = jobs.get(Number(id));
  if (!job) return false;
  jobs.delete(Number(id));
  clearTimeout(job.timer);
  if (clip && clip.length > MAX_CLIP_BYTES) {
    why = `clip is ${(clip.length / 1048576).toFixed(1)} MB, over the 20 MB limit`;
    clip = null;
  }
  if (!clip) activity('video', { message: `No clip for reply #${id}: ${why || 'failed'}` });
  job.resolve(clip);
  return true;
}
