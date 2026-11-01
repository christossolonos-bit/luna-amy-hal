"""1,000 long, natural conversations that use everything he can do, with checks on every answer.

Each conversation is built from a random little world (a person, their family, pets, jobs, places) and
runs through a random mix of what people actually do in a chat: introduce themselves in full sentences,
tell stories about family, mention pets, tell him things about himself, teach a rule, vent, change the
subject and come back, correct something from earlier, and then ask for it all back in different words,
several turns later. Every recall question has the word the answer must contain (and, after a
correction, the word it must not). Failures are grouped by which ability failed.

  python -m tools.convo_eval.long_convos --count 1000
"""

import argparse
import collections
import json
import os
import random
import sys
import tempfile
import time

NAMES = ["Jen", "Sam", "Maria", "Luis", "Priya", "Owen", "Grace", "Dana", "Marcus", "Elena", "Tyler", "Nina"]
KIN = ["sister", "brother", "friend", "cousin", "neighbor", "boss"]
CITIES = ["Denver", "Dayton", "Boston", "Austin", "Seattle", "Phoenix", "Tampa", "Chicago", "Portland", "Memphis"]
JOBS = [("nurse", "hospital"), ("teacher", "school"), ("baker", "bakery"), ("mechanic", "garage"),
        ("lawyer", "firm"), ("chef", "restaurant"), ("pilot", "airport"), ("farmer", "farm")]
PETS = [("dog", "Chance"), ("cat", "Milo"), ("dog", "Rex"), ("cat", "Luna"), ("horse", "Duke"), ("dog", "Bella")]
COLORS = ["blue", "green", "red", "purple", "orange", "yellow"]
FOODS = ["pizza", "tacos", "sushi", "pasta", "burgers", "curry"]
STATES = ["Alabama", "Texas", "Ohio", "Georgia", "Oregon", "Nevada"]
NUMBERS = ["two", "three", "four", "five", "six"]
CHAT = ["Honestly I'm pretty tired today, work was rough.", "It's been a long week, but I'm doing alright.",
        "I had a great morning, the weather was perfect.", "I'm kind of stressed about money lately.",
        "Man, traffic was terrible on the way home.", "I finally got some sleep last night, so that's good.",
        "Anyway, I wanted to talk to you for a bit.", "You know, I've been thinking about getting a new car."]


def build(rng: random.Random) -> list[tuple[str, str, list[str]]]:
    """[(ability, what's said, checks)]: a check is a word the reply must have, or '!word' it mustn't."""
    me_city, me_job = rng.choice(CITIES), rng.choice(JOBS)
    kin, kname = rng.choice(KIN), rng.choice(NAMES)
    k_city = rng.choice([c for c in CITIES if c != me_city])
    k_job = rng.choice([j for j in JOBS if j != me_job])
    pet, pname = rng.choice(PETS)
    age, color, food, state = rng.choice(NUMBERS), rng.choice(COLORS), rng.choice(FOODS), rng.choice(STATES)
    my_color = rng.choice([c for c in COLORS if c != color])
    new_city = rng.choice([c for c in CITIES if c not in (me_city, k_city)])
    she = "she" if kin in ("sister",) else "he" if kin in ("brother",) else rng.choice(["she", "he"])

    teach = {
        "intro": [("intro", f"Hey Adam, I live in {me_city} and I work as a {me_job[0]}.", []),
                  ("intro", f"I really like {food}, it's my favorite food.", [])],
        "family": [("family", f"My {kin} is named {kname}.", []),
                   ("family", f"{she.capitalize()} lives in {k_city} and {she} is a {k_job[0]}.", [])],
        "pet": [("pet", f"I have a {pet} named {pname}.", []),
                ("pet", f"{pname} is {age} years old.", [])],
        "self": [("self", f"You are from {state}.", []),
                 ("self", f"Your favorite color is {color}.", [])],
        "mine": [("mine", f"My favorite color is {my_color}.", [])],
    }
    recall = {
        "intro": [("recall-me", rng.choice(["Where do I live?", "Do you remember where I live?", "Where's my home?"]), [me_city]),
                  ("recall-me", rng.choice(["What do I do for work?", "What's my job?", "What do I work as?"]), [me_job[0]]),
                  ("recall-me", rng.choice(["What do I like?", "What's my favorite food?"]), [food])],
        "family": [("recall-family", rng.choice([f"Where does {kname} live?", f"Where does my {kin} live?"]), [k_city]),
                   ("recall-family", rng.choice([f"What does {kname} do?", f"What is {kname}?", f"What's {kname}'s job?"]), [k_job[0]]),
                   ("recall-family", rng.choice([f"Who is {kname}?", f"Who's my {kin}?"]), [kname if "my" in kin else kin])],
        "pet": [("recall-pet", rng.choice([f"What's my {pet}'s name?", f"What is my {pet} called?", f"Do you remember my {pet}'s name?"]), [pname]),
                ("recall-pet", rng.choice([f"How old is {pname}?", f"How old is my {pet}?"]), [age]),
                ("reason-dictionary", f"Is {pname} an animal?", ["yes"])],
        "self": [("recall-self", rng.choice(["Where are you from?", "Where do you come from?"]), [state]),
                 ("recall-self", "What's your favorite color?", [color])],
        "mine": [("recall-me", "What's my favorite color?", [my_color, f"!{color}"])],
    }
    # fix the "Who is X?" check: the answer should say what they are to me
    recall["family"][2] = ("recall-family", recall["family"][2][1], [kin] if "Who is" in recall["family"][2][1] else [kname])

    blocks = list(teach)
    rng.shuffle(blocks)
    turns: list[tuple[str, str, list[str]]] = []
    for b in blocks:
        turns += teach[b]
        if rng.random() < 0.5:
            turns.append(("chat", rng.choice(CHAT), ["!I've stored", "!I've recorded"]))
    # conversation-level abilities, mixed in
    extras = []
    if rng.random() < 0.6:
        extras += [("rule", f"If someone is a {k_job[0]}, they work at a {k_job[1]}.", []),
                   ("reason-rule", f"Does {kname} work at a {k_job[1]}?", ["yes"])]
    if rng.random() < 0.6:
        extras += [("correction", f"Actually, {kname} moved. {she.capitalize()} lives in {new_city} now.", []),
                   ("recall-correction", f"Where does {kname} live?", [new_city, f"!{k_city}"])]
    if rng.random() < 0.5:
        extras += [("topic", f"Anyway, back to {pname}, {'she' if pet == 'cat' else 'he'} loves playing outside.", []),
                   ("recall-topic", f"What does {pname} love?", ["play"])]
    if rng.random() < 0.5:
        extras += [("self-question", rng.choice(["How are you feeling today?", "How are you doing?", "Are you conscious?",
                                                  "What are you?"]), ["!didn't catch", "!couldn't work out"])]
    if rng.random() < 0.5:
        extras += [("conversation", rng.choice(["What have we talked about so far?", "What were we talking about?"]),
                    ["!didn't catch"])]
    if rng.random() < 0.4:
        extras += [("conversation", f"What did I tell you about {pname}?", [pname])]
    if rng.random() < 0.4:
        extras += [("followup", f"Where does {kname} live?", []), ("followup", "And what about me?", [me_city])]
    turns += extras
    # ask everything back, in a shuffled order, after all of that
    asks = [q for b in blocks for q in recall[b]]
    rng.shuffle(asks)
    return turns + asks


def run(count: int, seed: int, report: str) -> None:
    os.environ["HALMINI_WEB"] = "0"
    sys.path.insert(0, os.getcwd())
    from db.repository import Repository
    from app.conversation import Conversation
    rng = random.Random(seed)
    by_ability = collections.Counter()
    failed = collections.Counter()
    examples: dict[str, list] = collections.defaultdict(list)
    started, turns_total, clean = time.time(), 0, 0
    for n in range(count):
        convo = Conversation(Repository(os.path.join(tempfile.mkdtemp(), "t.db")), speaker="Travis", self_name="Adam")
        convo.curiosity.rng.random = lambda: 1.0
        ok = True
        for ability, said, checks in build(rng):
            turns_total += 1
            try:
                reply = convo.handle(said, speaker="Travis").text
            except Exception as exc:
                reply = f"CRASH {exc!r}"
                checks = checks + ["!CRASH"]
            if not checks:
                continue
            by_ability[ability] += 1
            bad = [c for c in checks if (c[1:].lower() in reply.lower()) if c.startswith("!")] + \
                  [c for c in checks if not c.startswith("!") and c.lower() not in reply.lower()]
            if bad:
                ok = False
                failed[ability] += 1
                if len(examples[ability]) < 12:
                    examples[ability].append({"said": said, "reply": reply[:220], "expected": checks})
        clean += ok
        convo.repo.close()
        if n % 100 == 0:
            print(f"{n}/{count} ({time.time() - started:.0f}s)", file=sys.stderr)
    summary = {"conversations": count, "turns": turns_total, "fully_clean": clean, "seconds": round(time.time() - started),
               "by_ability": {a: f"{by_ability[a] - failed[a]}/{by_ability[a]}" for a in sorted(by_ability, key=lambda a: -failed[a])}}
    json.dump({"summary": summary, "examples": examples}, open(report, "w", encoding="utf-8"), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--report", default=os.path.join("data", "datasets", "long_convos_report.json"))
    a = ap.parse_args()
    run(a.count, a.seed, a.report)
