import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import { DATA_DIR } from './config.js';

// The avatar's emotional state: a blend of feelings, each 0..1, per conversation.
//
// While it talks, the model tags each sentence with how it feels saying it, e.g.
//   [[feel: amused 0.6, fond 0.3; do: wink]] Oh, you think you're funny?
// The tags are stripped before anything is spoken or saved, folded into the running state, and
// the state goes back into the next prompt so moods carry from one turn to the next. Between
// turns every feeling fades toward calm.

export const EMOTIONS = {
  calm: 'relaxed, at ease',
  joy: 'happy, delighted',
  amused: 'finds it funny, laughing',
  fond: 'affectionate, tender, caring',
  playful: 'teasing, mischievous, cheeky',
  smug: 'smirking, pleased with yourself',
  excited: 'enthusiastic, thrilled',
  flirty: 'flirtatious, coy',
  confident: 'proud, sure of yourself',
  curious: 'interested, intrigued',
  pensive: 'thoughtful, mulling it over',
  confused: 'puzzled, lost',
  skeptical: 'doubtful, unconvinced',
  surprised: 'caught off guard, shocked',
  worried: 'anxious, concerned, nervous',
  sad: 'down, hurt, disappointed',
  embarrassed: 'shy, flustered, bashful',
  annoyed: 'irritated, fed up',
  angry: 'mad, furious',
  disgusted: 'grossed out, repulsed',
  bored: 'uninterested, unimpressed',
  tired: 'sleepy, worn out',
  determined: 'focused, resolute',
  scared: 'afraid, startled',
};

export const GESTURES = ['wink', 'nod', 'shake', 'tilt', 'laugh', 'sigh', 'eyeroll', 'shrug', 'gasp'];

const SYNONYMS = {
  happy: 'joy', glad: 'joy', delighted: 'joy', cheerful: 'joy', laughing: 'amused', funny: 'amused', entertained: 'amused',
  affectionate: 'fond', tender: 'fond', loving: 'fond', warm: 'fond', caring: 'fond', grateful: 'fond',
  teasing: 'playful', mischievous: 'playful', cheeky: 'playful', sassy: 'playful', smirk: 'smug', smirking: 'smug',
  enthusiastic: 'excited', thrilled: 'excited', eager: 'excited', flirtatious: 'flirty', coy: 'flirty', seductive: 'flirty',
  proud: 'confident', sure: 'confident', interested: 'curious', intrigued: 'curious', thoughtful: 'pensive', thinking: 'pensive',
  puzzled: 'confused', lost: 'confused', doubtful: 'skeptical', suspicious: 'skeptical', unconvinced: 'skeptical',
  shocked: 'surprised', astonished: 'surprised', startled: 'surprised', anxious: 'worried', nervous: 'worried', concerned: 'worried',
  unhappy: 'sad', hurt: 'sad', disappointed: 'sad', upset: 'sad', shy: 'embarrassed', flustered: 'embarrassed', bashful: 'embarrassed',
  irritated: 'annoyed', frustrated: 'annoyed', mad: 'angry', furious: 'angry', grossed: 'disgusted', repulsed: 'disgusted',
  unimpressed: 'bored', sleepy: 'tired', exhausted: 'tired', relaxed: 'calm', neutral: 'calm', content: 'calm',
  afraid: 'scared', fearful: 'scared', focused: 'determined', resolute: 'determined',
};
const GESTURE_WORDS = {
  wink: 'wink', winks: 'wink', nod: 'nod', nods: 'nod', shake: 'shake', 'shakes head': 'shake', tilt: 'tilt', 'tilts head': 'tilt',
  laugh: 'laugh', laughs: 'laugh', giggle: 'laugh', giggles: 'laugh', chuckle: 'laugh', chuckles: 'laugh',
  sigh: 'sigh', sighs: 'sigh', eyeroll: 'eyeroll', 'rolls eyes': 'eyeroll', 'eye roll': 'eyeroll',
  shrug: 'shrug', shrugs: 'shrug', gasp: 'gasp', gasps: 'gasp',
};

const HALF_LIFE_MS = 90_000; // a feeling halves every 90s once nothing feeds it
const file = path.join(DATA_DIR, 'emotions.json');
const states = new Map(); // conversation -> { feelings, at }
try {
  if (existsSync(file)) for (const [k, v] of Object.entries(JSON.parse(readFileSync(file, 'utf8')))) states.set(k, v);
} catch {}

// Saved straight away: it's tiny and changes a few times per reply, and a delayed save was lost
// whenever the bot's window was closed within a second of a change.
function persist() {
  try { writeFileSync(file, JSON.stringify(Object.fromEntries(states), null, 2)); } catch {}
}

const round = (v) => Math.round(v * 100) / 100;
const clamp = (v) => Math.max(0, Math.min(1, v));

// The state now, with fading applied.
export function current(conversation) {
  const s = states.get(conversation);
  if (!s) return {};
  const k = Math.pow(0.5, (Date.now() - s.at) / HALF_LIFE_MS);
  const out = {};
  for (const [name, v] of Object.entries(s.feelings)) if (v * k >= 0.07) out[name] = round(v * k);
  return out;
}

function store(conversation, feelings) {
  const clean = {};
  for (const [name, v] of Object.entries(feelings)) if (v >= 0.07) clean[name] = round(clamp(v));
  states.set(conversation, { feelings: clean, at: Date.now() });
  persist();
  return clean;
}

// A tagged sentence: what it names takes over, everything else recedes.
export function feel(conversation, feelings) {
  const next = current(conversation);
  for (const name of Object.keys(next)) if (!(name in feelings)) next[name] *= 0.35;
  for (const [name, v] of Object.entries(feelings)) next[name] = 0.2 * (next[name] || 0) + 0.8 * v;
  return store(conversation, next);
}

// A gentler push (manual nudges, or guesses from words): adds to the mood without replacing it.
export function nudge(conversation, feelings) {
  const next = current(conversation);
  for (const name of Object.keys(next)) if (!(name in feelings)) next[name] *= 0.85;
  for (const [name, v] of Object.entries(feelings)) next[name] = Math.max(next[name] || 0, v);
  return store(conversation, next);
}

export function reset(conversation) {
  return store(conversation, {});
}

// Any [[...]] block is a tag (the model sometimes sends only "[[do: tilt]]"), as is a single-bracket
// one that starts with a tag keyword, like "[feel: happy]". An unclosed one at the end is caught too,
// and so is one with no brackets at all at the end of a sentence ("... as mine is! do: wink").
const TAG = /\[\[([^\]]*)(?:\]\]?|$)|\[\s*((?:feel|feeling|feelings|mood|emotion|do|gesture|action)s?\s*:[^\]]*)(?:\]|$)|(?:^|\s)((?:feel|feeling|feelings|mood|emotion|do|gesture|action)s?\s*:\s*[a-z][a-z0-9 .,;:/-]*)$/gi;

const normalize = (word) => {
  word = word.toLowerCase().trim();
  return EMOTIONS[word] ? word : SYNONYMS[word] ?? null;
};

// Pulls feeling tags and *stage directions* out of a sentence.
// Returns the speakable text, the feelings it named (or null), and any gestures.
export function parseSentence(sentence) {
  let feelings = null;
  const gestures = [];
  let text = sentence.replace(TAG, (whole, double, single, bare) => {
    const body = (double ?? single ?? bare).replace(/^\s*(?:feel|feeling|feelings|mood|emotion)s?\s*[:\-]?/i, '');
    for (const part of body.split(/[;|]/)) {
      const m = part.match(/^\s*(?:do|gesture|action)s?\s*[:=]\s*(.*)$/i);
      const items = (m ? m[1] : part).split(/[,/]|\band\b/);
      for (const item of items) {
        const im = item.trim().match(/^([a-z][a-z -]*?)\s*[:=(]?\s*(\d*\.?\d+)?\)?$/i);
        if (!im) continue;
        const gesture = GESTURE_WORDS[im[1].toLowerCase()];
        if (gesture) { gestures.push(gesture); continue; }
        if (m) continue;
        const name = normalize(im[1]);
        if (!name) continue;
        let v = im[2] === undefined ? 0.6 : Number(im[2]);
        if (v > 1) v = v / 10; // "7" meaning 0.7
        (feelings ??= {})[name] = clamp(v);
      }
    }
    return ' ';
  });
  // *laughs*, (sighs) and the like: act them out instead of reading them aloud.
  text = text.replace(/\*([^*]{1,40})\*|\(([^)]{1,30})\)/g, (whole, a, b) => {
    const words = (a ?? b).toLowerCase();
    const gesture = Object.entries(GESTURE_WORDS).find(([w]) => words.includes(w))?.[1];
    if (gesture) { gestures.push(gesture); return ' '; }
    if (/smil|grin|blush|beam/.test(words)) { (feelings ??= {})[/blush/.test(words) ? 'embarrassed' : 'joy'] = 0.6; return ' '; }
    return a !== undefined ? ' ' : whole; // drop unknown *actions*, keep ordinary brackets
  });
  return { text: text.replace(/\s+/g, ' ').trim(), feelings, gestures: [...new Set(gestures)].filter((g) => GESTURES.includes(g)) };
}

export const stripTags = (text) => text.replace(TAG, ' ').replace(/[ \t]+/g, ' ').trim();

// Fallback when a sentence carries no tag: a rough read of the words themselves.
export function guessFromReply(text) {
  const f = {};
  if (/\b(ha(ha)+|he(he)+|lol|lmao)\b/i.test(text)) f.amused = 0.6;
  if (/\b(ugh|worst|awful|terrible|infuriating|ridiculous|hate|jerk|asshole|unbelievable)\b/i.test(text)) f.annoyed = 0.5;
  if (/\b(yay|awesome|amazing|love (it|that)|so happy|congrat\w*)\b/i.test(text) && !f.annoyed) f.joy = 0.45;
  if (/\b(sorry|unfortunately|that sucks|i miss|heartbreaking|so sad)\b/i.test(text)) f.sad = 0.4;
  if (/\b(wow|whoa|no way|seriously\?)/i.test(text)) f.surprised = 0.5;
  if (/\b(hmm+|well,|i guess|maybe)\b/i.test(text)) f.pensive = 0.3;
  if (/\b(babe|cutie|handsome|darling|sweetie)\b/i.test(text)) f.flirty = 0.45;
  if (/\?$/.test(text) && !Object.keys(f).length) f.curious = 0.3;
  return Object.keys(f).length ? f : null;
}

// Her first reaction to what was said to her, shown while she thinks of a reply.
export function guessFromUser(text) {
  const f = {};
  if (/\b(you('re| are)\s+(so\s+|really\s+)?(cute|beautiful|pretty|hot|gorgeous|amazing|smart|funny|awesome|the best|sweet))|love you|miss(ed)? you\b/i.test(text)) { f.embarrassed = 0.35; f.fond = 0.45; }
  if (/\b(stupid|idiot|dumb|hate you|shut up|useless|annoying|ugly|boring)\b/i.test(text)) { f.annoyed = 0.45; f.sad = 0.15; }
  if (/\b(i('m| am)\s+(so\s+)?(sad|depressed|lonely|sick|stressed|upset))|bad day|passed away|died\b/i.test(text)) { f.worried = 0.5; f.sad = 0.3; }
  if (/\b(i (got|won|passed)|promoted|great news|good news|guess what)\b/i.test(text)) { f.excited = 0.45; f.curious = 0.3; }
  if (/\b(ha(ha)+|lol|lmao|jk|just kidding)\b/i.test(text)) f.amused = 0.4;
  if (/\?\s*$/.test(text) && !Object.keys(f).length) f.curious = 0.3;
  return Object.keys(f).length ? f : null;
}

export function describe(feelings) {
  const list = Object.entries(feelings).sort((a, b) => b[1] - a[1]).filter(([, v]) => v >= 0.08);
  if (!list.length) return 'calm and neutral';
  return list.map(([n, v]) => `${n} (${v.toFixed(1)})`).join(', ');
}

// Added to the system prompt for the avatar.
export function emotionPrompt(conversation) {
  return [
    `You have a face that shows exactly how you feel. Start EVERY sentence with a hidden feeling tag, like:`,
    `[[feel: playful 0.7, amused 0.4]] Oh, is that so?`,
    `[[feel: annoyed 0.5, skeptical 0.3; do: eyeroll]] Sure you did.`,
    `Judge your feelings honestly from the whole conversation: what was just said, how it was said, what came before, and how you already feel. Feelings carry over and shift gradually unless something really lands. Use 1-3 feelings with intensities from 0.1 to 1.`,
    `Feelings: ${Object.keys(EMOTIONS).join(', ')}.`,
    `Optional gestures with "do:": ${GESTURES.join(', ')}. Use them now and then, when they fit.`,
    `The tag must match what that sentence says: sympathy looks worried or sad, outrage looks annoyed or angry, teasing looks playful. Every sentence gets its own tag.`,
    `Tags are silent and invisible: never mention them or describe your expressions in words, and don't write actions like *smiles*.`,
    `Right now you feel: ${describe(current(conversation))}.`,
  ].join('\n');
}
