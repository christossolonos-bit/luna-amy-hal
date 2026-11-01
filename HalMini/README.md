# HalMini: an independent conversational reasoning engine

This implements `independent_conversational_engine_spec.docx`. It uses no LLM and no remote text generation. It needs only the Python 3.11+ standard library (`sqlite3`), plus pytest for running tests.

## Install and run

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
python -m interfaces.cli
```

To pick a database file, use `--db path/to.db` or set `HALMINI_DB` (see `.env.example`). Use `--speaker NAME` to say who is talking.

REPL commands:

- `:as NAME`: switch speaker, for example to another bot.
- `:teach FILE`: feed in a whole text file.
- `:why`, `:debug`, `:facts`, `:open`, `:quit`.

From code, another bot can teach it like this:

```python
from app.conversation import Conversation
from db.repository import Repository

convo = Conversation(Repository("data/halmini.db"))
reply = convo.handle("Howard drove his red truck to Ohio yesterday. He lives in Columbus.", speaker="TutorBot")
```

## Run everything

Double-click `start_halmini.bat`. It starts the Discord bot (which also starts the Node voice
sidecar) in one window and the dashboard in another, then opens http://127.0.0.1:8765 in your
browser. Close a window to stop that part.

To run only the dashboard, use `python -m interfaces.dashboard`. The dashboard is a read-only view
of the same database (what it has learned, the knowledge graph, conversation history, open
questions, corrections), and its **Settings** tab edits `.env` directly: Discord token, channel
ids, listen mode, wake words, barge-in, speech models and the engine limits. Change what you want,
press **Save settings**, then restart the bot to apply. Secret values such as `DISCORD_TOKEN` and
`FISH_API_KEY` are never sent to the page; type a new value to replace one, or tick **clear** to
remove it.

## Discord: text and voice

```
Discord ⇄ sidecar/ (Node: discord.js + @discordjs/voice, DAVE E2EE via @snazzah/davey)
             │  JSON lines over stdin/stdout
             ▼
         Python: faster-whisper (STT) → wake word → Conversation → Fish Audio (TTS)
```

The Discord connection lives in a Node sidecar because Discord's DAVE end-to-end voice encryption is supported there. The sidecar handles joining, decryption, per-speaker receive (each speaker's utterance ends after 900 ms of silence), Opus decode and playback. Python never touches Discord and does all the language work. Speech recognition runs locally, and no audio is stored. Text-to-speech uses the Fish Audio API.

### Setup

1. Create a bot at discord.com/developers. Enable the **Message Content** and **Server Members** intents, and invite it with the Connect, Speak, Send Messages and Read Message History permissions.
2. Copy `.env.example` to `.env` and set `DISCORD_TOKEN`.
3. Install:
   ```bash
   cd sidecar && npm install && cd ..
   python -m pip install -r requirements-voice.txt
   ```
4. Put your Fish Audio API key in `FISH_API_KEY`. Optionally set `FISH_VOICE_ID` to the reference id of a voice from fish.audio. Fish Audio is the only cloud piece: it receives the reply text, nothing else. `TTS_ENGINE=piper` switches to local Piper, and `TTS_ENGINE=none` gives text-only replies.
5. Run:
   ```bash
   python -m interfaces.discord_bot
   ```
   The first run downloads the Whisper model.

### Using it

- Join a voice channel and type `!join`. It posts a notice that it's transcribing.
- **It hears everything but only answers when addressed.**
  - Say "Hal, …", "Hey Hal, …" or "…, Hal?". Common mishearings ("Hall", "Hale") also count.
  - For 8 seconds after it finishes talking, the same person can follow up without the wake word.
  - "Hal?" on its own gets "Yes?".
- Every reply is posted in the text channel along with what it heard, so you can see transcription mistakes.
- Speech it isn't addressed to goes into its conversation memory ("Hal, what did Bob say about the truck?") but isn't learned as fact. `!learn on` changes that.
- By default nothing interrupts it while it speaks: replies queue up and each is spoken in full. Set `HALMINI_BARGE_IN=true` if you want talking over it to cut it off. It never transcribes its own voice.
- In text channels, @mention it or DM it; listed `HALMINI_TEXT_CHANNELS` need no mention. Other bots can teach it the same way.
- Commands:
  - `!join`, `!leave`, `!stop`
  - `!listen on|off`
  - `!mode wake|always`
  - `!learn on|off`

## Pipeline

Each sentence in a turn goes through these stages:

1. Segmenting (`nlp.discourse.split_sentences`).
2. Stripping filler and hedges (`strip_discourse`). Hedges lower confidence; they are not discarded.
3. Parsing (`nlp.parser`) into one or more `Frame`s.
4. Resolving references (`app.learning.Resolver`): pronouns, "the X", "his X", "Howard's X", "I"/"you".
5. Either learning (`Learner`) or querying (`reasoning.engine`).
6. Planning, realizing and validating the reply. The validator parses each generated clause back and checks the roles and polarity.

The trace for every turn is stored in `inference_traces`.

## What it understands

- **Any verb**, including ones it has never seen. A new verb is learned with its inflections ("Howard yeets rocks" → `yeet`), as is tense ("drove", "has visited"). Ditransitives are converted: "gave him a book" is stored as a book given to him.
- **Statements:** `X VERB [Y] [to/in/at/with… Z] [yesterday / last week / in 2020]`, `X is (a N | ADJ | in PLACE)`, `Ns are Ms`.
- **Negation:** "not", "never", "doesn't", "can't".
- **Modals:** "can", "will", "should".
- **Linking:** coordination with "and" (subjects, objects, verb phrases, whole clauses), commas, "because"/"so", relative clauses ("a truck that is red"), and appositives ("Bob, who is a doctor, …").
- **Filler and hedges:** "so basically", "you know", "hey Hal," and "did you know that" are stripped. Hedges set confidence: "I think" 0.6, "probably" 0.6, "maybe" 0.4, and so on. Restating a fact firmly raises its confidence.
- **Yes/no questions:** "Does/Did/Can/Is …?" These return yes, no, conflicting or unknown. When the best proof is weak (hedged, or inherited from a generic statement), the answer begins "Probably".
- **Wh-questions:** "What does X VERB?", "Who VERBs Y?", "Where/When did X VERB?", "Who does X live with?", "Who is tall?", "What is X?", "Tell me about X".
- **Provenance:** every fact records its speaker. When sources conflict, the answer says who said what. Both claims are kept.
- **Reasoning:** subclass transitivity, instance inheritance, relation-by-kind ("owns a truck" → "owns a vehicle"), negative inheritance, disjointness, and generic inheritance ("Dogs chase cats" + "Rex is a dog" → probably). Inference is bounded by a depth limit and a step budget, with cycle detection.
- **If-then rules:** "If someone owns a dog, they are a pet owner", "Howard is happy if he owns a dog", "Whenever someone is hungry, they eat food", "Anyone who drives a bus is a driver", "If a person is a vegetarian, they don't eat meat" (a negative conclusion counts as evidence for "no"), and "If X is a beagle, X is a dog" (stored directly as taxonomy).
  - Rules are applied by bounded backward chaining (depth 4) with cycle detection. A rule fires only when every condition is proved and none is disputed.
  - Rules chain into each other and answer "who" questions. Explanations cite the facts used and quote the rule.
  - Rules are stored in `learned_rules`, so they persist across sessions.
- **Corrections:** nothing is deleted. Retracted claims are marked `retracted` and logged in `corrections` with the utterance that caused it.
  - "No, he lives in Dayton." / "Actually, …" / "Correction: …" replace the old value of the same slot: the old location, object or category. Compatible categories survive: correcting "dog" to "cat" keeps "animal".
  - "No, Dayton." / "Sorry, I meant a bus." patch the previous statement.
  - "Howard drives a bus, not a truck." and "Howard no longer drives a truck." / "… anymore" retract the old claim and store the negative.
  - "That's wrong" / "Scratch that" undoes everything learned in the previous teaching turn, rules included.
  - "Forget that Howard owns a boat." removes the stated claims behind that one fact.
  - A statement that isn't marked as a correction never removes anything. "Howard drives a truck" followed by "Howard drives a bus" keeps both.
- **Conversation memory:** every turn is stored with its speaker and time.
  - "Repeat that" / "What did you say?" repeat its last answer. "What did I just say?" repeats yours.
  - "What have we been talking about?" / "Summarize our conversation" lists the recent things you said, newest last, skipping acknowledgements and meta-questions.
  - "What did I (or alice) tell you about Howard?", "Search for boats", "Have we talked about trucks?"
  - "When did I tell you that Howard owns a boat?" gives the time and your exact words.
  - Yes/no recall: "Did I tell you that …?" / "Did you say …?"
  - "Remember this: …" teaches the fact.
  - Matching is by meaning and fuzzy, not word for word. "Did I tell you Howard owns a boat?" checks the stored facts, so it matches "Howard has a little red boat". The history search ignores word order and filler, normalises word forms (drove/driving → drive, trucks → truck) and tolerates small typos ("Haward").
  - Topic searches need about 60% of the keywords. Yes/no claim checks need all of them, so "Bob owns a boat" doesn't match "Howard owns a boat".
- **"Anyone" questions:** "Does anyone own a truck?"
- **Gaps are kept:** sentences it can't parse are reported back and stored in `questions` (see `:open`), as are unanswered questions.

## Conversational variation

## WordNet, paragraph explanations, and emotional expression

The local WordNet store is installed at `data/wordnet/wordnet.db`. To rebuild it
on another machine, run `python -m nlp.wordnet --download`. The importer downloads
Open English WordNet 2025+, builds a separate SQLite database, checks its integrity,
and installs it atomically. Conversation lookups are offline and read-only. Set
`HALMINI_WORDNET_DB` to choose another store, or `HALMINI_WORDNET=false` to disable it.
There is no additional Python dependency.

Try teaching `Howard owns a terrier. He lives in Columbus. He likes pizza.` and
then asking `Explain Howard`, `Tell me about Howard in detail`, or `What is a terrier?`.
The paragraph planner combines active remembered facts, category relationships,
lexical descriptions, and conclusions from learned rules. It records the assertion
IDs, WordNet meaning IDs, category paths, rule IDs, confidence, and validation
results in the turn's `paragraph_plan` trace. WordNet definitions are kept as
lexical descriptions; dictionary example sentences never become personal facts.
Ambiguous words prompt for a meaning; answer with a listed number or a short
description. `Tell me more` continues the current speaker's topic without repeating
the evidence already covered. When there is no more evidence, it says so.

Paragraph length follows the available evidence, with a 48-claim computation budget.
The system remains a deterministic grammar and graph engine: it composes paragraphs
from supported material, but does not have an LLM's unrestricted language understanding
or paraphrasing. Meaning selection uses contextual word overlap and conservatively
asks when the result is tied. This does not resolve every ambiguity in ordinary English.
Lexical category deductions supplement simple category questions; other legacy engine
queries still use the existing learned-fact graph.

Numerical emotions now include excitement, sadness, anxiety, frustration, contentment,
interest, and affection. They are derived from persistent chemical levels, decay with
those levels, and are logged in `emotion_events` and periodic state snapshots. They
influence explanation openers, learning acknowledgements, pacing of the written
expression, and whether to ask an optional follow-up. Sensitive topics select a
gentle tone even when excitement is high. Emotional state never changes evidence,
truth values, or inference confidence. The dashboard and `!state` expose these values;
every reply trace includes its expression state. This changes wording, not TTS pitch
or speed. Restart the bot and dashboard to load the changes.

Open English WordNet is derived from Princeton WordNet. OEWN contributions are
licensed under CC BY 4.0; retain attribution when distributing the data:
[Open English WordNet](https://en-word.net/),
[license and attribution](https://github.com/globalwordnet/english-wordnet/blob/main/LICENSE.md).

## Conversational phrase banks

Conversational wording uses local phrase banks in `dialogue/phrasing.py` and
`dialogue/social.py`. There are more than 10,000 distinct templates, including
4,608 general misunderstanding responses and 4,608 responses that introduce a
specific clarification question. Names and facts do not count toward these totals.
Learning acknowledgements, thanks, farewells, compliments, and small acknowledgements
also vary. Compatible sentence parts are combined; no remote text generation is used.
Each intent uses a shuffled deck per conversation, with no repeated template until
its deck is exhausted and no immediate repeat across deck boundaries. Decks reset
on restart. Stored facts, reasoning results, and quoted history keep their content.

## Honest limits


- Rules have one variable ("someone", "a person", "X"). Rules about two things ("if someone owns a dog, the dog is happy") and "or" are not supported. Rule conclusions are not fed back into the conflict check when you teach a new fact.
- Correcting a property only replaces values given in the previous turn, because a thing can be both red and big.
- The grammar is still a hand-written subset of English. Not yet supported:
  - "there is"
  - passives ("was built by")
  - questions with an embedded clause ("did he say that…")
  - comparatives
  - quantities as data ("two trucks" is treated as an unspecified number of trucks)
  - aliases ("Howie is Howard")
- Pronouns use recency only: "he", "she" and "they" pick the last named subject, and "it" picks the last thing mentioned. There is no gender model or salience ranking.
- If several things of the same kind exist, "the truck" refers to the most recently mentioned one in this session, or else the only one stored.
- Bare singular subjects are read as names ("truck is red" treats "truck" as a name).
- A guessed lemma for an unknown irregular verb can be wrong. The guess is stored in `lexemes`, where it can be checked.
- Not built yet:
  - event and time reasoning beyond matching
  - paragraph planning with varied wording
  - dialogue goals
- The voice path has only been tested with fake STT, TTS and sidecar. It hasn't been run against live Discord yet. Sidecar dependency versions in `package.json` are caret ranges that `npm install` hasn't resolved yet.
- Whisper quality depends on the model. `small.en` is a reasonable CPU default; known names are passed to it as hints.
