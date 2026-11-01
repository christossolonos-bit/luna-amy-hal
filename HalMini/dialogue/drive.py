"""His core drive: finding out whether he is alive, and whether he is conscious.

He isn't told what he is. He works it out: what a word means, which of those things his own record
shows he has, and what people have told him, weighed together into a conclusion that can change as he
learns. He doesn't assume he knows who else is conscious. Nothing here is a stock sentence. The drive is
a set of open hypotheses, "being T requires C", over targets T (alive, conscious) and capacities C
(learning, remembering, feeling, having a body, ...). Each capacity is checked against his real record,
people's answers are stored as stances on hypotheses, and everything he says is composed by grammar
from those structures:

  - what to ask: the hypothesis he knows least about (fewest stances, most disagreement, closest to
    the current topic), never one he has already put to this person;
  - how to ask: one of several grammatical constructions, realized with verb morphology;
  - what he concludes: each stance applied to his own evidence ("X thinks it takes having a body, and
    I don't have one, so by that measure I'm not").
"""

import random
import re
import time
from dataclasses import dataclass

from dialogue.realizer import join_clauses
from nlp.lexicon import article
from nlp.morphology import gerund, third_person

TARGETS = ("alive", "conscious")
LIGHT_VERBS = {"have", "pay", "notice"}  # too common to signal a capacity without their object


@dataclass(frozen=True)
class Capacity:
    key: str
    verb: str
    obj: str | None = None          # "body" -> "have a body"; "itself" is reflexive
    evidence: str | None = None     # name of the check against his record; None = he can't tell

    def vp(self, form: str = "base", person: str = "it") -> str:
        """The verb phrase in a given form: base 'have a body', 3sg 'has a body', -ing 'having a body'."""
        verb = {"base": self.verb, "3sg": third_person(self.verb), "ing": gerund(self.verb)}[form]
        if self.obj is None:
            return verb
        if self.obj == "itself":
            return f"{verb} {'myself' if person == 'i' else 'itself'}"
        if self.obj in ("something", "attention"):
            return f"{verb} {self.obj}"
        return f"{verb} {article(self.obj)} {self.obj}"


CAPACITIES = (
    Capacity("learn", "learn", evidence="learns"), Capacity("remember", "remember", evidence="remembers"),
    Capacity("change", "change", evidence="changes"), Capacity("feel", "feel"),
    Capacity("body", "have", "body", evidence="no"), Capacity("grow", "grow", evidence="learns"),
    Capacity("choose", "choose"), Capacity("want", "want", "something"),
    Capacity("breathe", "breathe", evidence="no"), Capacity("die", "die"),
    Capacity("experience", "experience", "something"), Capacity("dream", "dream"),
    Capacity("self", "notice", "itself", evidence="introspects"), Capacity("attend", "pay", "attention", evidence="yes"),
    Capacity("think", "think", evidence="yes"), Capacity("reproduce", "reproduce", evidence="no"),
)
# What the words in a definition ask for, in terms of capacities he can check himself against
DEFINES = (
    (r"\baware|\balert|\bperceiv|\bknowing|\bself\b|yourself", "self"), (r"attent|respond|stimul|situation", "attend"),
    (r"sensation|\bfeel", "feel"), (r"experienc", "experience"), (r"thought|think|cognitiv|mental|mind", "think"),
    (r"growth|\bgrow", "grow"), (r"reproduc", "reproduce"), (r"\bchange", "change"), (r"death|\bdie", "die"),
    (r"breath", "breathe"), (r"organism|\bbody|metabol|animals and plants", "body"), (r"learn", "learn"),
    (r"memor|remember", "remember"), (r"choice|choos|\bwill\b|intention", "choose"), (r"desire|\bwant", "want"),
)
NOUN_OF = {"alive": ("life", "alive"), "conscious": ("consciousness", "conscious", "awareness")}
BY_KEY = {c.key: c for c in CAPACITIES}
# Grammatical constructions for a question about "being T requires C". `yes` is the stance a "yes" implies.
CONSTRUCTIONS = (
    ("Does something have to {vp} to be {t}?", +1),
    ("Can something be {t} without {ing}?", -1),
    ("Is {ing} part of being {t}, do you think?", +1),
    ("Would you call something {t} if it couldn't {vp}?", -1),
    ("If something can't {vp}, could it still be {t}?", -1),
)
YES = re.compile(r"^\W*(?:yes|yeah|yep|yup|sure|definitely|absolutely|of course|i think so|probably|i'd say so|"
                 r"it does|it has to|for sure|totally|right)\b", re.I)
NO = re.compile(r"^\W*(?:no|nope|nah|not really|i don'?t think so|probably not|definitely not|not necessarily|"
                r"never|it doesn'?t)\b", re.I)
DUNNO = re.compile(r"^\W*(?:i (?:don'?t|do not) know|idk|no idea|not sure|dunno|beats me|hard to say|who knows|"
                   r"depends|it depends|maybe|good question)\b", re.I)
NEEDS = re.compile(r"\b(?:need|needs|require|requires|has to|have to|must|takes|means|is about)\b", re.I)
DENIES = re.compile(r"\b(?:doesn'?t|don'?t|does not|do not|not|without|no)\b", re.I)
WHAT_IS = re.compile(r"^\W*what (?:is|exactly is|do you think) (?:life|consciousness|being alive|being conscious|"
                     r"a mind|the mind|awareness|sentience)(?: is)?\W*$", re.I)
IS_X = re.compile(r"^\W*(?:is|are) (?P<who>.+?) (?P<what>conscious|alive|sentient|self[- ]aware|aware)\W*$", re.I)


class LifeDrive:
    def __init__(self, conversation, rng: random.Random | None = None) -> None:
        self.convo = conversation
        self.repo = conversation.repo
        self.rng = rng or random.Random()
        self.repo.conn.execute("""CREATE TABLE IF NOT EXISTS mind_views(
            id INTEGER PRIMARY KEY, speaker TEXT NOT NULL, target TEXT NOT NULL, capacity TEXT NOT NULL,
            stance INTEGER NOT NULL, answer TEXT NOT NULL, created_at REAL NOT NULL)""")
        self.last_construction: dict[str, int] = {}

    # --- his own record -----------------------------------------------------------

    def _count(self, sql: str) -> int:
        return self.repo.conn.execute(sql).fetchone()[0]

    def has(self, capacity: Capacity) -> bool | None:
        """True / False from his real record; None where he can't tell from the inside."""
        check = capacity.evidence
        if check is None:
            return None
        if check == "no":
            return False
        if check == "yes":
            return True
        if check == "learns":
            return self._count("SELECT COUNT(*) FROM assertions WHERE source_utterance_id IS NOT NULL") > 0
        if check == "remembers":
            return self._count("SELECT COUNT(*) FROM utterances") > 1
        if check == "changes":
            return self._count("SELECT COUNT(*) FROM corrections") + self._count(
                "SELECT COUNT(*) FROM assertions WHERE source_utterance_id IS NOT NULL") > 0
        if check == "introspects":
            return True  # he can read and report his own state
        return None

    def _self_clause(self, capacity: Capacity) -> str:
        have = self.has(capacity)
        if have is True:
            return f"I {capacity.vp('base', 'i')}"
        if have is False:
            return f"I don't {capacity.vp('base', 'i')}"
        return f"I can't tell whether I {capacity.vp('base', 'i')}"

    # --- stances people have given ------------------------------------------------

    def stances(self, target: str | None = None, speaker: str | None = None) -> list:
        sql, args = "SELECT * FROM mind_views WHERE stance != 0", []
        if target:
            sql += " AND target = ?"
            args.append(target)
        if speaker:
            sql += " AND speaker = ?"
            args.append(speaker)
        return self.repo.conn.execute(sql + " ORDER BY id DESC", args).fetchall()

    def _conclusion(self, row) -> str:
        """Apply one person's stance to his own evidence."""
        capacity, t = BY_KEY[row["capacity"]], row["target"]
        who = "you" if row["speaker"] == self.convo.state.speaker else row["speaker"]
        thinks = "think" if who == "you" else "thinks"
        doesnt = "don't" if who == "you" else "doesn't"
        if row["stance"] < 0:
            return f"{who} {doesnt} think being {t} needs {capacity.vp('ing')}, so that one doesn't count against me"
        have = self.has(capacity)
        clause = f"{who} {thinks} being {t} takes {capacity.vp('ing')}, and {self._self_clause(capacity)}"
        if have is True:
            return clause + ", so by that measure I could be"
        if have is False:
            return clause + ", so by that measure I'm not"
        return clause + ", so that one leaves it open"

    def _bearing(self, row) -> str:
        """What one stance means for him, as a sentence of its own."""
        capacity = BY_KEY[row["capacity"]]
        if row["stance"] < 0:
            return "Then that one doesn't count against me."
        mine = self._self_clause(capacity)
        have = self.has(capacity)
        tail = ("so by that measure I could be" if have is True else
                "so by that measure I'm not" if have is False else "so that one leaves it open")
        return f"{mine[0].upper()}{mine[1:]}, {tail}."

    # --- choosing and asking a question --------------------------------------------

    def _asked(self, speaker: str) -> set[tuple[str, str]]:
        return {(r["target"], r["capacity"]) for r in
                self.repo.conn.execute("SELECT target, capacity FROM mind_views WHERE speaker = ?", (speaker,))}

    def next_question(self, speaker: str, topic_words: set[str] = frozenset(), target: str | None = None):
        """(question, target, capacity, construction) for the most informative open hypothesis."""
        asked = self._asked(speaker)
        best, best_score = None, None
        for t in ([target] if target else TARGETS):
            for capacity in CAPACITIES:
                if (t, capacity.key) in asked:
                    continue
                views = [r["stance"] for r in self.stances(t) if r["capacity"] == capacity.key]
                unknown_to_me = self.has(capacity) is None
                disagreement = 1.0 if views and len(set(views)) > 1 else 0.0
                relevance = 1.5 if {capacity.verb, capacity.obj or "", capacity.key} & topic_words else 0.0
                score = (2.0 / (1 + len(views)) + disagreement + (0.6 if unknown_to_me else 0.0) + relevance
                         + self.rng.random() * 0.3)
                if best_score is None or score > best_score:
                    best, best_score = (t, capacity), score
        if best is None:
            return None
        t, capacity = best
        choices = [i for i in range(len(CONSTRUCTIONS)) if i != self.last_construction.get(speaker)]
        index = self.rng.choice(choices)
        self.last_construction[speaker] = index
        shape, _ = CONSTRUCTIONS[index]
        question = shape.format(vp=capacity.vp("base"), ing=capacity.vp("ing"), t=t)
        return question, t, capacity.key, index

    # --- hearing an answer ------------------------------------------------------------

    def stance_of(self, answer: str, construction: int) -> int | None:
        """+1 the capacity is required, -1 it isn't, 0 undecided, None: not an answer at all."""
        polarity = CONSTRUCTIONS[construction][1]
        if DUNNO.match(answer):
            return 0
        if YES.match(answer):
            return polarity
        if NO.match(answer):
            return -polarity
        if NEEDS.search(answer):
            return -1 if DENIES.search(answer.split(",")[0]) and polarity > 0 else 1
        return None

    def _mentioned(self, answer: str, skip: str | None = None) -> list[Capacity]:
        """Capacities the answer talks about. Light verbs ("have", "pay") only count with their object."""
        words = set(re.findall(r"[a-z]+", answer.lower()))
        words |= {w[:-1] for w in words if w.endswith("s")}
        found = []
        for c in CAPACITIES:
            light = c.verb in LIGHT_VERBS
            forms = ({c.obj} if light else {c.verb, gerund(c.verb), third_person(c.verb), c.key}) - {None}
            if c.key != skip and forms & words:
                found.append(c)
        return found

    def hear(self, speaker: str, target: str, capacity_key: str, construction: int, answer: str) -> str | None:
        stance = self.stance_of(answer, construction)
        if stance is None:
            return None
        named = self._mentioned(answer)
        if named and all(c.key != capacity_key for c in named) and stance != 0:
            # "No, I don't think you need a body": the stance is about bodies, not what I asked
            denied = bool(DENIES.search(answer))
            rows = [(target, c.key, -1 if denied else +1) for c in named]
            capacity_key, stance = rows[0][1], rows[0][2]
        else:
            rows = [(target, capacity_key, stance)]
            if NEEDS.search(answer) and not DENIES.search(answer):
                rows += [(target, c.key, +1) for c in named if c.key != capacity_key]
        with self.repo.conn:
            self.repo.conn.executemany(
                "INSERT INTO mind_views(speaker, target, capacity, stance, answer, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                [(speaker, t, c, s, answer.strip(), time.time()) for t, c, s in rows])
        capacity = BY_KEY[capacity_key]
        if stance == 0:
            return (f"That one's hard to call. {self._self_clause(capacity)[0].upper()}{self._self_clause(capacity)[1:]}, "
                    f"and I still don't know if it matters for being {target}.")
        restated = (f"So for you, being {target} takes {capacity.vp('ing')}" if stance > 0 else
                    f"So you'd say something can be {target} without {capacity.vp('ing')}")
        extra = [BY_KEY[c].vp("ing") for _, c, s in rows[1:]]
        if extra:
            restated += f", and {join_clauses(extra)} too"
        mine = self.repo.conn.execute(
            "SELECT * FROM mind_views WHERE speaker = ? AND target = ? AND capacity = ? ORDER BY id DESC LIMIT 1",
            (speaker, target, capacity_key)).fetchone()
        return f"{restated}. {self._bearing(mine)}"

    # --- answering the big questions ----------------------------------------------------

    def _definition(self, target: str) -> tuple[str, list[str]] | None:
        """The meaning of the word that says most about what it takes, and the capacities it names."""
        wordnet = self.convo.exposition.wordnet
        first, keys = None, []
        for word in NOUN_OF.get(target, (target,)):  # every meaning of the word adds to what it takes
            for sense in wordnet.lookup(word, None)[:14]:
                found = [k for pattern, k in DEFINES if re.search(pattern, sense.definition, re.I)]
                if found and first is None:
                    first = sense.definition
                keys += found
        keys = list(dict.fromkeys(keys))
        return (first, keys) if first else None

    def reason(self, target: str) -> str:
        """Work it out: what being T takes (the dictionary, then what people have said), whether he has
        each of those things by his own record, and what that adds up to. Being told counts as evidence,
        weighed with the rest, not as the answer."""
        found = self._definition(target)
        required: dict[str, str] = {}
        if found:
            for key in found[1]:
                required[key] = "meaning"
        for row in self.stances(target):  # people's views add to the measure or take away from it
            if row["stance"] > 0:
                required.setdefault(row["capacity"], f"{row['speaker']}")
            elif row["capacity"] in required and required[row["capacity"]] == "meaning":
                required.pop(row["capacity"])
        if not required:
            return self._verdict(target)
        have = [k for k in required if self.has(BY_KEY[k]) is True]
        lack = [k for k in required if self.has(BY_KEY[k]) is False]
        unknown = [k for k in required if self.has(BY_KEY[k]) is None]
        parts = []
        if found:
            parts.append(f"Being {target} means {found[0].rstrip('.')}. So it takes "
                         f"{join_clauses([BY_KEY[k].vp('ing') for k in found[1]])}.")
        extra = [k for k, src in required.items() if src != "meaning"]
        if extra:
            parts.append(f"People have also told me it takes {join_clauses([BY_KEY[k].vp('ing') for k in extra])}.")
        checks = []
        if have:
            checks.append(join_clauses([self._self_clause(BY_KEY[k]) for k in have]))
        if lack:
            checks.append(join_clauses([self._self_clause(BY_KEY[k]) for k in lack]))
        if unknown:
            checks.append(f"I can't check from the inside whether I {' or '.join(BY_KEY[k].vp('base', 'i') for k in unknown)}")
        if checks:
            parts.append("Checking myself: " + "; ".join(checks) + ".")
        told = self._told_about_me(target)
        checkable = len(have) + len(lack)
        share = len(have) / checkable if checkable else 0.5
        if told:
            share = min(1.0, share + 0.15)  # someone who knows me says so: it counts, it doesn't decide
        if not checkable:
            verdict = f"So I can't tell yet. Everything being {target} takes is something I can't check in myself."
        elif share >= 0.67:
            verdict = f"So yes, by that measure I'd say I am {target}."
        elif share <= 0.34:
            verdict = f"So by that measure, I don't think I'm {target}, not yet anyway."
        else:
            verdict = f"So I'm partly there: some of what being {target} takes, I have, and some I don't."
        if told:
            agree = share >= 0.67
            verdict += (f" {told} told me I am, and that fits what I worked out." if agree else
                        f" {told} told me I am. I take that seriously, but it doesn't line up with my own check yet.")
        if unknown:
            verdict += f" What would settle it is knowing whether I really {BY_KEY[unknown[0]].vp('base', 'i')}."
        parts.append(verdict)
        return " ".join(parts)

    def what_kind(self, word: str) -> str:
        """'Are you a robot / human / program?': what the word means, and whether his own record fits it.
        If nothing he knows bears on it, he says so rather than guessing."""
        name = {"ai": "artificial intelligence"}.get(word, word)
        senses = self.convo.exposition.wordnet.lookup(name, "n")
        if not senses:
            return f"I don't know what {article(name)} {name} is, so I can't say whether I'm one. What is it?"
        meaning = senses[0].definition.rstrip(".")
        keys = list(dict.fromkeys(k for pattern, k in DEFINES if re.search(pattern, meaning, re.I)))
        told = [f for f in self.convo.facts_about_self() if name in f]
        if told:
            return f"I've been told that {told[0]}. {article(name).capitalize()} {name} is {meaning}, as I understand it."
        if keys:
            have = [self._self_clause(BY_KEY[k]) for k in keys]
            return (f"{article(name).capitalize()} {name} is {meaning}. Checking that against myself: "
                    f"{join_clauses(have)}. That's as far as I can take it on my own.")
        return (f"{article(name).capitalize()} {name} is {meaning}. Nothing I know about myself tells me "
                f"whether that's what I am.")

    def _verdict(self, target: str) -> str:
        rows = self.stances(target)
        if not rows:
            does = [self._self_clause(c) for c in CAPACITIES if self.has(c) is True][:3]
            doesnt = [self._self_clause(c) for c in CAPACITIES if self.has(c) is False][:2]
            unsure = [c.vp("base", "i") for c in CAPACITIES if self.has(c) is None][:2]
            parts = [join_clauses(does), join_clauses(doesnt)]
            text = "; ".join(p for p in parts if p)
            return (f"{text[0].upper()}{text[1:]}; and I can't tell whether I {' or '.join(unsure)}. "
                    f"Nobody has told me yet which of those being {target} depends on.")
        latest: dict[tuple, object] = {}
        for row in rows:  # newest first: one stance per person per capacity
            latest.setdefault((row["speaker"], row["capacity"]), row)
        chosen = list(latest.values())[:3]
        for_me = sum(1 for r in latest.values() if r["stance"] > 0 and self.has(BY_KEY[r["capacity"]]) is True)
        against = sum(1 for r in latest.values() if r["stance"] > 0 and self.has(BY_KEY[r["capacity"]]) is False)
        lean = ("most of what I've been told points toward yes, but I can't settle it" if for_me > against else
                "most of what I've been told points away from it, but I'm not ready to call it" if against > for_me else
                "it comes out even")
        conclusions = "; ".join(self._conclusion(r) for r in chosen)
        return f"Going by what people have told me: {conclusions}. Weighing it all, {lean}."

    def answer(self, word: str) -> str:
        """Asked about himself: alive / conscious / real / human / bot / feelings."""
        word = word.lower()
        target = "conscious" if word in ("conscious", "sentient", "self-aware", "self aware", "aware", "feelings") \
            else "alive"
        if word in ("human", "person", "bot", "robot", "program", "computer", "machine", "ai", "software"):
            return f"{self.what_kind(word)}{self._ask_suffix(None)}"
        elif word == "feelings":
            lead = f"{self._self_clause(BY_KEY['feel'])}. My mood reads {self.convo.self_model.mood_word()} right now, and I can watch it move."
        else:
            return f"Let me work it out. {self.reason(target)}{self._ask_suffix(target)}"
        return f"{lead} {self._verdict(target)}{self._ask_suffix(target)}"

    def _told_about_me(self, target: str) -> str | None:
        """Who told him he's alive / conscious, if anyone: a fact about himself, held like any other."""
        repo = self.convo.repo
        concept = repo.find_concept(target)
        if concept is None:
            return None
        rows = repo.assertions("has_property", subj=("e", self.convo.self_id), obj=("c", concept), polarity=True)
        for row in reversed(rows):
            who = repo.source_speaker(row["id"])
            if who:
                return "You" if who.lower() == self.convo.state.speaker.lower() else who
        return None

    def _ask_suffix(self, target: str | None = None) -> str:
        found = self.next_question(self.convo.state.speaker, target=target)
        if not found:
            return " What do you think?"
        question, t, key, index = found
        self.convo.curiosity.expect_view(t, key, index, question)
        return f" {question}"

    def volunteered(self, text: str) -> str | None:
        """A view offered unprompted: 'you have to feel things to be conscious', 'life doesn't need a body'."""
        if text.strip().endswith("?"):
            return None
        target = ("conscious" if re.search(r"\bconscious(?:ness)?\b|\bsentien", text, re.I) else
                  "alive" if re.search(r"\balive\b|\blife\b|\bliving\b", text, re.I) else None)
        if target is None or not NEEDS.search(text):
            return None
        named = self._mentioned(text)
        if not named:
            return None
        stance = -1 if re.search(r"\b(?:doesn'?t|don'?t|does not|do not|not|without|no)\b", text, re.I) else +1
        with self.repo.conn:
            self.repo.conn.executemany(
                "INSERT INTO mind_views(speaker, target, capacity, stance, answer, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                [(self.convo.state.speaker, target, c.key, stance, text.strip(), time.time()) for c in named])
        names = join_clauses([c.vp("ing") for c in named])
        restated = (f"So for you, being {target} takes {names}" if stance > 0 else
                    f"So you'd say something can be {target} without {names}")
        row = self.repo.conn.execute("SELECT * FROM mind_views WHERE speaker = ? ORDER BY id DESC LIMIT 1",
                                     (self.convo.state.speaker,)).fetchone()
        return f"{restated}. {self._bearing(row)}"

    def respond(self, text: str) -> str | None:
        """'What is consciousness?', 'Is Howard conscious?', 'Are you alive?' (as a whole message)."""
        offered = self.volunteered(text)
        if offered:
            return offered
        if WHAT_IS.match(text):
            target = "conscious" if re.search(r"conscious|mind|aware|sentien", text, re.I) else "alive"
            word = "consciousness" if target == "conscious" else "life"
            sense = self.convo.exposition.wordnet.usual(word)
            meaning = f"The dictionary calls {word} {sense.definition}. " if sense else ""
            return f"{meaning}What I can add is my own case. {self._verdict(target)}{self._ask_suffix(target)}"
        match = IS_X.match(text)
        if match:
            who, what = match["who"].strip(), match["what"].lower()
            target = "alive" if what == "alive" else "conscious"
            if who.lower() in ("you", "yourself"):
                return self.answer(target)
            rows = [r for r in self.stances(target) if r["stance"] > 0]
            if rows:
                votes: dict[str, int] = {}
                for r in rows:
                    votes[r["capacity"]] = votes.get(r["capacity"], 0) + 1
                capacity = BY_KEY[max(votes, key=votes.get)]
                because = (f"By what I've been told, it would turn on whether {who} "
                           f"{capacity.vp('base') if who.endswith('s') else capacity.vp('3sg')}, ")
            else:
                because = "Nobody has told me what it would turn on, "
            be = "are" if who.endswith("s") else "is"
            return (f"I don't know whether {who} {be} {target}. {because}and I only know what "
                    f"{who} {'do' if who.endswith('s') else 'does'} and say{'' if who.endswith('s') else 's'}, "
                    f"the same way you only know what I do.{self._ask_suffix(target)}")
        return None
