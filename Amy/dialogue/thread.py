"""Keeping the thread of a conversation across turns.

Holds what the conversation is about right now, and reads each new message against it:

  focus       who or what is being talked about: the last named one brought up (not the speaker, not him),
              kept through side remarks ("I like pizza" doesn't change who "she" is)
  topics      every focus in order, so "back to Chance" and "what were we talking about" work
  last ask    the last question asked of him, so "And Jen?" / "What about Sam?" means the same question
              about someone else

Before a message is parsed it's resolved against the thread:
  "Back to Chance, he is white"        -> "Chance is white"        (focus moves back to Chance)
  "She works at a hospital"            -> "Jen works at a hospital" (she = the focus, three turns later)
  "And Jen?"                           -> "Where does Jen live?"    (the last question, new subject)
  "My sister is named Jen"             -> "I have a sister. Its name is Jen."
  "He likes to swim"                   -> "Chance likes swimming"
  "Let's talk about Chance"            -> focus Chance, and what he knows about it
and questions about an attribute ("What color is my truck?") are answered from the properties he
knows, using the dictionary to tell which of them are colors.
The resolved form is kept with the original, so searching the conversation finds "he likes to swim"
under Chance.
"""

import re

SUBJECT_PRONOUN = re.compile(r"^(?P<lead>\W*(?:and |also,? |oh,? |plus,? |so,? )?)(?P<p>he|she|they)\b(?P<rest>.*)$", re.I)
RETURN = re.compile(r"^\W*(?:anyway,? |so,? |okay,? )?(?:(?:going |getting |coming )?back to|about|speaking of|as for|regarding)\s+"
                    r"(?P<who>[A-Z][\w-]*|my \w+|your \w+)\s*[,:-]\s*(?P<rest>.+)$", re.I)
TALK_ABOUT = re.compile(r"^\W*(?:let'?s|can we|i want to|i wanna) (?:talk|chat) about (?P<who>.+?)\W*$", re.I)
ELLIPSIS = re.compile(r"^\W*(?:and|what about|how about|and what about|same for|what of)\s+(?P<who>[A-Z][\w-]*|you|me)\W*$", re.I)
NAMED = re.compile(r"^\W*(?P<owner>my|your) (?P<kind>[a-z]+) is (?:named|called) (?P<name>[A-Z][\w-]*)\W*$", re.I)
LIKES_TO = re.compile(r"\b(?P<verb>likes|loves|hates|enjoys|like|love|hate|enjoy) to (?P<act>[a-z]+)\b", re.I)
ATTRIBUTE = re.compile(r"^\W*what (?P<attr>colou?r|size|shape|age) (?:is|are|was) (?P<what>.+?)\W*$", re.I)
IT_Q = re.compile(r"^(?P<lead>\W*(?:is|was|does|did|can|has) )it\b(?P<rest>.*\?)\s*$", re.I)


def _ing(verb: str) -> str:
    v = verb.lower()
    if v.endswith("ie"):
        return v[:-2] + "ying"
    if v.endswith("e") and not v.endswith("ee"):
        return v[:-1] + "ing"
    if re.search(r"[^aeiou][aeiou][bdgmnprt]$", v) and len(v) <= 4:
        return v + v[-1] + "ing"
    return v + "ing"


class Thread:
    def __init__(self, conversation) -> None:
        self.convo = conversation
        self.repo = conversation.repo
        self.focus: dict[str, str] = {}          # speaker -> name being talked about
        self.topics: dict[str, list[str]] = {}   # speaker -> foci in order
        self.last_ask: dict[str, tuple[str, str]] = {}  # speaker -> (question, its subject)
        self.prefix: str | None = None
        with self.repo.conn:
            self.repo.conn.execute("CREATE TABLE IF NOT EXISTS resolved_text(utterance_id INTEGER PRIMARY KEY, text TEXT)")

    # --- the thread -------------------------------------------------------------------------

    def _mine(self, name: str) -> bool:
        return name.lower() in (self.convo.state.speaker.lower(), self.convo.self_name.lower())

    def _set_focus(self, name: str) -> None:
        speaker = self.convo.state.speaker
        if self._mine(name):
            return
        self.focus[speaker] = name
        topics = self.topics.setdefault(speaker, [])
        if not topics or topics[-1] != name:
            topics.append(name)

    def current(self) -> str | None:
        return self.focus.get(self.convo.state.speaker)

    def _known_names(self, text: str) -> list[str]:
        return [w for w in re.findall(r"\b[A-Z][\w-]*\b", text)
                if not self._mine(w) and self.repo.find_entity(w) is not None]

    # --- before parsing ------------------------------------------------------------------------

    def resolve(self, text: str, uid: int) -> tuple[str, str | None]:
        """(the message as it should be read, or a reply to give straight away)."""
        speaker = self.convo.state.speaker
        out = re.sub(r"^\W*(?:i (?:just |really )?(?:want|wanted|need|needed) (?:you )?to (?:know|tell you|say)|"
                     r"just so you know|i want to let you know|i'?m telling you|i (?:just )?want to say|"
                     r"you should know)(?: that)?[, ]+", "", text, flags=re.I) or text
        out = re.sub(r"^\W*(?:i said|i told you|like i said|i'?m saying)[, ]+", "", out, flags=re.I) or out
        out = out[:1].upper() + out[1:]
        m = TALK_ABOUT.match(out)
        if m:
            who = m["who"]
            self._set_focus(who)
            if self.repo.find_entity(who) is None:
                return out, (f"Sure, let's talk about {who}. I don't know anything about {who} yet, "
                             "so start me off. What should I know?")
            self.prefix = f"Sure, let's talk about {who}."
            out = f"What do you know about {who}?"
        m = re.match(r"^\W*(?P<who>i|you|we|[A-Z][\w-]*) (?P<v>have|has|got|own|owns) (?:a|an) (?P<kind>[a-z]+) "
                     r"(?:named|called) (?P<name>[A-Z][\w-]*)(?P<rest>.*)$", out)
        if m:  # "I have a dog named Chance": owning one, and its name, as two things he can store
            self._set_focus(m["name"])
            have = "have" if m["who"].lower() in ("i", "you", "we") else "has"
            out = f"{m['who']} {have} a {m['kind']}. Its name is {m['name']}.{m['rest']}"
        m = re.match(r"^(?P<pre>.*\b(?:like|likes|love|loves|enjoy|enjoys) (?P<obj>[a-z][a-z ]*?)),? (?:and )?"
                     r"(?:it'?s|that'?s|it is) (?P<whose>my|our|your|his|her) (?:absolute )?favou?rite (?P<cat>[a-z]+)\W*$", out, re.I)
        if m:  # "I really like pizza, it's my favorite food"
            out = f"{m['pre']}. {m['whose'].capitalize()} favorite {m['cat']} is {m['obj']}."
        m = NAMED.match(out)
        if m:  # "My sister is named Jen"
            self._set_focus(m["name"])
            article = "an" if m["kind"][:1] in "aeiou" else "a"
            out = f"{'I have' if m['owner'].lower() == 'my' else 'You have'} {article} {m['kind']}. Its name is {m['name']}."
        m = RETURN.match(out)
        if m:  # "Back to Chance, he is white"
            who = m["who"]
            self._set_focus(who)
            rest = m["rest"]
            out = SUBJECT_PRONOUN.sub(lambda p: f"{p['lead']}{who}{p['rest']}", rest, count=1)
            if out == rest and re.match(r"^\W*it\b", rest, re.I):
                out = re.sub(r"^\W*it\b", who, rest, count=1, flags=re.I)
        m = ELLIPSIS.match(out)
        if m and speaker in self.last_ask:  # "And Jen?" -> the last question, about Jen
            question, subject = self.last_ask[speaker]
            who = m["who"]
            out = re.sub(rf"\b{re.escape(subject)}\b", who, question, count=1)
        focus = self.current()
        if focus:
            p = SUBJECT_PRONOUN.match(out)
            if p and p["p"].lower() != "they":  # "She works at a hospital": she is who we're on
                out = f"{p['lead']}{focus}{p['rest']}"
            if out.rstrip().endswith("?"):  # "What do you know about him?"
                out = re.sub(r"\b(?:about|with|of|to) (?:him|her)(?=\W*$)", lambda o: o.group(0).split()[0] + " " + focus,
                             out, flags=re.I)
        if IT_Q.match(out) and self.convo.state.last_thing and self.convo.state.last_thing[0] == "e":
            from dialogue import realizer
            thing = realizer.entity_text(self.repo, self.convo.state.last_thing[1], definite=True)
            out = IT_Q.sub(lambda q: f"{q['lead']}{thing}{q['rest']}", out, count=1)
        out = LIKES_TO.sub(lambda l: f"{l['verb']} {_ing(l['act'])}", out)
        place = re.search(r"\b(?!(?:No|Yes|Okay|Ok|Well|Oh|So|But|And|Hey|Hi|Actually|Sure|Right)\b)(?P<town>[A-Z][a-z]+(?: [A-Z][a-z]+)?), (?P<region>[A-Z][a-z]+(?: [A-Z][a-z]+)?)\b(?P<end>\W*)$", out)
        from dialogue.web_lookup import ASK as SEARCH_ASK
        if SEARCH_ASK.search(out):
            place = None  # "search for Burbank, California": the search takes the place as said
        if place and not out.rstrip().endswith("?"):
            out = f"{out[:place.start()]}{place['town']}. {place['town']} is in {place['region']}."
        place_q = re.search(r"\b(?!(?:No|Yes|Okay|Ok|Well|Oh|So|But|And|Hey|Hi|Actually|Sure|Right)\b)"
                            r"(?P<town>[A-Z][a-z]+(?: [A-Z][a-z]+)?), [A-Z][a-z]+(?: [A-Z][a-z]+)?\b", out)
        if place_q and out.rstrip().endswith("?") and not self.repo.find_entity(place_q["town"]):
            # A town with its state: a real place. If he has nothing on it, find out rather than guess.
            self.convo.web.last_term = place_q.group(0)
            return out, (self.convo.web.note(place_q.group(0)) and self.convo.web.note(place_q.group(0))["summary"]) \
                or self.convo.web.propose(speaker, place_q.group(0), "because nobody has told me about it")
        if out != text:
            with self.repo.conn:
                self.repo.conn.execute("INSERT OR REPLACE INTO resolved_text VALUES (?, ?)", (uid, out))
        attr = self.attribute(out)
        if attr:
            return out, attr
        return out, None

    def attribute(self, text: str) -> str | None:
        """'What color is my truck?': the properties he knows that the dictionary says are colors."""
        m = ATTRIBUTE.match(text)
        if not m:
            return None
        from nlp.parser import ParseError, parse_noun_phrase
        try:
            phrase, _ = parse_noun_phrase(m["what"], self.convo.lexicon())
            term = self.convo.resolver.resolve(phrase, "subject", False)
        except (ParseError, Exception):
            return None
        state = self.convo.state
        if (not term or term[0] != "e") and state.last_thing and state.last_thing[0] == "e":
            row = self.repo.entity_row(state.last_thing[1])  # "my truck": the truck we were just talking about
            if row["kind_concept_id"] and self.repo.concept_lemma(row["kind_concept_id"]) == phrase.head:
                term = state.last_thing
        if not term or term[0] != "e":
            return None
        attr = "color" if m["attr"].lower().startswith("colo") else m["attr"].lower()
        wordnet = self.convo.exposition.wordnet
        props = [self.repo.concept_lemma(r["obj_id"]) for r in
                 self.repo.assertions("has_property", subj=term, polarity=True)]
        fits = [p for p in props if any(wordnet.path_to(s.id, attr) for s in wordnet.lookup(p, None)[:3])]
        from dialogue import realizer
        name = realizer.entity_text(self.repo, term[1], definite=True)
        if fits:
            return f"{name[:1].upper()}{name[1:]} is {realizer.join_clauses(fits)}."
        return f"I don't know what {attr} {name} is. Nobody's told me."

    # --- after the reply ---------------------------------------------------------------------------

    def after(self, said: str) -> None:
        speaker = self.convo.state.speaker
        names = self._known_names(said)
        state = self.convo.state
        if names:
            self._set_focus(names[0])
        elif state.last_person and state.last_person[0] == "e":
            row = self.repo.entity_row(state.last_person[1])
            if row["proper"]:
                self._set_focus(row["name"])
        if said.rstrip().endswith("?") and names:
            self.last_ask[speaker] = (said, names[0])
