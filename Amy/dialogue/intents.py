"""Understanding requests however they're phrased, not by one or two magic words.

Every request he handles (the news, a lookup, the guide, his questions, memory, small talk about
himself...) is an intent with a canonical phrasing that his handlers already understand, and a small
grammar of the ways people actually say it. The grammar is expanded into every combination, tens of
thousands of example phrasings, and each one is reduced to its meaningful words: contractions opened,
filler dropped ("um", "like", "hey Adam", "real quick"), words reduced to a stem, and words that mean
the same thing here put in one group ("check" ~ "look at" ~ "read" ~ "pull up").

A new message is reduced the same way and scored against every example by how much of its meaning
they share (rare words count for more). The best intent wins only if the message is mostly made of
that intent's words, so a fact like "Jen lives in Denver" never gets mistaken for a request. Words
that aren't part of the request are the topic ("any news on the Binance hack" -> topic "Binance hack"),
which goes into the canonical phrasing. The result is handed to the usual handlers as if it had been
said the canonical way.
"""

import itertools
import math
import re
from dataclasses import dataclass

# --- the grammar ---------------------------------------------------------------------------------------
# "(a|b)" is a choice, "[a|b]" an optional choice, "{x}" the topic. Shared pieces are named with <...>.

PIECES = {
    "<can>": "[can you|could you|would you|will you|please|i want you to|i need you to|go ahead and|i'd like you to]",
    "<tell>": "(tell me|give me|show me|let me know|fill me in on|catch me up on|run me through|walk me through)",
    "<what>": "(what is|what are|whats|what's)",
}

INTENTS: dict[str, dict] = {
    "news_brief": {
        "canonical": "What's in the news?",
        "say": [
            "<can> (check|read|look at|pull up|go through|get|grab|scan) [the|your|my|today's|some] [latest|recent|new|current|morning|daily] "
            "(news|headlines|rss feed|rss feeds|news feed|news feeds|feeds|stories|news stories)",
            "<can> <tell> [the|today's|some] [latest|recent|current|top] (news|headlines|stories|current events)",
            "<what> (going on|happening|new) (in the world|in the news|out there) [today|lately|right now]",
            "<what> [the|today's] [latest|top] (news|headlines|stories)",
            "(any|got any) [new|good|interesting|big] (news|headlines|stories) [today|lately]",
            "(daily brief|news brief|morning brief|briefing|the brief|news update|news rundown)",
            "<can> (brief me|update me|catch me up) [on the news|on what is going on|on the world]",
            "what [is|are] [the] (news|headlines|papers) (saying|reporting|talking about)",
        ],
    },
    "news_detail": {
        "canonical": "Tell me more about {x}",
        "slot": True,
        "say": [
            "<can> <tell> [more|the details] (about|on|regarding) [the] {x} [story|news|thing]",
            "(what about|how about|more on|whats the latest on|what is the latest on|any news on|any news about|"
            "any updates on|what happened with|what is going on with|whats going on with|what is happening with) [the] {x} [story]",
            "<can> (pull up|check on) [the] {x} (story|news|headline)",
            "(give me|<tell>) [the] (rundown|lowdown|scoop|details|story|latest) (on|about) [the] {x}",
            "what [is|are] [the] (news|papers|headlines) saying about [the] {x}",
        ],
    },
    "web_lookup": {
        "canonical": "Look up {x}",
        "slot": True,
        "say": [
            "<can> (look up|search for|search|google|research|find out about|look into|check) {x} "
            "(online|on the internet|on the web|on wikipedia)",
            "<can> (look up|google|research|wiki|look into|dig into|search) {x}",
            "<can> (search|check) (the internet|the web|online|wikipedia) (for|about) {x}",
            "what does (the internet|wikipedia|google|the web) say about {x}",
        ],
    },
    "guide_all": {
        "canonical": "How do I use you?",
        "say": [
            "how (do|can|should) (i|we|people|someone) (use|talk to|work with|operate|get started with) you",
            "<can> (give me|show me|walk me through|explain) [a|the|your] [user] (guide|manual|tutorial|instructions|rundown|basics|overview)",
            "<can> (teach me|show me|explain) how (to use you|you work|this works|to talk to you)",
            "what (can i do|can i say|can i ask|should i say|do i say) [to you|with you|here]",
            "(where do i start|how does this work|what are the commands|what commands do you know|help me|i need help)",
        ],
    },
    "guide_news": {"canonical": "How do I get the news?", "say": [
        "how (do|can) (i|we) (get|see|hear|check|read|use) [the] (news|headlines|feeds|rss|news feeds|daily brief)",
        "how (does|do) [the|your] (news|news feeds|rss|feeds|briefing) work"]},
    "guide_teach": {"canonical": "How do I teach you?", "say": [
        "how (do|can|should) (i|we) (teach|train|educate|tell) you [things|stuff|facts|something]",
        "how do you (learn|pick things up|get smarter)"]},
    "guide_lookup": {"canonical": "How do I look something up?", "say": [
        "how (do|can) (i|we) (get you to|make you|have you) (look up|search|google|research) [things|stuff|something]",
        "how (does|do) [the|your] (search|lookup|looking things up|internet search) work"]},
    "guide_correct": {"canonical": "How do I correct you?", "say": [
        "how (do|can) (i|we) (correct|fix|undo|change) [you|your mistakes|what you learned|something you got wrong]",
        "what do i do when you (get it wrong|make a mistake|are wrong)"]},
    "guide_wake": {"canonical": "How do I get your attention?", "say": [
        "how (do|can) (i|we) (get your attention|wake you|call you|get you to listen|get you to answer)",
        "(why do not you|why dont you|why will not you) (answer|respond|listen) [to me|sometimes]"]},
    "my_questions": {"canonical": "What questions do you have?", "say": [
        "<can> (ask me|give me) [some|your|a few] [of your] questions",
        "(what|which) [things] (questions do you have|are you curious about|are you wondering about|do you want to know|"
        "would you like to know|do you wonder about)",
        "(do you have|got) any questions [for me]",
        "(quiz me|lets do your questions|let us go through your questions|go ahead and ask)",
    ]},
    "why_ask": {"canonical": "Why do you ask?", "say": [
        "why (do you ask|are you asking|that question|ask that|do you want to know|do you care|does that matter)",
        "what (made you ask|makes you ask|got you asking) [that]",
    ]},
    "who_am_i": {"canonical": "Who am I?", "say": [
        "(who am i|do you know who i am|do you remember me|you know who this is|who is this|who are you talking to)",
    ]},
    "about_me": {"canonical": "What do you know about me?", "say": [
        "what (do you know|have you learned|have i told you|do you remember) about me",
        "<can> <tell> (what you know|everything you know) about me",
    ]},
    "conversation_summary": {"canonical": "What have we talked about?", "say": [
        "what (have we|did we|were we) (talk about|talked about|talking about|discuss|discussed|chat about|covered)",
        "(do you remember|can you remember|remind me) what we (talked about|discussed|were talking about)",
        "<can> (sum up|summarize|recap) [our|the|this] (conversation|chat|talk)",
    ]},
    "last_said": {"canonical": "What did I just say?", "say": [
        "what did i (just|last) (say|tell you|ask you|ask)",
        "(do you remember|did you catch|did you hear) what i (just|last) said",
    ]},
    "repeat": {"canonical": "Repeat that.", "say": [
        "<can> (repeat that|say that again|say it again|repeat yourself|run that by me again)",
        "(what did you say|come again|i missed that|i did not catch that|i didnt catch that)",
    ]},
    "how_are_you": {"canonical": "How are you?", "say": [
        "(how are you|how are you doing|how is it going|how are things|how have you been|how you doing|how are you holding up|"
        "you doing okay|you good|how is your day|how are you today)",
    ]},
    "what_are_you": {"canonical": "What are you?", "say": [
        "(what are you|who are you|who are you exactly|what exactly are you|what kind of thing are you|"
        "what is your deal|tell me about yourself|describe yourself)",
    ]},
    "feelings": {"canonical": "How do you feel?", "say": [
        "how (do you feel|are you feeling) [right now|today|about that|at the moment]",
        "(what mood are you in|how is your mood)",
    ]},
    "alive": {"canonical": "Are you alive?", "say": [
        "(are you alive|do you think you are alive|are you a living thing|are you real|is there life in you)",
    ]},
    "conscious": {"canonical": "Are you conscious?", "say": [
        "(are you conscious|are you sentient|are you aware|are you self aware|do you think you are conscious|"
        "do you have a mind|do you have consciousness|is anybody in there|is anyone home)",
    ]},
    "maker": {"canonical": "Who made you?", "say": [
        "who (made|created|built|programmed|designed|wrote) you", "(who is your creator|who is your maker|who owns you)"]},
    "age": {"canonical": "How old are you?", "say": [
        "(how old are you|what is your age|how long have you been around|how long have you existed|when did you start)"]},
    "thanks": {"canonical": "Thanks.", "say": [
        "(thanks|thank you|thank you so much|thanks a lot|i appreciate it|much appreciated|appreciate you|cheers) "
        "[for that|for the help|for asking|buddy|man|adam]"]},
    "goodbye": {"canonical": "Goodbye.", "say": [
        "(goodbye|bye|see you|see you later|talk to you later|catch you later|good night|night|i am heading out|"
        "i got to go|i have to go|i am out|later)"]},
    "meta_meaning": {"canonical": "What do you mean?", "say": [
        "(what do you mean|what did you mean|i do not understand you|i do not get it|that does not make sense|"
        "you lost me|what are you talking about|what are you saying|i am not sure what you mean|i do not follow)"
        " [by that|right now|there]"]},
}

# Words that mean the same thing for these purposes; everything in a group is matched as the first word.
SYNONYMS = [
    "check look read scan view see pull peek browse",
    "news headlines headline stories story feed feeds rss papers paper report reports update updates events",
    "tell give show let fill catch run walk brief inform update",
    "latest recent new current newest fresh top today todays morning daily",
    "search google lookup look research find wiki investigate",
    "internet web online wikipedia net",
    "use operate start",
    "guide manual tutorial instructions basics overview help commands",
    "teach train educate learn",
    "fix correct undo change",
    "talk discuss chat speak cover covered",
    "remember recall remind",
    "made created built programmed designed wrote make create build program design",
    "goodbye bye later night",
    "thanks thank appreciate cheers",
    "curious wonder wondering",
    "alive real",
    "conscious sentient aware",
    "happen happening going",
]
FILLER = re.compile(r"\b(?:um+|uh+|er+|hmm+|like|so|okay|ok|alright|all right|well|adam|minime|just|real quick|"
                    r"quickly|actually|basically|kind of|kinda|sort of|maybe|please|for me|right now|now|really|you know|"
                    r"i mean|man|dude|bro|buddy|honestly|then|even|heck|hell|the hell|the heck|earlier|out|there|again|exactly)\b", re.I)
CONTRACTIONS = {"what's": "what is", "whats": "what is", "it's": "it is", "don't": "do not", "dont": "do not",
                "didn't": "did not", "didnt": "did not", "i'm": "i am", "im": "i am", "you're": "you are",
                "can't": "can not", "cant": "can not", "won't": "will not", "let's": "let us", "lets": "let us",
                "how's": "how is", "who's": "who is", "that's": "that is", "doesn't": "does not", "isn't": "is not",
                "gotta": "got to", "wanna": "want to", "gonna": "going to", "y'all": "you", "ya": "you", "u": "you",
                "today's": "today", "i'd": "i would", "you've": "you have", "we've": "we have"}
STOP = {"the", "a", "an", "to", "of", "is", "are", "am", "be", "do", "does", "my", "your", "us", "our", "it", "that", "this", "some", "any", "on", "in", "for", "about", "with", "at", "and", "or",
        "could", "would", "will", "were", "was", "would", "go", "ahead", "there"}
# Two-word verbs mean one thing: "look up" isn't "look" plus "up"
PHRASAL = [("look up", "lookup"), ("looking up", "lookup"), ("search for", "search"), ("search up", "search"),
           ("find out about", "lookup"), ("find out", "lookup"), ("pull up", "pull"), ("look into", "research"),
           ("dig into", "research"), ("look at", "check"), ("check out", "check"), ("check on", "check"),
           ("go through", "check"), ("catch me up", "brief"), ("fill me in", "brief")]


def _stem(word: str) -> str:
    for end in ("ing", "ed", "es", "s"):
        if word.endswith(end) and len(word) - len(end) >= 3:
            stem = word[: -len(end)]
            return stem[:-1] if len(stem) > 3 and stem[-1] == stem[-2] and stem[-1] not in "ls" else stem
    return word


_GROUP: dict[str, str] = {}
for _line in SYNONYMS:
    _words = _line.split()
    for _w in _words:
        _GROUP.setdefault(_stem(_w), _stem(_words[0]))


def _words(text: str) -> list[str]:
    text = text.lower().replace("’", "'")
    text = " ".join(CONTRACTIONS.get(w, w) for w in text.split())
    for phrase, word in PHRASAL:
        text = re.sub(rf"\b{phrase}\b", word, text)
    text = FILLER.sub(" ", text)
    return re.findall(r"[a-z]+", text)


def meaning(text: str) -> list[str]:
    """The words that carry meaning, stemmed and grouped, in order."""
    return [_GROUP.get(_stem(w), _stem(w)) for w in _words(text)]


def _expand(template: str) -> list[str]:
    for key, value in PIECES.items():
        template = template.replace(key, value)
    parts = re.split(r"(\([^)]*\)|\[[^\]]*\])", template)
    choices = []
    for part in parts:
        if part.startswith("("):
            choices.append(part[1:-1].split("|"))
        elif part.startswith("["):
            choices.append([""] + part[1:-1].split("|"))
        elif part.strip():
            choices.append([part])
    out = []
    for combo in itertools.product(*choices):
        out.append(re.sub(r"\s+", " ", " ".join(combo)).strip())
    return out


@dataclass
class Match:
    intent: str
    score: float
    canonical: str
    topic: str | None


class IntentRouter:
    def __init__(self) -> None:
        self.examples: list[tuple[str, frozenset, bool]] = []  # (intent, meaning words, has slot)
        self.vocab: dict[str, set[str]] = {}
        seen = set()
        for name, intent in INTENTS.items():
            words_here: set[str] = set()
            for template in intent["say"]:
                for phrase in _expand(template):
                    has_slot = "{x}" in phrase
                    key = frozenset(w for w in meaning(phrase.replace("{x}", "")) if w not in STOP)
                    if (name, key) in seen or not key:
                        continue
                    seen.add((name, key))
                    self.examples.append((name, key, has_slot))
                    words_here |= key
            self.vocab[name] = words_here
        counts: dict[str, int] = {}
        for name, words in self.vocab.items():
            for w in words:
                counts[w] = counts.get(w, 0) + 1
        self.idf = {w: math.log(1 + len(INTENTS) / n) for w, n in counts.items()}

    @property
    def variant_count(self) -> int:
        return sum(len(_expand(t)) for i in INTENTS.values() for t in i["say"])

    def route(self, text: str) -> Match | None:
        raw = _words(text)
        said = [w for w in meaning(text) if w not in STOP]
        if not said:
            return None
        bag = set(said)
        best: tuple[float, str, frozenset, bool] | None = None
        for name, key, has_slot in self.examples:
            shared = bag & key
            weak = {"i", "you", "we", "me", "what", "how", "who", "can", "did", "happen", "check", "tell", "latest"}
            strong_key = key - weak
            if not shared or len(shared & strong_key) < min(2, len(strong_key)) or not (shared - weak):
                continue  # one shared everyday word is a coincidence, not a request
            weight = lambda ws: sum(self.idf.get(w, 1.0) for w in ws)
            recall = weight(shared) / weight(key)            # how much of the example is in the message
            if has_slot:
                # The request words have to be strong ones ("tell me more about", "look up"), not just "what";
                # what's left over is the topic, and it can't be most of the request's own words either.
                if weight(key) < 1.8 or recall < 1.0:
                    continue
                extra = bag - self.vocab[name]
                if not extra or len(extra) > 4:
                    continue
                precision = 0.9  # a topic in a strong frame: good, but a whole-request match beats it
            else:
                precision = weight(shared) / weight(bag)      # how much of the message is the example
            score = 2 * recall * precision / (recall + precision)
            if best is None or score > best[0]:
                best = (score, name, key, has_slot)
        if best is None or best[0] < 0.9:
            return None
        score, name, key, has_slot = best
        topic = None
        if has_slot:
            vocab = self.vocab[name] | STOP
            keep = [w for w, m in zip(raw, meaning(" ".join(raw))) if m not in vocab and w not in STOP]
            topic = " ".join(keep) or None
            if not topic or topic in ("world", "everything", "things", "stuff", "today", "lately", "you", "me", "i"):
                return None
        canonical = INTENTS[name]["canonical"].replace("{x}", topic or "")
        return Match(name, round(score, 3), canonical, topic)


ROUTER: IntentRouter | None = None


def router() -> IntentRouter:
    global ROUTER
    if ROUTER is None:
        ROUTER = IntentRouter()
    return ROUTER
