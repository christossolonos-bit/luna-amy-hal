"""Local conversational wording, with separate banks for separate meanings.

Compose complete, compatible sentences rather than swapping arbitrary synonyms.
Each conversation owns a shuffled deck per intent, so wording cannot repeat until
that deck is exhausted. No names, facts, or changing timestamps count as variants.
"""

import random
from itertools import product


def combinations(*parts: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(" ".join(p for p in row if p) for row in product(*parts)))


OPENERS = ("", "Sorry.", "I'm sorry.", "Hold on.", "One moment.",
           "Let me pause there.", "I need to pause there.", "Let me check what you mean.")
MISUNDERSTOOD = (
    "I didn't understand that.", "I didn't follow that.",
    "I'm not sure what you mean.", "I couldn't make sense of that sentence.",
    "I'm having trouble following that.", "I haven't worked out what you mean.",
    "That didn't quite make sense to me.", "I couldn't work out the meaning there.",
    "I'm missing the meaning of that.", "I haven't understood that clearly.",
    "I couldn't put that sentence together.", "I'm not following what you're saying.",
    "I can't quite tell what you mean.", "That wording has me stuck.",
    "I don't have a clear reading of that.", "I'm unsure how to interpret that.",
    "I couldn't get a clear meaning from that.", "I'm struggling with that wording.",
    "I haven't quite caught your meaning.", "I'm not sure how those words fit together.",
    "I couldn't follow the sentence as written.", "I'm not clear on what you're telling me.",
    "I can't reliably interpret that sentence.", "That sentence isn't clear to me yet.",
)
REPHRASE = (
    "Could you put that another way?", "Could you rephrase that?",
    "Can you say that more simply?", "Would you try a shorter sentence?",
    "Could you break that into smaller parts?", "Can you give me one idea at a time?",
    "Could you explain what you mean?", "Would you try different wording?",
    "Can you restate the main point?", "Could you give me a simpler version?",
    "Could you say that in a different way?", "Can you make the sentence more direct?",
    "Would you spell out the main idea?", "Could you tell me who or what you mean?",
    "Can you explain that with an example?", "Could you start with the main fact?",
    "Would you split that into separate sentences?", "Can you give me a more explicit version?",
    "Could you walk me through what you mean?", "Can you help me understand that wording?",
    "Would you try saying just the key part?", "Could you make the connection clearer?",
    "Can you tell me that in plain terms?", "Could you try a more straightforward sentence?",
)
TARGETED_BRIDGES = (
    "I need to check one part.", "Let me ask about a detail.",
    "There's something I'd like you to explain.", "I have a question about that.",
    "I need a little help with the meaning.", "Let me ask a more specific question.",
    "There's a detail I'm unsure about.", "I need to understand one piece first.",
    "Help me with the following part.", "I have something to clarify first.",
    "There's a gap I'd like to clear up.", "I need some context for this part.",
    "Let's start with something specific.", "Here's where I could use your help.",
    "Let me narrow down what I need to know.", "I need to ask you something about it.",
    "I could use an explanation of one part.", "There's a piece I need help with.",
    "I'd like to clarify the meaning before going on.", "I have a specific question first.",
    "I need to fill in a gap in my understanding.", "Let's clarify a detail.",
    "I'd like to check something before continuing.", "Here's what I need to understand first.",
)
LEARNED_OPENERS = (
    "Understood.", "Got it.", "Okay.", "Alright.", "Noted.", "I follow.",
    "Thanks.", "Thanks for telling me.", "I've got that.", "That makes sense.",
    "I understand.", "I've taken that in.",
)
LEARNED_LABELS = (
    "I've recorded this:", "I've noted this:", "I'll remember this:",
    "I've added this to my knowledge:", "I've kept this information:",
    "This is what I've recorded:", "Here's what I've taken from that:",
    "I've stored the following:", "I've made a note of this:",
    "I've saved this information:", "Here's what I'll remember:",
    "This is what I've learned:", "I've kept the following:",
    "Here's the information I've added:", "I've recorded the following:",
    "I've added the following to my memory:",
)
BANKS = {
    "misunderstood": combinations(OPENERS, MISUNDERSTOOD, REPHRASE),
    # A targeted follow-up already asks for the missing information.
    "misunderstood_targeted": combinations(OPENERS, MISUNDERSTOOD, TARGETED_BRIDGES),
    "learned": tuple(f"{p} {{fact}}." for p in combinations(LEARNED_OPENERS, LEARNED_LABELS)),
    "learned_many": tuple(f"{p} I learned {{count}} things: {{facts}}."
                          for p in LEARNED_OPENERS),
    "partial_failure": (
        "I couldn't understand: {details}", "I couldn't interpret this part: {details}",
        "This part wasn't clear to me: {details}", "I couldn't follow this part: {details}",
        "I still need help with this part: {details}", "I haven't understood this part: {details}",
    ),
    "nothing_learned": (
        "I didn't find anything to learn in that.", "I couldn't extract a fact from that.",
        "I haven't found a clear statement to remember there.",
        "I couldn't identify anything to add to my knowledge from that.",
        "I don't have a new fact to record from that.",
    ),
}

# These follow-ups are specific to the intent; a farewell never offers to keep
# chatting, and an acknowledgement never claims to have stored a new fact.
SOCIAL_TAILS = {
    "thanks": (
        "", "Let me know what else you need.", "Anything else on your mind?",
        "I'm here if you need another hand.", "We can keep going whenever you're ready.",
        "Happy to work through the next part.", "You can ask me something else.",
        "What would you like to do next?", "Just say if you need anything more.",
        "We can take the next bit together.", "I'm ready for another question.",
        "Let me know if anything still needs explaining.",
    ),
    "ack": (
        "", "Go ahead.", "I'm listening.", "You can keep going.", "What comes next?",
        "Take your time.", "I'm ready for the next part.", "What's next?",
        "Tell me more when you're ready.", "We can keep going.",
        "I'm here.", "You have my attention.",
    ),
    "farewell": (
        "", "Until we talk again.", "We can pick this up another time.",
        "I'll be here when you come back.", "Have a good rest of your day.",
        "Come back whenever you like.", "Talk to you another time.",
        "We can continue later.", "Thanks for the conversation.",
        "I look forward to our next conversation.", "See you next time.",
        "We'll talk again soon, I hope.",
    ),
    "compliment": (
        "", "I'm glad I could help.", "I'll keep working at it.",
        "It's good to be useful.", "Let me know what you'd like to do next.",
        "I'm glad that helped.", "There's always more for me to learn.",
        "I appreciate you saying so.", "I'll do my best with the next question too.",
        "I'm here for the next part.", "That was good to hear.",
        "Thanks for taking the time to say that.",
    ),
}


class PhraseDeck:
    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng if rng is not None else random.Random()
        self.remaining: dict[str, list[str]] = {}
        self.last: dict[str, str] = {}
        self.banks = BANKS

    def pick(self, key: str, bank: tuple[str, ...] | None = None, **fields) -> str:
        bank = self.banks[key] if bank is None else bank
        bank=getattr(self,'overrides',{}).get(key,bank)
        history=getattr(self,'history',None)
        if history is not None:
            scope='*'
            candidates=list(dict.fromkeys(bank))
            self.rng.shuffle(candidates)
            for line in candidates:
                rendered=line.format(**fields)
                if history.available(scope,line,rendered):
                    history.selected(scope,line,rendered)
                    return rendered
            self.exhausted=key
            return ''
        if getattr(self, "expand_small_banks", False) and len(bank) < 15:
            from dialogue.variation import expand
            bank = expand(bank)
        deck = self.remaining.setdefault(key, [])
        if not deck:
            deck.extend(bank)
            self.rng.shuffle(deck)
            if len(deck) > 1 and deck[-1] == self.last.get(key):
                deck[0], deck[-1] = deck[-1], deck[0]
        line = deck.pop()
        self.last[key] = line
        return line.format(**fields)
