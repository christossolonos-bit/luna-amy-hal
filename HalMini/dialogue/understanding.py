"""Second and third passes for messages the parser couldn't take whole.

Pass 2, what is it: simplify and decompose the sentence, then classify it.
  - drop intensifiers and asides ("very", "really", "right now", "just"), and read "is being X" as "is X";
  - split a conditional into its assumption and its question ("Am I an animal if I'm human?");
  - resolve "that" / "this" to what he just said;
  - notice evaluation: adjectives whose WordNet similarity links lead to good or bad (and right/wrong,
    smart/stupid, helpful/useless, kind/mean), aimed at him or at his last reply;
  - notice questions about the conversation itself ("Do you understand what I said?").
Pass 3, reason over it:
  - a conditional is answered by assuming its premise inside a savepoint, asking, and rolling back, so
    the assumption never becomes a stored fact;
  - feedback moves his state and is answered in terms of what he actually just did;
  - "did you understand" is answered honestly from his last turn;
  - anything still unclear gets a reply tied to the words he does know and to what was just being
    discussed, not a generic request for help.
"""

import re
from dataclasses import replace

from dialogue import realizer
from nlp.lexicon import article
from nlp.parser import ParseError, parse_sentence
from nlp.semantic_frames import Frame

ASIDES = re.compile(r"\b(?:very|really|so|super|quite|pretty|just|right now|at the moment|today|kind of|sort of|"
                    r"totally|honestly|actually|literally|truly)\b\s*", re.I)
BEING = re.compile(r"\b(am|is|are|was|were) being\b", re.I)
CONDITIONAL = [re.compile(r"^(?P<q>.+?),?\s+if\s+(?P<p>.+?)\W*$", re.I),
               re.compile(r"^if\s+(?P<p>.+?),\s*(?:then\s+)?(?P<q>.+?)\W*$", re.I)]
UNDERSTAND = re.compile(r"^\W*(?:do|did) you (?:understand|get|follow|catch) (?:what i (?:said|meant|asked|mean)|me|"
                        r"that|it|what i'?m saying)\W*$|^\W*(?:does that|did that) make sense\W*$|^\W*got it\?\W*$", re.I)
EVALUATION = re.compile(r"^\W*(?:that|this|it|you)(?:'s| is| are|'re| were| was)?\s+(?:being\s+)?(?P<adj>[a-z]+(?:\s[a-z]+)?)"
                        r"(?:\s+(?:right now|today|now|so far|at this))?\W*$", re.I)
CONTINUE = re.compile(r"^\W*(?:ok(?:ay)?|alright|good|great|yes|yeah)?[,\s]*(?:that|this|it)(?:'s| has| is)?\s*"
                      r"(?:got to|gotta|has to|needs to|should|must)\s+(?:continue|keep going|keep up|stay)\W*$", re.I)
ANCHORS = {+1: ("good", "right", "correct", "smart", "intelligent", "helpful", "kind", "nice", "impressive",
                "clever", "interesting", "funny", "useful", "excellent", "awesome", "cool", "perfect", "special",
                "amazing", "brilliant"),
           -1: ("bad", "wrong", "incorrect", "stupid", "unhelpful", "useless", "mean", "rude", "boring",
                "annoying", "broken", "confusing", "horrible", "terrible", "dumb", "lame", "pointless")}


_POLARITY_CACHE: dict[str, dict[str, int]] = {}  # built once per WordNet database


class Understanding:
    def __init__(self, conversation) -> None:
        self.convo = conversation
        self.repo = conversation.repo
        self._polarity: dict[str, int] | None = None
        self.pending_confirm: dict[str, list[str]] = {}

    # --- word knowledge -------------------------------------------------------------

    def polarity(self, word: str) -> int:
        """+1 / -1 for evaluative adjectives. A word counts by its most common adjective sense: that sense is
        an anchor, or WordNet links it as similar to one ('great' ~ good, 'awful' ~ bad)."""
        wordnet = self.convo.exposition.wordnet
        word = word.lower().strip()
        if self._polarity is None and str(wordnet.path) in _POLARITY_CACHE:
            self._polarity = _POLARITY_CACHE[str(wordnet.path)]
        if self._polarity is None:
            synsets: dict[str, int] = {}
            if wordnet.available:
                for sign, anchors in ANCHORS.items():
                    for anchor in anchors:
                        for sense in wordnet.lookup(anchor, "a")[:1] + wordnet.lookup(anchor, "s")[:1]:
                            synsets.setdefault(sense.id, sign)
                            for near in wordnet.related(sense.id, ("similar", "also"), limit=60):
                                synsets.setdefault(near.id, sign)
                                for further in wordnet.related(near.id, ("similar",), limit=20):
                                    synsets.setdefault(further.id, sign)
            self._polarity = _POLARITY_CACHE[str(wordnet.path)] = synsets
        for anchor_sign, anchors in ANCHORS.items():
            if word in anchors:
                return anchor_sign
        if not wordnet.available:
            return 0
        for sense in (wordnet.lookup(word, "a") + wordnet.lookup(word, "s"))[:4]:
            if sense.id in self._polarity:  # a polar sense among its common ones ('old' as in 'good old' is rank 5)
                return self._polarity[sense.id]
        return 0

    # --- pass 2: what is it -----------------------------------------------------------

    @staticmethod
    def simplify(text: str) -> str:
        text = BEING.sub(lambda m: m.group(1), text)
        text = ASIDES.sub("", text)
        return re.sub(r"\s+", " ", text).strip(" ,")

    def _frames(self, text: str):
        try:
            return parse_sentence(text, self.convo.lexicon(), names={self.convo.self_name.lower()})
        except ParseError:
            return None

    def classify(self, sentence: str, reason: str = "") -> tuple[str, dict]:
        """(kind, details) for a sentence the first parse couldn't take."""
        plain = self.simplify(sentence)
        if UNDERSTAND.match(sentence) or UNDERSTAND.match(plain):
            return "meta", {}
        if CONTINUE.match(sentence):
            return "feedback", {"sign": +1, "word": "continue", "about": "last"}
        match = EVALUATION.match(plain)
        if match:
            words = match["adj"].split()
            sign = next((self.polarity(w) for w in reversed(words) if self.polarity(w)), 0)
            if sign:
                about = "him" if re.match(r"^\W*you\b", plain, re.I) else "last"
                return "feedback", {"sign": sign, "word": words[-1], "about": about}
        for pattern in CONDITIONAL:
            m = pattern.match(plain)
            if m:
                premise, question = self._frames(m["p"] + "."), self._frames(m["q"] + ("?" if not m["q"].endswith("?") else ""))
                if premise and question and all(isinstance(f, Frame) for f in premise + question):
                    return "conditional", {"premise": premise, "question": question}
        if plain != sentence:
            frames = self._frames(plain)
            if frames:
                return "simplified", {"text": plain, "frames": frames}
        if any(not self.convo.inquiry._known_word(w) for w in re.findall(r"[a-z]{3,}", sentence.lower())
               if not re.search(r"(.)\1{2,}", w)):
            return "unknown-word", {}
        return "unclear", {"words": self._known_words(sentence), "sentence": sentence, "reason": reason}

    def pre(self, sentence: str) -> tuple[str, dict] | None:
        """Before the full parse: feedback about him and questions about the conversation, which would
        otherwise parse into junk facts ('You are being good' -> 'I am being')."""
        plain = self.simplify(sentence)
        if UNDERSTAND.match(sentence) or UNDERSTAND.match(plain):
            return "meta", {}
        if CONTINUE.match(sentence):
            return "feedback", {"sign": +1, "word": "continue", "about": "last"}
        named = self.name_statement(sentence)
        if named:
            return "named", {"reply": named}
        heard = self.heard_me(sentence)
        if heard:
            return "heard", {"reply": heard}
        if re.match(r"^\W*(?:you(?:'re| are)?|that(?:'s| is| was)|this is)\b", plain, re.I) and "?" not in sentence:
            polar = [w for w in re.findall(r"[a-z]+", plain.lower()) if self.polarity(w)]
            if polar and not re.search(r"\b(?:not|never|isn'?t|aren'?t)\b", plain, re.I):
                about = "him" if re.match(r"^\W*you", plain, re.I) else "last"
                return "feedback", {"sign": self.polarity(polar[-1]), "word": polar[-1], "about": about}
        match = EVALUATION.match(plain)
        if match and not re.match(r"^\W*it\b", plain, re.I):
            words = match["adj"].split()
            sign = next((self.polarity(w) for w in reversed(words) if self.polarity(w)), 0)
            if sign:
                about = "him" if re.match(r"^\W*you\b", plain, re.I) else "last"
                return "feedback", {"sign": sign, "word": words[-1], "about": about}
        return None

    def _known_words(self, sentence: str) -> list[tuple[str, str]]:
        wordnet = self.convo.exposition.wordnet
        out = []
        for word in re.findall(r"[a-z]+", sentence.lower()):
            if len(word) < 4 or word in ("that", "this", "what", "with", "have", "your", "about", "just", "there"):
                continue
            sense = wordnet.usual(word) if wordnet.available else None
            if sense and word not in [w for w, _ in out]:
                out.append((word, sense.definition))
        return out

    # --- pass 3: reason over it ---------------------------------------------------------

    def answer(self, kind: str, details: dict, uid: int) -> str | None:
        """Pass 3 for a classified sentence, or None to leave it to the usual fallback."""
        if kind in ("named", "heard"):
            return details["reply"]
        if kind == "meta":
            return self._understood()
        if kind == "feedback":
            return self._feedback(details)
        if kind == "conditional":
            return self._suppose(details["premise"], details["question"], uid)
        if kind == "unclear":
            return self._ask_about_part(details)
        return None

    def _last_turn(self) -> tuple[str | None, str | None, bool]:
        """(what they said before this, what I replied, whether I understood it)."""
        rows = self.repo.conn.execute(
            "SELECT t.role, t.text, t.utterance_id FROM dialogue_turns t JOIN utterances u ON u.id = t.utterance_id "
            "WHERE u.speaker = ? ORDER BY t.id DESC LIMIT 6", (self.convo.state.speaker,)).fetchall()
        said = mine = None
        understood = True
        current = rows[0]["utterance_id"] if rows else None
        for r in rows:
            if r["utterance_id"] == current:
                continue
            if r["role"] == "engine" and mine is None:
                mine = r["text"]
            elif r["role"] == "user" and said is None:
                said = r["text"]
                break
        if mine and re.search(r"didn't follow|not following|couldn't|I'm unsure|I'm not sure what you mean", mine):
            understood = False
        return said, mine, understood

    def gist(self, said: str) -> str:
        """What the person was doing when they said this, in his own words."""
        early = self.pre(said)
        if early and early[0] == "feedback":
            return ("you were telling me I was doing well" if early[1]["sign"] > 0
                    else "you weren't happy with what I'd said")
        if early and early[0] == "meta":
            return "you were checking whether I'd understood you"
        frames = self._frames(said)
        if frames and all(isinstance(f, Frame) for f in frames):
            if frames[0].act == "ask":
                return f"you were asking me {chr(8220)}{said.rstrip('.!?')}{chr(8221)}"
            facts = [realizer.realize_frame(f, f.polarity, "you" if f.subject and f.subject.pronoun == "i" else
                                            "I" if f.subject and f.subject.pronoun == "you" else None)
                     for f in frames if f.act == "assert" and not hasattr(f, "conditions")]
            if facts:
                return f"you were telling me that {realizer.join_clauses(facts)}"
        return f"you said {chr(8220)}{said.rstrip('.!?')}{chr(8221)}"

    def _understood(self) -> str:
        said, mine, understood = self._last_turn()
        if said is None:
            return "There's nothing before this for me to have understood yet."
        if understood:
            return f"I think so. As I understood it, {self.gist(said)}."
        return (f"Not fully. {self.gist(said)[:1].upper()}{self.gist(said)[1:]}, and I couldn't put the whole "
                "sentence together. If you say it another way, I'll try again.")

    def _feedback(self, details: dict) -> str:
        sign, word = details["sign"], details["word"]
        before = self.convo.self_model.current()
        self.convo.self_model.event("complimented" if sign > 0 else "insulted")
        after = self.convo.self_model.current()
        said, mine, understood = self._last_turn()
        moved = after["valence"] - before["valence"]
        felt = ("" if abs(moved) < 0.03 else
                " That lifted my mood." if moved > 0.15 else " That lifted my mood a little." if moved > 0 else
                " That pulled my mood down." if moved < -0.15 else " That stung a little.")
        substantive = (said and mine and len(mine) > 60 and said.rstrip().endswith("?")
                       and not self.pre(said))
        what = f" That was my answer to {chr(8220)}{said.rstrip('.!?')}{chr(8221)}." if substantive else ""
        if sign > 0:
            lead = "Then I'll keep going the same way." if word == "continue" else \
                f"Thank you. I'm glad that came across as {word}."
            return lead + felt + what
        fix = (" I didn't fully follow you last time, so if that's what went wrong, tell me what you meant."
               if not understood else " If something I said was wrong, tell me what's right and I'll correct it.")
        return f"I hear that you think it was {word}.{felt}{fix}"

    def _personal(self, frame: Frame) -> Frame:
        """'I' and 'you' as the actual people, so category reasoning can find them by name."""
        from nlp.semantic_frames import NounPhrase
        subject = frame.subject
        if subject is not None and subject.pronoun in ("i", "you"):
            name = self.convo.state.speaker if subject.pronoun == "i" else self.convo.self_name
            frame = replace(frame, subject=NounPhrase(head=name.lower(), proper=True, surface=name))
        return frame

    def _as_category(self, frame: Frame) -> Frame:
        """'I'm human' -> 'I am a human' when the description is really a kind of thing."""
        from nlp.semantic_frames import NounPhrase
        wordnet = self.convo.exposition.wordnet
        if frame.relation == "property" and len(frame.properties) == 1 and wordnet.available \
                and wordnet.lookup(frame.properties[0], "n"):
            word = frame.properties[0]
            return replace(frame, relation="is_a", properties=(),
                           obj=NounPhrase(head=word, determiner="a", surface=word))
        return frame

    def _suppose(self, premise: list[Frame], question: list[Frame], uid: int) -> str:
        """Assume the premise, answer the question, forget the premise. The reasoning runs on a copy of
        his memory, so the assumption can never become one of his facts."""
        import sqlite3
        from app.conversation import Conversation
        from db.repository import Repository
        spoken_premise, spoken_question = premise, question
        premise = [self._personal(self._as_category(f)) for f in premise]
        question = [self._personal(f) for f in question]
        scratch = Repository(":memory:")
        try:
            scratch.conn.close()
            scratch.conn = sqlite3.connect(":memory:")
            scratch.conn.row_factory = sqlite3.Row
            self.repo.conn.backup(scratch.conn)
            mind = Conversation(scratch, speaker=self.convo.state.speaker, self_name=self.convo.self_name,
                                wordnet=self.convo.exposition.wordnet)
            mind.resolver.begin(None)
            for frame in premise:
                mind.learner.learn(replace(frame, act="assert"), None)
            mind.resolver.begin(None)
            q = replace(question[0], act="ask", polarity=True)
            query, _, subject_text = mind._build_query(q)
            answer = mind.engine.ask(query, q) if query else None
            status = answer.status if answer else "unknown"
            because = realizer.explain(scratch, answer.support[0]) if answer and answer.support else None
            if status == "unknown":
                lexical = mind.exposition.category_answer(q)
                if lexical:
                    status = "yes"
                    because = lexical[0].split(" The category connection is ")[-1].rstrip(".").replace(" to ", " \u2192 ")
        finally:
            scratch.close()
        pov = lambda f: realizer.realize_frame(f, True, "you" if f.subject and f.subject.pronoun == "i"  # noqa: E731
                                               else "I" if f.subject and f.subject.pronoun == "you" else None)
        assumed = realizer.join_clauses([pov(self._as_category(f)) for f in spoken_premise])
        asked = pov(spoken_question[0])
        if status == "yes":
            link = f", going by {because}" if because else ""
            return (f"Yes. If {assumed}, then {asked}{link}. I haven't stored that as a fact, since you only "
                    "asked me to suppose it.")
        if status == "no":
            return f"No. Even if {assumed}, it doesn't follow that {asked}."
        return f"Even supposing {assumed}, I can't work out whether {asked}. Nothing I know connects the two."

    def _ask_about_part(self, details: dict) -> str | None:
        """Ask about the exact part he couldn't follow, so it can be taught: not 'what did you mean?'."""
        sentence, reason = details.get("sentence", ""), details.get("reason", "")
        words = details.get("words", [])
        stuck = re.search(r"(?:stopped at|lost at|after|at) '([^']+)'", reason)
        token = stuck.group(1) if stuck else None
        tokens = re.findall(r"[\w']+", sentence)
        lowered = [t.lower() for t in tokens]
        if token and token.lower() in lowered:
            i = lowered.index(token.lower())
            left, right = " ".join(tokens[:i]), " ".join(tokens[i:])
            if left and right:
                if token.lower() in ("it", "that", "this", "he", "she", "they", "him", "her", "them"):
                    return (f"I followed '{left}', but I couldn't tell who or what '{token}' means there. "
                            f"What were you referring to?")
        return self.make_sense(sentence)

    GIST_STOP = {"i", "me", "my", "you", "your", "we", "us", "our", "it", "its", "this", "that", "these", "those",
                 "a", "an", "the", "and", "or", "but", "so", "to", "of", "in", "on", "at", "for", "with", "about",
                 "is", "are", "was", "were", "be", "been", "am", "do", "does", "did", "have", "has", "had", "can",
                 "could", "would", "should", "will", "just", "really", "very", "what", "why", "how", "who", "where",
                 "when", "which", "if", "then", "not", "no", "yes", "like", "know", "think", "mean", "get", "got",
                 "some", "any", "all", "there", "here", "right", "now", "up", "out", "kind", "thing", "things",
                 "something", "anything", "everything", "lot", "way", "whenever", "wherever", "whatever", "honestly", "him", "her", "he", "she", "they", "them"}

    def make_sense(self, sentence: str) -> str | None:
        """For a sentence he couldn't parse: work out the gist instead of fixating on one word.
        Is it a question or a statement, what is it about (people and things he knows first, then the
        main words), what does he already know about that, and the one word, if any, he really doesn't know."""
        words = re.findall(r"[A-Za-z][\w'-]*", sentence)
        if not words:
            return None
        asking = sentence.rstrip().endswith("?") or words[0].lower() in (
            "what", "why", "how", "who", "where", "when", "which", "do", "does", "did", "is", "are", "can",
            "could", "would", "will", "have", "has")
        repo, wordnet = self.repo, self.convo.exposition.wordnet
        named, plain, unknown = [], [], []
        dets = {"the", "a", "an", "my", "your", "his", "her", "our", "their", "this", "that", "some", "any", "no"}
        for i, w in enumerate(words):
            low = w.lower()
            if low in self.GIST_STOP or len(low) < 3:
                continue
            before = words[i - 1].lower() if i else ""
            eid = repo.find_entity(w)
            if eid is not None and eid != self.convo.self_id and w.lower() != self.convo.state.speaker.lower():
                named.append(w)
            elif repo.find_concept(low) or getattr(wordnet.usual(low), "pos", None) == "n" and (
                    i == 0 or before in dets or not (wordnet.lookup(low, "v") or wordnet.lookup(low, "a")
                                                     or wordnet.lookup(low, "s"))):  # "the weather", not "come"
                if low not in ("stuff", "somewhere", "someone", "lately", "today", "honestly", "way", "time"):
                    plain.append(low)
            elif wordnet.usual(low) is not None:
                pass  # a verb or describing word: part of what's said, not what it's about
            elif w[:1].islower() and not any(wordnet.usual(low[:-n]) for n in (1, 2, 3) if len(low) - n >= 2):
                unknown.append(low)  # "goes" is "go"; only a word the dictionary can't place at all
        about = list(dict.fromkeys(named + plain))[:2]
        if re.match(r"^\W*i (?:feel|think|guess|believe)\b", sentence, re.I) and not named:
            return "I'm not sure I followed all of that, but I'd like to. What makes you say so?"
        if not about and not unknown:
            return ("I didn't catch what that was about. Say it a different way, maybe shorter, "
                    "and I'll have another go.")
        if asking and len(about) == 1 and words[0].lower() in ("what", "who", "whats", "what's", "tell", "explain"):
            # A garbled question about one thing ("What do a truck is?") is asking what that thing is
            thing = about[0]
            question = f"Who is {thing}?" if thing in named else f"What is {article(thing)} {thing}?"
            if question.lower().rstrip("?") != sentence.lower().strip(" ?.!"):
                return self.convo.handle(question, speaker=self.convo.state.speaker).text
        topic = " and ".join(about) if about else unknown[0]
        lead = (f"I think you're asking me something about {topic}, but I couldn't work out exactly what."
                if asking else f"I think you're telling me something about {topic}, but I couldn't pin down what.")
        known = ""
        for name in named[:1]:
            eid = repo.find_entity(name)
            rows = repo.conn.execute("SELECT * FROM assertions WHERE status='active' AND polarity=1 AND subj_type='e' "
                                     "AND subj_id=? ORDER BY id DESC LIMIT 3", (eid,)).fetchall()
            said = [t for t in (realizer.realize_assertion(repo, r) for r in rows) if t]
            if said:
                known = f" What I do know is that {realizer.join_clauses(said)}."
        gap = f" The one word I don't know there is '{unknown[0]}'." if unknown and about else ""
        ask = (" Could you ask it another way?" if asking else
               " What's the main thing you want me to know about it?")
        return f"{lead}{gap}{known}{ask}"

    # --- names ---------------------------------------------------------------------

    NAME_IS = re.compile(r"^\W*(?P<owner>(?i:my|his|her|its|their|your)|[A-Z][\w-]*'s) name(?:'s| is)\s+"
                         r"(?P<name>[A-Z][\w-]*(?: [A-Z][\w-]*)?)\W*$")
    CALL_ME = re.compile(r"^\W*(?:(?:you can |just )?call me|i'?m called|i go by)\s+(?P<name>[A-Z][\w-]*)\W*$", re.I)

    def name_statement(self, sentence: str) -> str | None:
        """'My name is Sam', 'His name is Chance', 'Call me T': names are aliases, and naming a thing
        someone owns makes it a named individual ('your white pit bull is called Chance')."""
        m = self.NAME_IS.match(sentence) or self.CALL_ME.match(sentence)
        if not m:
            return None
        name = m["name"]
        owner = (m.groupdict().get("owner") or "my").lower()
        repo, state = self.repo, self.convo.state
        if owner == "my":
            me = repo.entity(state.speaker)
            with repo.conn:
                repo.conn.execute("CREATE TABLE IF NOT EXISTS told_names(entity_id INTEGER, name TEXT, "
                                  "PRIMARY KEY(entity_id, name))")
            told = repo.conn.execute("SELECT 1 FROM told_names WHERE entity_id=? AND lower(name)=lower(?)",
                                     (me, name)).fetchone()
            with repo.conn:
                repo.conn.execute("INSERT OR IGNORE INTO told_names VALUES (?, ?)", (me, name))
            if told:
                return f"I know, {name}. You told me before."
            if name.lower() == state.speaker.lower():
                # Only the account name was known until now; being told is new
                return f"Nice to meet you, {name}. That's the name I see on your account too, so now I'm sure of it."
            repo.add_alias(me, name)
            account = state.speaker
            key = getattr(self.convo, "person_key", None)
            if key:  # the name they gave is the one he uses from now on; the account name still finds them
                with repo.conn:
                    repo.conn.execute("UPDATE people SET name = ? WHERE key = ?", (name, key))
            state.speaker = name
            return (f"Nice to meet you, {name}. Your account shows {account}, so I'll know you by both, "
                    f"and call you {name}.")
        if owner == "your":
            repo.add_alias(self.convo.self_id, name)
            return f"Okay. My name is {self.convo.self_name}, but I'll answer to {name} as well."
        if owner.endswith("'s"):
            target = repo.find_entity(m["owner"][:-2])
            target = ("e", target) if target else None
        else:
            target = state.last_thing if owner == "its" else (state.last_thing or state.last_person)
        if not target or target[0] != "e":
            return f"Whose name is {name}? I'm not sure who you mean."
        row = repo.entity_row(target[1])
        if row["proper"]:
            repo.add_alias(row["id"], name)
            return f"Got it: {row['name']} is also called {name}."
        described = realizer.entity_text(repo, row["id"], definite=True)
        with repo.conn:
            repo.conn.execute("UPDATE entities SET name = ?, proper = 1 WHERE id = ?", (name, row["id"]))
        repo.add_alias(row["id"], name)
        return f"Got it: {described} is called {name}. I'll call it {name} from now on."

    # --- "did you hear me?" -------------------------------------------------------------

    HEARD = re.compile(r"^\W*(?:did|do) you (?:hear|catch|get) (?:me|that|what i said)(?: about (?P<topic>.+?))?\W*$", re.I)

    def heard_me(self, sentence: str) -> str | None:
        """What he overheard from this person without being addressed, offered back to learn."""
        m = self.HEARD.match(sentence)
        if not m:
            return None
        speaker = self.convo.state.speaker
        rows = self.repo.conn.execute(
            "SELECT u.id, u.text FROM utterances u WHERE u.speaker = ? AND u.created_at >= datetime('now', '-15 minutes') "
            "AND NOT EXISTS (SELECT 1 FROM dialogue_turns t WHERE t.utterance_id = u.id AND t.role = 'engine') "
            "ORDER BY u.id DESC LIMIT 12", (speaker,)).fetchall()
        topic = re.sub(r"\bmy\b", "your", (m["topic"] or "").lower())
        words = set(re.findall(r"[a-z]{3,}", topic)) - {"your", "the", "that", "this"}
        picked = [r["text"] for r in rows
                  if not self.HEARD.match(r["text"]) and (not words or words & set(re.findall(r"[a-z]+", r["text"].lower())))]
        picked = list(reversed(picked[:3]))
        if not picked:
            return ("No, I didn't catch anything you said just now. Say it again with my name and I'll take it in."
                    if not topic else f"I didn't hear anything about {topic}. Tell me again?")
        quoted = " ".join(f"\u201c{t.rstrip('.!?')}\u201d" for t in picked)
        self.pending_confirm[speaker] = picked
        return (f"I heard you say {quoted}, but you weren't talking to me then, so I didn't learn it. "
                "Do you want me to?")

    def try_confirm(self, text: str) -> str | None:
        speaker = self.convo.state.speaker
        picked = self.pending_confirm.pop(speaker, None)
        if not picked:
            return None
        if re.match(r"^\W*(?:yes|yeah|yep|sure|please|go ahead|do it|ok(?:ay)?|learn it|remember it)\b", text, re.I):
            reply = self.convo.handle(" ".join(picked), speaker=speaker)
            return reply.text
        return None


