"""Reasoning over WordNet relations for questions about words and kinds of things.

    How is a dog like a cat? / What's the difference between a car and a bus?   (closest shared category)
    What are the parts of a car? / What is a tree made of? / Does a car have an engine?   (meronyms)
    What is an engine part of?   (holonyms)          What kinds of dog are there?   (hyponyms)
    What's another word for big?   (synonyms)        What does listen mean?   (usual sense)

Every sentence is composed from relations that were actually followed, and the plan records the
synset paths. WordNet is dictionary knowledge, kept apart from what people have taught: taught
facts about the same kinds are added and attributed separately.
"""

import re

from dialogue import realizer
from nlp.lexicon import article
from nlp.morphology import lemmatize_noun
from nlp.wordnet import Sense, WordNet

NP = r"(?:an? |the |some )?([a-z][a-z' \-]*?)"
COMPARE = [re.compile(p, re.I) for p in (
    rf"^\W*how (?:is|are) {NP} (?:like|similar to|different from|related to|compared to) {NP}\W*$",
    rf"^\W*what(?:'s| is) the difference between {NP} and {NP}\W*$",
    rf"^\W*compare {NP} (?:and|with|to) {NP}\W*$",
    rf"^\W*what do {NP} and {NP} have in common\W*$",
    rf"^\W*(?:is|are) {NP} (?:the same as|similar to|related to) {NP}\W*$",
    rf"^\W*are {NP} and {NP} (?:related|similar|the same|alike)\W*$",
)]
PARTS = [re.compile(p, re.I) for p in (
    rf"^\W*what (?:are the |are some )?parts (?:of|does) {NP}(?: have)?\W*$",
    rf"^\W*what(?:'s| is| are) {NP} made (?:of|from|up of)\W*$",
    rf"^\W*what does {NP} consist of\W*$",
    rf"^\W*what(?:'s| is) (?:in|inside) {NP}\W*$",
)]
HAS_PART = re.compile(rf"^\W*(?:does|do) {NP} (?:have|contain|include) {NP}\W*$", re.I)
PART_OF = re.compile(rf"^\W*what (?:is|are) {NP} (?:a )?part of\W*$", re.I)
KINDS = [re.compile(p, re.I) for p in (
    rf"^\W*what (?:kinds|types|sorts) of {NP} are there\W*$",
    rf"^\W*(?:name|list|give me) (?:some )?(?:kinds|types|examples) of {NP}\W*$",
    rf"^\W*what are some (?:kinds|types|examples) of {NP}\W*$",
)]
SYNONYM = [re.compile(p, re.I) for p in (
    rf"^\W*what(?:'s| is) (?:another|a different|an?other|other) words? for {NP}\W*$",
    rf"^\W*(?:give me )?(?:a )?(?:synonyms?|other words) (?:for|of) {NP}\W*$",
    rf"^\W*what(?:'s| is) a synonym (?:for|of) {NP}\W*$",
)]
MEANS = [re.compile(p, re.I) for p in (
    r"^\W*what does (?:the word )?['\"“]?([a-z][a-z \-]*?)['\"”]? mean\W*$",
    rf"^\W*what(?:'s| is) the meaning of (?:the word )?{NP}\W*$",
    rf"^\W*define {NP}\W*$",
)]
ABSTRACT = {"entity", "physical entity", "object", "whole", "unit", "abstraction", "matter", "thing",
            "causal agent", "living thing", "organism", "artifact", "artefact", "instrumentality", "group"}


class LexicalReasoner:
    def __init__(self, conversation, wordnet: WordNet) -> None:
        self.convo = conversation
        self.repo = conversation.repo
        self.wordnet = wordnet

    # --- helpers ------------------------------------------------------------------

    def _noun(self, phrase: str) -> tuple[str, Sense | None]:
        word = phrase.strip().lower()
        senses = self.wordnet.lookup(word, "n")
        if not senses:
            word = lemmatize_noun(word.split()[-1])[0] if " " not in word else word
            senses = self.wordnet.lookup(word, "n")
        return word, (senses[0] if senses else None)

    @staticmethod
    def _a(word: str) -> str:
        return f"{article(word)} {word}"

    def _label(self, ident: str) -> str:
        sense = self.wordnet.sense(ident)
        return sense.lemma if sense else ident

    def _taught(self, word: str, limit: int = 2) -> list[str]:
        """What people have taught about this kind, kept separate from the dictionary."""
        cid = self.repo.find_concept(word)
        if cid is None:
            return []
        rows = self.repo.conn.execute(
            "SELECT * FROM assertions WHERE status='active' AND subj_type='c' AND subj_id=? "
            "AND source_utterance_id IS NOT NULL ORDER BY id DESC LIMIT ?", (cid, limit)).fetchall()
        return [t for r in rows if (t := realizer.realize_assertion(self.repo, r))]

    def _list(self, senses: list[Sense], limit: int = 6) -> list[str]:
        return list(dict.fromkeys(s.lemma for s in senses))[:limit]

    # --- entry point --------------------------------------------------------------

    def respond(self, text: str) -> tuple[str, dict] | None:
        if not self.wordnet.available:
            return None
        text = text.strip()
        for pattern in COMPARE:
            if (m := pattern.match(text)):
                return self.compare(m.group(1), m.group(2))
        for pattern in PARTS:
            if (m := pattern.match(text)):
                return self.parts(m.group(1))
        if (m := HAS_PART.match(text)):
            return self.has_part(m.group(1), m.group(2))
        if (m := PART_OF.match(text)):
            return self.part_of(m.group(1))
        for pattern in KINDS:
            if (m := pattern.match(text)):
                return self.kinds(m.group(1))
        for pattern in SYNONYM:
            if (m := pattern.match(text)):
                return self.synonyms(m.group(1))
        for pattern in MEANS:
            if (m := pattern.match(text)):
                return self.meaning(m.group(1))
        return None

    # --- answers ------------------------------------------------------------------

    def compare(self, left: str, right: str):
        a_word, a = self._noun(left)
        b_word, b = self._noun(right)
        if a is None or b is None or self.repo.find_entity(left.strip()) or self.repo.find_entity(right.strip()):
            return None  # named things are compared from memory, not the dictionary
        # Comparing two words implies the meanings that relate them ("cat" next to "dog" is the animal,
        # not the CAT scan): pick the pair, among each word's common senses, with the nearest shared category.
        best = None
        for rank_a, sa in enumerate(self.wordnet.lookup(a_word, "n")[:4]):
            for rank_b, sb in enumerate(self.wordnet.lookup(b_word, "n")[:4]):
                found = self.wordnet.common_ancestor(sa.id, sb.id)
                if found and self._label(found[0]) not in ABSTRACT:
                    # rarer meanings must be much closer to win ("bus" as slang for an old car)
                    distance = len(found[1]) + len(found[2]) + 3 * (rank_a + rank_b)
                    if best is None or distance < best[0]:
                        best = (distance, sa, sb, found)
        if best:
            _, a, b, shared = best
        else:
            shared = self.wordnet.common_ancestor(a.id, b.id)
        sentences = []
        evidence = {"a": a.id, "b": b.id}
        if shared:
            top, path_a, path_b = shared
            evidence.update(shared=top, path_a=path_a, path_b=path_b)
            top_word = self._label(top)
            if top == b.id:
                sentences.append(f"{self._a(a_word).capitalize()} is a kind of {b_word}: "
                                 + " is a ".join(self._label(i) for i in path_a) + ".")
            elif top == a.id:
                sentences.append(f"{self._a(b_word).capitalize()} is a kind of {a_word}: "
                                 + " is a ".join(self._label(i) for i in path_b) + ".")
            elif top_word not in ABSTRACT:
                sentences.append(f"{self._a(a_word).capitalize()} and {self._a(b_word)} are both kinds of {top_word}.")
                parent_a, parent_b = self._label(path_a[1]), self._label(path_b[1])
                if path_a[1] != top and path_b[1] != top and parent_a != parent_b:
                    sentences.append(f"They differ one level down: {self._a(a_word)} is {self._a(parent_a)}, "
                                     f"while {self._a(b_word)} is {self._a(parent_b)}.")
            else:
                sentences.append(f"They aren't closely related. The nearest category they share is "
                                 f"just {top_word}.")
        else:
            sentences.append(f"I can't find any category that {self._a(a_word)} and {self._a(b_word)} share.")
        sentences.append(f"{self._a(a_word).capitalize()} is {a.definition}; {self._a(b_word)} is {b.definition}.")
        for word in (a_word, b_word):
            taught = self._taught(word)
            if taught:
                sentences.append("People have also told me that " + realizer.join_clauses(taught) + ".")
        return " ".join(sentences), {"intent": "compare", "wordnet": evidence}

    def _parts_of(self, sense: Sense) -> tuple[list[Sense], list[Sense], list[Sense], str]:
        """Parts, members and substances, borrowing from the nearest category that has any."""
        source = sense
        for ident in [sense.id, *list(self.wordnet.ancestors(sense.id, depth=3, budget=40))[1:]]:
            parts = self.wordnet.related(ident, ("mero_part",))
            members = self.wordnet.related(ident, ("mero_member",))
            stuff = self.wordnet.related(ident, ("mero_substance",))
            if parts or members or stuff:
                source = sense if ident == sense.id else (self.wordnet.sense(ident) or sense)
                return parts, members, stuff, (None if ident == sense.id else source.lemma)
        return [], [], [], None

    def parts(self, phrase: str):
        word, sense = self._noun(phrase)
        if sense is None:
            return None
        parts, members, stuff, via = self._parts_of(sense)
        if not (parts or members or stuff):
            return (f"I don't have any parts listed for {self._a(word)}. Its meaning is {sense.definition}.",
                    {"intent": "parts", "wordnet": {"sense": sense.id}})
        sentences = []
        lead = self._a(word).capitalize() if via is None else \
            f"{self._a(word).capitalize()} is {self._a(via)}, and {self._a(via)}"
        if parts:
            sentences.append(f"{lead} has parts such as {realizer.join_clauses(self._list(parts))}.")
        if members:
            sentences.append(f"Its members include {realizer.join_clauses(self._list(members))}.")
        if stuff:
            sentences.append(f"It's made of {realizer.join_clauses(self._list(stuff))}.")
        return " ".join(sentences), {"intent": "parts", "wordnet": {"sense": sense.id, "via": via}}

    def has_part(self, whole: str, part: str):
        if self.repo.find_entity(whole.strip()):
            return None  # "Does Howard have a truck?" is about ownership
        whole_word, whole_sense = self._noun(whole)
        part_word, _ = self._noun(part)
        if whole_sense is None:
            return None
        parts, members, stuff, via = self._parts_of(whole_sense)
        part_senses = {s.id for s in self.wordnet.lookup(part_word, "n")}
        for found in parts + members + stuff:
            kinds = self.wordnet.ancestors(found.id, depth=6, budget=60)
            if found.id in part_senses or any(p in kinds for p in part_senses):
                named = "" if found.lemma == part_word else f" (its {found.lemma})"
                since = "" if via is None else f", since it is {self._a(via)}"
                return (f"Yes. {self._a(whole_word).capitalize()} has {self._a(part_word)}{named}{since}.",
                        {"intent": "parts", "status": "yes", "wordnet": {"whole": whole_sense.id, "part": found.id}})
        return None  # not listed is not evidence of absence: let normal reasoning answer

    def part_of(self, phrase: str):
        word, sense = self._noun(phrase)
        if sense is None:
            return None
        wholes = self.wordnet.related(sense.id, ("holo_part", "holo_member", "holo_substance"))
        if not wholes:
            return (f"I don't have anything listed that {self._a(word)} is part of.",
                    {"intent": "part_of", "wordnet": {"sense": sense.id}})
        return (f"{self._a(word).capitalize()} is part of {realizer.join_clauses([self._a(w) for w in self._list(wholes)])}.",
                {"intent": "part_of", "wordnet": {"sense": sense.id, "wholes": [w.id for w in wholes]}})

    def kinds(self, phrase: str):
        word, sense = self._noun(phrase)
        if sense is None:
            return None
        kinds = self.wordnet.related(sense.id, ("hyponym",), limit=40)
        if not kinds:
            return (f"I don't know of any particular kinds of {word}.", {"intent": "kinds", "wordnet": {"sense": sense.id}})
        names = self._list(kinds, limit=10)
        more = len(kinds) - len(names)
        tail = f", and {more} more" if more > 0 else ""
        return (f"Kinds of {word} include {', '.join(names)}{tail}.",
                {"intent": "kinds", "wordnet": {"sense": sense.id, "kinds": [k.id for k in kinds]}})

    def synonyms(self, phrase: str):
        word = phrase.strip().lower()
        for pos in ("n", "v", "a", "s", "r"):
            for sense in self.wordnet.lookup(word, pos):
                others = [w for w in self.wordnet.lemmas(sense.id) if w.lower() != word]
                if others:
                    listed = realizer.join_clauses([f"“{w}”" for w in others[:5]]).replace(" and ", " or ")
                    return (f"Another word for {word} is {listed}, in the sense of {sense.definition}.",
                            {"intent": "synonyms", "wordnet": {"sense": sense.id}})
        sense = self.wordnet.usual(word)
        if sense is None:
            return None
        return (f"I don't have another word for {word}. It means {sense.definition}.",
                {"intent": "synonyms", "wordnet": {"sense": sense.id}})

    def meaning(self, phrase: str):
        word = phrase.strip().lower()
        sense = self.wordnet.usual(word)
        if sense is None:
            return None
        total = len(self.wordnet.lookup(sense.lemma, sense.pos))
        if sense.pos == "v":
            gloss = re.sub(r"^to\s+", "", sense.definition, flags=re.I)
            core = f"To {word} means to {gloss}."
        elif sense.pos == "n":
            core = f"{self._a(word).capitalize()} is {sense.definition}."
        else:
            core = f"{word.capitalize()} means {sense.definition}."
        if total > 1:
            other = "s" if total > 2 else ""
            core += f" That's the usual meaning; it has {total - 1} other{other}."
        return core, {"intent": "meaning", "wordnet": {"sense": sense.id}}

    def topic_hint(self, sentence: str) -> str | None:
        """For a sentence it couldn't parse: say what it does know about a word in it, so the reply
        connects to what was said instead of only asking for a rephrase."""
        return self.convo.understanding.make_sense(sentence) if hasattr(self, "convo") else None
        words = [w for w in re.findall(r"[a-z]+", sentence.lower()) if len(w) > 3]
        from nlp.wordnet import STOP
        for word in sorted(set(words) - STOP, key=lambda w: (-len(w), w)):
            sense = self.wordnet.usual(word)
            if sense is None:
                continue
            meaning = ("to " + re.sub(r"^to\s+", "", sense.definition, flags=re.I)) if sense.pos == "v" \
                else sense.definition  # nouns and adjectives read as-is
            return (f"I didn't follow the whole sentence, but I know '{word}': {meaning}. "
                    f"What would you like to tell me or ask about it?")
        return None
