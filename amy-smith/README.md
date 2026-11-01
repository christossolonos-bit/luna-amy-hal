# new champ

A Discord companion bot. It chats in text and voice, remembers people, runs its brain on local Ollama, speaks with Fish Audio, and listens with local Whisper. It also has a local web dashboard.

## What it does

- **Text chat:** replies to DMs, @mentions, and every message in channels you choose.
- **Voice:** `/join` puts it in your voice channel. It listens to everyone, transcribes speech locally with Whisper (free, on your GPU), answers out loud in a Fish Audio voice, and stops talking if you interrupt it.
- **Memory:**
  - Short-term: keeps a rolling history per channel or voice session.
  - Long-term: after each exchange it saves facts about each person and recalls the relevant ones semantically using `bge-m3` embeddings. Everything is stored in `data/companion.db`.
- **Dashboard:** open http://127.0.0.1:3000 for:
  - a live activity feed and voice controls (leave, make it say something)
  - a chat playground
  - a memory browser and editor
  - conversation history
  - settings: persona, model, voice ID, channels, wake word

- **Avatar studio:** open http://127.0.0.1:3000/avatar.html, or use the Avatar button in the dashboard. It shows a 3D character that talks with the same brain, memory and Fish Audio voice:
  - lip sync driven by the voice audio
  - blinking, eyes that follow your pointer, idle head motion and breathing
  - an emotional state that follows the conversation: the model tags each sentence with how she feels (playful, smug, worried, flirty and about 20 more), and her face, head pose, gaze and gestures blend from that. Moods carry between messages and fade back to calm. The Expressions button lets you nudge her feelings or play a gesture.
  - push-to-talk: hold Space or the mic button. There is also a hands-free mode.
  - realistic rendering: soft shadows, skin with light scattering, wet eyes, antialiased hair, ambient occlusion, bloom and film grain. Settings has a Graphics level (High / Medium / Low), and it steps down on its own if the frame rate drops.
  - a cast: Amy (the main bot) and Leo stand side by side and share one conversation. Name someone ("Leo, ...") and they answer; otherwise whoever you last spoke to does. You can also click a character or use the picker at the top. Each has their own persona, Fish voice and mood, set under Settings > Characters. Amy's name, persona and voice are also the Discord bot's. Extra characters are in `data/characters.json`, with models in `data/characters/`.
  - The avatar is `data/avatar.glb`. To use another file, set `AVATAR_PATH` in `.env`. For a quick try, drop a `.glb` on the page.
  - Avatars with ARKit face shapes and `viseme_*` targets work best, such as Avaturn and Ready Player Me.

## Setup

1. **Discord app.** Create one at https://discord.com/developers/applications.
   - **Bot** tab: Reset Token and copy it. Under *Privileged Gateway Intents*, turn on **Message Content** and **Server Members**.
   - **General Information:** copy the Application ID.
   - **OAuth2 → URL Generator:** tick `bot` and `applications.commands`, then under bot permissions tick *View Channels, Send Messages, Read Message History, Connect, Speak*. Open the URL to invite the bot.
2. **Fish Audio.** Get an API key at https://fish.audio. Optionally copy the ID of a voice you like, or one you clone, and use it as `FISH_VOICE_ID`.
3. **Ollama.** It must be running with your chat model (default `gemma4:12b`) and `bge-m3` pulled.
   - **OpenRouter (optional):** put a key from https://openrouter.ai/keys in `OPENROUTER_API_KEY`.
     - In Settings > Brain, switch "Replies come from" to OpenRouter and pick any model, including big reasoning ones, with how hard it should think.
     - The key also turns on image generation in Discord and the avatar studio. Ask her to draw or make a picture; images are saved to `data/images/`.
     - Memory and embeddings stay on local Ollama either way.
     - The owner can switch brains just by asking ("switch to OpenRouter and use kimi", "go back to local").
     - Music with Google Lyria 3: ask her for a song. She asks what it's about and whether you want a 30-second clip ($0.04) or a full song ($0.08), then sends the MP3. Songs are saved to `data/music/`.
- **Music player:** everything in `data/music/` (her songs plus any MP3, WAV, OGG, M4A or FLAC you drop in) is her library. Ask her to play a song by title, make a playlist ("a playlist called gym rage with the heavy ones"), shuffle, queue, pause, skip or stop. In a voice call it plays in the channel and pauses while she talks; in the avatar studio it plays in the page. Playlists are in `data/playlists.json`.
- **Projects on your PC (owner only):** she can browse folders, read files, create files and folders, make precise edits, undo a change, move, rename, copy, find files, and send one into the chat. Paths can start with Desktop, Documents, Downloads, Pictures, Music or Videos. She can't delete anything or run commands, and keys, `.env` files and AppData are off limits. The version before each change is kept in `data/file-history/`.
- **Video replies:** open the dashboard's **Recorder ↗** page, click it once to allow audio, and leave it open (not minimised). Her Discord text replies then also get a 720×720 WebM of her face saying them, added to the same message a few seconds after the text. Replies over ~45 seconds of speech stay text-only, and if the recorder is closed or anything fails, the text simply goes out alone. `AVATAR_VIDEO=off` in `.env` switches it off. Each clip uses Fish Audio for the voice.
- **Pressure system (Amy's design):** internal buckets (boredom, curiosity, irritation, restlessness, usefulness, unease, and loneliness per person) fill and drain from conversation and time, and colour how she talks through a one-line mood in her prompt. When one crosses its threshold she "wants" something (research, make something, reach out); for now those wants are only logged (activity type `pressure`), nothing is said or done. Every rate and threshold is in `data/pressure-config.json` (created on first start); state in `data/pressure-state.json`. Saying "back off" or "be quiet" calms her for two hours. Owners can ask her for her mood (`self` → mood) or see `/api/pressure`.
- **Check-ins (opt-in):** once, in person in a voice call, she asks people she knows whether she may check in on them on her own, how often, and what they'd like to talk about. The answer goes on their profile card; a no, or telling her to back off, is permanent. For now she only checks in during voice calls they're in, never more often than they said, at most 3 unprompted lines a day and 30 minutes apart, and never over a conversation (30 s of quiet first). DMs and text channels come later (`reach_channels` in `data/pressure-config.json`).
- **Journal:** when she's bored or curious she looks something up on her own and writes a short entry to `data/journal/<date>.md` (up to 8 a day); it can come up later in conversation.
- **Continuity:** every reply carries a small block (about 250 tokens) with the facts of time (the time now, when this conversation and person were last active, whether she was restarted and how long she was offline) and her recent mistakes: tool errors, phantom claims and times someone told her she was wrong stay as cautions for a day and escalate if repeated. Her mood line shows what changed in the last hour, and open notes show their age, nagging after a day. State is in `data/continuity.json`.
- **Tool reliability:** if she needs a tool the planner didn't hand her, she can request it mid-reply (up to 2 per reply); a tool that was asked for but not yet used stays in hand for 3 more turns. After every reply, any claim that something was saved, sent, played or done is checked against the actual tool results; one with nothing behind it is re-done or corrected (out loud in voice). Every tool failure, cut-short read, tool request and phantom claim is logged to `data/tool-failures.jsonl`, with a rollup sorted by count in `knowledge/tool-failures.md`.
- **Git (owner only):** status, diff, add, commit and push on any repo in your home folder; nothing else (no reset, checkout, branches or force). Git runs directly, never through a shell. A push first shows exactly what would go up, and only happens after you say yes in your next message.
- **Models on the fly:** ask "what are the best coding models?" for a numbered list from OpenRouter, then "go with 2" to switch. Also works for reasoning, general and cheap.
- **Background helpers:** "spin up a helper to build X in Documents/x" hands the job to a sub-agent with file access and web search. It reports back in the chat (and out loud in voice) when done; reports are saved in `data/tasks/`. Ask how it's going, or cancel it.
- **Working notes:** each conversation has a private scratchpad (moods, threads, commitments, open questions, callbacks; max 40 notes, ~2000 tokens, cleared after 24h idle, `SCRATCHPAD_TTL_HOURS`), and up to 5 active threads follow her across every channel (dropped after 14 days untouched). Neither becomes a long-term memory. The owner can see or wipe them with `/memories notes`.
- **Memory rebuild:** tell her to go through the chat history and rebuild her memories (add "wipe them first" to start clean). It runs in the background; the activity feed shows when it's done.
4. **.env file.** Fill in `.env`.
5. **Install and register the slash commands:**

```bash
npm install
```

```bash
npm run deploy
```

6. **Start the bot:**

```bash
npm run dev
```

## Commands

| Command | |
|---|---|
| `/join` | Join your voice channel and start listening |
| `/leave` | Leave voice |
| `/memories list` | See what it remembers about you (only you can see it) |
| `/memories forget [id]` | Forget one memory or all of them |
| `/ping` | Latency check |

## Tuning

- **Voice reply speed:** depends mostly on model size. `gemma4:12b` is a good balance; `gemma4:e4b` is snappier, and `gemma4:26b` is smarter but slower. You can switch from the dashboard.
- **Persona:** lives in `persona.md`. Edit it there or in the dashboard.
- **Turn-taking:** `VOICE_SILENCE_MS` controls how long a pause counts as the end of someone's turn.

## Layout

```
src/
  index.js            Discord client, text chat
  companion.js        prompt building, replies, memory extraction
  llm.js              Ollama chat (streamed by sentence) + embeddings
  memory.js           SQLite history + long-term memories
  voice/session.js    listen → transcribe → reply → speak, barge-in
  voice/fishAudio.js  Fish Audio TTS + ASR
  dashboard/          web dashboard (server + single-page UI)
  commands/           slash commands
```
