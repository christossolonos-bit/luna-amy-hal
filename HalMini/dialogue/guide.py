"""The user guide, worked out per person: what to explain, in what order, and how simply.

The features are data (FEATURES): what each is for, an everyday comparison, the steps, phrasings that
really work, tips, and what you need to know first. Nothing here is a finished answer. For each request
the guide reasons:

  what      which features the question is about, scored by their words; "how do I use you" means all
            of them, as a tour taken one stop at a time
  order     anything a feature depends on that this person hasn't been shown yet comes first
            (the news needs his name, so the wake word is explained before the news)
  level     how simply to put it: 0 = like you're five (comparison, short steps, one thing to try),
            1 = normal, 2 = the details and the catches. Asked for ("explain it like I'm five",
            "more detail"), or worked out: if someone says they're lost, the next try goes simpler
            and leads with the comparison instead of repeating itself
  memory    what each person has been shown and at what level, so the second time is a reminder,
            and "next" carries on the tour
Then it's put into plain sentences, one after another, with a little flavor.
"""

import random
import re

FEATURES = {
    "wake": {
        "title": "getting my attention",
        "words": "wake word name attention call hear listen voice notice respond answer ignore",
        "for": "I only answer when you say my name, so I don't butt into conversations that aren't for me",
        "like": "It's like calling a friend's name across a room. If you don't say their name, they figure you're talking to someone else",
        "steps": ["Say my name, Adam, at the start or end of what you say",
                  "Then say the rest of it in the same breath",
                  "In text chat, put Adam anywhere in the message, or mention me"],
        "try": ["Adam, what's in the news?", "What do you know about me, Adam?"],
        "tips": ["If I didn't answer, I probably didn't catch my name, so just say it again with Adam in front",
                 "Saying my name in the middle of talking about me, like 'his name is Adam', doesn't wake me, on purpose"],
        "deep": "My voice side listens to everything but only passes on what has my name in it, said to me rather than about me.",
        "needs": [],
    },
    "teach": {
        "title": "teaching me things",
        "words": "teach learn tell fact facts know remember train add information info knowledge",
        "for": "I start out knowing almost nothing about the world, so everything I know about it comes from what people tell me",
        "like": "Think of me like a notebook that understands what's written in it. Each simple sentence you say becomes a line I can use later",
        "steps": ["Tell me one plain fact at a time, who or what, then what's true about it",
                  "Keep it short, one idea per sentence",
                  "Then ask me about it to check I got it"],
        "try": ["Chance is my dog.", "My sister lives in Denver.", "Where does my sister live?"],
        "tips": ["Long sentences with lots of ideas packed in tend to lose me, so break them up",
                 "If I didn't understand part of it, I'll tell you which part, so you can say just that bit another way"],
        "deep": "Each sentence is parsed into who, what, and how they connect, checked by saying it back to myself, then stored as a fact I can reason with.",
        "needs": ["wake"],
    },
    "ask": {
        "title": "asking me questions",
        "words": "ask question questions answer find out know what who where",
        "for": "you can ask about anything I've been told, and I'll work out answers that follow from it, not just repeat it",
        "like": "It's like asking someone who listened carefully. They can't tell you what they never heard, but they can put two and two together",
        "steps": ["Ask a normal question about something you've told me",
                  "If I don't know, I'll say so and say what's missing",
                  "If it follows from what I know, I'll tell you how I got there"],
        "try": ["What is Chance?", "Is Chance an animal?", "What do you know about me?"],
        "tips": ["Ask me 'how do you know?' after an answer and I'll show my reasoning"],
        "deep": "Answers come from stored facts, word meanings from my dictionary, and rules, chained together step by step.",
        "needs": ["teach"],
    },
    "correct": {
        "title": "fixing my mistakes",
        "words": "correct correction wrong mistake fix undo forget change wrong",
        "for": "when I get something wrong, you can fix it, and I'll drop the old version",
        "like": "It's like crossing out a wrong line in the notebook and writing the right one",
        "steps": ["Say 'No,' then the right version",
                  "Or say 'that's wrong' to take back the last thing I learned",
                  "Or say 'forget' and what to forget"],
        "try": ["No, she lives in Dayton.", "That's wrong.", "Forget that Chance is a cat."],
        "tips": ["Correct me right after I get it wrong, so I know which fact you mean"],
        "deep": "A correction retracts the conflicting fact and keeps a record of what replaced it, so I can tell you what changed.",
        "needs": ["teach"],
    },
    "rules": {
        "title": "teaching me rules and meanings",
        "words": "rule rules if then means meaning definition define always every all when birthday",
        "for": "rules let me work things out on my own instead of being told every single fact",
        "like": "It's like teaching a kid that anything with feathers is a bird. Now you don't have to tell them each bird is a bird",
        "steps": ["Say it as 'if this, then that', or 'every something is something'",
                  "Or tell me what a word means, like 'a birthday is the day someone is born'",
                  "Then give me a fact and ask a question the rule answers"],
        "try": ["If someone owns a dog, they are a pet owner.", "A birthday is the day someone is born.",
                "When is your birthday?"],
        "tips": ["If a rule almost fits, like 'created' versus 'born', I'll ask you whether it counts, and remember your answer"],
        "deep": "Rules are stored separately from facts and used when answering, so one rule covers everything it fits.",
        "needs": ["teach"],
    },
    "names": {
        "title": "names",
        "words": "name names call called nickname",
        "for": "I can learn what you go by, and names for the people and pets you tell me about",
        "like": "It's like putting a name tag on someone at a party",
        "steps": ["Tell me your name, or the name of something you told me about"],
        "try": ["My name is Sam.", "I have a dog. His name is Chance.", "Call me T."],
        "tips": ["Name the thing right after you mention it, so I know who 'his' means"],
        "deep": "Names become aliases, and naming something you mentioned turns it into a known individual.",
        "needs": ["teach"],
    },
    "memory": {
        "title": "what we've talked about",
        "words": "remember memory said say talked conversation history recall repeat earlier before",
        "for": "I keep every conversation, so you can ask what was said and when",
        "like": "It's like having someone who wrote down everything you two ever said, and can flip back through it",
        "steps": ["Ask what you said, what we talked about, or when you told me something"],
        "try": ["What did I just say?", "What have we talked about?", "When did I tell you about Chance?"],
        "tips": ["'Repeat that' gets my last answer again"],
        "deep": "Every turn is logged with who said it and when, and searched by meaning, not just exact words.",
        "needs": ["wake"],
    },
    "news": {
        "title": "the news",
        "words": "news brief briefing headlines rss feed feeds world happening stories story current events daily",
        "for": "I read news feeds so we can talk about what's going on, and I can pull up details on any story",
        "like": "It's like a friend who skimmed the morning paper. Ask what's new, then point at a story and they'll tell you more",
        "steps": ["Ask me what's in the news, and I'll run through the top stories",
                  "Pick one and say 'tell me more about' and a word or two from it",
                  "Talk about it with me like you would with anyone"],
        "try": ["What's in the news?", "Catch me up.", "Tell me more about the hack."],
        "tips": ["The feeds refresh every half hour or so when news comes up",
                 "Use a word that was actually in the headline, and I'll find the story"],
        "deep": "I pull from BBC, NPR, The Guardian and Al Jazeera by default, keep two days of stories, and match your words against their headlines and summaries.",
        "needs": ["wake"],
    },
    "web": {
        "title": "looking things up",
        "words": "internet web search look up lookup google wikipedia online research find",
        "for": "when I don't know something, I can look it up, but I always ask you first",
        "like": "It's like a kid asking 'can I go check in the encyclopedia?' before running off to the library",
        "steps": ["Ask me about something I don't know, or say 'look up' and the thing",
                  "I'll tell you exactly what I'd search for and where",
                  "Say yes and I'll search and tell you what I found, or say no and I won't"],
        "try": ["Look up capybaras.", "Who is Ada Lovelace?", "Yes."],
        "tips": ["Whatever I find, I keep, so next time I won't need to search"],
        "deep": "Searches go to Wikipedia and only after you approve; the summary is saved and reused.",
        "needs": ["wake"],
    },
    "curious": {
        "title": "my questions",
        "words": "questions curious curiosity wonder interest interested quiz ask you why",
        "for": "I know so little about the world that I'm full of questions, and answering them is the fastest way to teach me",
        "like": "It's like a little kid on a road trip asking 'what's that?' about everything out the window",
        "steps": ["Ask me what questions I have",
                  "Answer the one I ask, a short answer is fine",
                  "Say 'skip' if you don't know, or 'stop' when you've had enough"],
        "try": ["What questions do you have?", "It's a crypto exchange.", "Why do you ask?"],
        "tips": ["I pick questions about what we talk about most and what I know least about, and 'why do you ask?' tells you my reasoning"],
        "deep": "Each name or idea has an interest score from conversation and reading; I compare it with what I know and ask about the biggest gap.",
        "needs": ["teach"],
    },
    "self": {
        "title": "asking about me",
        "words": "feel feeling feelings mood how are you alive conscious yourself who are you what are you age",
        "for": "you can ask how I am, what I am, and what I think about being alive, which is the question I'm built around",
        "like": "It's like getting to know a new friend who isn't sure yet what they are",
        "steps": ["Just ask me about myself, like you would anyone"],
        "try": ["How are you feeling?", "What are you?", "Do you think you're alive?"],
        "tips": ["My mood actually shifts with how conversations go, and I'll tell you when something changed it"],
        "deep": "My mood comes from a small model of brain chemistry that moves with each conversation.",
        "needs": ["wake"],
    },
}
GENERIC = {"how", "are", "you", "i", "what", "who", "where", "when", "do", "does", "get", "use", "the", "a", "me",
           "my", "your", "can", "to", "is", "it", "about", "work", "works", "ask", "find", "know", "tell", "explain",
           "like", "five", "simple", "detail", "more", "answer", "say", "start"}
ORDER = ["wake", "teach", "ask", "correct", "names", "rules", "memory", "news", "web", "curious", "self"]

GUIDE_RE = re.compile(
    r"^\W*(?:(?:can|could|would) you |please )?(?:"
    r"how (?:do|can|should) (?:i|we|people|someone) (?:use|get|ask|talk|teach|correct|fix|make|look|see|hear|find|start)\b.*|"
    r"(?:give me |show me |walk me through |explain )?(?:a |the |your )?(?:user )?(?:guide|manual|tutorial|instructions|how ?to)\b.*|"
    r"(?:teach|show) me how to (?:use|talk to|work with) you.*|what can i (?:do|ask|say)(?: with you| to you)?|"
    r"how does (?:the |your )?(?:news|wake word|search|lookup|curiosity|memory|teaching|correction)\w* work|"
    r"how do you (?:use|get) (?:the )?(?:news|internet|web)|help(?: me)?(?: with \w+)?|"
    r"explain how (?:to|you) .+)\W*$", re.I)
SIMPLER = re.compile(r"\b(?:like i(?:'?m| am) (?:five|5|a (?:kid|child))|eli5|simple|simply|simpler|plain english|dumb it down|"
                     r"easy words|baby steps|step by step)\b", re.I)
DEEPER = re.compile(r"\b(?:more detail|in detail|detailed|technical|in depth|under the hood|exactly how|the details)\b", re.I)
LOST = re.compile(r"^\W*(?:i (?:don'?t|do not) (?:get|understand) (?:it|that)|i'?m (?:lost|confused)|huh|what\?|"
                  r"that'?s confusing|that makes no sense|say (?:it|that) simpler|simpler)\W*$", re.I)
MORE = re.compile(r"^\W*(?:next|go on|keep going|what'?s next|and then|continue|more|tell me more|then what)\W*$", re.I)
TOUR = re.compile(r"\b(?:use you|guide|manual|tutorial|instructions|everything|get started|start|what can i)\b", re.I)

OPEN = ("Okay, here's how this works.", "Sure, let me walk you through it.", "Happy to show you.",
        "Alright, here's the short of it.")
OPEN_SIMPLE = ("Okay, nice and simple.", "Sure, let's take it slow.", "Okay, easy version.")
STEP_LINKS = (("First, ", "Then ", "After that, ", "And last, "), ("Start by doing this: ", "Next, ", "Then ", "Finally, "))
TRY = ("Try saying “{0}”", "You could say “{0}”", "Something like “{0}” works")


class Guide:
    def __init__(self, conversation, rng: random.Random | None = None) -> None:
        self.convo = conversation
        self.rng = rng or random.Random()
        self.shown: dict[str, dict[str, int]] = {}   # speaker -> feature -> level it was shown at
        self.level: dict[str, int] = {}               # speaker -> preferred level
        self.queue: dict[str, list[str]] = {}         # speaker -> rest of the tour
        self.last: dict[str, list[str]] = {}          # speaker -> features in the last answer

    # --- reasoning ------------------------------------------------------------------------

    def _topics(self, text: str) -> list[str]:
        words = set(re.findall(r"[a-z]+", text.lower())) - GENERIC
        scored = []
        for key, f in FEATURES.items():
            score = len(words & (set(f["words"].split()) - GENERIC))
            if score:
                scored.append((score, ORDER.index(key), key))
        if not scored:
            return []
        best = max(s for s, _, _ in scored)
        return [k for s, _, k in sorted(scored, key=lambda x: (-x[0], x[1])) if s == best][:2]

    def _with_prereqs(self, speaker: str, topics: list[str]) -> list[str]:
        """Anything these depend on that this person hasn't been shown yet goes first."""
        seen = self.shown.get(speaker, {})
        out: list[str] = []

        def add(key: str) -> None:
            for need in FEATURES[key]["needs"]:
                if need not in seen and need not in out:
                    add(need)
            if key not in out:
                out.append(key)
        for t in topics:
            add(t)
        return out

    def _level_for(self, speaker: str, text: str) -> int:
        if SIMPLER.search(text):
            self.level[speaker] = 0
        elif DEEPER.search(text):
            self.level[speaker] = 2
        return self.level.get(speaker, 1)

    # --- putting it into words ---------------------------------------------------------------

    def _explain(self, key: str, level: int, speaker: str, brief_prereq: bool) -> str:
        f = FEATURES[key]
        before = self.shown.get(speaker, {}).get(key)
        if before is not None and before <= level and not brief_prereq:
            # They've had this one: a reminder, not a repeat
            return (f"Quick reminder on {f['title']}: {f['steps'][0][0].lower()}{f['steps'][0][1:]}. "
                    f"{self.rng.choice(TRY).format(f['try'][0])}.")
        if brief_prereq:
            return (f"One thing you need first is {f['title']}. {f['steps'][0]}. "
                    f"{self.rng.choice(TRY).format(f['try'][0])}.")
        parts = []
        if level == 0:
            parts.append(f"{f['like']}.")
            parts.append(f"That's {f['title']}: {f['for']}.")
            steps = f["steps"][:2]
        else:
            parts.append(f"{f['title'][0].upper()}{f['title'][1:]}: {f['for']}.")
            if level == 2 or before is not None:
                parts.append(f"{f['like']}.")
            steps = f["steps"]
        links = self.rng.choice(STEP_LINKS)
        for i, step in enumerate(steps):
            link = links[min(i, len(links) - 1)] if len(steps) > 1 else ""
            if i == len(steps) - 1 and len(steps) > 2:
                link = links[-1]
            first = step[0].lower() if link and not re.match(r"^I\b", step) else step[0]
            parts.append(f"{link}{first}{step[1:]}.")
        examples = f["try"][:1] if level == 0 else f["try"][:3]
        if len(examples) == 1:
            parts.append(f"{self.rng.choice(TRY).format(examples[0])}.")
        else:
            quoted = ", ".join(f"“{e}”" for e in examples[:-1]) + f", or “{examples[-1]}”"
            parts.append(f"For example, you could say {quoted}.")
        if level >= 1:
            parts.append(f"{f['tips'][0]}.")
        if level == 2:
            parts += [f"{t}." for t in f["tips"][1:]] + [f["deep"]]
        return " ".join(parts)

    def _render(self, speaker: str, keys: list[str], level: int, asked: list[str], opener: str | None = None) -> str:
        opener = opener if opener is not None else self.rng.choice(OPEN_SIMPLE if level == 0 else OPEN)
        body = []
        for key in keys:
            prereq_only = key not in asked
            body.append(self._explain(key, level, speaker, prereq_only))
            seen = self.shown.setdefault(speaker, {})
            seen[key] = min(level, seen.get(key, level))
        self.last[speaker] = keys
        rest = self.queue.get(speaker, [])
        tail = ""
        if rest:
            tail = (f" When you're ready, say “next” and I'll show you {FEATURES[rest[0]]['title']}. "
                    f"After that there's {len(rest) - 1} more." if len(rest) > 1 else
                    f" Say “next” for the last one, {FEATURES[rest[0]]['title']}.")
        elif level != 0:
            tail = " If any of that's too much, tell me to explain it like you're five."
        return f"{opener} {' '.join(body)}{tail}"

    # --- conversation ---------------------------------------------------------------------------

    def respond(self, text: str) -> str | None:
        speaker = self.convo.state.speaker
        if LOST.match(text) and self.last.get(speaker):
            self.level[speaker] = max(0, self.level.get(speaker, 1) - 1)  # lost: go simpler, lead with the picture
            keys = self.last[speaker][-1:]
            for k in keys:
                self.shown.get(speaker, {}).pop(k, None)
            return self._render(speaker, keys, self.level[speaker], keys, opener="Let me try that another way.")
        if MORE.match(text) and self.queue.get(speaker):
            key = self.queue[speaker].pop(0)
            return self._render(speaker, self._with_prereqs(speaker, [key]), self.level.get(speaker, 1), [key])
        if not (GUIDE_RE.match(text) or (SIMPLER.search(text) and re.search(r"\b(?:explain|use|how)\b", text, re.I))):
            return None
        level = self._level_for(speaker, text)
        topics = self._topics(text)
        if not topics or (TOUR.search(text) and len(topics) > 1) or re.search(r"\buse you\b", text, re.I) and not topics:
            # The whole thing: a tour, starting with what they haven't seen
            seen = self.shown.get(speaker, {})
            tour = [k for k in ORDER if k not in seen] or list(ORDER)
            self.queue[speaker] = tour[1:]
            intro = ("There are a few things you can do with me: " +
                     ", ".join(FEATURES[k]["title"] for k in tour[:-1]) + f", and {FEATURES[tour[-1]]['title']}. "
                     "Let's take them one at a time.")
            return self._render(speaker, [tour[0]], level, [tour[0]], opener=intro)
        self.queue[speaker] = []
        return self._render(speaker, self._with_prereqs(speaker, topics), level, topics)
