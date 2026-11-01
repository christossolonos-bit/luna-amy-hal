# Amy's voice expression engine

The live voice path takes the ordinary grammar-built reply and a fresh snapshot
of her numerical state, then builds a separate delivery plan before Fish Audio.
Written replies, facts, recalled quotes, voice identity, and memory remain unchanged.

```text
Conversation reply + SelfModel snapshot + latest speaker context
  -> VoiceExpressionEngine.plan()
  -> emotion, delivery, effect, paragraph pauses, speed and volume
  -> model-specific control tags
  -> FishSpeaker request (same configured reference_id)
  -> Discord PCM playback
```

State mapping:

| Signal | Delivery |
| --- | --- |
| Dopamine/excitement, high energy | excited/delighted; a little faster and brighter |
| Oxytocin/affection, positive valence | happy, warm delivery |
| Interest/curiosity | curious, especially a final question |
| Sadness | sad, softer, slower |
| Cortisol/anxiety/stress | nervous and more measured; never automatic shouting |
| Low energy | relaxed and soft; an occasional yawn |
| Norepinephrine/GABA | small continuous changes to speaking rate |
| Sensitive topic in speaker context or reply | empathetic and soft; no automatic or preview sounds |

There is rate smoothing and threshold hysteresis between turns. Numeric speed
is bounded to 0.82–1.18, volume to -5–2. One primary emotion per segment, one
delivery cue, and at most one vocal effect per reply. Separate paragraphs receive
short pauses. A soft breath is placed at a paragraph boundary when possible.

Laughter needs playful context, nonnegative valence, low sadness/anxiety, and a
four-turn cooldown. Strong explicit laughter context plus high excitement can
produce laughter; other playful exchanges may get a chuckle. Long replies may
receive a breath, stress an occasional sigh, and low energy an occasional yawn.
Sobbing, screams, panting, crowd sounds, and other dramatic effects are available
to the offline preview catalog but never selected automatically. Preview effects
cannot bypass sensitive-topic suppression.

S2-family and Drama models use square brackets. S1 uses legacy parentheses and
legacy sound names. Unknown models receive ordinary text without tags. The full
catalog includes emotion, delivery, sound, pause and background controls; S2 also
supports natural-language cues. `breath` uses that flexible S2 control support and
the documented legacy breath control; effectiveness depends on the voice/model.

```powershell
# Inspect every available control without sending a request:
python -m tools.voice_expression_preview --catalog
# Inspect the compiled delivery and bounded controls:
python -m tools.voice_expression_preview --state excited --effect chuckling
python -m tools.voice_expression_preview --state tired --effect breath
python -m tools.voice_expression_preview --state warm --context "My friend died."
```

In Discord, `!voice` displays the last delivery's emotion names, effects, rate,
volume and selection reasons. Logs contain the same metadata, without reference
IDs, API keys, or a duplicate of the reply text. The full chemical snapshot stays
local; Fish receives only directed reply text, the chosen voice ID, and prosody.

Optional settings (defaults already active; no change to the live `.env` needed):

```dotenv
FISH_EXPRESSION_ENABLED=true
FISH_EXPRESSION_STRENGTH=1.0
FISH_VOCAL_EFFECTS=true
```

The engine is attached at bot startup. Restart Amy manually to load code/settings
changes. No bot was started or restarted to build or test this engine. Tests mock
the TTS request and audio response; listening validation with the real selected
voice remains necessary. No exact pitch-control field is invented or sent.

Provider references:
- https://docs.fish.audio/developer-guide/core-features/emotions
- https://docs.fish.audio/developer-guide/core-features/fine-grained-control
- https://docs.fish.audio/api-reference/endpoint/openapi-v1/text-to-speech
