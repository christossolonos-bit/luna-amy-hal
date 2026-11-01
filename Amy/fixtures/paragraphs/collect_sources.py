"""Explicit corpus refresh, never used by tests or the evaluation runner.

Run from the project root: python fixtures/paragraphs/collect_sources.py
Retains original excerpts, URLs, retrieval dates and revision IDs for attribution.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import unescape
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).parent
WIKI = ["Burbank, California", "Dayton, Ohio", "Ohio", "Alabama", "Moon", "Mars", "Jupiter",
        "Saturn", "Earth", "Sun", "Amazon River", "Nile", "Mount Everest", "Pacific Ocean",
        "Sahara", "Photosynthesis", "Telescope", "Bicycle", "Volcano", "Penguin",
        "Mercury (planet)", "Venus", "Neptune", "Uranus", "Giraffe", "Elephant", "Oak",
        "Oxygen", "Water", "Carbon dioxide", "Marie Curie", "Albert Einstein"]


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "HalMiniParagraphEvaluation/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def plain(text):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]*>", " ", text))).strip()


def wiki(title):
    data = json.loads(fetch("https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title)))
    return {"id": "wiki-" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"),
            "category": "wikipedia", "title": title, "text": data["extract"],
            "source": "wikipedia:" + title, "url": data["content_urls"]["desktop"]["page"],
            "revision": data.get("revision"), "license": "CC BY-SA; Wikipedia contributors",
            "retrieved_at": datetime.now(timezone.utc).isoformat()}


def news(feed_url, count):
    tree = ET.fromstring(fetch(feed_url))
    out = []
    for item in tree.findall("./channel/item")[:count]:
        encoded = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
        html = encoded.text if encoded is not None else item.findtext("description", "")
        paragraphs = [plain(p) for p in re.findall(r"<p\b[^>]*>(.*?)</p>", html or "", re.S | re.I)]
        text = next((p for p in paragraphs if len(p.split()) >= 25 and not p.startswith("The post ")),
                    plain(item.findtext("description", "")))
        if not text:
            continue
        link = item.findtext("link", "")
        out.append({"category": "news", "title": plain(item.findtext("title", "")), "text": text,
                    "source": "news:NASA:" + link.rstrip("/").rsplit("/", 1)[-1], "url": link,
                    "published_at": item.findtext("pubDate"), "feed": feed_url,
                    "license": "NASA source excerpt; individual third-party credits may apply",
                    "retrieved_at": datetime.now(timezone.utc).isoformat()})
    return out


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(wiki, WIKI))
    news_rows = news("https://www.nasa.gov/news-release/feed/", 16)
    for i, row in enumerate(news_rows, 1):
        row["id"] = f"news-{i:02}"
    seen = set()
    news_rows = [r for r in news_rows if not (r["text"] in seen or seen.add(r["text"]))]
    (ROOT / "sources.json").write_text(json.dumps(rows + news_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(rows)} Wikipedia and {len(news_rows)} news excerpts")
