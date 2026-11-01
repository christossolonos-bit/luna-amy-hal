"""1,000 generated conversations: scripted teach-and-ask cases with known right answers, plus noise,
slang, junk input, social talk and dictionary questions that only need to stay coherent."""

import random

NAMES = ["Howard", "Bob", "Alice", "Maria", "Chen", "Priya", "Sam", "Jordan", "Luis", "Fatima", "Grace", "Omar"]
SPEAKERS = ["Travis", "ultrahal", "Chris", "Dana", "kingskanyonbrooo"]
KINDS = [("truck", "vehicle"), ("sedan", "car"), ("beagle", "dog"), ("parrot", "bird"), ("guitar", "instrument"),
         ("oak", "tree"), ("laptop", "computer"), ("bicycle", "vehicle"), ("violin", "instrument"), ("cat", "animal")]
ADJ = ["red", "blue", "old", "new", "small", "big", "green", "shiny"]
PLACES = ["Ohio", "Dayton", "Texas", "Paris", "Columbus", "Denver", "Toronto", "Seattle"]
JOBS = [("bakery", "works at"), ("hospital", "works at"), ("school", "works at"), ("garage", "works at")]
JUNK = ["", "   ", "?", "!!!!", "...", "\U0001F602\U0001F602\U0001F602", "lol", "k", "lmao", "bruh", "asdfghjkl",
        "https://example.com/some/page?x=1", "```python\nprint('hi')\n```", "<@123456789012345678> yo",
        "42", "3.14159", "naïve café résumé", "**bold** _italic_ ~~strike~~", "hola como estas",
        "x" * 2000, "word " * 300, "WHAT IS GOING ON", "?????", "ok ok ok", "​", "the the the",
        "Is is is?", "Why?", "How?", "Because.", "No.", "Yes.", "Maybe.", "idk", "lol whats up bots",
        "gramps about to piss my ram off", "why u messing with me", "nah fam", "I'm tired of this",
        "You suck", "shut up", "fuck off", "Stop.", "Never mind.", "brb", "Can you hear me?"]
SOCIAL = ["Hi", "Hello Adam", "Hey Adam, how are you?", "Good morning", "Thanks!", "Thank you so much", "Bye",
          "What are you?", "Who are you?", "What's your name?", "Are you a bot?", "Are you listening?",
          "Do you have feelings?", "How are you feeling right now?", "What can you do?", "Who made you?",
          "Are you happy?", "You're awesome", "Good night", "What's up?", "How's it going?", "Are you real?"]
LEXICAL = ["How is a dog like a cat?", "What's the difference between a car and a bus?", "What is a tree made of?",
           "What are the parts of a house?", "What kinds of bird are there?", "What's another word for happy?",
           "What does run mean?", "What is a computer?", "Does a car have wheels?", "What is a leaf part of?",
           "Compare a violin and a guitar.", "Are a hammer and a nail related?", "What is an engine?",
           "Define gravity.", "What does the word serendipity mean?", "How is a river different from a lake?"]


def expect(description, fn):
    fn.description = description
    return fn


def yes():
    return expect("yes", lambda reply, status: status == "yes" or reply.startswith(("Yes", "Probably")))


def no():
    return expect("no", lambda reply, status: status == "no" or reply.startswith("No."))


def not_yes():
    return expect("not yes (unknown)", lambda reply, status: status != "yes" and not reply.startswith("Yes"))


def conflicting():
    return expect("conflicting", lambda reply, status: status == "conflicting")


def contains(*words, absent=()):
    return expect(f"contains {words}" + (f", not {absent}" if absent else ""),
                  lambda reply, status: all(w.lower() in reply.lower() for w in words)
                  and not any(a.lower() in reply.lower() for a in absent))


def a(word):
    return ("an " if word[0] in "aeiou" else "a ") + word


def light_noise(text, rng):
    """Changes that a person would still expect the bot to cope with."""
    choice = rng.randrange(7)
    if choice == 0:
        return text.lower()
    if choice == 1:
        return text.upper()
    if choice == 2:
        ends = ["!!", " :)", " \U0001F600", "..", " lol"] + (["??"] if text.endswith("?") else [])
        return text.rstrip(".?!") + rng.choice(ends)
    if choice == 3:
        return text.replace(" ", "  ", 1)
    if choice == 4:
        return rng.choice(["Adam, ", "Hey Adam, ", "@Adam ", "so ", "ok so "]) + text[0].lower() + text[1:]
    return text


def typo(text, rng):
    words = text.split()
    if not words:
        return text
    i = rng.randrange(len(words))
    w = words[i]
    if len(w) > 3:
        j = rng.randrange(1, len(w) - 1)
        w = w[:j] + w[j + 1] + w[j] + w[j + 2:]
    words[i] = w
    return " ".join(words)


def scripted(rng):
    """One conversation with known right answers."""
    n, n2 = rng.sample(NAMES, 2)
    kind, sup = rng.choice(KINDS)
    adj = rng.choice(ADJ)
    p1, p2 = rng.sample(PLACES, 2)
    sp = rng.choice(SPEAKERS)
    pron = "he" if n in ("Howard", "Bob", "Chen", "Sam", "Luis", "Omar") else "she"
    pron = "he" if pron == "he" else "she"
    kind_pl = kind + ("es" if kind.endswith("s") else "s")
    sup_pl = sup + ("es" if sup.endswith("s") else "s")
    templates = [
        ("teach-ask", [(f"{n} owns {a(adj + ' ' + kind)}.", None), (f"Does {n} own {a(kind)}?", yes())]),
        ("teach-superclass", [(f"{n} owns {a(kind)}.", None), (f"{kind_pl.capitalize()} are {sup_pl}.", None),
                              (f"Does {n} own {a(sup)}?", yes())]),
        ("negative", [(f"{n} doesn't own {a(kind)}.", None), (f"Does {n} own {a(kind)}?", no())]),
        ("unknown-person", [(f"{n} owns {a(kind)}.", None), (f"Does {n2} own {a(kind)}?", not_yes())]),
        ("where", [(f"{n} lives in {p1}.", None), (f"Where does {n} live?", contains(p1))]),
        ("correction", [(f"{n} lives in {p1}.", None), (f"No, {pron} lives in {p2}.", None),
                        (f"Where does {n} live?", contains(p2, absent=(p1,)))]),
        ("fragment-correction", [(f"{n} lives in {p1}.", None), (f"No, {p2}.", None),
                                 (f"Where does {n} live?", contains(p2, absent=(p1,)))]),
        ("pronoun", [(f"{n} drives {a(kind)}.", None), (f"Does {pron} drive {a(kind)}?", yes())]),
        ("rule", [("If someone owns a dog, they are a pet owner.", None), (f"{n} owns a dog.", None),
                  (f"Is {n} a pet owner?", yes())]),
        ("recall", [(f"{n} owns {a(kind)}.", None), ("What did I just say?", contains(n, kind))]),
        ("contradiction", [(f"{n} owns {a(kind)}.", None), (f"{n} doesn't own {a(kind)}.", None),
                           (f"Does {n} own {a(kind)}?", conflicting())]),
        ("what-does", [(f"{n} drives {a(adj + ' ' + kind)}.", None), (f"What does {n} drive?", contains(kind))]),
        ("job", [(f"{n} works at a {(job := rng.choice(JOBS)[0])}.", None), (f"Where does {n} work?", contains(job))]),
        ("did-i-tell", [(f"{n} owns {a(kind)}.", None), (f"Did I tell you that {n} owns {a(kind)}?", yes())]),
        ("forget", [(f"{n} owns {a(kind)}.", None), (f"Forget that {n} owns {a(kind)}.", None),
                    (f"Does {n} own {a(kind)}?", not_yes())]),
        ("multi-sentence", [(f"{n} lives in {p1}. {pron.capitalize()} drives {a(kind)}.", None),
                            (f"Does {n} drive {a(kind)}?", yes()), (f"Where does {n} live?", contains(p1))]),
        ("is-a", [(f"{n} is a teacher.", None), (f"Is {n} a teacher?", yes()), (f"Is {n} a doctor?", not_yes())]),
        ("property", [(f"{n} is tall.", None), (f"Is {n} tall?", yes())]),
    ]
    label, turns = rng.choice(templates)
    noisy = rng.random() < 0.35
    out = []
    for text, exp in turns:
        out.append((sp, light_noise(text, rng) if noisy else text, exp))
    return f"edge/{label}{'+noise' if noisy else ''}", out


def free(rng):
    """Conversations that only need to stay coherent."""
    sp = rng.choice(SPEAKERS)
    kind = rng.random()
    if kind < 0.3:
        turns = [(sp, rng.choice(JUNK)) for _ in range(rng.randint(1, 4))]
        return "edge/junk", turns
    if kind < 0.55:
        turns = [(sp, rng.choice(SOCIAL)) for _ in range(rng.randint(2, 5))]
        return "edge/social", turns
    if kind < 0.75:
        turns = [(sp, rng.choice(LEXICAL)) for _ in range(rng.randint(1, 3))]
        return "edge/lexical", turns
    if kind < 0.9:
        name, turns = scripted(rng)
        return name.replace("edge/", "edge/typo-"), [(s, typo(t, rng)) for s, t, _ in turns]
    # several people talking at once
    turns = []
    for _ in range(rng.randint(3, 7)):
        speaker = rng.choice(SPEAKERS)
        turns.append((speaker, rng.choice(SOCIAL + JUNK[:20] + [f"{rng.choice(NAMES)} owns {a(rng.choice(KINDS)[0])}."])))
    return "edge/group", turns


def generate(count: int, rng: random.Random) -> list:
    return [scripted(rng) if rng.random() < 0.5 else free(rng) for _ in range(count)]
