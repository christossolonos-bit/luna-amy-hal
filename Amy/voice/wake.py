"""Wake-word gating: the bot hears everything but only answers when addressed.

"Adam, does Howard own a truck?" / "Hey Adam, ..." / "... right, Adam?" are addressed. After the
bot answers someone, that person has a short follow-up window in which no wake word is needed.
Whisper often mishears short names, so sound-alikes count too.
"""

import re
import time
from dataclasses import dataclass, field

# Ways Whisper tends to spell each name. Only near-misses that aren't everyday words: "hell" and
# "hall" made the old name wake on "what the hell" / "down the hall".
SOUNDALIKES = {
    "amy": ("amy", "aimee", "aime", "amie", "aymee"),
    "adam": ("adam", "adams", "addam", "adem", "adum", "adom", "adim", "addem"),
    "hal": ("hal", "hale"),
}
DEFAULT_ALIASES = SOUNDALIKES["amy"]
# Noises and fillers that don't count as a follow-up ("Hmm." right after an answer is not a question).
FILLER = re.compile(r"^\W*(?:h+m+|u+m+|u+h+|a+h+|e+r+|o+h+|huh|mm+|m+-?h+m+|u+h+-?h+u+h+|mhm+|ok(?:ay)?|right|yeah|"
                    r"yep|sure|cool|nice|wow|lol|i see)\W*$", re.I)
GREETING = r"(?:hey|hi|hello|ok|okay|yo)"
# Words right before the name that make it a mention, not someone talking to him: "his name is Adam",
# "talk to Adam", "ask Adam".
MENTION_BEFORE = re.compile(r"\b(?:is|was|named|called|name|to|with|about|for|ask|tell|like|of|from|by|"
                            r"not|nor|except|unlike|without|at|than|that|the|his|her|their|our|my|your)\W*$", re.I)


@dataclass
class WakeResult:
    addressed: bool
    text: str            # the utterance with the wake word removed
    wake_only: bool = False
    by_follow_up: bool = False


@dataclass
class WakeWord:
    name: str = "Adam"
    aliases: tuple[str, ...] | None = None  # None: the known sound-alikes for `name`
    follow_up_seconds: float = 15.0
    _last_answered: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.aliases is None:
            self.aliases = SOUNDALIKES.get(self.name.lower(), (self.name.lower(),))
        words = sorted({self.name.lower(), *self.aliases}, key=len, reverse=True)
        names = "|".join(re.escape(w).replace(r"\ ", r"\s*") for w in words)
        self._start = re.compile(rf"^\W*(?:{GREETING}\W+)?(?:{names})\b[\s,.!?:;-]*", re.IGNORECASE)
        self._greeted = re.compile(rf"\b{GREETING}\W+(?:{names})\b[\s,.!?:;-]*", re.IGNORECASE)
        self._end = re.compile(rf"[\s,]+(?:{names})\W*$", re.IGNORECASE)
        # His name anywhere at all: talked to or talked about, he answers
        self._anywhere = re.compile(rf"\b(?:{names})(?:'s)?\b", re.IGNORECASE)
        # Anywhere in a longer message, at the start of a sentence or clause, or set off by commas:
        # "Let's see here. Adam, what are you?"
        self._vocative = re.compile(rf"(?:(?<=^)|(?<=[.!?;:,]\s)|(?<=[.!?;:,])|(?<=,\sor\s)|(?<=,\sand\s))\s*(?:{GREETING}\W+)?"
                                    rf"(?P<name>{names})\b(?P<after>[,!?.:;]|\s+(?=[a-z]))", re.IGNORECASE)

    def add_alias(self, name: str) -> None:
        """Also wake on another name, e.g. the Discord account name. 'MiniMe' also matches 'mini me'."""
        spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).lower()
        self.aliases = tuple(dict.fromkeys([*self.aliases, name.lower(), spaced]))
        self.__post_init__()

    def check(self, text: str, speaker: str, now: float | None = None) -> WakeResult:
        now = time.monotonic() if now is None else now
        stripped = text.strip()
        for pattern in (self._start, self._greeted, self._end):
            match = pattern.search(stripped)
            if match and (pattern is not self._start or match.start() == 0):
                if pattern is self._end and MENTION_BEFORE.search(stripped[:match.start()]) \
                        and "," not in match.group(0):  # "What is my name, Adam?" is still said to him
                    continue  # "his name is Adam"
                rest = (stripped[:match.start()] + " " + stripped[match.end():]).strip(" ,")
                if not re.search(r"\w", rest):
                    greeting = re.match(r"^\W*(hi|hey|hello|yo)\b", match.group(0), re.I)
                    if greeting:
                        rest = greeting.group(1)
                rest = rest[:1].upper() + rest[1:] if rest else rest
                return WakeResult(True, rest, wake_only=not re.search(r"\w", rest))
        for match in self._vocative.finditer(stripped):
            before = stripped[:match.start()]
            if MENTION_BEFORE.search(before):
                continue
            # What he answers is what was said to him: from his name on ("Let's see here. Adam, what
            # are you?" -> "What are you?"), or what came before if the name closes the message.
            rest = stripped[match.end():].strip(" ,")
            if not re.search(r"\w", rest):
                rest = re.split(r"(?<=[.!?])\s+", before.strip(" ,"))[-1].strip(" ,")
            rest = rest[:1].upper() + rest[1:] if rest else rest
            return WakeResult(True, rest, wake_only=not re.search(r"\w", rest))
        if self._anywhere.search(stripped):  # "you want me to pull Adam in here?": about him counts too
            return WakeResult(True, stripped)
        other = re.match(r"^\W*([A-Z][a-z]+)\s*,", stripped)
        if other and other.group(1).lower() not in {self.name.lower(), *self.aliases} \
                and other.group(1).lower() not in ("okay", "ok", "so", "well", "yeah", "yes", "no", "hey", "alright"):
            return WakeResult(False, stripped)  # "Amy, you can't see him": said to someone else
        if now - self._last_answered.get(speaker, float("-inf")) <= self.follow_up_seconds                 and not FILLER.match(stripped):
            return WakeResult(True, stripped, by_follow_up=True)
        return WakeResult(False, stripped)

    def answered(self, speaker: str, now: float | None = None) -> None:
        """Open (or extend) the follow-up window for this speaker."""
        self._last_answered[speaker] = time.monotonic() if now is None else now
