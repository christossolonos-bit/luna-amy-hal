# Luna under HalMini-share

```
D:\HalMini-share\
  START.bat
  HalMini\      fact engine
  Amy\          HalMini dialogue persona
  Luna\         avatar + LunaBot + NVIDIA
  amy-smith\    Amy Smith companion (memory + dashboard :3000)
```

## Stack

| Layer | Role |
|-------|------|
| Luna Riko | Avatar, Discord, TTS |
| Amy / HalMini | Answers first when she knows |
| Amy Smith memory | Long-term per-person facts (imported from `amy-smith\data\companion.db`) |
| NVIDIA | Talks on HalMini miss → teach-back |

Luna’s persona stays Luna. Amy Smith’s Discord bot is **not** started by `START.bat` (avoid two bots).
