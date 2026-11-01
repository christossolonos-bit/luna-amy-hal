"""Shared variation for literal responses that bypass the social reply banks.

Only exact, inventoried canned responses are eligible. Never transforms generated
facts, quoted history, or arbitrary user text. Uses the conversation's shuffled deck.
"""

import json
from pathlib import Path
import re

FRAMES = (
    "{line}", "Let me put it this way: {lower}", "To put it plainly: {lower}",
    "Here's what I mean: {lower}", "Let me explain: {lower}",
    "In other words: {lower}", "Put another way: {lower}",
    "Here's the short version: {lower}", "To be clear: {lower}",
    "What I can say is this: {lower}", "Let me be direct: {lower}",
    "Here's my answer: {lower}", "Let me say it a little differently: {lower}",
    "The way I'd put it is: {lower}", "Here's how I'd explain that: {lower}",
)
DIRECT = {
    "No problem.": (
        "No problem.", "That's okay.", "No worries.", "That's alright.", "It's alright.",
        "No trouble.", "That's perfectly fine.", "Not a problem.", "That's fine with me.",
        "No problem at all.", "We can leave that open.", "It's okay not to know.",
        "We don't have to settle that now.", "We can come back to it.", "That's fine; we can move on."),
    "Okay.": (
        "Okay.", "Alright.", "Got it.", "I follow.", "I'm with you.", "Understood.",
        "Right.", "Noted.", "Fair enough.", "I hear you.", "That's clear.",
        "I see.", "I've got that.", "That makes sense.", "Okay, I follow."),
    "Take your time.": (
        "Take your time.", "There's no rush.", "No need to hurry.", "At your own pace.",
        "Whenever you're ready.", "You can take a moment.", "Go at a pace that suits you.",
        "We can take this slowly.", "A pause is fine.", "You don't need to rush an answer.",
        "I'm fine with a pause.", "We can leave some space for that.", "You can think it over.",
        "There's room to take a breath.", "We can come back to it when you're ready."),
}


def expand(lines, minimum=15):
    """Preserve each proposition while varying contractions and delivery wording."""
    if len(lines) == 1 and lines[0] in DIRECT:
        return DIRECT[lines[0]]
    out = list(dict.fromkeys(lines))
    if len(out) >= minimum:
        return tuple(out)
    for line in tuple(out):
        for before, after in (("I haven't", "I have not"), ("I don't", "I do not"),
                              ("I couldn't", "I could not"), ("I didn't", "I did not"),
                              ("I'll", "I will"), ("I'm", "I am"),
                              ("That's", "That is"), ("You're", "You are"),
                              ("Could you", "Would you"), ("Tell me", "You can tell me")):
            if before in line:
                out.append(line.replace(before, after))
    for frame in FRAMES[1:]:
        for line in tuple(lines):
            lower = line if line.startswith(("I ", "I'm", "I'll", "I've", "I'd")) else line[:1].lower() + line[1:]
            out.append(frame.format(line=line, lower=lower))
    return tuple(dict.fromkeys(out))


class ScriptedReplies:
    def __init__(self, deck):
        self.deck = deck
        path = Path(__file__).with_name("scripted_replies.json")
        self.banks = json.loads(path.read_text(encoding="utf-8"))

    def render(self, reply):
        bank = self.banks.get(reply.strip())
        if bank is None:
            return reply
        # Numbered keys do not contain arbitrary reply text or user identifiers.
        selected=self.deck.pick("literal:" + str(list(self.banks).index(reply.strip())), tuple(bank))
        return selected or reply  # Preserve the answer for text even if spoken wording is exhausted.
