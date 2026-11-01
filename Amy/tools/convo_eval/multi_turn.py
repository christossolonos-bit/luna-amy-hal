"""Scripted multi-turn conversations: does he keep the thread?

Each conversation is a list of (what Travis says, checks). A check is a word the reply must contain
("+word"), must not contain ("-word"), or None for no check. Run: python -m tools.convo_eval.multi_turn
"""

import os
import sys
import tempfile

CONVERSATIONS = {
    "pronoun-three-back": [
        ("My sister is named Jen.", None),
        ("She lives in Denver.", None),
        ("I like pizza.", None),
        ("She works at a hospital.", None),
        ("Where does Jen work?", ["+hospital"]),
        ("Where does she live?", ["+Denver"]),
    ],
    "switch-and-return": [
        ("Chance is my dog.", None),
        ("He likes to swim.", None),
        ("My car is red.", None),
        ("Back to Chance, he is white.", None),
        ("Is Chance white?", ["+yes"]),
        ("What color is my car?", ["+red"]),
    ],
    "it-after-topic": [
        ("I bought a truck.", None),
        ("It is blue.", None),
        ("What color is my truck?", ["+blue"]),
        ("Is it blue?", ["+yes"]),
    ],
    "also-continuation": [
        ("Chance is a dog.", None),
        ("Also, he is white.", None),
        ("And he is friendly.", None),
        ("Is Chance friendly?", ["+yes"]),
    ],
    "late-correction": [
        ("Jen lives in Denver.", None),
        ("I like tacos.", None),
        ("My car is red.", None),
        ("Actually, Jen lives in Boston.", None),
        ("Where does Jen live?", ["+Boston", "-Denver"]),
    ],
    "what-were-we-talking-about": [
        ("Chance is my dog.", None),
        ("He is white.", None),
        ("What were we talking about?", ["+Chance"]),
    ],
    "topic-question": [
        ("Let's talk about Chance.", ["-couldn't", "-didn't follow"]),
        ("He is my dog.", None),
        ("What do you know about him?", ["+dog"]),
    ],
    "two-people": [
        ("My brother is named Sam.", None),
        ("My sister is named Jen.", None),
        ("Sam lives in Ohio.", None),
        ("Jen lives in Texas.", None),
        ("Where does Sam live?", ["+Ohio"]),
        ("And Jen?", ["+Texas"]),
    ],
    "fragment-answer-to-own-question": [
        ("Bob lives in Dayton.", None),
        ("Sam is my friend.", None),
        ("Where does Sam live?", None),
        ("Sam lives in Ohio.", None),
        ("Where does Sam live?", ["+Ohio"]),
    ],
    "about-what-i-said": [
        ("Chance is my dog.", None),
        ("He likes to swim.", None),
        ("What did I say about Chance?", ["+swim"]),
    ],
    "follow-up-why": [
        ("If something is a dog, it is an animal.", None),
        ("Chance is a dog.", None),
        ("Is Chance an animal?", ["+yes"]),
        ("How do you know?", ["+dog"]),
    ],
    "ellipsis-and-what-about": [
        ("Jen likes tacos.", None),
        ("Sam likes pizza.", None),
        ("What does Jen like?", ["+tacos"]),
        ("What about Sam?", ["+pizza"]),
    ],
    "his-question-answered-late": [
        ("Binance got hacked by North Korea.", None),
        ("What questions do you have?", ["+?"]),
        ("Hmm, let me think.", None),
        ("It's a country.", ["+country"]),
        ("What is North Korea?", ["+country"]),
    ],
    "mixed-topics": [
        ("Chance is my dog.", None),
        ("My sister is named Jen.", None),
        ("She lives in Denver.", None),
        ("Back to Chance, he is two.", None),
        ("Where does Jen live?", ["+Denver"]),
        ("What do you know about Chance?", ["+dog"]),
    ],
    "pronoun-after-question": [
        ("My brother is named Sam.", None),
        ("Where does Sam live?", None),
        ("He lives in Ohio.", None),
        ("Where does Sam live?", ["+Ohio"]),
    ],
}


def run(names=None, verbose=True) -> int:
    sys.path.insert(0, os.getcwd())
    from db.repository import Repository
    from app.conversation import Conversation
    failures = 0
    for name, turns in CONVERSATIONS.items():
        if names and name not in names:
            continue
        convo = Conversation(Repository(os.path.join(tempfile.mkdtemp(), "t.db")), speaker="Travis", self_name="Adam")
        convo.curiosity.rng.random = lambda: 1.0  # no surprise questions in scripted runs
        bad = []
        lines = []
        for said, checks in turns:
            reply = convo.handle(said, speaker="Travis").text
            lines.append(f"  > {said}\n    {reply[:220]}")
            for check in checks or []:
                word, want = check[1:], check[0] == "+"
                if (word.lower() in reply.lower()) != want:
                    bad.append(f"{said!r}: expected {'' if want else 'no '}'{word}'")
        failures += bool(bad)
        print(f"{'FAIL' if bad else 'ok  '} {name}")
        if bad and verbose:
            print("\n".join(lines))
            print("    -> " + "; ".join(bad))
    print(f"{len(CONVERSATIONS) - failures}/{len(CONVERSATIONS)} conversations kept the thread")
    return failures


if __name__ == "__main__":
    sys.exit(1 if run(sys.argv[1:] or None) else 0)
