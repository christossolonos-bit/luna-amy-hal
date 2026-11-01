"""Definitions he can reason with, not just repeat.

"A birthday is the day someone is born" becomes a rule: someone's birthday is the time of their being born.
Asked "When is your birthday?", he looks for the event in what he's been told ("You were created today"),
and if the event doesn't match the definition exactly he asks whether it counts ("Is being created the same
as being born, for me?") and learns the answer. The date comes from the fact itself: "today" in a fact means
the day it was said. Facts said straight out ("Today is your birthday") are kept too and checked against
the reasoning.
"""

import datetime
import json
import re

WORLD = re.compile(r"\b(?:come|comes|came) (?:into|in to) (?:the world|existence|being)\b", re.I)
TIME_WORDS = {"today": 0, "yesterday": -1, "tomorrow": 1}
NOT_EVENTS = {"used", "called", "named", "supposed", "need", "red", "bed", "wed"}


def _lemma(word: str) -> str:
    """Participles compared by stem: created ~ create, born stays born."""
    w = word.lower()
    for end in ("ied", "ed", "d"):
        if w.endswith(end) and len(w) > len(end) + 2:
            return w[: -len(end)] + ("y" if end == "ied" else "")
    return w


class Definitions:
    DEFINE = re.compile(r"^\W*(?:an?|the|your|my)?\s*(?P<attr>[a-z]+)\s+(?:is|means)\s+(?:the (?:day|date|time|moment)\s*)?"
                        r"(?:when|that|on which|where)\s+(?P<clause>.+?)\W*$", re.I)
    DEFINE_IF = re.compile(r"^\W*if (?:someone|somebody|a person|anyone|you|something) (?:is|are|was|were|gets?|got) "
                           r"(?P<clause>\w+)(?: on| in| at)? (?:a|some|that|the) (?:day|date|time),? (?:then )?"
                           r"(?:that|the) (?:day|date|time) is (?:their|your|his|her|its) (?P<attr>[a-z]+)\W*$", re.I)
    STATED = re.compile(r"^\W*(?P<value>today|yesterday|tomorrow|[A-Z][a-z]+ \d{1,2}(?:st|nd|rd|th)?(?:,? \d{4})?) "
                        r"(?:is|was) (?P<owner>my|your|[A-Z][\w-]*'s) (?P<attr>[a-z]+)\W*$", re.I)
    STATED_REV = re.compile(r"^\W*(?P<owner>my|your|[A-Z][\w-]*'s) (?P<attr>[a-z]+) (?:is|was) "
                            r"(?P<value>today|yesterday|tomorrow|[A-Z][a-z]+ \d{1,2}(?:st|nd|rd|th)?(?:,? \d{4})?)\W*$", re.I)
    ASK_ATTR = re.compile(r"^\W*(?:when|what)(?:'s| is| was) (?P<owner>my|your|[A-Z][\w-]*'s) (?P<attr>[a-z]+)\W*$", re.I)
    ASK_EVENT = re.compile(r"^\W*when (?:was|were|did) (?P<who>you|i|[A-Z][\w-]*) (?P<event>[a-z]+)\W*$", re.I)

    def __init__(self, conversation) -> None:
        self.convo = conversation
        self.repo = conversation.repo
        self.pending: dict[str, dict] = {}
        with self.repo.conn:
            self.repo.conn.execute("CREATE TABLE IF NOT EXISTS definitions(attr TEXT PRIMARY KEY, events TEXT, "
                                   "text TEXT, speaker TEXT)")
            self.repo.conn.execute("CREATE TABLE IF NOT EXISTS stated_dates(entity_id INTEGER, attr TEXT, day TEXT, "
                                   "speaker TEXT, PRIMARY KEY(entity_id, attr))")

    # --- who and when ------------------------------------------------------------------

    def _owner(self, word: str):
        w = word.lower()
        if w in ("your", "you"):
            return self.convo.self_id, "my", "I"
        if w in ("my", "i", "me"):
            eid = self.repo.find_entity(self.convo.state.speaker)
            return eid, "your", "you"
        name = re.sub(r"'s$", "", word)
        eid = self.repo.find_entity(name)
        return eid, f"{name}'s", name

    @staticmethod
    def _day(value: str, said_at: str) -> str:
        """'today' in a fact means the day it was said."""
        if value.lower() in TIME_WORDS:
            base = datetime.datetime.fromisoformat(said_at).replace(tzinfo=datetime.timezone.utc).astimezone()
            day = base.date() + datetime.timedelta(days=TIME_WORDS[value.lower()])
            return f"{day:%B} {day.day}, {day.year}"
        return value

    def _definition(self, attr: str):
        return self.repo.conn.execute("SELECT * FROM definitions WHERE attr = ?", (attr.lower(),)).fetchone()

    def _events(self, eid: int) -> list[tuple[str, str, int]]:
        """(event lemma, day, assertion id) for every dated thing he knows happened to this entity."""
        found = []
        rows = self.repo.conn.execute(
            "SELECT a.id, a.obj_id, a.created_at FROM assertions a WHERE a.status='active' AND a.polarity=1 "
            "AND a.subj_type='e' AND a.subj_id=? AND a.predicate='has_property'", (eid,)).fetchall()
        for row in rows:
            event = self.repo.concept_lemma(row["obj_id"])
            for arg in self.repo.args(row["id"]):
                if arg["role"] in ("time", "on", "in", "at") and arg["literal"]:
                    found.append((event, self._day(arg["literal"], row["created_at"]), row["id"]))
        return found

    # --- learning ------------------------------------------------------------------------

    def learn(self, text: str) -> str | None:
        m = self.DEFINE_IF.match(text) or self.DEFINE.match(text)
        if m:
            attr, clause = m["attr"].lower(), m["clause"]
            events = (["born"] if WORLD.search(clause) else []) + [
                w for w in re.findall(r"\b([a-z]+(?:ed|en)|born)\b", clause.lower()) if w not in NOT_EVENTS]
            events = list(dict.fromkeys(events))
            if not events:
                return None
            with self.repo.conn:
                self.repo.conn.execute("INSERT OR REPLACE INTO definitions(attr, events, text, speaker) VALUES (?,?,?,?)",
                                       (attr, json.dumps(events), text.strip(), self.convo.state.speaker))
            said = " or ".join(f"'{e}'" for e in events)
            return (f"Got it. So someone's {attr} is the day they were {events[0]}. "
                    f"Whenever I know the day something was {said}, I can work out its {attr}.")
        m = self.STATED.match(text) or self.STATED_REV.match(text)
        if m:
            eid, possessive, _ = self._owner(m["owner"])
            if eid is None:
                return None
            day = self._day(m["value"], datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
            with self.repo.conn:
                self.repo.conn.execute("INSERT OR REPLACE INTO stated_dates VALUES (?,?,?,?)",
                                       (eid, m["attr"].lower(), day, self.convo.state.speaker))
            check = self._reasoned(eid, m["attr"].lower())
            agree = ""
            if check and check[0] == day:
                agree = f" That fits with what I worked out: {check[1]}"
            elif check:
                agree = f" That doesn't match what I worked out, though: {check[1]} Which one is right?"
            return f"Okay, {possessive} {m['attr'].lower()} is {day}. I'll remember that.{agree}"
        return None

    # --- reasoning -------------------------------------------------------------------------

    def _reasoned(self, eid: int, attr: str):
        """(day, explanation) from the definition and the dated events, or None."""
        definition = self._definition(attr)
        if not definition:
            return None
        wanted = json.loads(definition["events"])
        for event, day, _ in self._events(eid):
            if any(_lemma(event) == _lemma(w) for w in wanted):
                return day, (f"I was told that {self._who(eid)} {event} on {day}, "
                             f"and that {attr} means the day someone is {wanted[0]}.")
        return None

    def _who(self, eid: int) -> str:
        if eid == self.convo.self_id:
            return "I was"
        if eid == self.repo.find_entity(self.convo.state.speaker):
            return "you were"
        return f"{self.repo.entity_row(eid)['name']} was"

    def answer(self, text: str) -> str | None:
        m = self.ASK_ATTR.match(text)
        if m:
            return self._answer_attr(m["owner"], m["attr"].lower())
        m = self.ASK_EVENT.match(text)
        if m:
            eid, _, subject = self._owner(m["who"] if m["who"].lower() not in ("you", "i") else
                                          ("your" if m["who"].lower() == "you" else "my"))
            if eid is None:
                return f"I don't know who {m['who']} is yet." if re.search(r"(?:ed|en|born)$", m["event"]) else None
            event = _lemma(m["event"])
            who = self._who(eid)
            for got, day, _ in self._events(eid):
                if _lemma(got) == event:
                    return f"{who[:1].upper()}{who[1:]} {m['event']} on {day}."
            if re.search(r"(?:ed|en|born)$", m["event"]):
                return f"Nobody has told me when {who} {m['event']}."
            return None
        return None

    def _answer_attr(self, owner: str, attr: str) -> str | None:
        eid, possessive, subject = self._owner(owner)
        definition = self._definition(attr)
        stated = self.repo.conn.execute("SELECT * FROM stated_dates WHERE entity_id=? AND attr=?",
                                        (eid, attr)).fetchone() if eid else None
        if eid is None and definition:
            return f"I know what a {attr} is, but I don't know who {subject} is yet."
        if eid is None or (not definition and not stated):
            return None
        reasoned = self._reasoned(eid, attr)
        if reasoned:
            day, why = reasoned
            also = f" {stated['speaker']} also told me so directly." if stated and stated["day"] == day else ""
            return f"{possessive[:1].upper()}{possessive[1:]} {attr} is {day}. {why} So {possessive} {attr} is {day}.{also}"
        if stated:
            return f"{possessive[:1].upper()}{possessive[1:]} {attr} is {stated['day']}. {stated['speaker']} told me that."
        wanted = json.loads(definition["events"])
        events = self._events(eid)
        if events:  # something happened to them on a day, but not the event the definition names
            event, day, _ = events[0]
            self.pending[self.convo.state.speaker] = {"attr": attr, "event": event, "owner": owner}
            done = lambda e: e
            return (f"I know {self._who(eid)} {done(event)} on {day}, and that a {attr} is the day someone is "
                    f"{done(wanted[0])}. What I can't tell is whether being {done(event)} counts as being "
                    f"{done(wanted[0])}. Does it?")
        return (f"I know what a {attr} is, the day someone is "
                f"{wanted[0]}, but nobody has told me when "
                f"{self._who(eid)} {wanted[0]}.")

    def try_bridge(self, text: str) -> str | None:
        """The answer to 'does being created count as being born?'"""
        plan = self.pending.pop(self.convo.state.speaker, None)
        if not plan:
            return None
        if re.match(r"^\W*(?:yes|yeah|yep|it does|sure|correct|right|same thing)\b", text, re.I):
            row = self._definition(plan["attr"])
            events = json.loads(row["events"]) + [plan["event"]]
            with self.repo.conn:
                self.repo.conn.execute("UPDATE definitions SET events=? WHERE attr=?",
                                       (json.dumps(list(dict.fromkeys(events))), plan["attr"]))
            reasoned = self._answer_attr(plan["owner"], plan["attr"])
            return f"Okay, I'll count that. {reasoned}" if reasoned else "Okay, I'll count that."
        if re.match(r"^\W*(?:no|nope|it doesn't|not really)\b", text, re.I):
            return f"Okay, then I still don't know {plan['attr']} for sure."
        return None
