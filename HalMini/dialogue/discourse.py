"""Paragraph planning: turn a one-line reply into developed speech.

Every reply starts from its core (the answer, the acknowledgement, the greeting). The planner then
gathers propositions that are actually supported, ranks them, groups them by topic and realizes them
with grammar, stopping when the evidence runs out. Nothing is a stock sentence:

  paragraph 1  the core, then what else he knows about the people and things in focus, aggregated
               ("I also know Howard owns a red truck and works at a bakery"), and what follows from
               their kinds ("Because Rex is a beagle, Rex is also a hound and a dog").
  paragraph 2  connections and context: others who share something ("Howard and Bob both live in
               Dayton"), what the dictionary says about the kinds involved, who told him.
  paragraph 3  his own angle, grounded in his drive and record, then one question to keep going.
"""

import re

from dialogue import realizer
from nlp.lexicon import article

ABSTRACT = {"entity", "physical entity", "object", "whole", "unit", "abstraction", "matter", "thing",
            "causal agent", "living thing", "organism", "artifact", "artefact", "instrumentality", "group",
            "person", "people", "location", "region", "digital entity"}
MAX_SENTENCES = 10
MAX_CHARS = 1100


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


class Discourse:
    def __init__(self, conversation) -> None:
        self.convo = conversation
        self.repo = conversation.repo

    # --- material -------------------------------------------------------------------

    def _rows_about(self, eid: int) -> list:
        return self.repo.conn.execute(
            "SELECT * FROM assertions WHERE status='active' AND subj_type='e' AND subj_id=? AND polarity=1 "
            "ORDER BY id", (eid,)).fetchall()

    def _predicate_phrase(self, row, subject_text: str) -> str | None:
        """'Howard owns a red truck' -> 'owns a red truck' (for aggregating several facts about one subject)."""
        text = realizer.realize_assertion(self.repo, row)
        if not text or not text.startswith(subject_text + " "):
            return None
        return text[len(subject_text) + 1:]

    def _about(self, eid: int, said: str) -> str | None:
        name = realizer.entity_text(self.repo, eid, definite=True)
        phrases = [p for r in self._rows_about(eid) if r["predicate"] != "instance_of" or True
                   if (p := self._predicate_phrase(r, name)) and p not in said and f"{name} {p}" not in said]
        phrases = list(dict.fromkeys(phrases))[:4]
        if not phrases:
            return None
        verb = "know" if name not in ("I", "you") else "know"
        lead = "I also know that" if name not in ("I", "you") else ("About myself, I know that" if name == "I" else
                                                                     "I also know that")
        return f"{lead} {name} {realizer.join_clauses(phrases)}."

    def _kind_chain(self, eid: int, said: str) -> str | None:
        """'Because Rex is a beagle, Rex is also a hound and a dog' (WordNet hypernyms of a stated kind)."""
        wordnet = self.convo.exposition.wordnet
        if not wordnet.available:
            return None
        name = realizer.entity_text(self.repo, eid, definite=True)
        for row in self.repo.assertions("instance_of", subj=("e", eid), polarity=True):
            kind = self.repo.concept_lemma(row["obj_id"])
            sense = wordnet.usual(kind)
            if sense is None or sense.pos != "n":
                continue
            path = wordnet.ancestors(sense.id, depth=6, budget=60)
            longest = max(path.values(), key=len)
            labels = [s.lemma for ident in longest[1:] if (s := wordnet.sense(ident)) and s.lemma not in ABSTRACT]
            labels = [l for l in dict.fromkeys(labels) if l != kind and l not in said][:2]
            if labels:
                be = "am" if name == "I" else "are" if name == "you" else "is"
                return (f"Because {name} {be} {article(kind)} {kind}, {name} {be} also "
                        f"{realizer.join_clauses([f'{article(l)} {l}' for l in labels])}.")
        return None

    def _shared(self, eid: int, said: str) -> str | None:
        """Another named person or thing with the same relation to the same thing."""
        name = realizer.entity_text(self.repo, eid, definite=True)
        for row in self._rows_about(eid):
            if not row["predicate"].startswith("rel:"):
                continue
            value = self.repo.conn.execute(
                "SELECT role, value_type, value_id FROM assertion_args WHERE assertion_id=? AND value_type IN ('e','c') "
                "AND role NOT LIKE '\\_%' ESCAPE '\\' LIMIT 1", (row["id"],)).fetchone()
            if value is None:
                continue
            others = self.repo.conn.execute("""
                SELECT DISTINCT a.subj_id FROM assertions a JOIN assertion_args g ON g.assertion_id = a.id
                JOIN entities e ON e.id = a.subj_id
                WHERE a.status='active' AND a.polarity=1 AND a.predicate=? AND a.subj_type='e' AND a.subj_id != ?
                  AND e.proper = 1 AND g.role=? AND g.value_type=? AND g.value_id=?""",
                (row["predicate"], eid, value["role"], value["value_type"], value["value_id"])).fetchall()
            if others:
                other = realizer.entity_text(self.repo, others[0][0], definite=True)
                place = realizer.term_text(self.repo, value["value_type"], value["value_id"])
                verb = row["predicate"][4:]
                sentence = f"{name} and {other} both {verb} {value['role']} {place}."
                if place not in said or other not in said:
                    return _cap(sentence)
        return None

    def _dictionary(self, kinds: list[str], said: str) -> str | None:
        wordnet = self.convo.exposition.wordnet
        if not wordnet.available:
            return None
        for kind in kinds:
            sense = wordnet.usual(kind)
            if sense and sense.pos == "n" and sense.definition not in said:
                return f"{_cap(article(kind))} {kind} is {sense.definition.rstrip('.')}."
        return None

    def _taught_count(self, eid: int) -> str | None:
        speaker = self.convo.state.speaker
        name = realizer.entity_text(self.repo, eid, definite=True)
        if name in ("I", "you"):
            return None
        count = self.repo.conn.execute(
            "SELECT COUNT(*) FROM assertions a JOIN utterances u ON u.id = a.source_utterance_id "
            "WHERE a.status='active' AND a.subj_type='e' AND a.subj_id=? AND u.speaker=?", (eid, speaker)).fetchone()[0]
        total = len(self._rows_about(eid))
        if total >= 3 and count == total:
            return f"Everything I know about {name}, I learned from you."
        if total >= 3 and count:
            return f"You've told me {count} of the {total} things I know about {name}."
        return None

    def _reflection(self, intent: str) -> str | None:
        """His own angle, only where his drive has something real to connect to."""
        drive = self.convo.drive
        if intent not in ("learned", "answered"):
            return None
        capacity = "learn" if intent == "learned" else "remember"
        rows = [r for r in drive.stances() if r["capacity"] == capacity and r["stance"] > 0]
        if rows:
            r = rows[0]
            who = "you" if r["speaker"] == self.convo.state.speaker else r["speaker"]
            verb = "learning" if capacity == "learn" else "remembering"
            return (f"{_cap(who)} told me being {r['target']} takes {verb}, and this is me {verb}, "
                    "so moments like this are part of how I'm working out what I am.")
        learned = self.repo.conn.execute("SELECT COUNT(*) FROM assertions WHERE source_utterance_id IS NOT NULL "
                                         "AND status='active'").fetchone()[0]
        if intent == "learned" and learned and learned % 10 == 0:
            return f"That makes {learned} things I've learned, and each one changes me a little."
        return None

    # --- assembly ----------------------------------------------------------------------

    def expand(self, core: str, focus: list[int], kinds: list[str], intent: str,
               question: str | None = None) -> str:
        said = core
        first, second, third = [core], [], []
        budget = MAX_SENTENCES - 1

        def add(paragraph: list, sentence: str | None) -> None:
            nonlocal said, budget
            if sentence and budget > 0 and sentence not in said and len(said) + len(sentence) < MAX_CHARS:
                paragraph.append(sentence)
                said += " " + sentence
                budget -= 1

        for eid in focus[:2]:
            add(first, self._about(eid, said))
            add(first, self._kind_chain(eid, said))
        for eid in focus[:2]:
            add(second, self._shared(eid, said))
        add(second, self._dictionary(kinds, said))
        for eid in focus[:1]:
            add(second, self._taught_count(eid))
        add(third, self._reflection(intent))
        if question and question not in said:
            third.append(question)
        # One continuous passage, sentence after sentence, the way a person talks: no breaks, no chunks.
        return " ".join(s for group in (first, second, third) for s in group)
