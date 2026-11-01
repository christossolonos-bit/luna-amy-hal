# Luna + Riko combine

**Base:** Riko VRM companion (avatar, expressions, lip-sync, local mic).  
**Luna:** Second character + Discord channel into the same brain/avatar.  
**Fact brain:** Amy skills + persona as **Luna** (see [`HALMINI.md`](HALMINI.md)).
Amy Discord bot is not used — LunaBot is `discord-bridge/`.

## Layout

| Path | Role |
|------|------|
| `character_config.yaml` | Riko |
| `character_config_luna.yaml` | Luna personality (Discord-first vibe) |
| `client/characters/riko/` | Riko VRM |
| `client/characters/luna/` | Luna VRM (`Luna.vrm`) |
| `discord-bridge/` | Discord bot → Riko `/send_message` + plays `/talk` audio in VC |
| `start_luna_local.bat` | Luna on desktop only |
| `start_luna_discord.bat` | Luna + Discord bridge |

## Architecture

```
Discord text / /luna say
        ↓
  POST :8100/send_message
        ↓
  chat_terminal (RIKO_CHARACTER=luna, RIKO_VOICE_SOURCE=browser)
        ↓
  HalMini (facts) ──knows──→ TTS → VRM /talk
        │ miss
        ↓
  LLM → TTS → VRM /talk  (+ teach answer back into HalMini)
        ↓
  Browser VRM lip-sync     Discord VC hears same clip
```

### HalMini hybrid (`server/halmini_bridge.py`)

Full scope: [`HALMINI.md`](HALMINI.md).

Env: `HALMINI_ROOT=D:\Amy\Amy`, `HALMINI_PERSONA=amy`, `HALMINI_SELF_NAME=Luna`,
`HALMINI_DB=data/luna_amy.db`.

When the brain cannot ground an answer, the LLM replies and that reply is taught
back (`Remember this: …`). Discord remains this repo’s LunaBot — no Amy bot.

## Quick start

**One click:** double-click **`START.bat`**

That starts Luna’s VRM page, chat loop, mic, and Discord bridge (if `discord-bridge\.env` exists).

Keep **Ollama** running first.

## Quick start — Luna Discord

1. Create a Discord application + bot; enable **Message Content** intent and **Voice**.
2. Invite the bot with `applications.commands` + connect/speak in voice.
3. Copy `discord-bridge/.env.example` → `discord-bridge/.env` and set:
   - `DISCORD_BOT_TOKEN`
   - `DISCORD_CLIENT_ID`
   - optional `DISCORD_GUILD_ID` (faster slash-command register)
4. `start_luna_discord.bat`
5. In Discord: join a VC → `/luna join` → talk, or `/luna say text:hey`
   Music: `/luna play query:never gonna give you up` · `/luna skip` · `/luna stop`
6. Keep the VRM page open so you see her face; Discord plays the same TTS.

### Discord commands

| Command | Action |
|---------|--------|
| `/luna join` | Join your VC and **listen** (Whisper STT → Luna) |
| `/luna leave` | Leave VC |
| `/luna say` | Text → Luna |
| `/luna play` | YouTube song name or URL |
| `/luna skip` | Skip track |
| `/luna stop` | Stop music + clear queue |
| `/luna status` | Health + now playing |
| `luna, …` / @mention | Text chat → Luna |

Needs on PATH: **yt-dlp**, **ffmpeg**. STT uses the project `.venv` faster-whisper.

## Env knobs

| Variable | Meaning |
|----------|---------|
| `RIKO_CHARACTER=luna` | Load Luna yaml + VRM |
| `RIKO_VOICE_SOURCE=both` | Mic + Discord/browser text |
| `LUNA_DISCORD_LISTEN=0` | Disable VC listening |
| `LUNA_WAKE_REQUIRED=1` | Hear everyone; reply only when someone says **Luna** |
| `LUNA_WAKE_WORDS` | Default: `luna,hey luna,…` (in `discord-bridge/.env`) |
| `LUNA_WAKE_FOLLOWUP_MS=0` | Must say **Luna** every time (no silent follow-up) |

## Patreon / licence

Riko core remains personal-use Patreon packaging. Do not redistribute the Patreon build.

