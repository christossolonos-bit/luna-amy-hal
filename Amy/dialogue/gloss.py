"""Compile one selected WordNet meaning into conservative, source-backed clauses.

This is not a dictionary importer or a paraphraser. Call only for an encountered,
disambiguated sense, and pass its clauses through Conversation.digest with a
wordnet:<sense-id> source. Examples, alternatives, qualifications and comparisons
are never silently promoted into unconditional personal facts.
"""

from dataclasses import dataclass, field
import math
import re

from nlp.morphology import third_person

WORD = re.compile(r"^[\w][\w '\-]*$", re.UNICODE)
QUALIFIER = re.compile(r"\b(?:usually|often|sometimes|mostly|generally|typically|probably|"
                       r"only|not|never|without|except|may|might|can|could|would|should)\b", re.I)
RELATIVE = re.compile(r"\s+(?:that|which|who)\s+", re.I)
INCOMPLETE = re.compile(r"\b(?:and|or|of|to|with|by|in|for|from|between|than|as|a|an|the)$", re.I)


def _value(obj, key, default=""):
    value = obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
    return value() if callable(value) else value


def _article(phrase):
    low = phrase.lower()
    if low.startswith(("hour", "honest", "honor", "heir")):
        return "an"
    if low.startswith(("uni", "use", "euro", "one ")):
        return "a"
    return "an" if low[:1] in "aeiou" else "a"


def _subject(word, pos):
    if pos != "n" or word[:1].isupper():
        return word[:1].upper() + word[1:]
    return f"{_article(word).capitalize()} {word}"


def _finish(text):
    return text[:1].upper() + text[1:].rstrip(" .;:") + "."


def gloss_sentences(word: str, sense, wordnet) -> list[str]:
    """Selected meaning -> simple sentences; unsupported material stays unasserted.

Uses only noun hypernyms (never antonyms, examples, similarity or part relations)
and recognizable gloss predicates. Return [] when nothing can be safely compiled.
No lookup of other senses, no database writes, and no network operations.
"""
    word = re.sub(r"\s+", " ", word.replace("_", " ")).strip()
    if not word or len(word.split()) > 8 or not WORD.fullmatch(word) or sense is None:
        return []
    definition = _value(sense, "definition")
    pos = _value(sense, "pos", "n")
    ident = _value(sense, "id")
    if not isinstance(definition, str) or not definition.strip():
        return []
    subject, out = _subject(word, pos), []

    def emit(predicate, subj=subject):
        predicate = predicate.strip(" .;:")
        if not predicate or len(predicate.split()) > 45 or INCOMPLETE.search(predicate):
            return
        text = _finish(subj + " " + predicate)
        if text.casefold() not in {s.casefold() for s in out}:
            out.append(text)

    if pos == "n" and ident and wordnet is not None and hasattr(wordnet, "related"):
        for parent in wordnet.related(ident, ("hypernym", "instance_hypernym"), limit=4):
            lemma = _value(parent, "lemma").replace("_", " ")
            if WORD.fullmatch(lemma) and lemma.casefold() != word.casefold():
                emit(f"is {_article(lemma)} {lemma}")

    # Parentheticals are omitted rather than being turned into independent claims.
    # They may contain uncertain etymology or illustrative material.
    definition = re.sub(r"\([^()]*\)", "", definition)
    definition = re.sub(r"\s+", " ", definition).strip()
    pieces = [p.strip() for p in definition.split(";") if p.strip()]
    if not pieces:
        return out
    first = pieces[0]

    if pos == "n":
        match = RELATIVE.search(first)
        main = first[:match.start()] if match else first
        rest = first[match.end():] if match else None
        # Split a head noun from its participial description. Preserve qualifiers
        # in the resulting predicate, e.g. usually having -> usually has.
        part = re.search(r"\s+((?:(?:usually|often|typically|generally)\s+)?(?:having|containing|including))\s+", main, re.I)
        extra = None
        if part:
            verb = {"having": "has", "containing": "contains", "including": "includes"}
            words = part.group(1).split()
            extra = " ".join([*words[:-1], verb[words[-1].lower()], main[part.end():]])
            main = main[:part.start()]
        # "or" denotes an alternative definition, not two unconditional kinds.
        # Richer disjunction belongs in the future semantic representation.
        if not re.search(r"\b(?:or|if|unless|when)\b", main, re.I) and not QUALIFIER.search(main):
            if main.startswith(("a ", "an ", "the ")):
                emit("is " + main)
            elif re.match(r"^[\w-]+(?: [\w-]+){0,5}$", main):
                emit(f"is {_article(main)} {main}")
        if extra:
            emit(extra)
        # A relative after an intervening noun phrase can refer to that phrase,
        # not to the headword. Do not guess its subject.
        if rest and not re.search(r"\b(?:with|near|beside|inside|containing)\b", main, re.I):
            if re.match(r"^(?:is|has|can|may|might|must|does|will|was|has been)\b", rest, re.I):
                emit(rest)
        for piece in pieces[1:]:
            if re.match(r"^(?:is|has|can|may|might|must|does|will|occurs|includes|contains)\b", piece, re.I):
                emit(piece)
    elif pos in ("a", "s"):
        for piece in pieces:
            colour = re.fullmatch(r"of the colou?r intermediate between (.+) and (.+)", piece, re.I)
            if colour:
                emit("is a color")
                emit(f"is between {colour[1]} and {colour[2]}")
            elif piece.startswith("having "):
                emit("has " + piece[7:])
            elif re.match(r"^(?:not |without |capable of |characterized by |full of )", piece, re.I):
                emit("is " + piece)
            elif re.fullmatch(r"[\w-]+(?: [\w-]+){0,4}", piece) and not re.search(r"\b(?:or|if|when)\b", piece):
                emit("is " + piece)
    elif pos == "v":
        # Rules preserve the implication's direction. Do not assert that someone
        # actually performs an action merely because its verb has a definition.
        predicate = re.sub(r"^to\s+", "", first)
        if re.fullmatch(r"[a-z]+(?:\s+(?:quickly|slowly|rapidly|quietly|fast|loudly))?", predicate) \
                and " " not in word and not QUALIFIER.search(predicate):
            parts = predicate.split(maxsplit=1)
            result = third_person(parts[0]) + (" " + parts[1] if len(parts) > 1 else "")
            out.append(f"If something {third_person(word)}, it {result}.")
    return list(dict.fromkeys(out))


@dataclass
class GlossDigester:
    """Optional on-demand adapter; Claude may instead call gloss_sentences directly.

    Cache is scoped to this adapter/conversation and only suppresses successful
    stores. Failed, empty, and fully skipped digestions remain retryable.
    """
    conversation: object
    wordnet: object
    stored: dict[str, object] = field(default_factory=dict)

    def digest(self, word, sense, confidence=0.8):
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("confidence must be finite and between zero and one")
        ident = _value(sense, "id")
        if not ident:
            raise ValueError("A selected sense ID is required")
        if ident in self.stored:
            return self.stored[ident]
        sentences = gloss_sentences(word, sense, self.wordnet)
        if not sentences:
            return None
        digest = getattr(self.conversation, "digest", None)
        if not callable(digest):
            raise RuntimeError("Conversation.digest is required to store a WordNet meaning")
        result = digest(" ".join(sentences), source="wordnet:" + ident, confidence=confidence)
        if result.facts:
            self.stored[ident] = result
        return result
