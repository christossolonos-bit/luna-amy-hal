"""Breaking a long sentence into simple ones the parser can read.

Real writing (Wikipedia openings, news, dictionary meanings, how people actually talk) packs several facts
into one sentence. The parser reads one simple clause at a time, so this stage runs first and splits:

  parentheses        "Burbank (pop. 107,000) is a city."       -> drops the aside
  semicolons         "It rained; the game was off."           -> two sentences
  leading clause     "Because it rained, the game was off."    -> "It rained." + "The game was off." (cause)
  trailing clause    "The game was off because it rained."     -> same, the link kept
  new subject        "Jen lives in Denver, and she is a nurse."-> "Jen lives in Denver." + "She is a nurse."
  which / who        "Burbank is in LA County, which is 7 miles northwest of downtown."
                                                              -> "Burbank is in LA County." + "LA County is 7 miles..."
  where              "He moved to Dayton, where he met Jen."   -> "He moved to Dayton." + "He met Jen in Dayton."
  appositive         "Burbank, a city in California, has an airport."
                                                              -> "Burbank is a city in California." + "Burbank has an airport."
  participle         "Burbank is a city, located 7 miles northwest of LA."
                                                              -> "Burbank is a city." + "Burbank is located 7 miles..."
Each piece remembers the sentence it came from, how it links to the one before (cause, time, contrast,
condition), and how sure the split is. Anything that doesn't split cleanly is passed through whole.
"""

import re
from dataclasses import dataclass

SUBORDINATORS = {
    "because": "cause", "since": "cause", "as": "cause", "so that": "purpose",
    "although": "contrast", "though": "contrast", "even though": "contrast", "while": "contrast", "whereas": "contrast",
    "when": "time", "after": "time", "before": "time", "once": "time", "until": "time", "whenever": "time",
    "if": "condition", "unless": "condition",
}
COORD = {"and": None, "but": "contrast", "so": "cause", "yet": "contrast"}
PRONOUN_SUBJECTS = r"(?:i|you|he|she|it|we|they|this|that|there)"
VERB_HINT = re.compile(r"\b(?:is|are|was|were|am|be|been|has|have|had|do|does|did|will|would|can|could|should|may|"
                       r"might|must|[a-z]+(?:ed|es)|[a-z]{3,}s|met|went|got|made|took|saw|came|ran|said|told|gave|left|"
                       r"found|thought|bought|sent|kept|began|became|wrote|ate|drove|flew|won|lost|held|stood|sat|"
                       r"grew|knew|led|paid|put|set|sold|spent|taught|felt|heard|built|brought|caught|fell|rose)\b", re.I)
NOT_VERBS = {"this", "his", "has", "was", "is", "us", "yes", "news", "always", "sometimes", "series", "species",
             "states", "years", "times", "miles", "less", "unless", "across", "process", "business", "its", "days"}


@dataclass
class ClausePiece:
    text: str          # a simple sentence the parser can read
    source: str        # the original sentence
    link: str | None   # "cause", "time", "contrast", "condition", "purpose", or None
    confidence: float


def _finish(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(" ,;:-")
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?" else text + "."


def _has_verb(text: str) -> bool:
    return any(w.lower() not in NOT_VERBS or w.lower() in ("is", "was", "has")
               for w in VERB_HINT.findall(text))


def _subject(clause: str) -> str | None:
    """The subject of a simple clause: the words before its first verb ('Burbank', 'The old city hall')."""
    words = clause.strip(" ,.").split()
    for i, w in enumerate(words):
        low = w.lower().strip(",")
        if i and low in ("is", "are", "was", "were", "has", "have", "had", "will", "can", "lives", "works",
                         "owns", "moved", "became", "remains", "contains", "includes", "covers", "lies", "sits") \
                or (i and re.fullmatch(r"[a-z]+(?:ed|s)", low) and low not in NOT_VERBS and not words[i - 1].istitle()):
            return " ".join(words[:i])
    return None


def _last_noun_phrase(text: str) -> str | None:
    """The noun phrase a 'which' or 'where' points back to: the last name or 'the/a ...' before it."""
    text = text.strip(" ,")
    m = re.search(r"((?:[A-Z][\w.'-]*(?:\s+(?:of\s+)?[A-Z][\w.'-]*)*)|(?:\b(?i:the|a|an|this|that|his|her|their|my|your|our)"
                  r"\s+(?:[a-z-]+\s+){0,3}[a-z-]+))\s*$", text)
    return m.group(1) if m else None


def _split_parentheses(sentence: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", sentence)


def _split_semicolons(sentence: str) -> list[str]:
    return [p for p in re.split(r"\s*;\s*", sentence) if p.strip()]


def _leading_subordinate(sentence: str) -> list[tuple[str, str | None]] | None:
    words = "|".join(sorted(map(re.escape, SUBORDINATORS), key=len, reverse=True))
    m = re.match(rf"^\W*(?P<sub>{words})\s+(?P<a>[^,]+),\s*(?P<b>.+)$", sentence, re.I)
    if not m or not _has_verb(m["a"]) or not _has_verb(m["b"]):
        return None
    return [(m["a"], None), (m["b"], SUBORDINATORS[m["sub"].lower()])]


def _trailing_subordinate(sentence: str) -> list[tuple[str, str | None]] | None:
    words = "|".join(sorted((w for w in SUBORDINATORS if w not in ("as", "once", "while", "since")),
                            key=len, reverse=True))
    m = re.search(rf"^(?P<a>.+?),?\s+(?P<sub>{words})\s+(?P<b>.+)$", sentence, re.I)
    if not m or not _has_verb(m["a"]) or not _has_verb(m["b"]) or len(m["a"].split()) < 3:
        return None
    return [(m["a"], None), (m["b"], SUBORDINATORS[m["sub"].lower()])]


def _coordination(sentence: str) -> list[tuple[str, str | None]] | None:
    """', and she ...' / ', but it ...': a second clause with its own subject."""
    m = re.search(rf"^(?P<a>.+?)(?:,\s*|\s+)(?P<c>and|but|so|yet)\s+(?P<b>(?:{PRONOUN_SUBJECTS}|[A-Z][\w-]*|the\s+\w+)\b.+)$",
                  sentence, re.I)
    if not m or not _has_verb(m["a"]) or not _has_verb(m["b"]):
        return None
    b_subject = _subject(m["b"])
    if b_subject is None:  # "Bob owns a truck and a boat": a list, not a new clause
        return None
    return [(m["a"], None), (m["b"], COORD[m["c"].lower()])]


def _relative(sentence: str) -> list[tuple[str, str | None]] | None:
    """', which/who ...,' about the thing just named; ', where ...' about the place just named."""
    m = re.search(r"^(?P<a>.+?),\s*(?P<rel>which|who|where|whose)\s+(?P<b>[^,]+?)(?:,\s*(?P<rest>.+))?$", sentence, re.I)
    if not m:
        return None
    antecedent = _last_noun_phrase(m["a"])
    if not antecedent or not _has_verb(m["b"]):
        return None
    rel = m["rel"].lower()
    if rel == "where":
        second = f"{m['b']} in {antecedent}"
    elif rel == "whose":
        second = f"{antecedent}'s {m['b']}"
    else:
        second = f"{antecedent} {m['b']}"
    first = m["a"] if not m["rest"] else f"{m['a']} {m['rest']}"
    return [(first, None), (second, None)]


def _appositive(sentence: str) -> list[tuple[str, str | None]] | None:
    """'Burbank, a city in California, has an airport.'"""
    m = re.match(r"^(?P<name>[A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*)*),\s*(?P<app>(?:a|an|the)\s+[^,]+),\s*(?P<rest>.+)$", sentence)
    if not m or not _has_verb(m["rest"]) or _has_verb(m["app"]) and re.search(r"\b(?:is|are|was|were)\b", m["app"]):
        return None
    return [(f"{m['name']} is {m['app']}", None), (f"{m['name']} {m['rest']}", None)]


def _participle(sentence: str) -> list[tuple[str, str | None]] | None:
    """'Burbank is a city, located 7 miles northwest of downtown.'"""
    m = re.search(r"^(?P<a>.+?),\s*(?P<part>(?:located|situated|known|founded|built|named|called|born|based|famous|"
                  r"home to|best known)\b.+)$", sentence, re.I)
    if not m:
        return None
    subject = _subject(m["a"])
    if not subject:
        return None
    verb = "has" if m["part"].lower().startswith("home to") else "is"
    part = m["part"][len("home to "):] if verb == "has" else m["part"]
    return [(m["a"], None), (f"{subject} {verb} {part}", None)]


def _leading_participle(sentence: str) -> list[tuple[str, str | None]] | None:
    """'Located 12 miles north of downtown, it is home to many companies.'"""
    m = re.match(r"^\W*(?P<part>(?:located|situated|founded|born|known|built|based|named|raised)\b[^,]+),\s*(?P<rest>.+)$",
                 sentence, re.I)
    if not m or not _has_verb(m["rest"]):
        return None
    subject = _subject(m["rest"])
    if not subject:
        return None
    return [(m["rest"], None), (f"{subject} is {m['part'][0].lower()}{m['part'][1:]}", None)]


def _kind_in_place(sentence: str) -> list[tuple[str, str | None]] | None:
    """'Burbank is a city in Los Angeles County' -> what it is, and where it is."""
    m = re.match(r"^(?P<x>[A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*)*) (?P<be>is|was|are|were) (?P<kind>(?:a|an|the)\s+(?:[a-z-]+\s+){0,3}[a-z-]+)"
                 r"\s+(?P<prep>in|on|near|of) (?P<place>[A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*)*)$", sentence)
    if not m or m["prep"] == "of":
        return None
    return [(f"{m['x']} {m['be']} {m['kind']}", None), (f"{m['x']} {m['be']} {m['prep']} {m['place']}", None)]


def _place_region(sentence: str) -> list[tuple[str, str | None]] | None:
    """'... in Los Angeles County, California' -> and Los Angeles County is in California."""
    m = re.match(r"^(?P<a>.+\b(?:in|of|near) (?P<place>[A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*)*)),\s*(?P<region>[A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*)*)$",
                 sentence)
    if not m:
        return None
    return [(m["a"], None), (f"{m['place']} is in {m['region']}", None)]


STEPS = (_place_region, _kind_in_place, _leading_subordinate, _leading_participle, _appositive, _relative, _participle, _trailing_subordinate,
         _coordination)


def split(sentence: str) -> list[ClausePiece]:
    """A sentence as simple clauses, in order. A sentence that doesn't split comes back whole."""
    source = sentence.strip()
    if not source:
        return []
    out: list[ClausePiece] = []
    for part in _split_semicolons(_split_parentheses(source)):
        out += _split_one(part, source, None, 1.0, depth=0)
    return [p for p in out if p.text]


REWRITES = [
    (r"\bis home to\b", "has"), (r"\bare home to\b", "have"), (r"\bwas home to\b", "had"),
    (r"\b(is|are|was|were) located (in|on|at|near)\b", r"\1 \2"),
    (r"\b(is|are|was|were) (?:located|situated|found) (?:about |approximately |some )?(?:\d[\d,.]*\s+\w+\s+)?"
     r"(?:north|south|east|west|northeast|northwest|southeast|southwest|outside|inland)\s+of\b", r"\1 near"),
    (r"\b(is|are|was|were) situated in\b", r"\1 in"),
]


def _plain(text: str) -> str:
    for pattern, repl in REWRITES:
        text = re.sub(pattern, repl, text, flags=re.I)
    return text


def _split_one(text: str, source: str, link: str | None, confidence: float, depth: int) -> list[ClausePiece]:
    text = _plain(text.strip(" ,."))
    if depth < 4:
        for step in STEPS:
            parts = step(text)
            if parts:
                pieces: list[ClausePiece] = []
                for i, (piece, piece_link) in enumerate(parts):
                    pieces += _split_one(piece, source, piece_link if i else link, confidence * 0.9, depth + 1)
                return pieces
    return [ClausePiece(_finish(text), source, link, round(confidence, 2))]


def split_text(text: str) -> list[ClausePiece]:
    """A whole paragraph: sentence by sentence, then clause by clause."""
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'])", re.sub(r"\s+", " ", text.strip()))
    return [p for s in sentences for p in split(s)]
