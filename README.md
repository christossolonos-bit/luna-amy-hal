# HalMini-share

**Main runtime = Amy Smith** (`amy-smith\`), with **Luna’s persona**.  
HalMini is the fact brain **inside** that stack (not a separate Discord bot).

| Folder | Role |
|--------|------|
| `amy-smith\` | **Main** — Discord, dashboard `:3000`, memory, tools, avatar studio |
| `Amy\` | HalMini dialogue/fact engine package (called from Amy Smith) |
| `Luna\` | Luna assets / HalMini DB / optional old Riko launcher |
| `HalMini\` | Shared HalMini package copy |

## Flow

```
You → Amy Smith (Luna persona)
        → HalMini first (facts)
        → miss → OpenRouter / Ollama
        → teach HalMini + remember in companion.db
```

## Start

Double-click **`START.bat`** → runs `amy-smith\start.bat`.

Dashboard: http://127.0.0.1:3000/

Wake word: **Luna**. Bot display name: **Luna**.

## Note

Hal was never shipped *inside* the Amy Smith repo originally — it’s wired in now via `amy-smith\src\halmini.js` → `tools\halmini_ask.py` → `Amy\` + `Luna\data\luna_amy.db`.
