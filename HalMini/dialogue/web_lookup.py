"""Looking things up: when he doesn't know a word or a name, he reads the Wikipedia summary, keeps the gist,
and uses it the next time it comes up. Stdlib only; set HALMINI_WEB=0 to keep him offline."""

import json
import os
import re
import sys
import urllib.parse
import urllib.request

API = "https://en.wikipedia.org"
AGENT = "HalMini/1.0 (local conversational bot)"
TIMEOUT = 5

ASK = re.compile(r"^\W*(?:(?:can you |could you |please |go )*(?:look up|search (?:the )?(?:internet|web|online) for|google|find out about|"
                 r"research)|what can you find (?:out )?about)\s+(?P<term>.+?)\W*$", re.I)


class WebLookup:
    def __init__(self, repo) -> None:
        self.repo = repo
        with repo.conn:
            repo.conn.execute("CREATE TABLE IF NOT EXISTS web_notes(term TEXT PRIMARY KEY, title TEXT, summary TEXT, "
                              "url TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")

    @property
    def enabled(self) -> bool:
        return os.environ.get("HALMINI_WEB", "1") != "0" and "pytest" not in sys.modules

    @staticmethod
    def _key(term: str) -> str:
        return re.sub(r"^(?:an?|the)\s+", "", term.strip().lower()).strip(" ?.!'\"")

    def note(self, term: str):
        return self.repo.conn.execute("SELECT * FROM web_notes WHERE term = ?", (self._key(term),)).fetchone()

    def _get(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": AGENT})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))

    def fetch(self, term: str):
        """The note for a term, from memory or else from the web. None when nothing usable turns up."""
        found = self.note(term)
        if found or not self.enabled:
            return found
        key = self._key(term)
        try:
            hits = self._get(f"{API}/w/api.php?action=opensearch&limit=1&format=json&search="
                             + urllib.parse.quote(key))
            if not hits[1]:
                return None
            page = self._get(f"{API}/api/rest_v1/page/summary/" + urllib.parse.quote(hits[1][0].replace(" ", "_")))
        except Exception:
            return None
        if page.get("type") == "disambiguation" or not page.get("extract"):
            return None
        sentences = re.split(r"(?<=[a-z0-9)][.!?])\s+(?=[A-Z])", page["extract"].strip())
        summary = " ".join(sentences[:3])
        with self.repo.conn:
            self.repo.conn.execute("INSERT OR REPLACE INTO web_notes(term, title, summary, url) VALUES (?, ?, ?, ?)",
                                   (key, page.get("title", key), summary,
                                    page.get("content_urls", {}).get("desktop", {}).get("page", "")))
        return self.note(term)

    # Reusable wording: {term} is what he's after, {query} the exact search, {why} how it came up,
    # {summary} what he found, {source} where.
    PROPOSE = (
        "I'd like to look that up. I'd search {source} for “{query}”, {why}. Is it okay if I search for that?",
        "Here's what I'd search for: “{query}” on {source}, {why}. Do you want me to go ahead?",
        "I could find out. My search would be “{query}”, on {source}, {why}. Should I look it up?",
    )
    FOUND = (
        "Here's what I found about {term}, from {source}. {summary} I'll remember that.",
        "I looked up {term}. According to {source}, {summary_lc} I'll keep that in mind from now on.",
        "Okay, I searched for {term}. {summary} That's from {source}, and I've saved it.",
    )
    NOT_FOUND = (
        "I searched {source} for “{query}” but didn't find anything clear. Could you tell me what {term} is?",
        "Nothing useful came back for “{query}”. If you tell me about {term}, I'll learn it that way.",
    )
    DECLINED = ("Okay, I won't look it up.", "Alright, I'll leave it. You can tell me about it yourself if you like.")
    RECALLED = ("Nobody's told me about {term} directly, but I read up on it before. {summary}",
                "I looked {term} up a while ago. {summary}")
    SOURCE = "Wikipedia"

    pending: dict = {}

    def _say(self, bank, **slots) -> str:
        import random
        summary = slots.get("summary", "")
        slots.setdefault("summary_lc", summary[:1].lower() + summary[1:] if summary else "")
        return random.choice(bank).format(source=self.SOURCE, **slots)

    def propose(self, speaker: str, term: str, why: str) -> str:
        """Put the search together and ask first; nothing goes online until they say yes."""
        query = self._key(term)
        self.pending = {**self.pending, speaker: {"term": term, "query": query}}
        return self._say(self.PROPOSE, term=term, query=query, why=why)

    def try_approve(self, speaker: str, text: str) -> str | None:
        plan = self.pending.get(speaker)
        if not plan:
            return None
        self.pending = {k: v for k, v in self.pending.items() if k != speaker}
        if re.match(r"^\W*(?:no|nope|nah|don'?t|do not|not now|never ?mind|skip it|cancel)\b", text, re.I):
            return self._say(self.DECLINED)
        if not re.match(r"^\W*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|go ahead|do it|please|search|look it up|go for it|"
                        r"approved?)\b", text, re.I):
            return None  # moved on: the plan is dropped and the message handled as usual
        if not self.enabled:
            return "I can't get online right now, so I can't look that up. You could tell me about it yourself."
        found = self.fetch(plan["query"])
        if found:
            world = getattr(self, "world", None)
            more = world.mention(world.read(found["summary"], f"what I read about {plan['term']}")) if world else ""
            return self._say(self.FOUND, term=plan["term"], summary=found["summary"]) + more
        return self._say(self.NOT_FOUND, term=plan["term"], query=plan["query"])

    def asked(self, speaker: str, text: str) -> str | None:
        """'Look up pit bulls': an explicit request, still run by them before searching."""
        m = ASK.match(text)
        if not m:
            return None
        term = m["term"]
        known = self.note(term)
        if known:
            return f"I already looked that up. {known['summary']}"
        return self.propose(speaker, term, "since you asked")

    def fill_gap(self, speaker: str, reply: str, heard: str = "") -> str | None:
        """When his reply admits he doesn't know a thing, offer to look it up (or use what he read before)."""
        asked = re.match(r"^\W*(?:tell me about|who (?:is|was)|what (?:is|are|was)|what's|who's)\s+(?P<t>.+?)\W*$",
                         heard, re.I)
        m = (asked if asked and "I don't know" in reply else None) or (re.search(r"I don't know anything about (?P<t>.+?) yet\.", reply)
             or re.search(r"What does '(?P<t>[^']+)' mean\?", reply)
             or re.search(r"^What is an? (?P<t>[\w -]+)\?$", reply))
        if not m:
            return None
        term = m["t"]
        if self._key(term) in ("it", "that", "this", "him", "her", "them", "me", "you") \
                or re.match(r"^(?:my|your|our|his|her|their|its)\b", self._key(term)):
            return None  # "your birthday" is about him or them, not something the internet knows
        had = self.note(term)
        if had:
            return self._say(self.RECALLED, term=term, summary=had["summary"])
        if not self.enabled:
            return None
        return f"{reply} {self.propose(speaker, term, 'because nobody has told me about ' + term + ' yet')}"
