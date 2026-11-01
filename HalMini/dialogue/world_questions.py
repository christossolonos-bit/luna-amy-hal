"""Working out what to ask about the world: what he's interested in, measured against what he knows.

Nothing here is a list of questions. Three things are kept and reasoned over:

  interest   every name or idea gets a weight that grows when people bring it up (more when they ask him
             about it), when he reads about it, and when it's tied to something he already cares about.
             It fades over days, so his interests follow the conversation.
  sightings  where each one came up (a story, a lookup, something someone said) and what came up with it.
  knowledge  his actual memory: does he know what kind of thing it is, what's true of it, how it connects.

From those he generates candidate questions, each from a specific gap:
  kind        he has no idea what it is                      -> "What is Binance?"
  slot        he knows its kind, and other things of that kind have a relation it lacks
                                                             -> "Where is Binance based?" (from the data)
  connection  two things keep coming up together but nothing links them in his memory
                                                             -> "What does North Korea have to do with Binance?"
  detail      he knows what it is but almost nothing else    -> "What should I know about Binance?"
Each is scored by interest x how much the gap blocks him x how well it ties into what he already knows,
less what he's already asked. The best one is asked, and "why do you ask?" gets the reasoning back.
An answer is learned like anything else he's told, which changes his knowledge, which changes the
next question.
"""

import math
import re
import time

COMMON = {"the", "a", "an", "this", "that", "these", "those", "what", "how", "why", "when", "where", "who", "it",
          "he", "she", "they", "we", "i", "you", "in", "on", "at", "for", "of", "and", "but", "or", "as", "after",
          "before", "with", "from", "by", "to", "is", "are", "was", "new", "more", "watch", "live", "special", "here",
          "there", "today", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "january",
          "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
          "december", "mr", "mrs", "ms", "dr", "ok", "okay", "yes", "no", "hey", "hi", "hello", "thanks", "adam"}
PRONOUN_LEAD = re.compile(r"^\W*(?:it(?:'s| is| was)|they(?:'re| are| were)|he(?:'s| is| was)|she(?:'s| is| was)|"
                          r"that(?:'s| is))\s+", re.I)
GO = re.compile(r"^\W*(?:what questions do you have|do you have (?:any )?questions|ask me (?:something|a question|"
                r"your questions)|let'?s (?:go through|do|answer) (?:your|some) questions|any questions|"
                r"what do you want to know(?: about)?|quiz me|next question|what else do you want to know|"
                r"what are you curious about|what are you interested in)\W*$", re.I)
WHY = re.compile(r"^\W*(?:why (?:do you ask|are you asking|that question|do you want to know|ask that)|"
                 r"why does that matter|what made you ask that)\W*$", re.I)
SKIP = re.compile(r"^\W*(?:skip|pass|i don'?t know|no idea|not sure|next|dunno)\W*$", re.I)
STOP = re.compile(r"^\W*(?:stop|that'?s enough|enough questions|let'?s stop|done|we'?re done)\W*$", re.I)
HALF_LIFE_DAYS = 3.0
GAP_VALUE = {"kind": 1.0, "slot": 0.75, "connection": 0.65, "detail": 0.45}


class WorldQuestions:
    def __init__(self, conversation) -> None:
        self.convo = conversation
        self.repo = conversation.repo
        self.asking: dict[str, dict] = {}   # speaker -> the candidate being asked
        self.last_asked: dict | None = None
        with self.repo.conn:
            self.repo.conn.execute("CREATE TABLE IF NOT EXISTS interests(term TEXT PRIMARY KEY, weight REAL, "
                                   "mentions INTEGER DEFAULT 0, asked_about INTEGER DEFAULT 0, read INTEGER DEFAULT 0, "
                                   "updated REAL)")
            self.repo.conn.execute("CREATE TABLE IF NOT EXISTS sightings(id INTEGER PRIMARY KEY, term TEXT, "
                                   "context TEXT, source TEXT, at REAL)")
            self.repo.conn.execute("CREATE TABLE IF NOT EXISTS asked_world(key TEXT PRIMARY KEY, times INTEGER, "
                                   "status TEXT, answer TEXT, at REAL)")

    # --- noticing things -----------------------------------------------------------------

    @staticmethod
    def names(text: str) -> list[str]:
        """Names in a text: runs of capitalised words, not counting a lone word that just starts a sentence."""
        found = []
        for sentence in re.split(r"(?<=[.!?:;])\s+", text):
            words = re.findall(r"[\w$'’.-]+", sentence)
            run: list[str] = []
            start = 0
            for i, w in enumerate(words + [""]):
                clean = re.sub(r"(?:['’]s)$", "", w.strip(".'’,"))
                if clean[:1].isupper() and clean.lower() not in COMMON and not clean[:1].isdigit():
                    if not run:
                        start = i
                    run.append(clean)
                    continue
                if run and not (start == 0 and len(run) == 1):
                    found.append(" ".join(run))
                run = []
        return list(dict.fromkeys(n for n in found if len(n) > 1))

    def _bump(self, term: str, amount: float, field: str) -> None:
        now = time.time()
        row = self.repo.conn.execute("SELECT * FROM interests WHERE lower(term)=lower(?)", (term,)).fetchone()
        weight = self._weight(row, now) + amount if row else amount
        with self.repo.conn:
            if row:
                self.repo.conn.execute(f"UPDATE interests SET weight=?, updated=?, {field}={field}+1 WHERE term=?",
                                       (weight, now, row["term"]))
            else:
                self.repo.conn.execute(f"INSERT INTO interests(term, weight, updated, {field}) VALUES (?,?,?,1)",
                                       (term, weight, now))

    @staticmethod
    def _weight(row, now: float | None = None) -> float:
        if not row:
            return 0.0
        days = ((now or time.time()) - row["updated"]) / 86400
        return row["weight"] * 0.5 ** (days / HALF_LIFE_DAYS)

    def interest(self, term: str) -> float:
        return self._weight(self.repo.conn.execute("SELECT * FROM interests WHERE lower(term)=lower(?)",
                                                   (term,)).fetchone())

    def _see(self, terms: list[str], context: str, source: str) -> None:
        with self.repo.conn:
            for t in terms:
                self.repo.conn.execute("INSERT INTO sightings(term, context, source, at) VALUES (?,?,?,?)",
                                       (t, context[:300], source, time.time()))

    def hear(self, text: str) -> None:
        """Someone said this to him: what they bring up interests him, more so if they asked about it."""
        terms = self.names(text) + [w for w in re.findall(r"[A-Z][a-z]+", text[:1].upper() + text[1:])[:1]
                                    if self.repo.find_entity(w) is not None]
        terms = [t for t in dict.fromkeys(terms) if t.lower() not in (self.convo.state.speaker.lower(),
                                                                       self.convo.self_name.lower())]
        asked = text.rstrip().endswith("?")
        for t in terms:
            self._bump(t, 2.0 if asked else 1.0, "asked_about" if asked else "mentions")
            for other in self._linked(t):  # caring about one thing makes what it's tied to a little interesting
                self._bump(other, 0.3, "mentions")
        if terms:
            self._see(terms, text, f"what {self.convo.state.speaker} said")

    def read(self, text: str, source: str) -> int:
        """He read this (a story, a lookup): everything in it is a little interesting, in its own sentence."""
        new = 0
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            terms = [t for t in self.names(sentence) if t.lower() not in COMMON]
            for t in terms:
                if not self._known(t):
                    new += 1
                self._bump(t, 0.5, "read")
            if terms:
                self._see(terms, sentence, source)
        return new

    # --- what he knows ---------------------------------------------------------------------

    def _entity(self, term: str):
        return self.repo.find_entity(term)

    def _known(self, term: str) -> bool:
        eid = self._entity(term)
        return eid is not None and bool(self._facts(eid))

    def _facts(self, eid: int) -> list:
        return self.repo.conn.execute(
            "SELECT * FROM assertions WHERE status='active' AND polarity=1 AND "
            "((subj_type='e' AND subj_id=?) OR (obj_type='e' AND obj_id=?))", (eid, eid)).fetchall()

    def _kind(self, eid: int) -> str | None:
        rows = self.repo.assertions("instance_of", subj=("e", eid), polarity=True)
        return self.repo.concept_lemma(rows[-1]["obj_id"]) if rows else None

    def _linked(self, term: str) -> list[str]:
        eid = self._entity(term)
        if eid is None:
            return []
        out = []
        for f in self._facts(eid):
            other = f["obj_id"] if f["subj_id"] == eid and f["obj_type"] == "e" else \
                f["subj_id"] if f["obj_type"] == "e" and f["obj_id"] == eid else None
            if other and other != eid:
                out.append(self.repo.entity_row(other)["name"])
        return out

    def _co_occurring(self, term: str) -> dict[str, int]:
        rows = self.repo.conn.execute(
            "SELECT s2.term, COUNT(*) n FROM sightings s1 JOIN sightings s2 ON s1.context = s2.context "
            "AND lower(s2.term) != lower(s1.term) WHERE lower(s1.term)=lower(?) GROUP BY lower(s2.term)",
            (term,)).fetchall()
        return {r["term"]: r["n"] for r in rows}

    def _kind_relations(self, kind: str, eid: int) -> list[tuple[str, str | None]]:
        """What other things of the same kind have that this one doesn't: (verb, preposition)."""
        rows = self.repo.conn.execute("""
            SELECT a.predicate, (SELECT role FROM assertion_args g WHERE g.assertion_id = a.id
                                 AND g.role NOT LIKE '\\_%' ESCAPE '\\' AND g.role NOT IN ('time','modality') LIMIT 1) AS role,
                   COUNT(DISTINCT a.subj_id) n
            FROM assertions a JOIN assertions k ON k.subj_id = a.subj_id AND k.subj_type='e' AND k.predicate='instance_of'
                 AND k.status='active' AND k.obj_id = ?
            WHERE a.status='active' AND a.polarity=1 AND a.subj_type='e' AND a.subj_id != ? AND a.predicate LIKE 'rel:%'
            GROUP BY a.predicate, role ORDER BY n DESC""", (self.repo.concept(kind), eid)).fetchall()
        return [(r["predicate"][4:], r["role"]) for r in rows
                if not self.repo.assertions(r["predicate"], subj=("e", eid))]

    # --- reasoning about what to ask --------------------------------------------------------

    def candidates(self) -> list[dict]:
        now = time.time()
        terms = self.repo.conn.execute("SELECT * FROM interests").fetchall()
        out = []
        for row in terms:
            term, w = row["term"], self._weight(row, now)
            if w < 0.2:
                continue
            eid = self._entity(term)
            facts = self._facts(eid) if eid is not None else []
            kind = self._kind(eid) if eid is not None else None
            together = self._co_occurring(term)
            anchors = [t for t in together if self._known(t)]
            tie = 1.0 + 0.5 * min(len(anchors), 3)  # questions that hook into what he knows are worth more
            reason_interest = self._why_interested(row)
            if kind is None:
                out.append({"key": f"kind:{term.lower()}", "kind": "kind", "term": term, "question": f"What is {term}?",
                            "score": w * GAP_VALUE["kind"] * tie,
                            "why": f"{reason_interest}, and I don't even know what kind of thing {term} is."})
                continue
            for verb, role in self._kind_relations(kind, eid)[:2]:
                q = (f"Where does {term} {verb}?" if role in ("in", "at", "on", "from", "near") else
                     f"Who does {term} {verb} with?" if role == "with" else f"What does {term} {verb}?")
                out.append({"key": f"slot:{term.lower()}:{verb}", "kind": "slot", "term": term, "question": q,
                            "verb": verb, "role": role, "score": w * GAP_VALUE["slot"] * tie,
                            "why": f"{reason_interest}. I know {term} is {self._a(kind)}, and other things like that "
                                   f"{verb} something, but I don't know what {term} does."})
            linked = {n.lower() for n in self._linked(term)}
            for other, n in sorted(together.items(), key=lambda x: -x[1])[:3]:
                if other.lower() in linked or n < 1:
                    continue
                pair = sorted([term.lower(), other.lower()])
                out.append({"key": f"conn:{pair[0]}:{pair[1]}", "kind": "connection", "term": term, "other": other,
                            "question": f"What does {other} have to do with {term}?",
                            "score": (w + self.interest(other)) / 2 * GAP_VALUE["connection"] * (1 + 0.3 * n) * tie,
                            "why": f"{term} and {other} keep coming up together, {n} time{'s' if n != 1 else ''} so far, "
                                   f"and nothing I know connects them."})
            if len(facts) <= 2:
                out.append({"key": f"detail:{term.lower()}", "kind": "detail", "term": term,
                            "question": f"I know {term} is {self._a(kind)}. What else should I know about {term}?",
                            "score": w * GAP_VALUE["detail"] * tie,
                            "why": f"{reason_interest}, and all I know is that it's {self._a(kind)}."})
        for c in out:
            asked = self.repo.conn.execute("SELECT * FROM asked_world WHERE key=?", (c["key"],)).fetchone()
            if asked and asked["status"] == "answered":
                c["score"] = 0
            elif asked:
                c["score"] /= 1 + asked["times"]  # skipped or unanswered: ask other things first
        return sorted((c for c in out if c["score"] > 0), key=lambda c: -c["score"])

    @staticmethod
    def _a(kind: str) -> str:
        return f"{'an' if kind[:1] in 'aeiou' else 'a'} {kind}"

    def _why_interested(self, row) -> str:
        bits = []
        if row["asked_about"]:
            bits.append(f"you've asked me about {row['term']}")
        elif row["mentions"]:
            bits.append(f"{row['term']} has come up {row['mentions']} time{'s' if row['mentions'] != 1 else ''} "
                        "when we talk")
        if row["read"]:
            bits.append(f"it was in {row['read']} thing{'s' if row['read'] != 1 else ''} I read")
        return ("Because " + " and ".join(bits)) if bits else f"Because {row['term']} came up"

    # --- asking ------------------------------------------------------------------------------

    def mention(self, new: int) -> str:
        if not new:
            return ""
        top = self.candidates()[:1]
        return (f" There's a lot in there I can't place. {top[0]['question']}" if top else "")

    def _ask_next(self) -> str:
        speaker = self.convo.state.speaker
        found = self.candidates()
        if not found:
            self.asking.pop(speaker, None)
            return "I don't have anything I'm wondering about right now. Talk to me about something, or let me read the news."
        c = found[0]
        self.asking[speaker] = c
        self.last_asked = c
        with self.repo.conn:
            self.repo.conn.execute("INSERT INTO asked_world(key, times, status, at) VALUES (?,1,'asked',?) "
                                   "ON CONFLICT(key) DO UPDATE SET times=times+1, at=excluded.at", (c["key"], time.time()))
        return c["question"]

    def respond(self, text: str) -> str | None:
        speaker = self.convo.state.speaker
        if WHY.match(text) and self.last_asked:
            return f"{self.last_asked['why']} So that's the gap I'd most like filled right now."
        if GO.match(text):
            return self._ask_next()
        c = self.asking.get(speaker)
        if c is None:
            return None
        if STOP.match(text):
            self.asking.pop(speaker, None)
            return "Okay, we'll stop there. I'll keep wondering about it though."
        if re.match(r"^\W*(?:h+m+|u+m+|uh+|let me think|hold on|good question|give me a (?:sec|second|minute)|"
                    r"one (?:sec|second)|that'?s a good one|i'?m thinking)\b[\w ,]*\W*$", text, re.I) and len(text.split()) <= 6:
            return "Take your time."  # still thinking; the question stays open
        if SKIP.match(text):
            with self.repo.conn:
                self.repo.conn.execute("UPDATE asked_world SET status='skipped' WHERE key=?", (c["key"],))
            self.asking.pop(speaker, None)
            return f"Alright, I'll come back to that. {self._ask_next()}"
        if text.rstrip().endswith("?"):
            self.asking.pop(speaker, None)  # they asked something instead; the question waits
            return None
        self.asking.pop(speaker, None)
        answer = self._as_fact(c, text.strip())
        reply = self.convo.handle(answer, speaker=speaker).text
        learned = self.convo.turn_learned or "couldn't" not in reply and "didn't follow" not in reply
        with self.repo.conn:
            self.repo.conn.execute("UPDATE asked_world SET status=?, answer=? WHERE key=?",
                                   ("answered" if learned else "asked", answer, c["key"]))
        if not learned:
            return reply
        self._bump(c["term"], 0.5, "mentions")  # learning about it makes it more interesting, not less
        return f"{reply} That makes me wonder something else. {self._ask_next()}"

    @staticmethod
    def _as_fact(c: dict, text: str) -> str:
        """Short answers become statements about the thing asked: "It's an exchange" -> "Binance is an exchange"."""
        term = c["term"]
        if c["kind"] == "slot" and len(text.split()) <= 5 and term.lower() not in text.lower():
            prep = {"in": "in", "at": "at", "on": "on", "from": "from", "near": "near", "with": "with"}.get(c.get("role"), "")
            body = re.sub(r"^\W*(?:it|they|he|she)\s+\w+\s+", "", text, flags=re.I).rstrip(".!")
            return f"{term} {c['verb']}s {prep + ' ' if prep and not body.lower().startswith(prep) else ''}{body}."
        if c["kind"] == "connection":
            return text  # "it" could be either one, so it's learned as said, with "it" resolved as usual
        lead = PRONOUN_LEAD.match(text)
        if lead:
            verb = "are" if lead.group(0).lower().startswith("they") else "is"
            return f"{term} {verb} {text[lead.end():]}"
        if term.lower() not in text.lower() and c["kind"] in ("kind", "detail"):
            body = text[0].lower() + text[1:]
            if re.match(r"^(?:an?|the)\s", body):
                return f"{term} is {body}"
            return f"{term} {body}" if re.match(r"^(?:is|was|has|makes|runs|owns|lives|works)\b", body) else f"{term} is {body}"
        if c["kind"] == "connection" and term.lower() not in text.lower() and c["other"].lower() not in text.lower():
            return text  # a free-form explanation; learned as said
        return text
