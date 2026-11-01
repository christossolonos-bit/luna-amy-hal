"""Automatic coherence checks on one reply. Each returns a short issue code, or nothing.

They look for what makes a reply incoherent to a person reading it, without any language model:
nonsense echoed back, garbage stored as fact, broken grammar, runaway length, repetition.
"""

import re

QUESTION_WORDS = {"how", "why", "what", "who", "where", "when", "which", "whose"}
FILLER_NAMES = {"so", "well", "yeah", "yes", "no", "ok", "okay", "lol", "lmao", "hmm", "um", "uh", "oh", "hey",
                "hi", "hello", "bro", "dude", "man", "like", "just", "but", "and", "then", "now", "here", "there",
                "this", "that", "it", "they", "we", "you", "i", "me", "what", "how", "why", "who", "fine",
                "sure", "nope", "yep", "nah", "thanks", "thank", "please", "sorry", "wait", "listen", "look",
                "dang", "damn", "shit", "fuck", "fucking", "holy", "wow", "omg", "bruh", "yo", "sup", "right", "true", "maybe", "anyway", "also", "actually", "really", "not"}
CANNED_FALLBACK = re.compile(r"can't phrase that answer reliably|\[stored, but", re.I)
ECHO_NONSENSE = re.compile(r"I don't know whether (?P<subj>[A-Z][a-z]+)\b", re.U)
CALLED = re.compile(r"(?:anyone or anything|anyone) called (?P<name>[A-Z][\w'-]*)")
LEARNED = re.compile(r"(?:^|[.!?] )(?:Understood|Now|I learned \d+ things|I've made a note of this|I'll keep this information)"
                     r":\s*(?P<facts>.+)$", re.I | re.S)
DOUBLED = re.compile(r"\b(\w+) \1\b", re.I)
BAD_ARTICLE = re.compile(r"\b(?:a|an) (?:a|an|the)\b|\ban [bcdfgjklmnpqrstvwxz]\w*\b|\ba [aeio]\w*\b", re.I)


def reply_issues(said: str, reply: str, trace: dict) -> list[str]:
    issues = []
    if not reply.strip():
        return ["empty-reply"]
    if CANNED_FALLBACK.search(reply):
        issues.append("validator-fallback")
    for m in ECHO_NONSENSE.finditer(reply):
        if m["subj"].lower() in QUESTION_WORDS | FILLER_NAMES:
            issues.append("echoed-nonsense")
            break
    for m in CALLED.finditer(reply):
        if m["name"].lower() in FILLER_NAMES | QUESTION_WORDS:
            issues.append("filler-as-name")
            break
    learned = LEARNED.search(reply)
    if learned:
        facts = learned["facts"]
        first = re.split(r"\W+", facts.strip(), maxsplit=1)[0].lower()
        if first in FILLER_NAMES - {"i", "you", "we", "they", "it", "my", "your"}:
            issues.append("learned-garbage")
        if re.search(r"[\U0001F300-\U0001FAFF]|https?://|<@\d+>|`", facts):
            issues.append("learned-junk-token")
    if len(reply) > 900 and len(said) < 200:
        issues.append("runaway-length")
    stripped = re.sub(r"“[^”]*”|\"[^\"]*\"|'[^']*'", "", reply)  # don't judge quoted text
    if DOUBLED.search(stripped) and not re.search(r"\b(that that|had had)\b", stripped, re.I):
        issues.append("doubled-word")
    if BAD_ARTICLE.search(re.sub(r"\ban? (?:one|uni\w*|eu\w*|u[a-z]*e\w*|hour\w*|honest\w*|herb\w*)\b", "", stripped)):
        issues.append("article")
    if "  " in reply.strip() or " ." in reply or " ," in reply or "..." in reply.replace("…", ""):
        issues.append("spacing")
    if re.search(r"\bNone\b|\{|\}|\[\]|\\n|Traceback", reply):
        issues.append("internal-leak")
    return issues


def conversation_issues(replies: list[str]) -> list[str]:
    issues = []
    run = 1
    for a, b in zip(replies, replies[1:]):
        run = run + 1 if a == b else 1
        if run == 3:
            issues.append("repeated-reply")
    return issues
