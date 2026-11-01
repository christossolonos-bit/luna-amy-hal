"""News from RSS feeds: a daily brief he can talk through, and details pulled on request.

Feeds come from HALMINI_RSS_FEEDS (comma-separated "Name=url" or bare urls), else a default set. They're
fetched when news comes up, at most every REFRESH_MINUTES, and kept in the database so he can come back
to a story later. Stdlib only.
"""

import html
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET

DEFAULT_FEEDS = {
    "BBC": "https://feeds.bbci.co.uk/news/rss.xml",
    "NPR": "https://feeds.npr.org/1001/rss.xml",
    "The Guardian": "https://www.theguardian.com/world/rss",
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
}
REFRESH_MINUTES = 30
AGENT = "HalMini/1.0 (news reader)"
STOP = {"the", "a", "an", "about", "with", "news", "story", "stories", "what", "whats", "that", "this", "more",
        "tell", "me", "is", "are", "going", "on", "happening", "any", "anything", "there", "of", "in", "and", "to",
        "for", "latest", "on", "today", "you", "know", "do", "details", "detail"}

BRIEF = re.compile(r"^\W*(?:(?:give me |what'?s |what is )?(?:the |my |today'?s |a )?(?:daily |news )?(?:brief(?:ing)?|"
                   r"headlines)|what'?s (?:in |going on in )?the news|what'?s going on in the world|any news|"
                   r"what'?s the news(?: today)?|what'?s happening (?:in the world|today)|catch me up)\W*$", re.I)
DETAIL = re.compile(r"^\W*(?:tell me (?:more )?about|what about|what'?s (?:happening|going on) with|"
                    r"any news (?:on|about)|what'?s the (?:latest|news) (?:on|about|with)|more on|"
                    r"what do you know about the)\s+(?P<topic>.+?)\W*$", re.I)


NEWSY = re.compile(r"\b(?:news|headlines?|rss|news ?feeds?|feeds|current events|daily brief|briefing|"
                   r"what'?s (?:going on|happening) (?:in the world|out there|today))\b", re.I)
ASKY = re.compile(r"\b(?:check|read|get|pull|give|tell|show|any|latest|what'?s|whats|catch me up|go through)\b", re.I)


def _text(node, tag: str) -> str:
    found = node.find(tag)
    if found is None or found.text is None:
        return ""
    plain = re.sub(r"<[^>]+>", " ", html.unescape(found.text))
    return re.sub(r"\s+", " ", plain).strip()


class News:
    def __init__(self, repo) -> None:
        self.repo = repo
        self.fetched_at = 0.0
        self.last_topic: list[int] = []
        with repo.conn:
            repo.conn.execute("CREATE TABLE IF NOT EXISTS news_items(link TEXT PRIMARY KEY, feed TEXT, title TEXT, "
                              "summary TEXT, published TEXT, fetched_at TEXT DEFAULT CURRENT_TIMESTAMP)")

    @staticmethod
    def feeds() -> dict[str, str]:
        raw = os.environ.get("HALMINI_RSS_FEEDS", "").strip()
        if not raw:
            return DEFAULT_FEEDS
        out = {}
        for part in filter(None, (p.strip() for p in raw.split(","))):
            name, _, url = part.partition("=") if "=" in part else (re.sub(r"^https?://(?:www\.|feeds\.)?", "", part).split("/")[0], "", part)
            out[name.strip()] = url.strip()
        return out

    def refresh(self, force: bool = False) -> None:
        if os.environ.get("HALMINI_WEB", "1") == "0" or "pytest" in __import__("sys").modules:
            return
        if not force and time.time() - self.fetched_at < REFRESH_MINUTES * 60:
            return
        for name, url in self.feeds().items():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": AGENT})
                with urllib.request.urlopen(req, timeout=6) as r:
                    root = ET.fromstring(r.read())
            except Exception:
                continue
            items = root.iter("item")
            with self.repo.conn:
                for item in list(items)[:15]:
                    link, title = _text(item, "link"), _text(item, "title")
                    if not link or not title:
                        continue
                    self.repo.conn.execute(
                        "INSERT OR IGNORE INTO news_items(link, feed, title, summary, published) VALUES (?,?,?,?,?)",
                        (link, name, title, _text(item, "description"), _text(item, "pubDate")))
        self.fetched_at = time.time()

    def _recent(self, limit: int = 60):
        return self.repo.conn.execute("SELECT rowid, * FROM news_items WHERE fetched_at >= datetime('now', '-2 days') "
                                      "ORDER BY fetched_at DESC, rowid ASC LIMIT ?", (limit,)).fetchall()

    def brief(self) -> str:
        self.refresh()
        rows = self._recent()
        if not rows:
            return "I couldn't get any news just now. The feeds didn't come through."
        by_feed: dict[str, list] = {}
        for r in rows:
            by_feed.setdefault(r["feed"], []).append(r)
        parts, shown = [], []
        for feed, items in by_feed.items():
            top = items[:2]
            shown += [i["rowid"] for i in top]
            lead = f"{feed} is leading with {top[0]['title'].rstrip('.')}"
            if len(top) > 1:
                lead += f", and also has {top[1]['title'].rstrip('.')}"
            parts.append(lead + ".")
        self.last_topic = shown
        return ("Here's what's in the news. " + " ".join(parts) +
                " Ask me about any of those and I'll tell you what I've got on it.")

    def detail(self, topic: str) -> str | None:
        words = [w for w in re.findall(r"[a-z0-9']+", topic.lower()) if w not in STOP and len(w) > 2]
        if not words:
            return None
        self.refresh()
        scored = []
        for r in self._recent(200):
            hay = f"{r['title']} {r['summary']}".lower()
            score = sum(2 if w in r["title"].lower() else 1 for w in words if w in hay)
            if score:
                scored.append((score, r))
        if not scored:
            return None
        scored.sort(key=lambda x: -x[0])
        best = [r for _, r in scored[:3]]
        self.last_topic = [r["rowid"] for r in best]
        first = best[0]
        out = f"{first['feed']} has it as {first['title'].rstrip('.')}. {first['summary']}".strip()
        others = [r for r in best[1:] if r["title"] != first["title"]]
        if others:
            out += " Related to that, " + " ".join(f"{r['feed']} reports {r['title'].rstrip('.')}." for r in others)
        return out

    def respond(self, text: str) -> str | None:
        if BRIEF.match(text):
            return self.brief()
        m = DETAIL.match(text)
        if m:
            return self.detail(m["topic"])
        # Any way of asking for the news: "check your RSS feed for some latest news", "can you check the news
        # feeds and tell me what's going on?" A topic after about/on/with narrows it to that story.
        if NEWSY.search(text) and not text.rstrip().endswith(".") or NEWSY.search(text) and ASKY.search(text):
            topic = re.search(r"\b(?:about|on|regarding|with)\s+(?:the\s+)?(?P<t>[\w' -]+?)\W*$", text, re.I)
            if topic and not NEWSY.fullmatch(topic["t"].strip()) and topic["t"].lower() not in ("it", "that", "now"):
                found = self.detail(topic["t"])
                if found:
                    return found
            return self.brief()
        return None
