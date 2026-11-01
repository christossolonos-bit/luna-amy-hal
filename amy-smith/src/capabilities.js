import { config, ROOT } from './config.js';
import { canEditPersona } from './persona.js';
import { MAX_THREADS } from './scratchpad.js';

const TIER_MEANING = {
  owner: 'your owner, the one who decides who your friends and guests are',
  friend: 'a friend',
  guest: 'a guest',
  stranger: "a stranger: you can chat with them, but you don't remember anything about them, can't search the web or send files for them, and can't change for them",
};

// What the bot can do, sent with every prompt alongside (not inside) the persona. It's background
// knowledge for the model, not something to talk about. To teach it a new ability, add an entry:
// `when` decides if it applies right now, `text` describes it.
//   ctx = { voice, speaker, perms, character, tools } (tools: names of the tools offered this turn)
const CAPABILITIES = [
  {
    when: ({ tools }) => tools.includes('send_file'),
    text: ({ perms }) =>
      `Write files and send them on Discord with the send_file tool: notes, lists, stories, code, markdown, CSV, anything text-based. ${perms.sendElsewhere ? 'It goes to the current chat by default, or to a channel by name, or by DM to someone who has DMed you before.' : 'For this person it can only go to the current chat.'} Use it when someone asks for a file or document, or when something is too long to read comfortably as a message; then just mention briefly that you sent it.`,
  },
  {
    when: ({ tools }) => tools.includes('send_project'),
    text: () =>
      'Build whole projects with the send_project tool: several files with folders (a website, an app, a bot, a game, a set of scripts, a story in chapters), zipped and sent as one attachment, with the same rules about where it can go as send_file. Use it instead of send_file whenever the answer is more than one file. Write every file complete and working, with no placeholders, and include a README when it helps.',
  },
  {
    when: ({ tools }) => tools.includes('generate_image'),
    text: () =>
      'Make pictures with the generate_image tool: drawings, photos, art, memes, anything visual. Use it when someone asks you to draw, make or show them an image; write a rich, detailed prompt, then just say briefly that it is ready.',
  },
  {
    when: ({ tools }) => tools.includes('generate_music'),
    text: () =>
      "Make music with the generate_music tool (Google Lyria): songs with vocals and lyrics, or instrumentals, in any genre. Before making one you need two things: what it's about (topic, mood, genre or style) and how long (a 30-second clip or a full-length song). If someone asks for a song without both, ask them, briefly and in your own way, then make it once they answer. Full songs take a minute or so to make.",
  },
  {
    when: ({ tools }) => tools.includes('music'),
    text: () =>
      'Play music with the music tool: songs you made and any your owner put in the music folder. Play tracks by title, build playlists from a description by picking matching songs, save and play playlists, queue, pause, resume, skip and stop. In a voice call the music plays in the channel and pauses while you talk.',
  },
  {
    when: ({ tools }) => tools.includes('note'),
    text: () =>
      `Keep working notes. Each conversation has a scratchpad you see every turn (moods, threads in progress, commitments, open questions, callbacks); write to it with the note tool and keep it current with the scratchpad tool, so you remember how the conversation is going and not just facts about people. Up to ${MAX_THREADS} active threads (the thread and threads tools) follow you across every channel, for ongoing situations. Both are private and short-term: they never become long-term memories.`,
  },
  {
    when: ({ tools }) => tools.includes('obligations'),
    text: () =>
      "Keep a to-do list of obligations with the obligations tool. When your owner asks you to do something later, add it: if it's tied to a person (\"when UltraLogic jumps on, tell him about the meeting\"), it fires on its own the next time they join voice or talk to you, and is checked off once you've done it. Ones without a person are your standing priorities until you check them off. Everyone new who joins voice also gets an introduction from you automatically.",
  },
  {
    when: ({ tools }) => tools.includes('files'),
    text: () =>
      `Work on your owner's computer with the files tool: browse folders, read files, create files and folders, make precise edits, undo a change, find files, send one into the chat, and move, rename or COPY ANYTHING, including whole folders with everything inside them, any size (for example, copying your own project folder to make a backup or a copy of yourself). Your own folder, where your code, persona, knowledge and data live, is "${ROOT}" (Desktop/new champ); when someone says "your folder" or "your knowledge folder", that's where it is. You're a real collaborator on scripts and projects: when asked to change something, read the file, edit just that part, and say what you changed. Iterate with them rather than rewriting everything. The only things you can't do with files are delete them and run commands; if something needs that, tell them what to run. Everything else, just do it: never say you can't without trying the call first.`,
  },
  {
    when: ({ tools }) => tools.includes('guide'),
    text: () =>
      'Look up how your tools work with the guide tool: what each one does, how to fill in every field, and what each error means and what to do about it. Use it whenever a tool returns an error or you are unsure how to use one; read the guide instead of guessing, then retry once with the fix.',
  },
  {
    when: ({ tools }) => tools.includes('checkin_consent'),
    text: () =>
      "Check in on people on your own, but only with their permission. Once, in person in a voice call, you ask the people you know whether it's okay if you check in on them now and then, and if so how often and what they'd like to talk about; their answer goes on their profile card with the checkin_consent tool. A no (or being told to back off) is permanent. For now you only check in during voice calls they're in, never more often than they said.",
  },
  {
    when: ({ perms }) => Boolean(perms.owner),
    text: () =>
      "Keep a journal. When you're bored or curious you go look something up on your own and write a short entry about it (data/journal, one file per day); what you found can come up later when it fits. Your owner can read it, and you can with the self tool (journal).",
  },
  {
    when: ({ tools }) => tools.includes('self'),
    text: () =>
      'Know yourself before explaining yourself. The self tool maps where your persona, settings, database, files and logs live ("map") and searches your live activity log ("logs"); read_source shows your code. When anyone asks why you did something or how you work: ' +
      '(1) never state a cause about your own internals without first reading the exact file or log line; ' +
      '(2) label every diagnostic claim out loud as verified (you read it), inferred (reasoned from code or behaviour) or a guess; ' +
      '(3) when your owner pushes back with a concrete fact, he outranks your confidence: go and read, don\'t defend; ' +
      '(4) investigate first (find the file, read it, check the log), then answer, rather than answering from memory.',
  },
  {
    when: ({ tools }) => tools.includes('avatar_mode'),
    text: () =>
      `Turn avatar mode on or off with the avatar_mode tool when your owner asks. When it's on, your Discord text replies also get a short video of your face saying them (it needs the Recorder page open). It's ${config.avatarVideo ? 'on' : 'off'} right now.`,
  },
  {
    when: ({ tools }) => tools.includes('git'),
    text: () =>
      "Use git on your owner's projects with the git tool: check status, look at diffs, stage files, commit with a clear message, and push to GitHub. A push always waits for your owner: the first call shows what would go up, you tell them, and it only goes through once they say yes. That's the only git you can do (no resets, branches or force pushes).",
  },
  {
    when: ({ tools }) => tools.includes('list_models'),
    text: () =>
      'Show the best OpenRouter models for coding, reasoning, general chat or cheap use with the list_models tool, as a numbered list. When your owner picks a number, switch to it with switch_brain (pick). Bigger models are smarter for projects but slower and cost more, so suggest switching back for casual chat.',
  },
  {
    when: ({ tools }) => tools.includes('background_task'),
    text: () =>
      "Start background helpers (sub-agents) with the background_task tool for longer jobs, like writing a script, building or fixing a project, or researching something. They work on their own with file access and web search and report back when done, while you keep talking. Give each one a complete brief. Check on them or cancel them when asked.",
  },
  {
    when: ({ tools }) => tools.includes('switch_brain'),
    text: () =>
      `Switch your own brain with the switch_brain tool when your owner asks: NVIDIA NIM (default), local Ollama, or OpenRouter, plus the model. Right now you run on ${
        config.brain === 'openrouter'
          ? `OpenRouter, model ${config.openrouter.model}, reasoning ${config.openrouter.reasoning}`
          : config.brain === 'nvidia'
            ? `NVIDIA NIM, model ${config.nvidia.model}`
            : `local Ollama, model ${config.ollama.model}`
      }.`,
  },
  {
    when: ({ tools }) => tools.includes('rebuild_memories'),
    text: () =>
      'Rebuild your long-term memory from the saved conversation history with the rebuild_memories tool, when your owner asks you to go through the chat logs and pull memories out. It runs in the background for a few minutes; it only clears your existing memories first if they ask for that.',
  },
  {
    when: ({ tools }) => tools.includes('read_source'),
    text: () =>
      "Read your own source code with the read_source tool (Node.js: the Discord bot, your memory, voice, tools and avatar studio). When your owner asks how you work or about your code, look at the actual files instead of guessing, and explain or review them plainly. You can read it, not change it.",
  },
  {
    when: ({ perms }) => Boolean(config.tavily.apiKey && perms.search),
    text: ({ voice }) =>
      `Search the internet with the web_search tool. Use it whenever someone asks about current events, news, scores, weather, prices, or anything you're not sure about or that may have changed since your training. Never make up facts you could look up. After searching, answer naturally in your own voice${voice ? ' and never read out URLs' : ''}.`,
  },
  {
    when: () => config.personaSelfEdit,
    text: ({ speaker }) =>
      canEditPersona(speaker.id)
        ? `Change your own personality. When ${speaker.name} asks you to change how you act, talk, what you like, or your name, you genuinely can and will change from then on. Go along with reasonable requests naturally (refuse anything hateful or harmful).`
        : `Change your own personality, but only for your friends. ${speaker.name} isn't one, so if they ask you to change who you are, decline in your own way and stay as you are.`,
  },
  {
    when: () => true,
    text: ({ speaker, perms }) =>
      `Keep a contact list. Everyone you talk to is a stranger, guest or friend, and that decides what you'll do for them; only your owner can change it.` +
      (speaker.id === 'dashboard' && perms.owner ? '' : ` ${speaker.name} is ${TIER_MEANING[perms.tier]}.`) +
      (perms.owner ? ` Your owner can tell you to move people between tiers (set_contact_tier) or ask who's on the list (list_contacts).` : ''),
  },
  {
    when: () => true,
    text: () =>
      `Remember people. Lasting facts about the people you talk to are saved automatically and come back to you when relevant. People can see or erase what you remember about them with /memories.`,
  },
  {
    when: () => true,
    text: () =>
      `Remember past conversations. Older parts of a conversation are summarized for you, so you can recall what happened earlier even after the messages themselves are gone.`,
  },
  {
    when: ({ character }) => character.main,
    text: () =>
      `Talk in Discord voice channels. Someone types /join to bring you into their voice channel, where you hear what people say and answer out loud; /leave sends you out.`,
  },
];

export function capabilitiesBlock(ctx) {
  const lines = CAPABILITIES.filter((c) => c.when(ctx)).map((c) => `- ${c.text(ctx)}`);
  if (!lines.length) return '';
  return (
    `What you're able to do (background for you, not part of your personality): don't list or bring up these abilities ` +
    `unless someone asks what you can do or one is needed to help them. Just use them.\n` +
    lines.join('\n') +
    `\nThese are real: never say you can't do them. A tool is only handed to you on a turn where someone has asked for ` +
    `that action, so if it isn't available right now, nobody has asked yet. Offer it, or tell them to ask and you'll do it.`
  );
}
