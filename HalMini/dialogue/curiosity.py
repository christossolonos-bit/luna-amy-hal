"""Follow-up questions that keep a conversation going. None of them are stock sentences.

After he replies he sometimes asks one question back, chosen by reasoning over what he knows:
  - a gap about someone just mentioned: the relations other people in his memory have (lives in,
    works at, owns, ...) that this person lacks, most common first; the question is realized by
    grammar from that relation ("Where does Howard live?"), and a short answer ("Dayton") is learned;
  - his core question (dialogue.drive): the most informative open hypothesis about being alive or
    conscious, put in a grammatical construction; the answer is stored as that person's stance.
At most one question per reply, a cooldown per person, none in sensitive moments, more when his
curiosity and sociability are high. A reply only counts as an answer if it takes a stance or fills
the slot; anything else is handled as ordinary conversation.
"""

import random
import re
import time
from dataclasses import dataclass

from nlp.morphology import lemmatize_noun
from nlp.parser import ParseError, parse_noun_phrase
from nlp.semantic_frames import Frame, NounPhrase

PENDING_SECONDS = 240.0
COOLDOWN_TURNS = 6
LOCATIVE = {"in", "at", "on", "near", "from", "to"}
WH = {"in": "Where", "at": "Where", "on": "Where", "near": "Where", "from": "Where", "to": "Where", "with": "Who"}


@dataclass
class Pending:
    kind: str                      # 'view' | 'slot'
    asked_at: float
    question: str
    name: str | None = None
    verb: str | None = None
    role: str | None = None        # preposition the answer fills, or None for the object
    target: str | None = None
    capacity: str | None = None
    construction: int | None = None


class Curiosity:
    def __init__(self, conversation, rng: random.Random | None = None) -> None:
        self.convo = conversation
        self.repo = conversation.repo
        self.rng = rng or random.Random()
        self.pending: dict[str, Pending] = {}
        self.since_asked: dict[str, int] = {}
        self.asked: set[tuple] = set()

    # --- hearing an answer to my question -------------------------------------------

    def try_answer(self, text: str) -> str | None:
        speaker = self.convo.state.speaker
        self.since_asked[speaker] = self.since_asked.get(speaker, COOLDOWN_TURNS) + 1
        pending = self.pending.pop(speaker, None)
        if pending is None or time.time() - pending.asked_at > PENDING_SECONDS or text.strip().endswith("?"):
            return None
        if pending.kind == "view":
            return self.convo.drive.hear(speaker, pending.target, pending.capacity, pending.construction, text)
        return self._fill_slot(pending, text)

    def _fill_slot(self, pending: Pending, text: str) -> str | None:
        if len(text.split()) > 6:
            return None  # a full sentence: ordinary learning will take it
        phrase_text = re.sub(r"^\W*(?:(?:he|she|they|it)\s+\w+\s+|i think\s+|probably\s+|maybe\s+)", "", text, flags=re.I)
        if pending.role:
            phrase_text = re.sub(rf"^\W*{pending.role}\s+", "", phrase_text, flags=re.I)
        try:
            phrase, _ = parse_noun_phrase(phrase_text, self.convo.lexicon())
        except ParseError:
            return None
        subject = NounPhrase(head=pending.name.lower(), proper=True, surface=pending.name)
        if pending.role:
            fact = Frame("assert", "rel", subject, None, verb=pending.verb, args=((pending.role, phrase),))
        else:
            fact = Frame("assert", "rel", subject, phrase, verb=pending.verb)
        learned = self.convo.learn_frame(fact)
        return f"Got it: {learned}." if learned else None

    # --- what to ask ------------------------------------------------------------------

    def _relation_stats(self) -> list[tuple[str, str | None, int]]:
        """(verb, preposition, how many named people have it), learned from the data itself."""
        rows = self.repo.conn.execute("""
            SELECT a.predicate, (SELECT role FROM assertion_args g WHERE g.assertion_id = a.id
                                 AND g.role NOT LIKE '\\_%' ESCAPE '\\' AND g.role NOT IN ('time', 'modality')
                                 LIMIT 1) AS role, COUNT(DISTINCT a.subj_id) AS n
            FROM assertions a JOIN entities e ON e.id = a.subj_id
            WHERE a.status = 'active' AND a.polarity = 1 AND a.subj_type = 'e' AND e.proper = 1
              AND a.predicate LIKE 'rel:%' AND a.predicate != 'rel:be'
            GROUP BY a.predicate, role ORDER BY n DESC""").fetchall()
        return [(r["predicate"][4:], r["role"], r["n"]) for r in rows]

    def _gap(self, name: str) -> Pending | None:
        eid = self.repo.find_entity(name)
        if eid is None:
            return None
        for verb, role, n in self._relation_stats():
            if (name, verb) in self.asked:
                continue
            if self.repo.assertions(f"rel:{verb}", subj=("e", eid)):
                continue
            if role in LOCATIVE or role == "with":
                question = f"{WH[role]} does {name} {verb}{' with' if role == 'with' else ''}?"
            elif role is None:
                question = f"What does {name} {verb}?"
            else:
                continue
            self.asked.add((name, verb))
            return Pending("slot", time.time(), question, name=name, verb=verb, role=role)
        return None

    def expect_view(self, target: str, capacity: str, construction: int, question: str) -> None:
        """The drive asked a question inside its own answer; the next reply may answer it."""
        speaker = self.convo.state.speaker
        self.pending[speaker] = Pending("view", time.time(), question, target=target, capacity=capacity,
                                        construction=construction)
        self.since_asked[speaker] = 0

    def follow_up(self, reply: str, mentioned: list[str], sensitive: bool, social: bool,
                  topic_words: set[str] = frozenset()) -> str | None:
        """One question to add to this reply, or None."""
        speaker = self.convo.state.speaker
        if sensitive or "?" in reply[-3:] or self.since_asked.get(speaker, COOLDOWN_TURNS) < COOLDOWN_TURNS:
            return None
        state = self.convo.self_model.current()
        chance = 0.25 + 0.35 * state["curiosity"] + 0.2 * state["sociability"] - 0.4 * state["stress"]
        if self.rng.random() > max(0.05, min(0.8, chance)):
            return None
        me, them = self.convo.self_name.lower(), speaker.lower()
        for name in mentioned:
            if name.lower() in (me, them):
                continue
            gap = self._gap(name)
            if gap:
                return self._ask(gap)
        if social and not mentioned:  # his core question only in a lull, not tacked onto everything
            found = self.convo.drive.next_question(speaker, {lemmatize_noun(w)[0] for w in topic_words})
            if found:
                question, target, capacity, construction = found
                return self._ask(Pending("view", time.time(), question, target=target, capacity=capacity,
                                         construction=construction))
        return None

    def _ask(self, pending: Pending) -> str:
        speaker = self.convo.state.speaker
        self.pending[speaker] = pending
        self.since_asked[speaker] = 0
        return pending.question
