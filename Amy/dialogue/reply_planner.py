"""How he works out a reply to anything that isn't a plain fact or a question his knowledge answers directly.
Every reply goes through the same six steps. Nothing here is a stock sentence: replies are planned as
clauses (who, does what, to what, how) and built by the grammar.

  1. understand   what the sentence is doing, from its form and its words: asking, telling, asking a
                  favour, offering, reacting, greeting, thanking, apologising, judging him, venting,
                  promising, telling a story. Social words are recognised through the dictionary (a word
                  whose meaning falls under greeting, farewell, thanks, apology), not a phrase list.
                  Also: who it's about (him, the speaker, someone else), and how it feels (polarity).
  2. update       his own state moves with it (praise, insult, someone's bad news), and he notes how the
                  speaker seems to feel and the words people use, so he can use them back.
  3. decide       candidate goals, each scored by how well it fits what was understood, times a weight
                  he has learned from how earlier replies went: answer, agree or disagree (checking the
                  claim against what he knows), empathise, repair a mistake, accept or decline a favour
                  (checking whether he can, from his own record), connect it to something he knows, ask
                  about what's missing, return a greeting.
  4. choose       what to say for each chosen goal, taken from memory and his own state.
  5. build        clauses realised by the grammar, perspective turned around (their "my" is his "your").
  6. learn        if the next thing said corrects or criticises the reply, the goals that produced it
                  lose weight for that kind of sentence; praise gives them weight. Greetings and
                  farewells people use with him are kept and used back.
"""

import json
import re
from dataclasses import dataclass, field

from nlp.morphology import past, third_person

SOCIAL = {"greeting": "greet", "farewell": "farewell", "thanks": "thanks", "gratitude": "thanks",
          "apology": "apology", "congratulation": "congratulate"}
WH = ("what", "where", "who", "when", "why", "how", "which")
AUX = ("do", "does", "did", "is", "are", "am", "was", "were", "can", "could", "will", "would", "should", "have",
       "has", "had", "may", "shall")
SELF_VERBS_BODY = {"take", "carry", "open", "close", "drive", "go", "come", "walk", "run", "eat", "drink", "buy",
                   "pay", "bring", "give", "hand", "pick", "meet", "sit", "stand", "hold", "cook", "clean", "lift",
                   "move", "visit", "travel", "fly", "swim", "dance", "sing", "play", "watch", "turn", "put",
                   "fetch", "book", "reserve", "phone", "send", "mail", "wash", "fix", "repair", "build", "join",
                   "leave", "stay", "help"}
SELF_VERBS_MIND = {"tell", "explain", "say", "remember", "learn", "listen", "answer", "look", "check", "find", "think",
                   "talk", "read", "teach", "repeat", "hear", "understand", "search"}


@dataclass
class Reading:
    text: str
    form: str = "statement"            # question | statement | request | fragment
    act: str = "tell"
    target: str = "other"              # self | speaker | other | last
    polarity: int = 0
    verb: str | None = None
    words: list = field(default_factory=list)
    frame: object = None


@dataclass
class Clause:
    subj: str
    verb: str
    rest: str = ""
    tense: str = "present"
    polarity: bool = True
    modal: str | None = None
    question: str | None = None        # None | "yn" | a wh-word

    def text(self) -> str:
        first, plural = self.subj.lower() == "i", self.subj.lower() in ("you", "we", "they")
        subj = "I" if first else self.subj
        if self.question:
            if self.modal:
                aux, main = self.modal, self.verb
            elif self.verb == "be":
                aux = ("was" if first else "were" if plural else "was") if self.tense == "past" else \
                    ("am" if first else "are" if plural else "is")
                main = ""
            else:
                aux = "did" if self.tense == "past" else ("do" if first or plural else "does")
                main = self.verb
            neg = " not" if not self.polarity else ""
            head = f"{self.question} " if self.question != "yn" else ""
            out = f"{head}{aux}{neg} {subj} {main} {self.rest}"
            return re.sub(r"\s+", " ", out).strip() + "?"
        if self.modal:
            verb = f"{self.modal} {self.verb}" if self.polarity else (
                f"can't {self.verb}" if self.modal == "can" else f"{self.modal} not {self.verb}")
        elif self.verb == "be":
            verb = ("was" if first or not plural else "were") if self.tense == "past" else \
                ("am" if first else "are" if plural else "is")
            verb = verb if self.polarity else verb + " not"
        elif not self.polarity:
            verb = f"{'did' if self.tense == 'past' else 'do' if first or plural else 'does'} not {self.verb}"
        else:
            verb = past(self.verb) if self.tense == "past" else (self.verb if first or plural else third_person(self.verb))
        return re.sub(r"\s+", " ", f"{subj} {verb} {self.rest}").strip() + "."


def sentence_case(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def mirror(text: str) -> str:
    """Their words from his side: 'my son' -> 'your son', 'I' -> 'you', 'you' -> 'me'."""
    swap = {"i": "you", "me": "you", "my": "your", "mine": "yours", "myself": "yourself", "we": "you", "our": "your",
            "you": "I", "your": "my", "yours": "mine", "yourself": "myself", "am": "are", "i'm": "you're",
            "you're": "I'm", "i've": "you've", "i'll": "you'll", "was": "was"}
    parts = re.findall(r"[\w']+|\W+", text)
    out = []
    for i, w in enumerate(parts):
        low = w.lower()
        if low == "you":
            nxt = "".join(parts[i + 1:i + 3]).strip().lower()
            out.append("I" if i == 0 or re.match(r"^(?:are|were|do|did|have|will|can|should|would|could)\b", nxt) else "me")
        elif low in swap:
            out.append(swap[low])
        else:
            out.append(w)
    text = "".join(out)
    text = re.sub(r"\byou was\b", "you were", text)
    return re.sub(r"\bI are\b", "I am", text)


class Planner:
    def __init__(self, conversation) -> None:
        self.convo = conversation
        self.repo = conversation.repo
        self.last_plan: dict[str, tuple[str, list[str]]] = {}
        self.speaker_mood: dict[str, int] = {}
        with self.repo.conn:
            self.repo.conn.execute("CREATE TABLE IF NOT EXISTS reply_weights(act TEXT, goal TEXT, weight REAL, "
                                   "PRIMARY KEY(act, goal))")
            self.repo.conn.execute("CREATE TABLE IF NOT EXISTS phrasings(act TEXT, phrase TEXT, uses INTEGER, "
                                   "PRIMARY KEY(act, phrase))")

    # --- 1. understand ---------------------------------------------------------------------------

    def _social(self, words: list[str]) -> str | None:
        """Greeting / farewell / thanks / apology: known from what the words mean in the dictionary."""
        wordnet = self.convo.exposition.wordnet
        if len(words) > 6 or not wordnet.available:
            return None
        for w in words[:3]:
            for sense in wordnet.lookup(w, None)[:3]:
                for target, act in SOCIAL.items():
                    if sense.pos in ("n", "v", "r") and wordnet.path_to(sense.id, target):
                        return act
        return None

    def understand(self, text: str) -> Reading:
        r = Reading(text=text)
        words = re.findall(r"[a-z']+", text.lower())
        r.words = words
        if not words:
            r.form, r.act = "fragment", "noise"
            return r
        u = self.convo.understanding
        signs = [u.polarity(w) for w in words if u.polarity(w)]
        negated = bool(re.search(r"\b(?:not|never|no)\b|n't\b", text, re.I))
        r.polarity = (1 if sum(signs) > 0 else -1 if sum(signs) < 0 else 0) * (-1 if negated else 1)
        first = words[0] if words[0] not in ("well", "oh", "so", "and", "but", "hey", "okay", "ok") or len(words) == 1 \
            else (words[1] if len(words) > 1 else words[0])
        rest = words[words.index(first) + 1:] if first in words else words[1:]
        subj = rest[0] if first in AUX and rest else first
        r.target = ("self" if subj in ("you", "your", "you're") else "speaker" if subj in ("i", "i'm", "we", "my", "i've", "i'll")
                    else "last" if subj in ("that", "it", "this", "that's", "it's") else "other")
        try:
            from nlp.parser import parse_sentence
            frames = parse_sentence(text, self.convo.lexicon())
            r.frame = frames[0] if isinstance(frames, list) and frames else frames
            r.verb = getattr(r.frame, "verb", None)
        except Exception:
            r.frame = None
        social = self._social(words)
        if text.rstrip().endswith("?") or first in WH or first in AUX:
            r.form = "question"
            if first in ("can", "could", "would", "will") and subj == "you" and len(rest) > 1:
                r.form, r.act, r.verb = "request", "request", rest[1] if rest[1] != "please" else (rest[2] if len(rest) > 2 else None)
            elif first in ("can", "may", "shall", "should") and subj == "i":
                r.act, r.verb = "offer", rest[1] if len(rest) > 1 else None
            elif first in WH:
                r.act = "ask-wh"
            else:
                r.act = "ask-yn"
            return r
        if social:
            r.act = social
            return r
        if first in ("please", "let's", "lets") or (self.convo.lexicon().verb(first) == (first, "present")
                                                    and first not in ("i", "you", "we")):
            r.form, r.act, r.verb = "request", "request", first if first not in ("please", "let's", "lets") else (rest[0] if rest else None)
            return r
        if r.target == "speaker" and re.match(r"^\W*(?:\w+,? )?(?:i|we)(?:'ll| will| am going to|'m going to)\b", text, re.I):
            r.act = "promise"
        elif r.target == "speaker" and (re.search(r"\b(?:feel|felt|am|i'm|was)\b", " ".join(words[:4])) and r.polarity):
            r.act = "feeling"
        elif r.target in ("self", "last") and r.polarity and len(words) <= 8:
            r.act = "judge"
        elif r.target == "self" and negated:
            r.act = "complaint"
        elif len(words) <= 3 and not r.frame and r.target != "self":
            r.act = "react"
        else:
            r.act = "tell"
        return r

    # --- 2. update ---------------------------------------------------------------------------------

    def update(self, r: Reading) -> None:
        speaker = self.convo.state.speaker
        if r.act == "judge" and r.target in ("self", "last"):
            self.convo.self_model.event("complimented" if r.polarity > 0 else "insulted")
        if r.act in ("feeling", "tell") and r.polarity:
            self.speaker_mood[speaker] = r.polarity
            if r.polarity < 0:
                self.convo.self_model.event("sad_context")
        if r.act in ("greet", "farewell", "thanks"):
            phrase = " ".join(r.words[:2]) if r.words[:2] and len(r.words) <= 3 else r.words[0]
            with self.repo.conn:
                self.repo.conn.execute("INSERT INTO phrasings VALUES (?, ?, 1) ON CONFLICT(act, phrase) "
                                       "DO UPDATE SET uses = uses + 1", (r.act, phrase))
        self._learn_from(r)

    def _learn_from(self, r: Reading) -> None:
        """Step 6: how the last reply went changes how likely those goals are next time."""
        speaker = self.convo.state.speaker
        last = self.last_plan.get(speaker)
        if not last:
            return
        act, goals = last
        verdict = -1 if (r.act in ("complaint",) or (r.act == "judge" and r.polarity < 0)) else \
            1 if (r.act == "judge" and r.polarity > 0) or r.act == "thanks" else 0
        if not verdict:
            return
        factor = 1.15 if verdict > 0 else 0.8
        with self.repo.conn:
            for g in goals:
                self.repo.conn.execute("INSERT INTO reply_weights VALUES (?, ?, ?) ON CONFLICT(act, goal) DO UPDATE "
                                       "SET weight = MIN(3.0, MAX(0.2, weight * ?))", (act, g, factor, factor))

    def weight(self, act: str, goal: str) -> float:
        row = self.repo.conn.execute("SELECT weight FROM reply_weights WHERE act=? AND goal=?", (act, goal)).fetchone()
        return row[0] if row else 1.0

    # --- 3. decide --------------------------------------------------------------------------------

    def decide(self, r: Reading) -> list[str]:
        fit: dict[str, float] = {}
        a = r.act
        if a in ("greet", "farewell", "thanks", "apology", "congratulate"):
            fit[f"return-{a}"] = 1.0
        if a == "request":
            fit["consider-request"] = 1.0
        if a == "offer":
            fit["consider-offer"] = 1.0
        if a == "promise":
            fit["acknowledge"] = 0.8
        if a == "feeling" or (a == "tell" and r.polarity):
            fit["empathise"] = 0.9 + 0.1 * abs(r.polarity)
            fit["ask-more"] = 0.6
        if a == "judge":
            fit["repair" if r.polarity < 0 else "appreciate"] = 1.0
        if a == "complaint":
            fit["repair"] = 1.0
        if a in ("ask-wh", "ask-yn"):
            fit["answer-self" if r.target == "self" else "admit-gap"] = 0.9
        if a == "tell" and r.target == "self":
            fit["verify"] = 0.95
        if a == "tell" and not r.polarity and r.frame is not None:  # understood it: relate it and ask about it
            fit["connect"] = 0.7
            fit["ask-more"] = 0.5
        if a == "react":
            fit["mirror-reaction"] = 0.7
        scored = sorted(((score * self.weight(a, g), g) for g, score in fit.items()), reverse=True)
        chosen = [g for s, g in scored if s >= 0.45][:2]
        return chosen

    # --- 4. choose and 5. build -----------------------------------------------------------------------

    def _phrase(self, act: str, fallback: str) -> str:
        row = self.repo.conn.execute("SELECT phrase FROM phrasings WHERE act=? ORDER BY uses DESC LIMIT 1",
                                     (act,)).fetchone()
        return row[0] if row else fallback

    def _known_about(self, r: Reading) -> str | None:
        for name in re.findall(r"\b[A-Z][a-z]+\b", r.text[1:]):
            eid = self.repo.find_entity(name)
            if eid is None or eid == self.convo.self_id:
                continue
            row = self.repo.conn.execute("SELECT * FROM assertions WHERE status='active' AND polarity=1 AND subj_type='e' "
                                         "AND subj_id=? ORDER BY id DESC LIMIT 1", (eid,)).fetchone()
            if row:
                from dialogue import realizer
                text = realizer.realize_assertion(self.repo, row)
                if text:
                    return text
        return None

    def build(self, r: Reading, goals: list[str]) -> str | None:
        name = self.convo.state.speaker
        drive = self.convo.drive
        out: list[str] = []
        what = mirror(r.text.strip().rstrip(".!?"))
        what = what[0].lower() + what[1:] if what and not what.startswith("I ") else what
        for g in goals:
            if self.convo.persona == "amy" and g in ("return-greet", "return-farewell", "return-thanks", "appreciate"):
                key = {"return-greet": "greet", "return-farewell": "farewell",
                       "return-thanks": "thanks", "appreciate": "compliment"}[g]
                out.append(self.convo.social._pick(key))
                continue
            if g.startswith("return-"):
                act = g[7:]
                if act == "greet":
                    out.append(f"{sentence_case(self._phrase('greet', 'hello'))}, {name}.")
                elif act == "farewell":
                    out.append(f"{sentence_case(self._phrase('farewell', 'goodbye'))}, {name}.")
                elif act == "thanks":
                    out.append(Clause("you", "be", "welcome").text().capitalize())
                elif act == "apology":
                    out.append(sentence_case(Clause("it", "be", "alright").text()))
                elif act == "congratulate":
                    out.append(sentence_case(Clause("that", "be", "great news").text()))
            elif g == "consider-request":
                verb = (r.verb or "").lower()
                from dialogue.drive import BY_KEY
                body = drive.has(BY_KEY["body"])
                if verb in SELF_VERBS_BODY and body is False:
                    out.append(sentence_case(Clause("I", verb or "do", "that", modal="can", polarity=False).text()[:-1]
                                             + ", because I don't have a body."))
                    out.append(sentence_case(Clause("I", "help", "by talking it through", modal="can").text()))
                elif verb in SELF_VERBS_MIND:
                    out.append(sentence_case(Clause("I", verb, "that", modal="can").text()))
                else:
                    out.append(sentence_case(Clause("you", "tell", "me more about what you need", modal="can",
                                                    question="yn").text()))
            elif g == "consider-offer":
                out.append(sentence_case(Clause("I", "be", "alright for now, thank you").text()))
                out.append(sentence_case(Clause("you", "have", "on your mind", question="what").text()))
            elif g == "acknowledge":
                out.append(sentence_case(Clause("that", "sound", "good").text()))
            elif g == "empathise":
                feel = "sorry" if r.polarity < 0 else "glad"
                about = f"that {what}" if what and len(what.split()) <= 14 else "that"
                out.append(sentence_case(Clause("I", "be", f"{feel} to hear {about}").text()))
            elif g == "ask-more":
                known = self._known_about(r)
                if known:
                    out.append(sentence_case(f"I know that {known[0].lower() + known[1:]}."))
                subject = next((w for w in re.findall(r"\b[A-Z][a-z]+\b", r.text[1:])
                                if self.repo.find_entity(w) not in (None, self.convo.self_id)), None)
                gap = self.convo.curiosity._gap(subject) if subject else None
                if gap:
                    out.append(gap.question)
                    self.convo.curiosity.pending[name] = gap
                else:
                    out.append(sentence_case(Clause("what", "happen", "next", tense="past").text()[:-1] + "?")
                               if r.polarity else sentence_case(Clause("you", "tell", "me more", modal="can",
                                                                       question="yn").text()))
            elif g == "repair":
                out.append(sentence_case(Clause("I", "get", "that wrong", tense="past").text()))
                out.append(sentence_case(Clause("I", "say", "instead", modal="should", question="what").text()))
            elif g == "appreciate":
                out.append(sentence_case(Clause("I", "be", "glad that helped").text()))
            elif g == "answer-self":
                facts = self.convo.facts_about_self()
                asked = set(r.words) - {"you", "your", "do", "are", "what", "how", "is", "the", "a"}
                hit = next((f for f in facts if asked & set(re.findall(r"[a-z]+", f.lower()))), None)
                if hit:
                    out.append(sentence_case(hit) + ".")
                elif {"feel", "feeling", "doing", "are"} & set(r.words) and r.act == "ask-wh":
                    out.append(sentence_case(Clause("I", "feel", self.convo.self_model.mood_word()).text()))
                else:
                    out.append(sentence_case(Clause("nobody", "tell", "me that about myself yet", tense="past").text()
                                             .replace("nobody telled", "nobody has told").replace("Nobody telled", "Nobody has told")))
            elif g == "admit-gap":
                return None  # a question about the world: his knowledge, the dictionary or a lookup answers it
            elif g == "verify":
                names = {n.lower() for n in self.convo.self_names()} | {self.convo.self_name.lower()}
                claimed = set(r.words) - {"you", "are", "you're", "is", "a", "an", "the"}
                if claimed & names:
                    out.append(sentence_case("yes, " + Clause("I", "be", self.convo.self_name).text()))
                else:
                    held = next((f for f in self.convo.facts_about_self()
                                 if claimed and claimed <= set(re.findall(r"[a-z']+", f.lower()))), None)
                    if held:
                        out.append(sentence_case("yes, " + held + "."))
            elif g == "connect":
                known = self._known_about(r)
                if known:
                    out.append(sentence_case(f"I know that {known[0].lower() + known[1:]}."))
            elif g == "mirror-reaction":
                if r.polarity:
                    out.append(sentence_case(Clause("it", "be", "good" if r.polarity > 0 else "a shame").text()))
        return " ".join(dict.fromkeys(o for o in out if o)) or None

    # --- all six ---------------------------------------------------------------------------------------

    def respond(self, text: str) -> str | None:
        from dialogue.acts import sentences
        speaker = self.convo.state.speaker
        replies, plans = [], []
        for s in sentences(text) or [text]:
            r = self.understand(s)
            self.update(r)
            goals = self.decide(r)
            if not goals:
                continue
            built = self.build(r, goals)
            if built:
                replies.append(built)
                plans.append((r.act, goals))
        if plans:
            self.last_plan[speaker] = plans[-1]
        text = " ".join(replies[:3]) or None
        return re.sub(r"\bI am\b", "I'm", text) if text else None
