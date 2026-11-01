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
  - moods and automatic expressions
  - push-to-talk: hold Space or the mic button. There is also a hands-free mode.
  - The avatar is `data/avatar.glb`. To use another file, set `AVATAR_PATH` in `.env`. For a quick try, drop a `.glb` on the page.
  - Avatars with ARKit face shapes and `viseme_*` targets work best, such as Avaturn and Ready Player Me.

## Setup

1. **Discord app.** Create one at https://discord.com/developers/applications.
   - **Bot** tab: Reset Token and copy it. Under *Privileged Gateway Intents*, turn on **Message Content** and **Server Members**.
   - **General Information:** copy the Application ID.
   - **OAuth2 → URL Generator:** tick `bot` and `applications.commands`, then under bot permissions tick *View Channels, Send Messages, Read Message History, Connect, Speak*. Open the URL to invite the bot.
2. **Fish Audio.** Get an API key at https://fish.audio. Optionally copy the ID of a voice you like, or one you clone, and use it as `FISH_VOICE_ID`.
3. **Ollama.** It must be running with your chat model (default `gemma4:12b`) and `bge-m3` pulled.
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
