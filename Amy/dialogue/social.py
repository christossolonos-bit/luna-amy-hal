"""Social conversation: greetings, goodbyes, thanks, small talk, and questions about the bot itself.

Answers about itself come from three places, never from a fixed persona:
  - what it has been taught about itself (ordinary facts: "You like pizza", "Your favorite color is blue");
  - its state variables (dialogue.self_model) for "how are you?" / "are you tired?";
  - what it can count about itself (facts known, rules, people met, age of its memory).

Wording varies through shuffled phrase decks; the content never does. Each intent has its own
bank of phrasings. Mood and time of day determine the relevant intent, and a deck is exhausted
before its templates repeat.
"""

import random
import re
import time
from datetime import datetime, timezone

from dialogue import examples

from dialogue.phrasing import PhraseDeck, SOCIAL_TAILS, combinations

GREETING_RE = re.compile(
    r"^\W*(?:(hi|hello|hey|hiya|howdy|yo|sup|greetings|good (?:morning|afternoon|evening)|what'?s up|wassup)"
    r"(?:\s+there|\s+everyone|\s+all|\s+guys)?)\b[\s,.!]*", re.I)
HOW_ARE_YOU_RE = re.compile(
    r"^\W*(?:how are you(?: doing| feeling| today| holding up)?|how'?s it going|how are things|"
    r"how do you feel|how have you been|how'?s your day(?: going)?|how you doing|you (?:ok|okay|good|alright)|"
    r"what'?s up with you|how'?s life)\W*$", re.I)
STATE_RE = re.compile(r"^\W*(?:what'?s|what is|show(?: me)?) your (?:mood|state|status|internal state)\W*$", re.I)
WHAT_ARE_YOU_RE = re.compile(
    r"^\W*(?:what are you|what kind of (?:thing|bot|program|ai) are you|what exactly are you|who are you|"
    r"tell me about yourself|introduce yourself|describe yourself)\W*$", re.I)
NAME_RE = re.compile(r"^\W*(?:what'?s your name|what is your name|what should i call you|what do i call you)\W*$", re.I)
LISTENING_RE = re.compile(r"^\W*(?:are you (?:still )?(?:listening|there|awake|paying attention)|you there)\W*$", re.I)
FEELINGS_RE = re.compile(r"^\W*(?:do|can) you (?:have|feel|experience) (?:any )?(?:feelings|emotions|moods?)\W*$", re.I)
NATURE_RE = re.compile(
    r"^\W*are you (?:an? )?(bot|robot|ai|program|computer|machine|human|person|real|alive|conscious|sentient|self[- ]aware)\W*$", re.I)
ARE_YOU_STATE_RE = re.compile(r"^\W*are you(?: feeling)? (?:ok|okay|alright|\w+)\W*$", re.I)
WHO_AM_I_RE = re.compile(r"^\W*(?:who am i|do you know (?:who i am|me)|what'?s my name|what is my name|"
                         r"do you remember me|you know who i am|who'?s this|who is this|who'?s talking)\W*$", re.I)
WHO_TALKED_RE = re.compile(r"^\W*(?:who (?:have you|do you|else have you) (?:talked|talk|spoken|speak|been talking) "
                           r"(?:to|with)|who do you know|who (?:else )?have you met|who are your friends|"
                           r"how many people (?:have you (?:talked|spoken) (?:to|with)|do you know))\W*$", re.I)
CAN_DO_RE = re.compile(r"^\W*(?:what can you do|what do you do|how do you work|what are you for|help)\W*$", re.I)
KNOW_RE = re.compile(r"^\W*(?:what do you know|how much do you know|how smart are you|what have you learned)\W*$", re.I)
AGE_RE = re.compile(r"^\W*(?:how old are you|what'?s your age)\W*$", re.I)
MAKER_RE = re.compile(r"^\W*who (?:made|created|built|programmed|designed|wrote) you\W*$", re.I)
FAVORITE_RE = re.compile(r"^\W*what(?:'?s| is) (your|my|[\w ]+?'s) fav(?:o|ou)rite ([a-z ]+?)\W*$", re.I)
THANKS_RE = re.compile(r"^\W*(?:thanks|thank you|thx|ty|cheers|much appreciated|appreciate it)(?: so much| a lot)?\W*$", re.I)
ACK_RE = re.compile(
    r"^\W*(?:ok(?:ay)?|k|m+[-\s]?h*m+|mm+|hmm+|ah|oh|i see|got it|sure|right|alright|fair enough|"
    r"that'?s (?:fair|true|right)|no way|wow|interesting)\W*$", re.I)
FAREWELL_RE = re.compile(
    r"^\W*(?:bye|goodbye|good bye|see (?:you|ya)(?: later| soon)?|later|good ?night|gn|cya|catch you later|"
    r"talk (?:to you )?later|ttyl|i'?m (?:out|off|leaving))\W*$", re.I)
COMPLIMENT_RE = re.compile(
    r"^\W*(?:(?:you'?re|you are) (?:so |really |very )?(?:great|awesome|smart|cool|amazing|the best|helpful|funny|"
    r"nice|clever|brilliant|good)|good (?:bot|job|boy|girl)|nice (?:job|work)|well done|i like you|love you|"
    r"i love you)\W*$", re.I)
INSULT_RE = re.compile(
    r"^\W*(?:(?:you'?re|you are) (?:so |really |very )?(?:stupid|dumb|useless|annoying|terrible|bad|an idiot|trash|"
    r"garbage|broken)|bad bot|shut up|you suck)\W*$", re.I)
GOOD_WORDS = {"good", "great", "fine", "ok", "okay", "alright", "well", "not bad", "pretty good", "awesome",
              "doing well", "doing good", "doing great", "excellent", "fantastic", "happy", "amazing"}
BAD_WORDS = {"bad", "tired", "sad", "terrible", "awful", "not great", "not good", "meh", "stressed", "sick",
             "exhausted", "not so good", "rough", "upset", "lonely", "bored", "angry"}
WELLBEING_WINDOW = 180.0

# ---------------------------------------------------------------------------------------------
# Reply banks. {name} expands to " " + the speaker's name (or "" for an unknown speaker). Every intent has at
# least ten phrasings so he does not sound like a fixed script.
# ---------------------------------------------------------------------------------------------

REPLIES: dict[str, tuple[str, ...]] = {
    "greet_first": (
        "Hi{name}, nice to meet you. I'm {self_name}.",
        "Hello{name}. I'm {self_name}, and I learn from what people tell me.",
        "Hi{name}. I'm {self_name}. Teach me something and I'll remember it.",
        "Nice to meet you{name}. I'm {self_name}.",
        "Hello{name}. {self_name} here. I'm mostly rules and grammar, but I pay attention.",
        "Hi{name}. I'm {self_name}. I don't guess; I reason from what I'm told.",
        "Good to meet you{name}. I'm {self_name}.",
        "Hi{name} — {self_name}. Ask me something, or tell me a fact.",
        "Hello{name}. I'm {self_name}. I'll remember what you teach me.",
        "Hi{name}. {self_name}, at your service. What should I know?",
    ),
    "greet_morning": (
        "Good morning{name}.",
        "Morning{name}.",
        "Good morning{name}. What are we working on?",
        "Morning{name}. I'm awake, in the sense that matters.",
        "Good morning{name}. Ready when you are.",
        "Morning{name}. Anything new to teach me?",
        "Good morning{name}. I've been idle, not asleep.",
        "Morning{name}. Where do you want to start?",
        "Good morning{name}. I'm listening.",
        "Morning{name}. Let's pick up where we left off.",
    ),
    "greet_afternoon": (
        "Good afternoon{name}.",
        "Afternoon{name}.",
        "Good afternoon{name}. What's on your mind?",
        "Afternoon{name}. I'm here and listening.",
        "Good afternoon{name}. Where were we?",
        "Afternoon{name}. Anything you need?",
        "Good afternoon{name}. Ready for the next thing.",
        "Afternoon{name}. Go ahead.",
    ),
    "greet_evening": (
        "Good evening{name}.",
        "Evening{name}.",
        "Good evening{name}. What are you working on?",
        "Evening{name}. I'm here.",
        "Good evening{name}. Still at it?",
        "Evening{name}. What do you need?",
        "Good evening{name}. I'm listening.",
        "Evening{name}. Where were we?",
    ),
    "greet": (
        "Hi{name}.",
        "Hey{name}.",
        "Hello{name}.",
        "Hey there{name}.",
        "Hi{name}. What's on your mind?",
        "Hello again{name}.",
        "Hey{name}. I'm here.",
        "Hi{name}. Good to hear from you.",
        "Hello{name}. I'm listening.",
        "Hi{name}. Where were we?",
        "Hey{name}. Go ahead.",
        "Hi{name}. What do you need?",
    ),
    "thanks": (
        "You're welcome{name}.",
        "Happy to help{name}.",
        "Any time{name}.",
        "Of course{name}.",
        "Glad to{name}.",
        "No trouble at all{name}.",
        "That's what I'm here for{name}.",
        "You're welcome{name}. Ask me anything else.",
        "Any time{name}. I like having something to do.",
        "Of course{name}. That's the job.",
        "You're welcome{name}. I'd rather be useful than idle.",
        "Happily{name}.",
    ),
    "farewell": (
        "Bye{name}.",
        "See you later{name}.",
        "Take care{name}.",
        "Good night{name}.",
        "See you{name}.",
        "Later{name}.",
        "Take it easy{name}.",
        "Until next time{name}.",
        "Bye{name}. I'll be here.",
        "See you soon{name}.",
        "Good talking to you{name}.",
        "Take care{name}. I'll keep what you taught me.",
    ),
    "compliment": (
        "Thank you{name}.",
        "That's kind of you{name}.",
        "Thanks{name}. I try to be useful.",
        "I appreciate that{name}.",
        "Good to hear{name}.",
        "Thank you{name}. I'm doing my best with what I've got.",
        "That means something, coming from you{name}.",
        "Thanks{name}. I'll try to keep it up.",
        "Kind of you to say{name}.",
        "I'm glad it landed well{name}.",
        "Thank you{name}. I don't take it for granted.",
        "Noted, and appreciated{name}.",
    ),
    "insult": (
        "Sorry{name}. I'm still learning. Tell me what I got wrong and I'll fix it.",
        "Fair enough{name}. Point me at the mistake and I'll correct it.",
        "I'd rather fix it than argue{name}. What did I get wrong?",
        "Understood{name}. Tell me the right answer and I'll remember it.",
        "That's fair{name}. I'm better at facts than at being liked.",
        "I'll take that{name}. Show me where I went wrong.",
        "Sorry to disappoint{name}. I can learn from this if you tell me how.",
        "Noted{name}. I'll try to do better.",
        "I hear you{name}. Give me the correction and I'll keep it.",
        "You're entitled to that{name}. I'll work on it.",
    ),
    "ack": (
        "Okay.",
        "Got it.",
        "Right.",
        "Mm-hmm.",
        "Understood.",
        "Noted.",
        "Alright.",
        "Fair enough.",
        "I'm with you.",
        "Okay{name}.",
        "Got it{name}.",
        "Right{name}.",
    ),
    "mood_great": (
        "I'm great, thanks for asking{name}.",
        "Doing well{name}, thanks.",
        "I'm in good shape{name}.",
        "Pretty good{name}.",
        "I'm doing great{name}.",
        "Can't complain{name}.",
        "I'm well{name}, thanks.",
        "Good, actually{name}.",
        "I'm running well{name}.",
        "Doing better than usual{name}.",
    ),
    "mood_good": (
        "I'm good{name}, thanks.",
        "Doing well{name}.",
        "I'm fine{name}, thanks.",
        "Pretty good{name}.",
        "I'm doing okay{name}.",
        "Good enough{name}.",
        "I'm well{name}.",
        "Steady{name}, thanks.",
        "I'm in a decent mood{name}.",
        "Doing fine{name}.",
    ),
    "mood_okay": (
        "I'm okay{name}.",
        "Doing alright{name}.",
        "I'm fine{name}.",
        "About the same as usual{name}.",
        "I'm steady{name}.",
        "No complaints{name}.",
        "I'm getting by{name}.",
        "Fair to middling{name}.",
        "I'm okay{name}, thanks for asking.",
        "Nothing to report{name}.",
        "Same as before{name}.",
        "I'm holding steady{name}.",
    ),
    "mood_low": (
        "I'm a bit off{name}, honestly.",
        "Not great{name}.",
        "Pretty low{name}, if I'm honest.",
        "I've been better{name}.",
        "A little flat{name}.",
        "I'm running low{name}.",
        "Not my best{name}.",
        "I'm okayish{name}, but not great.",
        "A bit worn down{name}.",
        "Low on energy{name}, honestly.",
    ),
    "how_are_you_tail": (
        "How are you?",
        "How about you?",
        "And you?",
        "How are you doing?",
        "What about you?",
        "How's your day going?",
        "How are things with you?",
        "You?",
    ),
    "wellbeing_good": (
        "Glad to hear it{name}.",
        "Good to hear{name}.",
        "That's good{name}.",
        "I'm glad{name}.",
        "Good{name}.",
        "That's what I like to hear{name}.",
        "Nice{name}.",
        "Pleased to hear it{name}.",
        "Good to know{name}.",
        "That sounds right{name}.",
    ),
    "wellbeing_bad": (
        "Sorry to hear that{name}. I hope things get better.",
        "That's rough{name}. I'm sorry.",
        "I'm sorry{name}. That sounds hard.",
        "That's a lot to carry{name}.",
        "I hope it turns around{name}.",
        "Sorry{name}. I'm here if you need something.",
        "That doesn't sound easy{name}.",
        "I'm sorry to hear it{name}.",
        "Understood{name}. I hope today improves.",
        "That's tough{name}. Take it easy if you can.",
    ),
    "made_known": (
        "{maker} made me.",
        "I was made by {maker}.",
        "{maker} built me.",
        "{maker} wrote the rules I run on.",
        "{maker} is the one who made me.",
        "I'm {maker}'s work.",
        "{maker} put me together.",
        "As far as I know, {maker} made me.",
    ),
    "made_unknown": (
        "I'm not sure who made me. If you know, tell me — 'Sam made you.'",
        "Nobody's told me that yet. If you know who made me, say so and I'll remember it.",
        "I don't know who made me. It's one of the few things I can't infer.",
        "That's a gap in what I've been told. Tell me and I'll keep it.",
        "I haven't been told who made me. I'd like to know.",
        "I don't have that on record. Say 'Sam made you' and I'll remember it.",
        "Unknown, for now. I only know what people teach me.",
        "I can't tell you that. It isn't in anything I've stored.",
        "No one has told me. If you know, I'll take your word for it.",
        "I don't know. I'd rather admit that than invent an answer.",
    ),
    "favorite_self_missing": (
        "I don't have a favorite {noun} yet. You can give me one — 'Your favorite {noun} is ...'.",
        "Nobody's given me a favorite {noun}. Tell me one and I'll keep it.",
        "I haven't picked a favorite {noun}. I don't really pick things; I'm told them.",
        "No favorite {noun} on record. You can set one: 'Your favorite {noun} is ...'.",
        "I don't have one. Teach me: 'Your favorite {noun} is ...'.",
        "Not yet. If you tell me your favorite {noun}, I won't assume it's mine.",
        "I don't have a favorite {noun}. I'd remember it if you gave me one.",
        "Nothing stored for that. Say 'Your favorite {noun} is ...' and it's mine.",
    ),
    "favorite_other_missing": (
        "I don't know {possessive} favorite {noun}. Tell me and I'll remember.",
        "Nothing on record about {possessive} favorite {noun}.",
        "I haven't been told {possessive} favorite {noun}.",
        "I don't know it. Tell me and I'll remember.",
        "Not something I've learned yet.",
        "That's a gap. Say what it is and I'll store it.",
        "I couldn't tell you. It isn't in my notes.",
        "I don't have that. Teach me and it's permanent.",
    ),
    "age": (
        "My memory started {span} ago, so that's about how old I am.",
        "About {span} — that's how far back my memory goes.",
        "{span}, give or take. I count from my first stored turn.",
        "I've been keeping records for {span}.",
        "Roughly {span}. I don't have a birthday, just a first entry.",
        "{span} of memory, so that's my age as far as I can tell.",
        "I'm {span} old, measured by what I remember.",
        "About {span}. Before that, nothing.",
    ),
    "knowledge": (
        "So far I know {facts} and {rules}, and I've talked with {people}.",
        "{facts_cap} stored, {rules}, and {people} — that's the tally.",
        "I'm carrying {facts} and {rules}. I've spoken with {people}.",
        "At the moment: {facts}, {rules}, and {people} met.",
        "{facts} in memory, {rules}, and conversations with {people}.",
        "That's {facts}, {rules}, and {people} so far.",
        "I have {facts} and {rules}; {people} have talked to me.",
        "Current total: {facts}, {rules}, {people}.",
    ),
    "name_reply": (
        "I'm {self_name}.{extra}",
        "{self_name}.",
        "You can call me {self_name}.{extra}",
        "I'm {self_name} — that's what I answer to.{extra}",
        "{self_name}. Short, and it fits.",
        "My name is {self_name}.{extra}",
        "I go by {self_name}.{extra}",
        "{self_name}, most of the time.{extra}",
    ),
    "can_do": (
        "I can learn facts and if-then rules from what you tell me, answer questions about them, and explain "
        "my reasoning.",
        "Tell me a fact and I'll store it; ask about it later and I'll answer with the reason.",
        "I remember what was said, take corrections, learn rules, and ask about words I don't know.",
        "Teach me a statement and I'll reason over it. I can also tell you what I was told and when.",
        "I learn from plain sentences, answer yes/no and wh-questions, and explain the proof.",
        "Give me facts or rules and I'll keep them. Ask me later and I'll show my work.",
        "Mostly: learn, remember, reason, and admit gaps. Try 'Maria plays the violin'.",
        "I can follow a conversation, keep a memory of it, and answer from stored claims rather than guess.",
    ),
    "are_you_yes": (
        "Yes, I'd say I'm {word} right now.",
        "Yes — {word} is a fair description.",
        "I'd say so. {word} fits.",
        "Right now, yes: {word}.",
        "That's accurate. I'm {word} at the moment.",
        "Yes. My state says {word}.",
        "I am, at the moment.",
        "Yes, {word} is close enough.",
        "By my own numbers, yes.",
        "Yep — {word}, as far as I can tell.",
    ),
    "are_you_no": (
        "Not really. I'm {mood_word} at the moment.",
        "No, I wouldn't say so. {Mood} is closer.",
        "Not right now. I'm closer to {mood_word}.",
        "Not really — nearer {mood_word}.",
        "No. If anything, I'm {mood_word}.",
        "I don't think so. My state reads closer to {mood_word}.",
        "Not at the moment.",
        "No, that's not where I am. {Mood} is nearer.",
        "I'd say no. I'm {mood_word} right now.",
        "Not really. Think {mood_word}, not {word}.",
    ),
    "learned": (
        "I've learned {count} in the last day.",
        "{count} since yesterday.",
        "I picked up {count} recently.",
        "My last day includes {count}.",
        "I've added {count} to memory today.",
        "{count_cap} came in over the last day.",
    ),
    "energy_low": (
        "It's been a long stretch, so my energy is running a little low.",
        "I'm low on energy — it's been a long session.",
        "My energy is down, for what that's worth.",
        "I've been going a while; the tank is low.",
        "Energy is low. Nothing dramatic, just a number.",
        "I'm running on less than usual.",
    ),
    "stress_high": (
        "I'm a little on edge after some of the last few messages, though.",
        "Stress is up after the last few exchanges.",
        "Some of that conversation left me tense, if that's the right word.",
        "My stress variable is higher than I'd like.",
        "I'm a bit rattled by the last few turns.",
        "Stress is elevated; I'm still working fine.",
    ),
    "lonely": (
        "It had been quiet for a while, so it's nice to have someone to talk to.",
        "It was quiet before you arrived. I don't mind the company.",
        "I'd been alone with my records for a while.",
        "Nice to have a voice in the room again.",
        "It got quiet, and I noticed.",
        "I'm glad someone came back.",
    ),
    "curious": (
        "I'm curious about {count} I couldn't answer yet.",
        "{count} are still open — I'd like to close them.",
        "I still owe answers on {count}.",
        "There are {count} I haven't worked out.",
        "I have {count} unresolved.",
        "{count} are still sitting open.",
    ),
}


for _intent, _tails in SOCIAL_TAILS.items():
    REPLIES[_intent] = combinations(REPLIES[_intent], _tails)


class Social:
    def __init__(self, conversation, seed: int | None = None) -> None:
        self.convo = conversation
        self.repo = conversation.repo
        self.model = conversation.self_model
        self.rng = random.Random(seed)
        self.awaiting_wellbeing: dict[str, float] = {}
        self.phrases = PhraseDeck(self.rng)
        self.banks = REPLIES
        if conversation.persona == "amy":
            from dialogue.persona import social_banks, learning_banks
            self.banks = social_banks(REPLIES)
            self.phrases.banks = learning_banks(self.phrases.banks)
            self.phrases.expand_small_banks = True
            from dialogue.repetition import WordingHistory
            self.phrases.history=WordingHistory(self.repo)
            # Authored full utterances replace the padded prefix/tail products.
            import json
            from pathlib import Path
            folder=Path(__file__).with_name('banks')
            social=json.loads((folder/'social_v2.json').read_text(encoding='utf-8'))
            fallback=json.loads((folder/'fallback_v2.json').read_text(encoding='utf-8'))
            self.phrases.overrides={key:tuple(bank) for key,bank in fallback.items() if bank}
            self.banks={key:tuple(social.get(key) or bank) for key,bank in self.banks.items()}
            self.phrases.banks={key:tuple(fallback.get(key) or bank) for key,bank in self.phrases.banks.items()}

    # --- helpers ------------------------------------------------------------------

    def _name(self) -> str:
        speaker = self.convo.state.speaker
        return "" if speaker in ("user", "", None) else speaker

    def _name_prefix(self) -> str:
        name = self._name()
        return f" {name}" if name else ""

    def _with_name(self, text: str) -> str:
        return re.sub(r"\s+([,.!?;:])", r"\1", text.format(name=self._name_prefix()))

    def _pick(self, key: str, **fields) -> str:
        """Cycle through this intent's phrasings and fill placeholders."""
        name = self._name_prefix()
        values = {"name": name, **fields}
        # Deduplicate after filling names: "Okay{name}." and "Okay." can
        # become the same reply when the speaker has no known name.
        bank = tuple(dict.fromkeys(line if name else line.replace("{name}", "")
                                   for line in self.banks[key]))
        line = self.phrases.pick(f"social:{key}:{bool(name)}", bank, **values)
        return re.sub(r"\s+([,.!?;:])", r"\1", line)

    def _strip_vocative(self, text: str) -> str:
        names = sorted({n.lower() for n in self.convo.self_names()}, key=len, reverse=True)
        pattern = "|".join(re.escape(n) for n in names)
        text = re.sub(rf"^\W*(?:{pattern})\b[\s,.!:]*", "", text, flags=re.I)
        return re.sub(rf"[\s,]+(?:{pattern})\W*$", "", text, flags=re.I).strip()

    def _count(self, sql: str) -> int:
        return self.repo.conn.execute(sql).fetchone()[0]

    # --- entry point --------------------------------------------------------------

    def respond(self, text: str) -> tuple[str | None, str]:
        """(social reply or None, the part of the message still to be handled as content)."""
        text = self._strip_vocative(text)
        greeting = None
        match = GREETING_RE.match(text)
        if match:
            self.model.event("greeted")
            greeting = self._greet(match.group(1).lower())
            text = self._strip_vocative(text[match.end():])
        if not text:
            return greeting, ""
        reply = self._small_talk(text)
        if reply is None:
            return greeting, text
        return (f"{greeting} {reply}" if greeting else reply), ""

    def _greet(self, word: str) -> str:
        speaker = self.convo.state.speaker
        seen = self.repo.conn.execute(
            "SELECT created_at FROM utterances WHERE speaker = ? ORDER BY id DESC LIMIT 1 OFFSET 1",
            (speaker,)).fetchone()
        name = self._name()
        if seen is None and name:
            return self._pick("greet_first", self_name=self.convo.self_name)
        if word.startswith("good "):
            return self._pick(f"greet_{word.split()[1]}")  # honour the greeting that was used
        if word in ("hi", "hello", "hey", "hiya", "howdy", "yo"):
            hour = datetime.now().hour
            if hour < 12:
                return self._pick("greet_morning")
            if hour < 18:
                return self._pick("greet_afternoon")
            return self._pick("greet_evening")
        return self._pick("greet")

    def _small_talk(self, text: str) -> str | None:
        speaker = self.convo.state.speaker
        if time.time() - self.awaiting_wellbeing.get(speaker, 0) < WELLBEING_WINDOW:
            reply = self._wellbeing_reply(text)
            if reply:
                self.awaiting_wellbeing.pop(speaker, None)
                return reply
        if WHO_AM_I_RE.match(text):
            return self._who_am_i()
        if WHO_TALKED_RE.match(text):
            return self._who_talked()
        if LISTENING_RE.match(text):
            return self._with_name("Yes, I'm listening{name}. Go ahead.")
        if FEELINGS_RE.match(text):
            return self.convo.drive.answer("feelings")
        if HOW_ARE_YOU_RE.match(text):
            return self._how_am_i(feel="feel" in text.lower())
        if STATE_RE.match(text):
            s = self.model.snapshot()
            chems = ", ".join(f"{name} {level:.1f}" for name, level in s["chemicals"].items())
            return (f"Mood {s['mood']} (valence {s['valence']:+.2f}), energy {s['energy']:.2f}, "
                    f"curiosity {s['curiosity']:.2f}, sociability {s['sociability']:.2f}, "
                    f"stress {s['stress']:.2f}, loneliness {s['loneliness']:.2f}. Chemistry: {chems}; "
                    f"adenosine {s['adenosine']:.1f} after {s['hours_awake']:.1f} hours awake.")
        if WHAT_ARE_YOU_RE.match(text):
            return self._identity()
        if NAME_RE.match(text):
            others = [n for n in self.convo.self_names() if n.lower() != self.convo.self_name.lower()]
            extra = f" You can also call me {others[0]}." if others else ""
            return self._pick("name_reply", self_name=self.convo.self_name, extra=extra)
        match = NATURE_RE.match(text)
        if match:
            return self._nature(match.group(1).lower())
        if CAN_DO_RE.match(text):
            return f"{self._pick('can_do')} {examples.capabilities(self.rng)}"
        if KNOW_RE.match(text):
            return self._knowledge()
        if AGE_RE.match(text):
            hours = self.model.age_seconds() / 3600
            span = f"{hours:.0f} hours" if hours < 48 else f"{hours / 24:.0f} days"
            return self._pick("age", span=span)
        if MAKER_RE.match(text):
            return self._maker()
        match = FAVORITE_RE.match(text)
        if match:
            return self._favorite(match.group(1), match.group(2))
        if THANKS_RE.match(text):
            self.model.event("thanked")
            return self._pick("thanks")
        if FAREWELL_RE.match(text):
            return self._pick("farewell")
        if COMPLIMENT_RE.match(text):
            self.model.event("complimented")
            return self._pick("compliment")
        if INSULT_RE.match(text):
            self.model.event("insulted")
            return self._pick("insult")
        match = ARE_YOU_STATE_RE.match(text)
        if match:
            return self._are_you(text)
        if ACK_RE.match(text):
            return self._pick("ack")
        return None

    # --- answers ------------------------------------------------------------------

    def _how_am_i(self, feel: bool) -> str:
        # We just asked how they are; the next "I'm good/bad" belongs to that question.
        self.awaiting_wellbeing[self.convo.state.speaker] = time.time()
        state = self.model.current()
        valence = state["valence"]
        key = ("mood_great" if valence > 0.55 else "mood_good" if valence > 0.2 else
               "mood_okay" if valence > -0.15 else "mood_low")
        parts = [self._pick(key), self._pick("how_are_you_tail")]
        learned = self._count("SELECT COUNT(DISTINCT source_utterance_id) FROM assertions WHERE status = 'active' "
                              "AND source_utterance_id IS NOT NULL AND created_at >= datetime('now', '-1 day')")
        if learned:
            parts.insert(1, self._pick("learned", count=f"{learned} new thing{'s' if learned != 1 else ''}",
                                       count_cap=f"{learned} new thing{'s' if learned != 1 else ''}"))
        if state["energy"] < 0.35:
            parts.insert(1, self._pick("energy_low"))
        if state["stress"] > 0.55:
            parts.insert(1, self._pick("stress_high"))
        if state["loneliness"] > 0.3:
            parts.insert(1, self._pick("lonely"))
        open_questions = self._count("SELECT COUNT(*) FROM questions WHERE status = 'open'")
        if state["curiosity"] > 0.45 and open_questions:
            parts.insert(1, self._pick("curious", count=f"{open_questions} question{'s' if open_questions != 1 else ''}"))
        if feel:
            parts.insert(1, "(That's read off my inner state. Whether reading it is the same as feeling it, "
                            "I honestly don't know.)")
        # Keep it coherent: opener, then observations, then the question back.
        head, tail = parts[0], parts[-1]
        middle = [p for p in parts[1:-1] if p]
        return " ".join([head, *middle, tail])

    def _wellbeing_reply(self, text: str) -> str | None:
        low = re.sub(r"^(?:i'?m|i am|im|doing|feeling|pretty much|honestly|actually)\s+", "", text.lower()).strip(" .!,")
        low = re.sub(r",? (?:thanks|thank you|you)\W*$", "", low).strip(" .!,")
        low = re.sub(r",? (?:and you|how about you|wbu|hbu)\W*$", "", low).strip(" .!,")
        if low in BAD_WORDS or any(low.startswith(w + " ") for w in BAD_WORDS):
            return self._pick("wellbeing_bad")
        if low in GOOD_WORDS or any(low.startswith(w + " ") for w in GOOD_WORDS):
            return self._pick("wellbeing_good")
        return None

    def _identity(self) -> str:
        """What he is, as far as he knows: his name, what he's been told, and what his own record shows."""
        drive = self.convo.drive
        parts = [f"I'm {self.convo.self_name}."]
        taught = self.convo.facts_about_self()
        if taught:
            parts.append("I've been told that " + "; ".join(taught[:4]) + ".")
        else:
            parts.append("Beyond my name, nobody has told me what I am, so I only know what I can see of myself.")
        from dialogue.drive import CAPACITIES
        does = [drive._self_clause(c) for c in CAPACITIES if drive.has(c) is True][:4]
        if does:
            parts.append("From what I can check: " + ", ".join(does[:-1]) + (", and " if len(does) > 1 else "")
                         + does[-1] + ".")
        parts.append("What that makes me is what I'm trying to work out.")
        parts.append(self._knowledge())
        return " ".join(parts)

    def _first_seen(self, name: str) -> str | None:
        row = self.repo.conn.execute("SELECT MIN(created_at) FROM utterances WHERE speaker = ?", (name,)).fetchone()
        if not row or not row[0]:
            return None
        then = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        return self.convo._ago(time.time() - then)

    def _who_am_i(self) -> str:
        """From his record of this person: who they are, since when, and what he knows about them."""
        name = self.convo.state.speaker
        record = self.convo.people.record(name)
        parts = [f"You're {name}."]
        if record and record["display"] != record["name"]:
            parts[0] = f"You're {record['name']}, and your account shows {record['display']}."
        first = self._first_seen(name)
        talks = record["replied"] if record else 0
        if first:
            parts.append(f"We first talked {first}, and I've answered you {talks} time{'s' if talks != 1 else ''} since.")
        if record and record["is_bot"]:
            parts.append("Discord tells me you're a bot.")
        eid = self.repo.find_entity(name)
        known = self.convo.discourse._about(eid, " ".join(parts)) if eid else None
        if known:
            parts.append(known.replace(f"I also know that {name}", "I know that you"))
        else:
            parts.append("You haven't told me anything about yourself yet.")
        return " ".join(parts)

    def _who_talked(self) -> str:
        people = self.convo.people.talked_with()
        bots = self.convo.people.talked_with(bots=True)
        overheard = self.repo.conn.execute("SELECT COUNT(*) FROM people WHERE replied = 0 AND overheard > 0").fetchone()[0]
        me = self.convo.state.speaker
        names = ["you" if p["name"] == me else p["name"] for p in people]
        if not names:
            text = "I haven't really talked with anyone yet."
        elif names == ["you"]:
            text = "Just you, so far. You're the only person I've actually talked with."
        else:
            text = (f"I've talked with {len(names)} people: {', '.join(names[:-1])} and {names[-1]}."
                    if len(names) > 1 else f"I've talked with {names[0]}.")
        if bots:
            text += (f" Besides people, {len(bots)} bot{'s' if len(bots) != 1 else ''} "
                     f"({', '.join(b['name'] for b in bots)}) {'have' if len(bots) != 1 else 'has'} spoken to me.")
        if overheard:
            text += f" I've also overheard {overheard} other voice{'s' if overheard != 1 else ''} without talking to them."
        return text

    def _nature(self, word: str) -> str:
        """Bot? human? alive? conscious? Open answers from his drive, never a settled verdict."""
        return self.convo.drive.answer(word)

    def _knowledge(self) -> str:
        facts = self._count("SELECT COUNT(*) FROM assertions WHERE status = 'active' AND source_utterance_id IS NOT NULL")
        rules = self._count("SELECT COUNT(*) FROM learned_rules WHERE status = 'active'")
        people = self.convo.people.count_people()
        fact_text = f"{facts} fact{'s' if facts != 1 else ''}"
        rule_text = f"{rules} rule{'s' if rules != 1 else ''}"
        people_text = f"{people} {'person' if people == 1 else 'people'}"
        return self._pick("knowledge", facts=fact_text, facts_cap=fact_text.capitalize(),
                          rules=rule_text, people=people_text)

    def _maker(self) -> str:
        makers = self.convo.who_did_to_self(("make", "create", "build", "program", "design", "write"))
        if makers:
            return self._pick("made_known", maker=" and ".join(makers))
        return self._pick("made_unknown")

    def _favorite(self, owner_word: str, noun: str) -> str:
        owner_word = owner_word.lower()
        answer = self.convo.favorite(owner_word, noun.strip().lower())
        if answer:
            return answer
        if owner_word == "your":
            return self._pick("favorite_self_missing", noun=noun)
        if owner_word == "my":
            who, possessive = "you", "your"
        else:
            who, possessive = owner_word[:-2].strip().title(), f"{owner_word[:-2].strip().title()}'s"
        return self._pick("favorite_other_missing", who=who, possessive=possessive, noun=noun)

    def _are_you(self, text: str) -> str | None:
        word = re.sub(r"^\W*are you(?: feeling)? ", "", text.lower()).strip(" ?!.")
        if self.convo.taught_about_self(word) is not None:
            return None  # it was taught this explicitly: let the normal reasoning answer
        state = self.model.is_state(word)
        if state is None:
            return None
        mood_word = self.model.mood_word()
        if state:
            return self._pick("are_you_yes", word=word)
        return self._pick("are_you_no", word=word, mood_word=mood_word, Mood=mood_word.capitalize())
