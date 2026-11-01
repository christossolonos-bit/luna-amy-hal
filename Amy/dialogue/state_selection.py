"""Rank already-supported facts and set a response budget from Adam's state.

Pure selection: no prose generation, no memory writes, no truth/confidence changes.
The reply planner remains responsible for grammar, questions, and disclosure of
uncertainty. A Fact must already have passed the caller's evidence/conflict checks.
"""

from dataclasses import dataclass
import math
import re


@dataclass(frozen=True)
class Fact:
    text: str
    subject: str
    source: str
    confidence: float
    about_self: bool


@dataclass(frozen=True)
class Selection:
    facts: list[Fact]
    max_sentences: int
    ask_back: bool


STOP = set("a an the is are was were be been am i you he she it they we me my your his her its "
           "of to in on at for from with as that this these those and but or so do does did "
           "what which who where when how why can could would should tell know about please".split())
ALIASES = {"alive": "life", "living": "life", "lives": "live", "conscious": "consciousness",
           "consciously": "consciousness", "existence": "exist", "exists": "exist",
           "born": "birth", "created": "create", "creation": "create",
           "remembers": "memory", "remember": "memory", "memories": "memory",
           "learns": "learn", "learning": "learn", "feels": "feel", "feeling": "feel"}
SELF_TOPICS = {"life", "consciousness", "exist", "birth", "create", "memory", "learn", "mind", "body", "feel"}


def _tokens(text):
    words = re.findall(r"[\w]+", text.casefold())
    return {ALIASES.get(w, w[:-1] if w.endswith("s") and not w.endswith(("ss", "us", "is"))
                        and len(w) > 4 else w) for w in words if w not in STOP}


def _number(state, key, default, low=0.0, high=1.0):
    value = state.get(key, default)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value)) if math.isfinite(value) else default


def _questions(state):
    raw = state.get("open_questions", state.get("questions", ()))
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (tuple, list)):
        return set()
    return set().union(*(_tokens(q) for q in raw if isinstance(q, str))) if raw else set()


def choose(facts: list[Fact], state: dict, topic: str | None) -> Selection:
    """Return original fact objects, ranked, plus length and optional-question policy.

    State accepts SelfModel.current() or snapshot() dictionaries. Emotion values
    can also be supplied in snapshot['emotions']. Missing/nonfinite values use
    neutral defaults. Low energy/stress override enthusiasm and curiosity.
    """
    energy = _number(state, "energy", 0.65)
    stress = _number(state, "stress", 0.3)
    curious = _number(state, "curiosity", 0.3)
    valence = _number(state, "valence", 0.0, -1.0, 1.0)
    emotions = state.get("emotions", {})
    emotions = emotions if isinstance(emotions, dict) else {}
    sad = _number(emotions, "sadness", 0)
    excited = _number(emotions, "excitement", 0)
    tired = energy < 0.35 or stress >= 0.65
    if tired:
        budget = 1
    elif sad >= 0.6:
        budget = 2
    else:
        budget = 3 + (2 if valence > 0.2 else 0) + (2 if curious >= 0.6 else 0)
        if energy > 0.8 and (valence > 0.55 or excited > 0.65):
            budget += 3
    ask_back = not tired and sad < 0.6 and curious >= 0.55
    # Reserve space for a planner-generated question; facts cannot consume its slot.
    fact_budget = max(1, budget - int(ask_back))
    topic_words = _tokens(topic or "")
    question_words = _questions(state)
    self_question_words = question_words & SELF_TOPICS
    self_topic = bool(topic_words & SELF_TOPICS) or (topic or "").casefold() in ("me", "myself", "self", "adam")
    anchors = set()
    if not tired and valence > 0.2 and topic_words:
        for fact in facts:
            if topic_words & _tokens(fact.subject):
                anchors |= _tokens(fact.text) - topic_words
        anchors -= {"own", "owns", "live", "like", "work", "drive", "have", "name", "named"}
    candidates = []
    for order, fact in enumerate(facts):
        if not fact.text.strip():
            continue
        try:
            conf = float(fact.confidence)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(conf) or not 0 < conf <= 1:
            continue
        subject = _tokens(fact.subject)
        words = _tokens(fact.text)
        direct = len(topic_words & subject)
        overlap = len(topic_words & words)
        question_link = len(self_question_words & words)
        related = len(anchors & subject)
        # Do not fill space with unrelated material, even in a very good mood.
        if topic_words and not (direct or overlap or related or (self_topic and fact.about_self)):
            continue
        relevance = direct * 8 + overlap * 3 + related
        if self_topic and fact.about_self:
            relevance += 6
        if question_link and (not topic_words or direct or overlap):
            relevance += curious * 3 * question_link
        candidates.append((relevance, conf, -order, fact))
    candidates.sort(key=lambda row: row[:3], reverse=True)
    selected, seen = [], set()
    related_choice = None
    if anchors and fact_budget > 1:
        related_choice = next((row[3] for row in candidates if anchors & _tokens(row[3].subject)
                               and not topic_words & _tokens(row[3].subject)), None)
    for _, _, _, fact in candidates:
        # Deduplicate presentation but retain distinct conflicting/negative text.
        # The caller must resolve conflicts before providing the candidate facts.
        key = re.sub(r"\W+", " ", fact.text.casefold()).strip()
        if key in seen:
            continue
        seen.add(key)
        selected.append(fact)
        if len(selected) >= fact_budget - int(related_choice is not None):
            break
    if related_choice is not None and related_choice not in selected:
        selected.append(related_choice)
    return Selection(selected, budget, ask_back)
