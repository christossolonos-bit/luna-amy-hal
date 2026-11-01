"""Sentence segmentation and removal of conversational filler before parsing.

Hedges are not thrown away: they lower the confidence of whatever is learned.
"""

import re

from nlp.tokenizer import Token

ABBREVIATIONS = re.compile(r"\b(Mr|Mrs|Ms|Dr|St|Jr|Sr|Prof|vs|etc)\.", re.IGNORECASE)
SENTENCE_RE = re.compile(r"[^.!?;\n]+[.!?]*")

GREETINGS = {"hey", "hi", "hello", "yo"}
FILLER_WORDS = {"so", "well", "um", "uh", "umm", "er", "basically", "okay", "ok", "oh",
                "anyway", "alright", "right", "now", "also", "yeah", "and", "plus", "but", "like"}
COMMA_FILLERS = {"like", "yes", "sure", "see", "look", "listen"}
CORRECTION_PHRASES = [
    ("sorry", ",", "i", "meant"), ("no", ",", "i", "meant"), ("i", "meant"), ("i", "mean"),
    ("correction",), ("actually", ","), ("actually",), ("nope", ","), ("nope",), ("no", ","),
    ("wait", ","), ("oops", ","), ("oops",), ("my", "bad", ","), ("not", "quite", ","), ("sorry", ","),
]
FILLER_PHRASES = [
    ("just", "so", "you", "know"), ("for", "your", "information"), ("by", "the", "way"),
    ("did", "you", "know"), ("you", "know"), ("fun", "fact"), ("heads", "up"),
    ("as", "you", "know"), ("keep", "in", "mind"), ("remember", "this"), ("please", "remember"), ("remember",), ("note",), ("fyi",), ("btw",),
]
HEDGES = {
    ("i'm", "pretty", "sure"): 0.8, ("i", "am", "pretty", "sure"): 0.8, ("i'm", "sure"): 0.9,
    ("i", "think"): 0.6, ("i", "believe"): 0.7, ("i", "guess"): 0.5, ("i", "heard"): 0.6,
    ("i", "suppose"): 0.5, ("rumor", "has", "it"): 0.4, ("apparently",): 0.6,
    ("supposedly",): 0.5, ("probably",): 0.6, ("maybe",): 0.4, ("perhaps",): 0.4,
    ("possibly",): 0.4, ("allegedly",): 0.5,
}
INLINE_HEDGES = {"probably": 0.6, "maybe": 0.4, "perhaps": 0.4, "possibly": 0.4,
                 "apparently": 0.6, "supposedly": 0.5, "allegedly": 0.5}
TRAILING_TAGS = [("right",), ("you", "know"), ("i", "think"), ("i", "guess"), ("ok",), ("okay",)]
# Tacked onto the end of a message without a comma: "Where does Chen live lol"
TRAILING_NOISE = {"lol", "lmao", "lmfao", "haha", "hahaha", "lolol", "xd", "rofl", "bro", "fam", "tho", "bruh"}
CONTRACTION = re.compile(
    r"\b(i|you|we|they|he|she|it|that|there|here|what|who|where|when|how|why|let)'(m|re|ve|ll|d|s)\b", re.I)
EXPANSION = {"m": "am", "re": "are", "ve": "have", "ll": "will", "d": "would", "s": "is"}


def expand_contractions(text: str) -> str:
    """"I'll" -> "I will", "what's" -> "what is", "let's" -> "let us". Names keep their possessive 's."""
    text = text.replace("\u2019", "'").replace("\u2018", "'")

    def repl(m: re.Match) -> str:
        word, tail = m.group(1), m.group(2).lower()
        if word.lower() == "let" and tail == "s":
            return word + " us"
        return f"{word} {EXPANSION[tail]}"
    return CONTRACTION.sub(repl, text)


def unshout(text: str) -> str:
    """ALL-CAPS messages read as names everywhere: "HOWARD OWNS A TRUCK" -> "howard owns a truck"."""
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 6 and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        return text.lower()
    return text


def split_sentences(text: str) -> list[str]:
    protected = ABBREVIATIONS.sub(lambda m: m.group(1), text.replace("’", "'"))
    return [s.strip() for s in SENTENCE_RE.findall(protected) if s.strip(" .!?;\t")]


def _starts(lows: list[str], phrase: tuple[str, ...]) -> bool:
    return tuple(lows[:len(phrase)]) == phrase


def strip_discourse(tokens: list[Token], names: set[str] = frozenset()) -> tuple[list[Token], float, bool]:
    """Returns (remaining tokens, confidence, is_correction)."""
    toks = list(tokens)
    confidence = 1.0
    correction = False
    changed = True
    while changed and toks:
        changed = False
        lows = [t.low for t in toks]
        if lows[0] == ",":
            toks, changed = toks[1:], True
            continue
        cut = next((len(p) for p in CORRECTION_PHRASES if _starts(lows, p) and len(lows) > len(p)), 0)
        if cut:
            correction = True
        for phrase, conf in ([] if cut else HEDGES.items()):
            if _starts(lows, phrase) and len(lows) > len(phrase):
                cut, confidence = len(phrase), min(confidence, conf)
                break
        if not cut:
            cut = next((len(p) for p in FILLER_PHRASES if _starts(lows, p) and len(lows) > len(p)), 0)
        if not cut and len(lows) > 1:
            if lows[0] in GREETINGS:  # "Hey Hal," / "Hi there," -> drop the vocative too
                cut = 3 if len(lows) > 3 and lows[2] == "," else 1
            elif lows[0] in FILLER_WORDS or (lows[0] in COMMA_FILLERS and lows[1] == ","):
                cut = 1
            elif lows[0] in names and lows[1] == ",":
                cut = 2
        if cut:
            toks = toks[cut:]
            if toks and toks[0].low == "that":
                toks = toks[1:]
            changed = True

    kept = []
    lows = [t.low for t in toks]
    if any(a == "no" and b == "longer" for a, b in zip(lows, lows[1:])):
        correction = True
    for t in toks:
        if t.low in ("anymore",):
            correction = True
        elif t.low in INLINE_HEDGES:
            confidence = min(confidence, INLINE_HEDGES[t.low])
        else:
            kept.append(t)
    toks = kept

    trimmed = True
    while trimmed and toks:
        trimmed = False
        if toks[-1].low == ",":
            toks, trimmed = toks[:-1], True
            continue
        lows = [t.low for t in toks]
        if len(toks) > 2 and lows[-1] in TRAILING_NOISE:
            toks, trimmed = toks[:-1], True
            continue
        for tag in TRAILING_TAGS:
            if len(lows) > len(tag) + 1 and tuple(lows[-len(tag):]) == tag and lows[-len(tag) - 1] == ",":
                toks, trimmed = toks[:-len(tag) - 1], True
                break
    return toks, confidence, correction
