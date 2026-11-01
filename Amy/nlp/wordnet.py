"""Versioned, local, sense-aware WordNet store. Never adds dictionary prose to memory.

Install with: python -m nlp.wordnet --download
Only the installer uses the network; conversation lookups open the finished DB read-only.
"""

import argparse
from collections import deque
from contextlib import closing
from dataclasses import dataclass
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import urllib.request
import xml.etree.ElementTree as ET
from nlp.morphology import analyses, lemmatize_noun

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "data" / "wordnet" / "wordnet.db"
URL = "https://en-word.net/static/english-wordnet-2025-plus.xml.gz"
EDITION = "Open English WordNet 2025+"
CREDIT = ("Open English WordNet, derived from Princeton WordNet. "
          "OEWN contributions: CC BY 4.0. https://en-word.net/ "
          "https://github.com/globalwordnet/english-wordnet/blob/main/LICENSE.md")
STOP = set("a an the of to and or is are was were be that this in on for with as it "
           "i you he she they me my his her its what who tell about explain please "
           "more detail detailed know does do did can could would should".split())


def tokens(text):
    words = {w for w in re.findall(r"[a-z]+", text.lower()) if w not in STOP and len(w) > 2}
    return words | {lemmatize_noun(w)[0] for w in words} | {lemma for w in words for lemma, _ in analyses(w)}


@dataclass(frozen=True)
class Sense:
    id: str
    lemma: str
    pos: str
    definition: str
    examples: tuple[str, ...] = ()


@dataclass
class Selection:
    sense: Sense | None
    alternatives: list[Sense]
    confidence: float


def import_xml(source: Path, target: Path) -> dict:
    """Build separately, check integrity, then replace atomically. Reruns are safe."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".building.db")
    if temp.exists():
        temp.unlink()
    try:
        with closing(sqlite3.connect(temp)) as conn:
            conn.executescript("""
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE synsets(id TEXT PRIMARY KEY, pos TEXT, definition TEXT, examples TEXT);
                CREATE TABLE senses(lemma TEXT COLLATE NOCASE, synset TEXT, pos TEXT, rank INTEGER,
                                    PRIMARY KEY(lemma, synset));
                CREATE TABLE edges(source TEXT, relation TEXT, target TEXT,
                                   PRIMARY KEY(source, relation, target));
                CREATE INDEX ix_senses_lemma ON senses(lemma, pos);
                CREATE INDEX ix_edges_source ON edges(source, relation);
            """)
            opener = gzip.open if source.suffix == ".gz" else open
            with opener(source, "rb") as stream:
                for _, elem in ET.iterparse(stream, events=("end",)):
                    if elem.tag == "LexicalEntry":
                        lemma = elem.find("Lemma")
                        if lemma is not None:
                            word, pos = lemma.get("writtenForm").replace("_", " "), lemma.get("partOfSpeech")
                            for rank, sense in enumerate(elem.findall("Sense")):
                                conn.execute("INSERT OR IGNORE INTO senses VALUES(?,?,?,?)",
                                             (word, sense.get("synset"), pos, rank))
                        elem.clear()
                    elif elem.tag == "Synset":
                        ident = elem.get("id")
                        conn.execute("INSERT INTO synsets VALUES(?,?,?,?)", (
                            ident, elem.get("partOfSpeech"),
                            " ".join("".join(d.itertext()) for d in elem.findall("Definition")),
                            json.dumps(["".join(e.itertext()) for e in elem.findall("Example")]),))
                        conn.executemany("INSERT OR IGNORE INTO edges VALUES(?,?,?)", (
                            (ident, r.get("relType"), r.get("target")) for r in elem.findall("SynsetRelation")))
                        elem.clear()
            dangling = conn.execute("SELECT COUNT(*) FROM senses WHERE synset NOT IN (SELECT id FROM synsets)").fetchone()[0]
            if dangling:
                raise ValueError(f"Incomplete WordNet: {dangling} missing synsets")
            counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in ("synsets", "senses", "edges")}
            if not all(counts.values()):
                raise ValueError("Empty WordNet data")
            conn.executemany("INSERT INTO metadata VALUES(?,?)", {
                "edition": EDITION, "attribution": CREDIT,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "counts": json.dumps(counts), "schema": "1", "url": URL,
            }.items())
            conn.commit()
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("WordNet integrity check failed")
        os.replace(temp, target)
        return counts
    finally:
        if temp.exists():
            temp.unlink()


class WordNet:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("HALMINI_WORDNET_DB", DEFAULT_PATH)).resolve()

    @property
    def available(self):
        return self.path.is_file() and os.environ.get("HALMINI_WORDNET", "true").lower() != "false"

    def connect(self):
        conn = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return closing(conn)

    def lookup(self, word: str, pos: str | None = "n") -> list[Sense]:
        if not self.available:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT s.synset, s.lemma, s.pos, y.definition, y.examples FROM senses s "
                "JOIN synsets y ON y.id=s.synset WHERE s.lemma=? COLLATE NOCASE "
                "AND (? IS NULL OR s.pos=?) "
                # the word as written before capitalised names: "tree" the plant before Herbert Tree
                "ORDER BY (s.lemma != ? COLLATE BINARY), s.rank, s.synset",
                (word.replace("_", " "), pos, pos, word.replace("_", " ")))
            return [Sense(r[0], r[1], r[2], r[3], tuple(json.loads(r[4]))) for r in rows]

    def sense(self, ident: str) -> Sense | None:
        if not self.available:
            return None
        with self.connect() as conn:
            rows = conn.execute("SELECT y.*, s.lemma FROM synsets y JOIN senses s ON s.synset=y.id "
                                "WHERE y.id=? ORDER BY s.rank, length(s.lemma), s.lemma", (ident,)).fetchall()
            preferred = ("dog", "animal", "vehicle", "person", "plant", "food", "tool", "machine", "building", "mammal")
            row = next((r for word in preferred for r in rows if r["lemma"] == word), rows[0] if rows else None)
            return Sense(row["id"], row["lemma"], row["pos"], row["definition"],
                         tuple(json.loads(row["examples"]))) if row else None

    def select(self, word: str, context: str = "", pos: str | None = "n", chosen: str | None = None) -> Selection:
        choices = self.lookup(word, pos)
        if chosen:
            exact = next((s for s in choices if s.id == chosen), None)
            if exact:
                return Selection(exact, choices, 1.0)
        if len(choices) == 1:
            return Selection(choices[0], choices, 1.0)
        # A matching lemma is not evidence for any particular sense.
        clues = tokens(context) - tokens(word)
        scored = []
        for sense in choices:
            family = self.ancestors(sense.id, depth=2, budget=30)
            related = " ".join(s.definition + " " + s.lemma for ident in family
                               if ident != sense.id and (s := self.sense(ident)))
            score = len(clues & tokens(sense.definition + " " + " ".join(sense.examples) + " " + related))
            scored.append((score, sense))
        scored.sort(key=lambda pair: -pair[0])
        if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return Selection(scored[0][1], choices, 0.8)
        return Selection(None, choices, 0.0)

    def ancestors(self, ident: str, depth: int = 12, budget: int = 200) -> dict[str, list[str]]:
        if not self.available:
            return {}
        paths, queue, steps = {ident: [ident]}, deque([ident]), 0
        with self.connect() as conn:
            while queue and steps < budget:
                current = queue.popleft()
                if len(paths[current]) - 1 >= depth:
                    continue
                for row in conn.execute("SELECT target FROM edges WHERE source=? "
                                        "AND relation IN ('hypernym','instance_hypernym') ORDER BY target", (current,)):
                    steps += 1
                    if steps > budget:
                        break
                    if row[0] not in paths:
                        paths[row[0]] = paths[current] + [row[0]]
                        queue.append(row[0])
        return paths

    def usual(self, word: str, pos_order: tuple[str, ...] = ("n", "v", "a", "s", "r")) -> Sense | None:
        """The most common sense, nouns first: what someone usually means by the bare word."""
        for pos in pos_order:
            found = self.lookup(word, pos)
            if found:
                return found[0]
        stem = lemmatize_noun(word.lower())[0]
        if stem != word.lower():
            return self.usual(stem, pos_order)
        for lemma, _ in analyses(word.lower()):
            if lemma != word.lower() and (found := self.lookup(lemma, "v")):
                return found[0]
        return None

    def proper_form(self, word: str) -> str | None:
        """'texas' -> 'Texas' when WordNet only knows the word as a capitalised name."""
        if not self.available or not word.isalpha():
            return None
        cache = self.__dict__.setdefault("_proper_cache", {})
        if word not in cache:
            with self.connect() as conn:
                forms = {r[0] for r in conn.execute("SELECT DISTINCT lemma FROM senses WHERE lemma=? COLLATE NOCASE "
                                                    "AND pos='n'", (word,))}
            cache[word] = next(iter(sorted(forms)), None) if forms and all(f[:1].isupper() for f in forms) else None
        return cache[word]

    def related(self, ident: str, relations: tuple[str, ...], limit: int = 12) -> list[Sense]:
        """Synsets linked by any of `relations` ('mero_part' from car gives its parts, etc.)."""
        if not self.available:
            return []
        marks = ",".join("?" * len(relations))
        with self.connect() as conn:
            targets = [r[0] for r in conn.execute(
                f"SELECT target FROM edges WHERE source=? AND relation IN ({marks}) ORDER BY target LIMIT ?",
                (ident, *relations, limit))]
        return [s for t in targets if (s := self.sense(t))]

    def lemmas(self, ident: str) -> list[str]:
        """Every word for this meaning (its synonyms)."""
        if not self.available:
            return []
        with self.connect() as conn:
            return [r[0] for r in conn.execute("SELECT lemma FROM senses WHERE synset=? ORDER BY rank, lemma", (ident,))]

    def common_ancestor(self, a: str, b: str) -> tuple[str, list[str], list[str]] | None:
        """The closest category two meanings share, with each one's hypernym path up to it."""
        up_a, up_b = self.ancestors(a, depth=16, budget=400), self.ancestors(b, depth=16, budget=400)
        shared = [ident for ident in up_a if ident in up_b]
        if not shared:
            return None
        best = min(shared, key=lambda ident: len(up_a[ident]) + len(up_b[ident]))
        return best, up_a[best], up_b[best]

    def path_to(self, start: str, target_word: str) -> list[str] | None:
        targets = {s.id for s in self.lookup(target_word)}
        return next((path for ident, path in self.ancestors(start).items() if ident in targets), None)

    def verify_path(self, path: list[str]) -> bool:
        if not path or not self.available:
            return False
        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM synsets WHERE id=?", (path[0],)).fetchone():
                return False
            return all(conn.execute("SELECT 1 FROM edges WHERE source=? AND target=? AND "
                                    "relation IN ('hypernym','instance_hypernym')", (a, b)).fetchone()
                       for a, b in zip(path, path[1:]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()
    source = args.source or args.output.parent / "english-wordnet-2025-plus.xml.gz"
    if args.download and not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        partial = source.with_suffix(source.suffix + ".part")
        with urllib.request.urlopen(URL, timeout=90) as response, partial.open("wb") as out:
            while block := response.read(1024 * 1024):
                out.write(block)
        os.replace(partial, source)
    if not source.exists():
        parser.error("Use --download or --source FILE")
    print(json.dumps(import_xml(source, args.output)))
    print(CREDIT)


if __name__ == "__main__":
    main()
