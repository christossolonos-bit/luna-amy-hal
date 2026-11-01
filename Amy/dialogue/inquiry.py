"""Asking to learn. When the bot hits a gap it asks the person it's talking to, and treats the
next reply as the answer:

  unknown word in a sentence  -> "What does 'flibbet' mean?"     reply: "It's a kind of bird."
  question needing a word     -> "What is a motorcycle?"          reply: "A vehicle." (then answers the question)
  question with no evidence   -> "Do you know?"                   reply: "Yes" / "No" / "No idea"

Questions are not repeated, and a pending question expires if the conversation moves on.
"""

import re
import time
from dataclasses import dataclass, replace

from nlp.lexicon import AUX_WORDS, BOUNDARY, DETERMINERS, OBJECT_PRONOUNS, POSSESSIVE_DETS, QUANTIFIERS, \
    SUBJECT_PRONOUNS, WH, article
from nlp.morphology import lemmatize_noun
from nlp.parser import ParseError, parse_noun_phrase
from nlp.semantic_frames import Frame, NounPhrase
from nlp.tokenizer import tokenize

PENDING_SECONDS = 300.0
YES_RE = re.compile(r"^\W*(?:yes|yeah|yep|yup|correct|right|true|he does|she does|they do|it does|it is|"
                    r"he is|she is|they are|definitely|of course|sure)\b", re.I)
NO_RE = re.compile(r"^\W*(?:no|nope|nah|false|wrong|he doesn'?t|she doesn'?t|they don'?t|it doesn'?t|"
                   r"he isn'?t|she isn'?t|it isn'?t|not really|definitely not)\b", re.I)
DUNNO_RE = re.compile(r"^\W*(?:i (?:don'?t|do not) know|no idea|not sure|dunno|idk|beats me|no clue)\b", re.I)
DEFINITION_RE = re.compile(
    r"^\W*(?:well,?\s*)?(?:it'?s|it is|that'?s|that is|they'?re|they are|it means|that means|means|basically|"
    r"(?:a|an)\s+\w+\s+is)?\s*(?:(?:a|an)\s+)?(?:kind|type|sort) of\s+(.+?)\W*$"
    r"|^\W*(?:well,?\s*)?(?:it'?s|it is|that'?s|that is|they'?re|they are|it means|that means|means)\s+(.+?)\W*$", re.I)
EXTRA_STOP = {"not", "no", "yes", "and", "but", "or", "if", "then", "so", "very", "really", "just", "also"}


@dataclass
class Pending:
    kind: str                      # 'define' | 'yes_no'
    asked_at: float
    term: str | None = None        # word being defined
    frame: Frame | None = None     # the question we couldn't answer


class Inquiry:
    def __init__(self, conversation) -> None:
        self.convo = conversation
        self.repo = conversation.repo
        self.pending: dict[str, Pending] = {}
        self.asked: set[tuple] = set()

    # --- asking -------------------------------------------------------------------

    def _known_word(self, word: str) -> bool:
        lexicon = self.convo.lexicon()
        lemma = lemmatize_noun(word)[0]
        if word in BOUNDARY or word in DETERMINERS or word in SUBJECT_PRONOUNS or word in OBJECT_PRONOUNS \
                or word in POSSESSIVE_DETS or word in QUANTIFIERS or word in WH or word in EXTRA_STOP \
                or word in AUX_WORDS:
            return True
        if lexicon.verb(word) is not None:
            return True
        if self.convo.exposition.wordnet.usual(word) is not None:
            return True  # the dictionary knows it; don't make people define everyday words
        return bool(self.repo.find_concept(lemma) or self.repo.find_concept(word) or self.repo.find_entity(word)
                    or self.repo.conn.execute("SELECT 1 FROM lexemes WHERE lemma IN (?, ?)", (word, lemma)).fetchone())

    def ask_about_unparsed(self, sentence: str) -> str | None:
        """For a sentence it couldn't parse: ask about the first word it has never seen."""
        for token in tokenize(sentence):
            if not token.text.isalpha() or token.text[:1].isupper() or len(token.text) < 3:
                continue
            word = token.low
            if len(word) > 18 or re.search(r"(.)\1{2,}|(ha|he|lo|ah){2,}", word):
                continue  # "hahahaha", "loooool": not a word to define
            if not self._known_word(word):
                return self._ask_define(lemmatize_noun(word)[0], frame=None)
        return self.convo.lexical.topic_hint(sentence)

    def ask_about_unknown(self, frame: Frame, missing: list[str]) -> str | None:
        """For a question it couldn't answer: ask for the missing word, or ask whether they know."""
        for gap in missing:
            match = re.match(r"what (?:a|an) (.+) is$", gap)
            if match and self.convo.exposition.wordnet.usual(match.group(1)) is not None:
                continue  # a dictionary word: what's missing is the fact, not the meaning
            if match:
                return self._ask_define(match.group(1), frame)
        if missing or frame.slot or frame.relation == "describe" or frame.subject is None:
            return None
        key = ("yes_no", repr((frame.relation, frame.verb, frame.subject.head,
                               frame.obj.head if frame.obj else None, frame.properties)))
        if key in self.asked or self.convo.self_model.current()["curiosity"] < 0.2:
            return None
        self.asked.add(key)
        self.pending[self.convo.state.speaker] = Pending("yes_no", time.time(), frame=frame)
        return "Do you know? If you tell me, I'll remember it."

    def _ask_define(self, term: str, frame: Frame | None) -> str | None:
        key = ("define", term)
        if key in self.asked:
            return None
        self.asked.add(key)
        self.pending[self.convo.state.speaker] = Pending("define", time.time(), term=term, frame=frame)
        if frame is None:
            return f"What does '{term}' mean? You can tell me something like 'It's a kind of tool.'"
        return f"What is {article(term)} {term}?"

    # --- hearing the answer -------------------------------------------------------

    def try_answer(self, text: str) -> str | None:
        """If this message answers a question I asked this speaker, learn from it and reply."""
        speaker = self.convo.state.speaker
        pending = self.pending.get(speaker)
        if pending is None:
            return None
        if time.time() - pending.asked_at > PENDING_SECONDS:
            self.pending.pop(speaker, None)
            return None
        if DUNNO_RE.match(text):
            self.pending.pop(speaker, None)
            return "No problem."
        if pending.kind == "yes_no":
            if YES_RE.match(text) or NO_RE.match(text):
                self.pending.pop(speaker, None)
                fact = replace(pending.frame, act="assert", polarity=bool(YES_RE.match(text)), confidence=1.0)
                learned = self.convo.learn_frame(fact)
                return f"Thanks, I'll remember that {learned}." if learned else "Thanks."
            self.pending.pop(speaker, None)
            return None
        return self._definition(pending, text)

    def _definition(self, pending: Pending, text: str) -> str | None:
        speaker = self.convo.state.speaker
        match = DEFINITION_RE.match(text)
        phrase_text = (match.group(1) or match.group(2)) if match else None
        if phrase_text is None and len(text.split()) <= 4 and pending.term not in text.lower():
            phrase_text = text  # a bare answer: "A vehicle."
        if phrase_text is None:
            self.pending.pop(speaker, None)
            return None  # a full sentence: let normal processing learn it
        try:
            phrase, _ = parse_noun_phrase(phrase_text, self.convo.lexicon())
        except ParseError:
            self.pending.pop(speaker, None)
            return None
        self.pending.pop(speaker, None)
        if phrase.proper:
            phrase = NounPhrase(head=phrase.surface.lower(), surface=phrase.surface.lower())
        fact = Frame("assert", "is_a", NounPhrase(head=pending.term, determiner="a", surface=pending.term),
                     NounPhrase(head=phrase.head, modifiers=phrase.modifiers, determiner="a", surface=phrase.surface))
        learned = self.convo.learn_frame(fact)
        reply = f"Got it: {learned}." if learned else "Got it."
        if pending.frame is not None:
            reply += " So: " + self.convo.answer_frame(pending.frame)
        return reply
