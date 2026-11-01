"""Conversational memory: recall what was said, when, and by whom.

Answers questions about the conversation itself rather than about the world:
    repeat that / what did you say?             what did I say / ask?
    what have we been talking about?            summarize / recap our conversation
    what did I tell you about X?                search for X / find X
    when did I tell you (that) S?               did I tell you (that) S?  did you say S?
    have we talked about X?
History comes from `dialogue_turns`; "did I tell you S" also checks the stored claims behind S.
Spoken input arrives with lead-ins ("Um, ...", "Okay so ..."), which are ignored before matching.
"""

import math
import re
from dataclasses import replace
from difflib import SequenceMatcher
from datetime import datetime, timezone

from nlp.lexicon import Lexicon
from nlp.morphology import lemmatize_noun
from nlp.parser import ParseError, parse_sentence
from nlp.semantic_frames import Frame
from nlp.tokenizer import tokenize

_I = re.IGNORECASE
REPEAT_RE = re.compile(
    r"^\W*(?:(?:can|could|would) you |please )*(?:repeat(?: that| yourself| it| what you (?:just )?said)?"
    r"|say (?:that|it) again|what did you (?:just )?say(?: there| again| last)?|what was that|come again"
    r"|pardon(?: me)?|what\?*)\W*$", _I)
LAST_USER_RE = re.compile(
    r"^\W*what (?:did|was it) i (?:just )?(?:say|tell you|ask(?: you)?|said)(?: there| last| just now| before)?\W*$", _I)
ABOUT_RE = re.compile(
    r"^\W*what (?:did|have|has) (i|you|we|\w+) (?:just )?(?:say|said|tell you|told you|tell me|told me|ask(?:ed)?|"
    r"talk(?:ed)? about|mention(?:ed)?)(?: to you)? about (.+?)\W*$", _I)
WHEN_RE = re.compile(
    r"^\W*when (?:did|have) (?:i|we) (?:tell|told|say|said|mention(?:ed)?)(?: you| to you)?(?: that| about)? (.+?)\W*$"
    r"|^\W*when did you (?:learn|hear|find out|get told)(?: that| about)? (.+?)\W*$", _I)
DID_SAY_RE = re.compile(
    r"^\W*(?:did|have) (i|you) (?:ever |already )?(?:tell(?: you| me)?|told(?: you| me)?|say|said|mention(?:ed)?|"
    r"ask(?: you)?|asked(?: you)?)(?: that| about| whether| if)? (.+?)\W*$", _I)
TALKED_RE = re.compile(
    r"^\W*(?:have|did) we (?:ever |already )?(?:talk(?:ed)?|speak|spoken|discuss(?:ed)?|chat(?:ted)?)(?: about)? (.+?)\W*$", _I)
SEARCH_RE = re.compile(
    r"^\W*(?:search|look up|find|look)(?: (?:back )?(?:through |in )?(?:our |the )?(?:conversation|chat|history))?"
    r"(?: for)? (.+?)\W*$", _I)
SUMMARY_RE = re.compile(
    r"^\W*(?:what(?:'?s| is| was)? (?:this|that|it) (?:all )?about"
    r"|what (?:have|had|did|were|was) (?:we|you and i) (?:been |just )?"
    r"(?:talk(?:ed|ing)?|speak|spoke|spoken|discuss(?:ed|ing)?|chat(?:ted|ting)?)(?: about)?"
    r"(?: (?:today|so far|just now|recently|before|last))?"
    r"|what (?:are|were) we (?:talking|speaking|chatting|discussing)(?: about)?"
    r"|(?:can|could) you (?:summarize|sum up|recap)(?: (?:our|the) (?:conversation|chat|discussion))?"
    r"|(?:summarize|sum up|recap)(?: (?:our|the) (?:conversation|chat|discussion))?"
    r"|where (?:were|are) we)\W*$", _I)
META_RES = [SUMMARY_RE, REPEAT_RE, LAST_USER_RE, ABOUT_RE, WHEN_RE, DID_SAY_RE, TALKED_RE, SEARCH_RE]

# Spoken speech arrives with lead-ins ("Um, ...", "Okay so ..."). They are not part of the
# question, so drop them before looking for a meta-question.
LEAD_FILLER = re.compile(
    r"^(?:\W*(?:um+|uh+|er+|ah+|oh|ok(?:ay)?|so|well|anyway|alright|right|now|also|yeah|and|plus|"
    r"hey|hi|hello|yo)\b[\s,.!;:-]*)+", _I)
ACK_RE = re.compile(
    r"^\W*(?:ok(?:ay)?|k|sure|yep|yeah|yes|no|nope|thanks|thank you|thx|ty|got it|i see|"
    r"mm+|hmm+|ah|oh|interesting|nice|cool|wow|lol|ha(?:ha)+|bye|good ?night|see (?:you|ya))\W*$", _I)


def tidy(text: str) -> str:
    """Drop leading filler words so 'Um, what did you just say' is recognised as a meta-question."""
    return LEAD_FILLER.sub("", text).strip()

STOP = {"a", "an", "the", "i", "you", "me", "my", "your", "we", "us", "our", "it", "that", "this", "is",
        "are", "was", "were", "be", "do", "does", "did", "to", "of", "and", "or", "about", "what", "anything",
        "something", "say", "said", "tell", "told", "talk", "talked", "ever", "any", "some", "for", "in", "on",
        "at", "with", "have", "has", "had", "s", "him", "her", "he", "she", "they", "them", "his",
        "its", "their", "mention", "mentioned", "remember", "know"}
MAX_HITS = 5
MATCH_SHARE = 0.6   # fraction of the query's keywords a past turn must contain
TYPO_RATIO = 0.8    # how close two words must be to count as the same (catches 'Haward', 'bost')


def is_meta(text: str) -> bool:
    text = tidy(text)
    return any(r.match(text) for r in META_RES)


def when_text(timestamp: str) -> str:
    """'today at 3:42 PM' / 'yesterday at ...' / 'on Oct 3 at ...' in local time."""
    moment = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone()
    now = datetime.now().astimezone()
    clock = moment.strftime("%I:%M %p").lstrip("0")
    days = (now.date() - moment.date()).days
    if days == 0:
        return f"today at {clock}"
    if days == 1:
        return f"yesterday at {clock}"
    return f"on {moment.strftime('%b')} {moment.day} at {clock}"


class Recall:
    def __init__(self, conversation) -> None:
        self.convo = conversation
        self.repo = conversation.repo

    # --- helpers ------------------------------------------------------------------

    def _norm(self, word: str, lexicon: Lexicon) -> str:
        verb = lexicon.verb(word)
        return verb[0] if verb and verb[0] != word else lemmatize_noun(word)[0]

    def _keywords(self, text: str, lexicon: Lexicon) -> set[str]:
        return {self._norm(t.low, lexicon) for t in tokenize(text)
                if t.low.replace("'", "").isalnum() and t.low not in STOP}

    def _who(self, row) -> str:
        if row["role"] == "engine":
            return "I"
        return "you" if row["speaker"] == self.convo.state.speaker else row["speaker"]

    @staticmethod
    def _word_match(word: str, words: set[str]) -> bool:
        if word in words:
            return True
        return len(word) >= 4 and any(len(w) >= 4 and SequenceMatcher(None, word, w).ratio() >= TYPO_RATIO
                                      for w in words)

    def _search(self, text: str, uid: int, role: str | None = None, speaker: str | None = None,
                share: float = MATCH_SHARE) -> list:
        """Fuzzy search: past turns sharing most of the query's keywords (any order, any word form,
        small typos allowed), best matches first, newest first among equals."""
        lexicon = self.convo.lexicon()
        wanted = self._keywords(text, lexicon)
        if not wanted:
            return []
        needed = max(1, math.ceil(share * len(wanted)))
        scored = []
        for row in self.repo.turns(before_utterance=uid):
            if role and row["role"] != role:
                continue
            if speaker and row["role"] == "user" and row["speaker"] != speaker:
                continue
            if is_meta(row["prompt"]):  # questions about the conversation, and my answers to them
                continue
            text = row["text"]
            if row["role"] == "user":  # "he likes to swim" is also "Chance likes swimming"
                resolved = self.repo.conn.execute("SELECT text FROM resolved_text WHERE utterance_id=?",
                                                  (row["utterance_id"],)).fetchone()
                text = f"{text} {resolved[0]}" if resolved else text
            have = self._keywords(text, lexicon)
            score = sum(self._word_match(w, have) for w in wanted)
            if score >= needed:
                scored.append((score, row))
        scored.sort(key=lambda pair: -pair[0])  # stable: keeps newest-first within a score
        return [row for _, row in scored]

    def _quote(self, row) -> str:
        return f"{when_text(row['created_at'])}, {self._who(row)} said: “{row['text']}”"

    def _substantive(self, text: str) -> bool:
        """A turn worth listing as a topic (not an acknowledgement or a meta-question)."""
        return len(text.split()) >= 2 and not ACK_RE.match(text) and not is_meta(text)

    def _summary(self, uid: int, limit: int = 4) -> str:
        """What we have been talking about: the most recent things this speaker said."""
        topics, seen = [], set()
        for row in self.repo.turns(before_utterance=uid):
            if row["role"] != "user" or row["speaker"] != self.convo.state.speaker:
                continue
            text = row["text"].strip()
            if not self._substantive(text):
                continue
            key = re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()
            if key in seen:
                continue
            seen.add(key)
            topics.append(text)
            if len(topics) >= 12:
                break
        if not topics:
            return "We haven't really talked about anything yet."
        recent = [f"“{t}”" for t in reversed(topics[:limit])]
        if len(recent) == 1:
            listing = recent[0]
        elif len(recent) == 2:
            listing = f"{recent[0]} and {recent[1]}"
        else:
            listing = ", ".join(recent[:-1]) + f", and {recent[-1]}"
        count = len(topics)
        return (f"We've talked about {count} thing{'s' if count != 1 else ''} so far. "
                f"Most recently: {listing}.")

    def _stated_by(self, content: str, speaker: str):
        """Utterances by `speaker` that stated the claim in `content` (through the knowledge base)."""
        try:
            frames = parse_sentence(content, self.convo.lexicon())
        except ParseError:
            return []
        self.convo.resolver.begin(None)
        found = []
        for frame in frames:
            if not isinstance(frame, Frame) or frame.act != "assert":
                continue
            query, _, _ = self.convo._build_query(replace(frame, act="ask"))
            if query is None:
                continue
            answer = self.convo.engine.ask(query)
            for proof in (answer.support if frame.polarity else answer.against):
                for aid in proof.steps:
                    source = self.repo.source_utterance(aid)
                    if source is not None and source["speaker"] == speaker and source not in found:
                        found.append(source)
        return found

    # --- entry point --------------------------------------------------------------

    def answer(self, text: str, uid: int) -> tuple[str, str | None] | None:
        """(reply, yes/no status or None) if `text` is a question about the conversation, else None."""
        speaker = self.convo.state.speaker
        text = tidy(text)

        if SUMMARY_RE.match(text):
            return self._summary(uid), None

        if REPEAT_RE.match(text):
            last = next((r for r in self.repo.turns(before_utterance=uid)
                         if r["role"] == "engine" and not REPEAT_RE.match(tidy(r["prompt"]))), None)
            return (f"I said: “{last['text']}”" if last else "I haven't said anything yet."), None

        if LAST_USER_RE.match(text):
            last = next((r for r in self.repo.turns(before_utterance=uid)
                         if r["role"] == "user" and r["speaker"] == speaker and not is_meta(r["text"])), None)
            return (f"You said: “{last['text']}”" if last else "You haven't said anything to me yet."), None

        m = ABOUT_RE.match(text)
        if m:
            who, topic = m.group(1).lower(), m.group(2)
            role = "engine" if who == "you" else "user" if who != "we" else None
            person = speaker if who == "i" else (None if who in ("you", "we") else m.group(1))
            hits = self._search(topic, uid, role, person)
            if not hits:
                subject = {"i": "you", "you": "I", "we": "we"}.get(who, m.group(1))
                return f"I don't have anything {subject} said about {topic}.", None
            return " ".join(self._quote(r) + "." for r in hits[:MAX_HITS]), None

        m = WHEN_RE.match(text)
        if m:
            content = m.group(1) or m.group(2)
            told_me = m.group(1) is not None
            sources = self._stated_by(content, speaker) if told_me else []
            if not sources:
                hits = self._search(content, uid, "user", speaker if told_me else None, share=1.0)
                sources = hits
            if not sources:
                return ("I don't have a record of you telling me that." if told_me
                        else "I don't have a record of learning that."), None
            lines = [f"{'You' if s['speaker'] == speaker else s['speaker']} told me that "
                     f"{when_text(s['created_at'])}: “{s['text']}”." for s in sources[:MAX_HITS]]
            return " ".join(lines), None

        m = DID_SAY_RE.match(text)
        if m:
            who, content = m.group(1).lower(), m.group(2)
            if who == "i":
                sources = self._stated_by(content, speaker) or self._search(content, uid, "user", speaker, share=1.0)
                if sources:
                    s = sources[0]
                    return f"Yes. You told me {when_text(s['created_at'])}: “{s['text']}”.", "yes"
                return "No, I don't have a record of you saying that.", "no"
            hits = self._search(content, uid, "engine", share=1.0)
            if hits:
                return f"Yes. I said {when_text(hits[0]['created_at'])}: “{hits[0]['text']}”.", "yes"
            return "No, I don't have a record of saying that.", "no"

        m = TALKED_RE.match(text)
        if m:
            hits = self._search(m.group(1), uid, "user")
            if hits:
                return (f"Yes, {len(hits)} time{'s' if len(hits) != 1 else ''}. Most recently "
                        f"{self._quote(hits[0])}."), "yes"
            return f"No, I don't have any record of us talking about {m.group(1)}.", "no"

        m = SEARCH_RE.match(text)
        if m:
            hits = self._search(m.group(1), uid, "user")
            if not hits:
                return f"I didn't find anything in our conversation about {m.group(1)}.", None
            more = f" (and {len(hits) - MAX_HITS} more)" if len(hits) > MAX_HITS else ""
            return f"I found {len(hits)} mention{'s' if len(hits) != 1 else ''}{more}: " + \
                " ".join(self._quote(r) + "." for r in hits[:MAX_HITS]), None
        return None
