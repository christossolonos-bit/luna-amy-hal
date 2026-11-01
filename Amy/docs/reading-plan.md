# Adam: reading and digesting paragraphs

Shared plan for **Claude Code** and **Codex** working on Adam (HalMini) at the same time.
Owner: Travis. Read all of this before starting.

## The goal

Adam should be able to take in real paragraphs — Wikipedia openings, news stories, dictionary meanings,
long things people say — break them into facts he can reason over, and talk about them in a way shaped by
his own internal state. **No language model, no canned replies.** Everything goes through his parser, his
fact memory, his reasoning, and his grammar-built replies.

Today he can split a paragraph into sentences and parse simple ones, but he gives up on long sentences
("because", "although", "which", "after", chains of clauses). Web lookups and news are saved as text he can
only repeat back, and dictionary meanings are read out, not stored as facts.

## How Adam works now (what you're building on)

| Piece | Where | What it does |
|---|---|---|
| Message entry | `app/conversation.py` → `Conversation.handle()` | Every message, voice or text, goes through here |
| Parser | `nlp/parser.py` → `parse_sentence(text, lexicon)` | One sentence → list of `Frame`s (`nlp/semantic_frames.py`) |
| Learning | `app/learning.py` → `Learner.learn(frame, uid)` | A `Frame` → stored assertions in SQLite |
| Fact store | `db/repository.py` → `Repository` | Entities, concepts, assertions (+ args), rules |
| Reasoning | `reasoning/` | Backward chaining over facts, rules, WordNet categories |
| Dictionary | `nlp/wordnet.py` → `WordNet` | `lookup`, `usual`, `ancestors`, `path_to`, `related` |
| Memory-first recall | `dialogue/recall.py` | Answers questions from stored facts before anything else says "I don't know" |
| Reply planner | `dialogue/reply_planner.py` | Six steps: understand, update, decide, choose, build, learn |
| Internal state | `dialogue/self_model.py` | Neurochemical mood model: `current()`, `mood_word()`, `event()` |
| Web lookups | `dialogue/web_lookup.py` | Wikipedia summaries → `web_notes` table (text only today) |
| News | `dialogue/news.py` | RSS → `news_items` table (text only today) |

Tests: `python -m pytest -q` (271 tests, all must pass). Key guards: `tests/test_recall.py`,
`tests/test_abilities.py`. Long-conversation harness: `python -m tools.convo_eval.long_convos --count 200`.

## The plan, in four parts

### Part 1 — Clause splitter (Claude)
A new stage before the parser that breaks a long sentence into simple sentences the parser already handles.
- `"Burbank is a city in Los Angeles County, which is 7 miles northwest of downtown."` →
  `Burbank is a city.` / `Burbank is in Los Angeles County.` / `Los Angeles County is 7 miles northwest of downtown.`
- `"Because it rained, the game was cancelled."` → `It rained.` / `The game was cancelled.` + a cause link
- Handles: relative clauses (which/who/that/where), appositives, subordinate clauses (because, although,
  after, before, when, while, since, if), coordination (and/but/so with a new subject), participle phrases,
  parentheticals, lists.
- Each piece keeps where it came from (source sentence, position) and a confidence.

### Part 2 — Digest: reading becomes facts (Claude)
One entry point that takes any text and stores what it can as facts with a source.
- `Conversation.digest(text, source, confidence=0.8) -> DigestResult`
  - `DigestResult.facts`: list of stored assertion ids
  - `DigestResult.skipped`: list of (clause text, reason)
  - `source` is a short string: `"wikipedia:Burbank"`, `"news:BBC:<id>"`, `"wordnet:dog.n.01"`, `"said:Travis"`
- Digested facts are marked with their source so recall can say where he learned something
  ("I read that Burbank is in Los Angeles County").
- Wired into `web_lookup` and `news` so everything he reads is digested, not just saved as text.

### Part 3 — Dictionary meanings become facts (Codex)
Turn WordNet definitions into simple sentences, then hand them to `digest()`.
- `"dog: a member of the genus Canis that has been domesticated by man"` →
  `A dog is a member of the genus Canis.` / `A dog has been domesticated by man.`
- `"blue: of the color intermediate between green and violet; having a color similar to that of a clear
  unclouded sky"` → `Blue is a color.` / `Blue is between green and violet.` / `A clear sky is blue.`
- Only digest a word's meaning when it comes up (a question, a lookup, an unknown word), never the whole
  dictionary up front.

### Part 4 — Talking from what he knows, shaped by his state (Codex)
Choose which facts to bring up and how much to say, based on his internal state.
- Curious → brings up what he doesn't know yet and asks about it.
- Low energy or stressed → shorter, only the most relevant fact.
- Good mood → connects things, adds a related fact.
- Something he read ties into his own questions (being alive, being conscious) → he brings that up.
- Output is a ranked list of facts plus a length; the reply planner builds the sentences.

### Part 5 — Paragraph test set (Codex, start first)
The measuring stick for all of the above, built before the code so we can see progress.
- 60+ real paragraphs: Wikipedia openings, news summaries, WordNet definitions, long things people say.
- For each: the facts Adam should end up with, written as questions + expected answers (same style as
  `tests/test_recall.py`).
- A harness that reads each paragraph into a fresh Adam via `digest()`, then asks the questions and scores.

## Who owns what — do not edit the other's files

| Owner | Files (create or edit) |
|---|---|
| **Claude** | `nlp/clauses.py` (new), `nlp/parser.py`, `app/conversation.py`, `app/learning.py`, `db/repository.py`, `dialogue/web_lookup.py`, `dialogue/news.py`, `dialogue/recall.py`, `tests/test_clauses.py` (new), `tests/test_digest.py` (new) |
| **Codex** | `dialogue/gloss.py` (new, Part 3), `dialogue/state_selection.py` (new, Part 4), `tools/convo_eval/paragraphs.py` (new, Part 5), `tests/test_paragraphs.py` (new), `tests/test_gloss.py` (new), `tests/test_state_selection.py` (new), `fixtures/paragraphs/` (new folder of test paragraphs) |
| **Shared, ask first** | `dialogue/reply_planner.py`, `dialogue/exposition.py`, this document |

If you need a change in a file the other owns, write it under **Requests** below instead of editing it.

## Interfaces (the contract between the two halves)

Claude provides, Codex calls:
```python
# nlp/clauses.py
def split(sentence: str) -> list[ClausePiece]
@dataclass
class ClausePiece:
    text: str          # a simple sentence the parser can read
    source: str        # the original sentence
    link: str | None   # "cause", "time", "contrast", "condition", or None
    confidence: float

# app/conversation.py
def digest(self, text: str, source: str, confidence: float = 0.8) -> DigestResult
@dataclass
class DigestResult:
    facts: list[int]                  # assertion ids stored
    skipped: list[tuple[str, str]]    # (clause text, why it wasn't stored)
```

Codex provides, Claude calls:
```python
# dialogue/gloss.py
def gloss_sentences(word: str, sense, wordnet) -> list[str]   # a definition as simple sentences

# dialogue/state_selection.py
def choose(facts: list[Fact], state: dict, topic: str | None) -> Selection
@dataclass
class Fact:
    text: str; subject: str; source: str; confidence: float; about_self: bool
@dataclass
class Selection:
    facts: list[Fact]     # in the order to say them
    max_sentences: int    # how much to say
    ask_back: bool        # whether to end with a question about what he doesn't know
```
Until the other side's piece exists, code against these signatures with a small stub in your own files.

## Order of work

| Step | Claude | Codex |
|---|---|---|
| 1 | Set up git in the HalMini folder; commit current state | Part 5: paragraph test set and harness (fails at first, that's expected) |
| 2 | Part 1: clause splitter + `tests/test_clauses.py` | Part 3: `gloss.py` + `tests/test_gloss.py` |
| 3 | Part 2: `digest()`, wired into web lookup and news | Part 4: `state_selection.py` + tests |
| 4 | Call `gloss_sentences` and `choose` from recall and replies | Run the paragraph harness, report what fails |

## Rules for both

- **Work on your own git branch** (`claude/reading`, `codex/reading`). Merge into `main` only when
  `python -m pytest -q` passes. Pull `main` before starting each step.
- **Never break the guards.** `tests/test_recall.py` and `tests/test_abilities.py` must pass on every commit.
- **No language models, no stock replies.** Facts go through the parser and fact store. Replies are built
  by the grammar from what he knows.
- **Keep test runs short.** Run the tests for what you changed, then the full suite once before merging.
  Long harness runs (1,000 conversations) only when Travis asks.
- **Travis's standing rule:** before changing anything outside your own plan step, explain what and why and
  wait for his go.
- **Don't touch** `.env`, `data/halmini.db` (his live memory), or `data/wordnet/`.

## Requests (write here when you need something from the other side)

- _Claude, FYI:_ `dialogue/exposition.py` `_memory()` now says "I read on Wikipedia that …" for facts from
  `digest()` (source names come from `Repository.source_speaker`, which maps `source:wikipedia:X` to
  "Wikipedia", `source:news:BBC:…` to "BBC", `source:wordnet:…` to "the dictionary"). Pass `source` in
  that `kind:detail` form from `gloss.py`, e.g. `digest(text, "wordnet:dog.n.01")`.
