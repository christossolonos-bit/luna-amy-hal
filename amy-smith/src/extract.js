import { config } from './config.js';
import { quickJson } from './llm.js';
import { remember, conversations, messagesPage, forgetEveryone } from './memory.js';
import { permissions } from './contacts.js';
import { activity } from './events.js';

// Pulling long-term memories out of conversations, shared by the after-each-reply pass (companion.js)
// and the "rebuild your memories from history" tool. Facts are filed under the person they're TRUE
// of, not whoever said them, and only for people whose tier is remembered (not strangers).

export const MEMORY_TASK = (names, botName) =>
  `"facts": durable facts worth remembering about the people in this conversation: ${names.join(', ')}. Preferences, plans, relationships, life details, things they asked to be remembered. ` +
  `Each fact is {"about": "<exactly one of: ${names.join(', ')}>", "fact": "<short standalone sentence starting with that person's name>"}. ` +
  `Get the person right: a fact belongs to whoever it is TRUE of, not whoever said it. If Travis says "my boy Brandon's girlfriend is Katie", that fact is about Brandon, not Travis. ` +
  `People may be called by a nickname or real name instead of their screen name; match them to the screen name from who is speaking and who is being addressed. ` +
  `Only keep facts that will still be true and useful weeks from now: who they are, their family, partner and friends, work, projects, interests, habits, strong opinions, plans. ` +
  `Skip: what someone said or asked in the moment, advice they gave, reactions, time of day, being tired or hungry right now, guesses ("likely", "implied", "or similar"), anything you're unsure who it belongs to, ` +
  `facts about anyone not in that list, small talk, anything already known, traits people assign to ${botName}, and requests about how ${botName} should act (those are persona changes, not facts). ` +
  `Fewer, solid facts beat many weak ones. Empty list if nothing new.`;

// Saves extracted facts under the right people. people: [{ id, name }]; a bare string fact (older
// prompt format) counts as being about fallbackName. Returns how many were saved.
export async function fileFacts(facts, people, fallbackName) {
  const byPerson = new Map();
  for (const f of Array.isArray(facts) ? facts.slice(0, 12) : []) {
    const fact = typeof f === 'string' ? f.trim() : typeof f?.fact === 'string' ? f.fact.trim() : '';
    if (!fact) continue;
    const about = (typeof f === 'object' && typeof f.about === 'string' ? f.about : fallbackName ?? '').trim().toLowerCase();
    const person = people.find((p) => p.name.toLowerCase() === about);
    if (!person || !permissions(person.id).remember) continue;
    if (!byPerson.has(person)) byPerson.set(person, []);
    byPerson.get(person).push(fact);
  }
  let count = 0;
  for (const [person, list] of byPerson) {
    const saved = await remember(person.id, list);
    count += saved.length;
    if (saved.length) activity('remembered', { speaker: person.name, userId: person.id, facts: saved });
  }
  return count;
}

// Reads through saved conversations and repopulates memories. Runs in the background (it can take
// a few minutes on a long history); progress and the result go to the activity feed.
// fresh: wipe every memory first. only: a conversation id to limit it to.
let rebuilding = null;
export function rebuildMemories({ fresh = false, only = null } = {}) {
  if (rebuilding) return { started: false, message: 'A rebuild is already running.' };
  const targets = conversations().filter((c) => !only || c.conversation === only).map((c) => c.conversation);
  if (!targets.length) return { started: false, message: 'There is no conversation history to read.' };
  rebuilding = (async () => {
    const started = Date.now();
    if (fresh) activity('persona', { reason: `Cleared ${forgetEveryone()} memories before rebuilding`, changedBy: 'owner' });
    let saved = 0, read = 0;
    for (const conversation of targets) {
      let after = 0;
      for (;;) {
        const page = messagesPage(conversation, after, 40);
        if (!page.length) break;
        after = page[page.length - 1].id;
        read += page.length;
        const ids = new Map();
        for (const m of page) if (m.role === 'user' && m.user_id && m.user_id !== 'dashboard') ids.set(m.user_id, m.name ?? 'Someone');
        // Everyone is named, strangers too, so their facts are recognised as theirs (and then dropped
        // by fileFacts) instead of being pinned on someone else.
        const people = [...ids].map(([id, name]) => ({ id, name }));
        if (!people.some((p) => permissions(p.id).remember)) continue;
        const transcript = page.map((m) => `${m.role === 'assistant' ? m.name ?? config.botName : m.name}: ${m.content}`).join('\n');
        const result = await quickJson([
          { role: 'system', content: `You maintain the long-term memory of a companion named ${config.botName}. Read this stretch of conversation and reply with JSON {${MEMORY_TASK(people.map((p) => p.name), config.botName)}}` },
          { role: 'user', content: transcript },
        ]).catch((err) => {
          activity('error', { where: 'memory rebuild', message: err.message });
          return null;
        });
        saved += await fileFacts(result?.facts, people);
      }
    }
    activity('persona', { reason: `Memory rebuild done: read ${read} messages, saved ${saved} memories in ${Math.round((Date.now() - started) / 1000)}s`, changedBy: 'owner' });
    return { read, saved };
  })().finally(() => (rebuilding = null));
  return { started: true, conversations: targets.length, done: rebuilding };
}
