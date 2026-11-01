"""Memory first: a question is checked against everything he knows about who or what it's about, before
any other part of him gets to say "I don't know" or "I didn't catch that".

The parser can't read every way a question gets asked, but it doesn't have to: if he was told "I am from
Ohio", the question "Where am I from?" is about the speaker and about being from somewhere, and that's
enough to find the fact. So:

  1. who it's about: you -> him, I/me/my -> the speaker, a name -> that one, "my dog" -> the speaker's dog
  2. every fact he holds about them (and, for "my dog", about the dog)
  3. the fact that shares the most meaning with the question, with words that ask for the same thing
     counted as the same ("home" ~ "live", "have" ~ "own", "called"/"name" ~ "named", "come from" ~ "from")
  4. a yes/no question gets "Yes." when a fact matches it; anything else gets the fact itself
"""

import re

from dialogue import realizer

SAME = {
    "home": "live", "lives": "live", "living": "live", "reside": "live", "stay": "live", "stays": "live",
    "come": "from", "comes": "from", "hometown": "from", "originally": "from",
    "have": "own", "has": "own", "had": "own", "owns": "own", "owned": "own", "got": "own",
    "called": "named", "name": "named", "names": "named",
    "job": "work", "works": "work", "working": "work", "do": "work",
    "likes": "like", "love": "like", "loves": "like", "enjoy": "like", "favorite": "favorite",
    "born": "born", "birth": "born", "old": "old", "age": "old",
    "colour": "color",
}
STOP = {"remember", "recall", "know", "think", "what", "where", "who", "when", "how", "which", "why", "is", "are",
        "am", "was", "were", "does", "did",
        "the", "a", "an", "of", "to", "in", "at", "on", "you", "your", "i", "my", "me", "mine", "yours", "know",
        "tell", "about", "anything", "something", "s", "can", "could", "would", "will", "please", "it", "that",
        "this", "there", "do", "come"}
QUESTION = re.compile(r"^\W*(?:what|where|who|when|how|which|why|is|are|am|was|were|do|does|did|have|has|can|"
                      r"tell me about|what'?s|where'?s|who'?s)\b", re.I)


def _meaning(words) -> set[str]:
    out = set()
    for w in words:
        if w == "come":
            out.add("from")
        elif w not in STOP:
            out.add(SAME.get(w, w))
    if "do" in words and "what" in words:
        out.add("work")
    return out


class Recall:
    def __init__(self, conversation) -> None:
        self.convo = conversation
        self.repo = conversation.repo

    def _subjects(self, text: str) -> list[int]:
        """Who the question is about, most specific first."""
        low = text.lower()
        out: list[int] = []
        speaker = self.repo.find_entity(self.convo.state.speaker)
        owned = re.search(r"\b(my|your)\s+([a-z]+)(?:'s)?\b", low)
        if owned:
            owner = speaker if owned.group(1) == "my" else self.convo.self_id
            kind = self.repo.find_concept(owned.group(2))
            if owner is not None and kind is not None:
                for row in self.repo.assertions("rel:own", subj=("e", owner), polarity=True):
                    if row["obj_type"] == "e":
                        inst = self.repo.entity_row(row["obj_id"])
                        if inst["kind_concept_id"] == kind:
                            out.append(inst["id"])
        for name in re.findall(r"\b[A-Z][\w-]*\b", text[1:] if text[:1].isupper() else text):
            eid = self.repo.find_entity(name)
            if eid is not None and eid not in out:
                out.append(eid)
        asking_him = re.sub(r"\b(?:do|did|can|could|would) you (?:know|remember|tell me|think|recall)\b", "", low)
        if re.search(r"\b(?:you|your|yourself)\b", asking_him):
            out.append(self.convo.self_id)
        if re.search(r"\b(?:i|me|my|mine|myself)\b", low) and speaker is not None:
            out.append(speaker)
        return out

    def _facts(self, eid: int) -> list[tuple[str, set[str]]]:
        rows = self.repo.conn.execute(
            "SELECT * FROM assertions WHERE status='active' AND polarity=1 AND "
            "((subj_type='e' AND subj_id=?) OR (obj_type='e' AND obj_id=? AND predicate LIKE 'rel:%'))",
            (eid, eid)).fetchall()
        out = []
        for row in rows:
            if row["source_utterance_id"] is None and row["predicate"] in ("instance_of", "subclass_of"):
                continue
            text = realizer.realize_assertion(self.repo, row)
            if not text:
                continue
            said = self.repo.source_utterance(row["id"]) if row["source_utterance_id"] else None
            if said and len(said["text"].split()) <= 15:
                # What was actually said keeps what storing it can lose ("two years old", "born in Texas")
                from dialogue.speech_acts import _mirror
                original = _mirror(said["text"].strip().rstrip(".!")) if said["speaker"] == self.convo.state.speaker \
                    else said["text"].strip().rstrip(".!")
                lost = {w for w in re.findall(r"[a-z]+|\d+", original.lower())
                        if w.isdigit() or w in ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                                                "ten", "twenty", "thirty", "forty", "fifty")} - set(re.findall(r"[a-z]+|\d+", text.lower()))
                lost |= {w.lower() for w in re.findall(r"\b[A-Z][a-z]+\b", original[1:])} - set(re.findall(r"[a-z]+", text.lower()))
                if lost and not re.search(r",| and | but ", original):  # only a single, simple sentence
                    text = original[0].lower() + original[1:] if not original.startswith("I ") else original
            read = self.repo.source_speaker(row["id"])
            if read in ("Wikipedia", "the dictionary") or (said is None and read and read[:1].isupper() and
                                                          self.repo.conn.execute("SELECT 1 FROM sqlite_master WHERE "
                                                                                 "name='fact_sources'").fetchone()
                                                          and self.repo.conn.execute("SELECT 1 FROM fact_sources WHERE "
                                                                                     "assertion_id=?", (row["id"],)).fetchone()):
                where = "in the dictionary" if read == "the dictionary" else f"on {read}" if read == "Wikipedia" else f"from {read}"
                text = f"I read {where} that {text[0].lower()}{text[1:]}"
            words = re.findall(r"[a-z']+", text.lower())
            tags = {SAME.get(row["predicate"][4:], row["predicate"][4:])}
            if row["obj_type"] == "e" and row["obj_id"] != eid:  # "you own Chance": Chance is a dog
                kind_id = self.repo.entity_row(row["obj_id"])["kind_concept_id"]
                if kind_id:
                    tags.add(self.repo.concept_lemma(kind_id))
            if row["predicate"] == "instance_of" and row["obj_id"] != self.repo.entity_row(eid)["kind_concept_id"]:
                tags |= {"work", "what"}  # "is a chef" answers "what's her job"; "is my boss" doesn't
            out.append((text, _meaning(words) | tags))
        row = self.repo.entity_row(eid)
        if row["proper"] and row["kind_concept_id"]:  # "a dog named Chance": the name is a fact too
            kind = self.repo.concept_lemma(row["kind_concept_id"])
            out.append((f"the {kind} is named {row['name']}", {"named", kind}))
        return out

    def _purpose(self, text: str) -> str | None:
        if not re.search(r"\b(?:why|purpose|exist|made|created|built|for)\b", text, re.I) or \
                not re.search(r"\b(?:you|your)\b", text, re.I):
            return None
        row = self.repo.conn.execute("SELECT * FROM about_me WHERE key='purpose'").fetchone()
        if not row:
            return None
        told = "You" if row["speaker"].lower() == self.convo.state.speaker.lower() else row["speaker"]
        return f"{told} told me {row['value'][0].lower()}{row['value'][1:]}"

    def _category(self, text: str) -> str | None:
        """'Is Chance an animal?': Chance is a dog, and the dictionary places dogs under animals."""
        m = re.match(r"^\W*(?i:is|was) (?P<who>[A-Z][\w-]*|my \w+) (?:an?|the)? ?(?P<kind>[a-z][\w ]*?)\W*$", text)
        if not m:
            return None
        named = self.repo.find_entity(m["who"]) if m["who"][:1].isupper() else None
        eids = [named] if named is not None else self._subjects(text)
        wordnet = self.convo.exposition.wordnet
        for eid in eids:
            kinds = [r["obj_id"] for r in self.repo.assertions("instance_of", subj=("e", eid), polarity=True)]
            entity_kind = self.repo.entity_row(eid)["kind_concept_id"]
            for kind_id in dict.fromkeys(kinds + ([entity_kind] if entity_kind else [])):
                kind = self.repo.concept_lemma(kind_id)
                for sense in wordnet.lookup(kind, "n")[:2]:
                    if kind == m["kind"] or wordnet.path_to(sense.id, m["kind"]):
                        name = self.repo.entity_row(eid)["name"]
                        a = "an" if kind[:1] in "aeiou" else "a"
                        b = "an" if m["kind"][:1] in "aeiou" else "a"
                        return f"Yes. {name} is {a} {kind}, and {a} {kind} is a kind of {m['kind']}, so {name} is {b} {m['kind']}."
        return None

    def _property(self, text: str) -> str | None:
        """'Is the sky blue?': the dictionary's meanings of the two words mention each other."""
        m = re.match(r"^\W*(?i:is|are) (?:the |a |an )?(?P<x>[a-z]+) (?P<adj>[a-z]+)\W*$", text)
        if not m:
            return None
        wordnet = self.convo.exposition.wordnet
        x, adj = m["x"].lower(), m["adj"].lower()
        for sense in [s for s in wordnet.lookup(adj, None) if s.pos in ("a", "s")][:4]:
            if re.search(rf"\b{x}\b", sense.definition, re.I):
                return (f"Yes, usually. My dictionary describes {adj} as \u201c{sense.definition}\u201d, so the {x} "
                        f"is {adj}.")
        for sense in wordnet.lookup(x, "n")[:2]:
            if re.search(rf"\b{adj}\b", sense.definition, re.I):
                return f"Yes. A {x} is {sense.definition}."
        return None

    def _kind_of(self, text: str) -> str | None:
        """'Is a dog a person?': where the dictionary's main meaning of the word puts it."""
        m = re.match(r"^\W*(?i:is|are) (?:an?|the) (?P<noun>[a-z]+) (?:an?|the) (?P<kind>[a-z]+)\W*$", text)
        if not m:
            return None
        wordnet = self.convo.exposition.wordnet
        senses = wordnet.lookup(m["noun"], "n")
        if not senses or not wordnet.lookup(m["kind"], "n"):
            return None
        noun, kind = m["noun"], m["kind"]
        if wordnet.path_to(senses[0].id, kind):
            return f"Yes. In its usual meaning, {self._a(noun)} {noun} is a kind of {kind}."
        parents = [s.lemma for s in wordnet.related(senses[0].id, ("hypernym",), limit=2)]
        what = f"a kind of {parents[0]}" if parents else senses[0].definition
        return f"No. In its usual meaning, {self._a(noun)} {noun} is {what}, not {self._a(kind)} {kind}."

    @staticmethod
    def _a(word: str) -> str:
        return "an" if word[:1] in "aeiou" else "a"

    def _located(self, text: str) -> str | None:
        """'Is Burbank in California?': Burbank is in Los Angeles County, which is in California."""
        m = re.match(r"^\W*(?i:is|are) (?P<x>[A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*)*) (?:located )?in (?:the )?"
                     r"(?P<y>[A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*)*)\W*$", text)
        if not m:
            return None
        start, goal = self.repo.find_entity(m["x"]), m["y"].lower()
        if start is None:
            return None
        chain, seen, frontier = {start: [m["x"]]}, {start}, [start]
        while frontier:
            eid = frontier.pop(0)
            for row in self.repo.assertions("rel:be", subj=("e", eid), polarity=True):
                for arg in self.repo.args(row["id"]):
                    if arg["role"] != "in" or arg["value_type"] != "e" or arg["value_id"] in seen:
                        continue
                    name = self.repo.entity_row(arg["value_id"])["name"]
                    path = chain[eid] + [name]
                    if name.lower() == goal:
                        steps = ", which is in ".join(path[1:])
                        return f"Yes. {path[0]} is in {steps}." if len(path) > 2 else f"Yes. {path[0]} is in {name}."
                    seen.add(arg["value_id"])
                    chain[arg["value_id"]] = path
                    frontier.append(arg["value_id"])
        return None

    def _either(self, text: str) -> str | None:
        """'Are you a male or a female?': what he's been told about himself, or that nobody has said."""
        m = re.match(r"^\W*(?i:are|were) you (?:an? )?(?P<a>[a-z]+) or (?:an? )?(?P<b>[a-z]+)\W*$", text)
        if not m:
            return None
        for fact in self.convo.facts_about_self():
            for word in (m["a"], m["b"]):
                if re.search(rf"\b{word}\b", fact, re.I):
                    return f"{fact[0].upper()}{fact[1:]}, from what I've been told."
        return (f"Nobody has told me whether I'm {self._a(m['a'])} {m['a']} or {self._a(m['b'])} {m['b']}, "
                "and nothing I know about myself settles it.")

    def answer(self, text: str, strict: bool = False) -> str | None:
        """strict: only a fact that matches what's asked (not just who), for use before anything else answers."""
        if not (QUESTION.match(text) or text.rstrip().endswith("?")):
            return None
        for special in (self._located(text), self._either(text), self._purpose(text), self._category(text), self._property(text),
                        self._kind_of(text)):
            if special:
                return special
        subjects = self._subjects(text)
        if not subjects:
            return None
        asked = _meaning(re.findall(r"[a-z]+", text.lower().replace("'s", "")))
        names = {self.repo.entity_row(e)["name"].lower() for e in subjects}
        for e in subjects:  # "my horse": being a horse is who it is, not what's being asked
            kind_id = self.repo.entity_row(e)["kind_concept_id"]
            if kind_id:
                names.add(self.repo.concept_lemma(kind_id))
        general = bool(re.match(r"^\W*(?:what|who)(?:'s| is| are)\s+[A-Z]\w*\W*$", text))
        best, score = None, 0
        for eid in subjects:
            for fact, words in self._facts(eid):
                shared = (asked & words) - names  # what's asked, not just who it's about
                points = len(shared) * 2 + (1 if general and (asked & words) else 0)
                if points > score:
                    best, score = fact, points
        if strict and score < 2:
            return None
        if best is None:
            # "Tell me about Jen", "What do you know about Jen": everything, if anything
            if re.search(r"\b(?:about|know about)\b", text, re.I):
                facts = [f for f, _ in self._facts(subjects[0])][:4]
                if facts:
                    return f"Here's what I know: {realizer.join_clauses(facts)}."
            return None
        fact = best[0].upper() + best[1:]
        if re.match(r"^\W*(?:is|are|am|was|were|do|does|did|have|has|can)\b", text, re.I):
            # Yes only if the fact says everything the question asks: a truck isn't a motorcycle
            fact_words = _meaning(re.findall(r"[a-z']+", best.lower())) | {
                w for f, ws in self._facts(subjects[0]) if f == best for w in ws}
            if (asked - names) - fact_words:
                return None
            return f"Yes. {fact}."
        return f"{fact}."
