"""English verb and noun morphology: analysis (form -> lemma) and generation (lemma -> form)."""

VOWELS = set("aeiou")

IRREGULAR_VERBS: dict[str, tuple[str, str]] = {  # lemma: (past, past participle)
    "be": ("was", "been"), "have": ("had", "had"), "do": ("did", "done"), "go": ("went", "gone"),
    "eat": ("ate", "eaten"), "drive": ("drove", "driven"), "ride": ("rode", "ridden"),
    "write": ("wrote", "written"), "see": ("saw", "seen"), "take": ("took", "taken"),
    "give": ("gave", "given"), "make": ("made", "made"), "buy": ("bought", "bought"),
    "sell": ("sold", "sold"), "build": ("built", "built"), "run": ("ran", "run"),
    "tell": ("told", "told"), "find": ("found", "found"), "know": ("knew", "known"),
    "think": ("thought", "thought"), "leave": ("left", "left"), "keep": ("kept", "kept"),
    "meet": ("met", "met"), "pay": ("paid", "paid"), "send": ("sent", "sent"),
    "spend": ("spent", "spent"), "teach": ("taught", "taught"), "catch": ("caught", "caught"),
    "bring": ("brought", "brought"), "fly": ("flew", "flown"), "grow": ("grew", "grown"),
    "draw": ("drew", "drawn"), "wear": ("wore", "worn"), "speak": ("spoke", "spoken"),
    "break": ("broke", "broken"), "choose": ("chose", "chosen"), "fall": ("fell", "fallen"),
    "feel": ("felt", "felt"), "hold": ("held", "held"), "lose": ("lost", "lost"),
    "sit": ("sat", "sat"), "stand": ("stood", "stood"), "win": ("won", "won"),
    "begin": ("began", "begun"), "swim": ("swam", "swum"), "sing": ("sang", "sung"),
    "drink": ("drank", "drunk"), "come": ("came", "come"), "get": ("got", "gotten"),
    "read": ("read", "read"), "put": ("put", "put"), "cut": ("cut", "cut"), "hit": ("hit", "hit"),
    "let": ("let", "let"), "set": ("set", "set"), "say": ("said", "said"),
    "hear": ("heard", "heard"), "sleep": ("slept", "slept"), "become": ("became", "become"),
    "forget": ("forgot", "forgotten"), "understand": ("understood", "understood"),
    "lead": ("led", "led"), "feed": ("fed", "fed"), "fight": ("fought", "fought"),
    "throw": ("threw", "thrown"), "hide": ("hid", "hidden"), "bite": ("bit", "bitten"),
    "shoot": ("shot", "shot"), "steal": ("stole", "stolen"), "wake": ("woke", "woken"),
    "lend": ("lent", "lent"), "dig": ("dug", "dug"), "hang": ("hung", "hung"),
    "mean": ("meant", "meant"), "deal": ("dealt", "dealt"), "bear": ("bore", "born"),
    "blow": ("blew", "blown"), "shake": ("shook", "shaken"), "sink": ("sank", "sunk"),
    "spin": ("spun", "spun"), "sting": ("stung", "stung"), "swing": ("swung", "swung"),
    "tear": ("tore", "torn"), "freeze": ("froze", "frozen"), "forgive": ("forgave", "forgiven"),
}
PAST_TO_LEMMA = {past: lemma for lemma, (past, _) in IRREGULAR_VERBS.items()}
PARTICIPLE_TO_LEMMA = {part: lemma for lemma, (_, part) in IRREGULAR_VERBS.items()}
THIRD_PERSON_IRREGULAR = {"be": "is", "have": "has", "do": "does", "go": "goes"}
THIRD_TO_LEMMA = {v: k for k, v in THIRD_PERSON_IRREGULAR.items()}

IRREGULAR_PLURALS = {
    "people": "person", "men": "man", "women": "woman", "children": "child",
    "mice": "mouse", "geese": "goose", "feet": "foot", "teeth": "tooth",
}


def lemmatize_noun(word: str) -> tuple[str, bool]:
    """Return (lemma, looks_plural)."""
    if word in IRREGULAR_PLURALS:
        return IRREGULAR_PLURALS[word], True
    if word.endswith(("ss", "us", "is")) or len(word) < 3 or not word.isalpha():
        return word, False
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y", True
    if word.endswith(("ches", "shes", "xes", "sses", "zzes")):
        return word[:-2], True
    if word.endswith("s"):
        return word[:-1], True
    return word, False


def pluralize(noun: str) -> str:
    reverse = {v: k for k, v in IRREGULAR_PLURALS.items()}
    if noun in reverse:
        return reverse[noun]
    if noun.endswith("y") and len(noun) > 1 and noun[-2] not in VOWELS:
        return noun[:-1] + "ies"
    if noun.endswith(("s", "sh", "ch", "x", "z")):
        return noun + "es"
    return noun + "s"


def third_person(lemma: str) -> str:
    if lemma in THIRD_PERSON_IRREGULAR:
        return THIRD_PERSON_IRREGULAR[lemma]
    if lemma.endswith("y") and len(lemma) > 1 and lemma[-2] not in VOWELS:
        return lemma[:-1] + "ies"
    if lemma.endswith(("s", "sh", "ch", "x", "z", "o")):
        return lemma + "es"
    return lemma + "s"


def _cvc(word: str) -> bool:
    """Short consonant-vowel-consonant words double their last letter: stop -> stopped."""
    return (len(word) >= 3 and sum(ch in VOWELS for ch in word) == 1
            and word[-1] not in VOWELS | set("wxy") and word[-2] in VOWELS and word[-3] not in VOWELS)


def gerund(lemma: str) -> str:
    """learn -> learning, have -> having, die -> dying, stop -> stopping, be -> being."""
    if lemma == "be":
        return "being"
    if lemma.endswith("ie"):
        return lemma[:-2] + "ying"
    if lemma.endswith("e") and not lemma.endswith(("ee", "ye", "oe")) and len(lemma) > 2:
        return lemma[:-1] + "ing"
    if _cvc(lemma):
        return lemma + lemma[-1] + "ing"
    return lemma + "ing"


def past(lemma: str) -> str:
    if lemma in IRREGULAR_VERBS:
        return IRREGULAR_VERBS[lemma][0]
    if lemma.endswith("e"):
        return lemma + "d"
    if lemma.endswith("y") and len(lemma) > 1 and lemma[-2] not in VOWELS:
        return lemma[:-1] + "ied"
    if _cvc(lemma):
        return lemma + lemma[-1] + "ed"
    return lemma + "ed"


def analyses(form: str) -> list[tuple[str, str]]:
    """Every plausible (lemma, tense) reading of a verb form, most specific first."""
    out: list[tuple[str, str]] = []
    if form in PAST_TO_LEMMA:
        out.append((PAST_TO_LEMMA[form], "past"))
    if form in PARTICIPLE_TO_LEMMA:
        out.append((PARTICIPLE_TO_LEMMA[form], "past"))
    if form in THIRD_TO_LEMMA:
        out.append((THIRD_TO_LEMMA[form], "present"))
    if form.endswith("ies") and len(form) > 4:
        out.append((form[:-3] + "y", "present"))
    if form.endswith("es"):
        out.append((form[:-2], "present"))
    if form.endswith("s") and not form.endswith("ss"):
        out.append((form[:-1], "present"))
    if form.endswith("ied"):
        out.append((form[:-3] + "y", "past"))
    if form.endswith("ing") and len(form) > 4:
        stem = form[:-3]
        out += [(stem, "present"), (stem + "e", "present")]
        if len(stem) > 2 and stem[-1] == stem[-2]:
            out.append((stem[:-1], "present"))
    if form.endswith("ed"):
        out += [(form[:-1], "past"), (form[:-2], "past")]
        if len(form) > 4 and form[-3] == form[-4]:
            out.append((form[:-3], "past"))
    out.append((form, "present"))
    return out


def guess(form: str) -> tuple[str, str]:
    """Best guess at an unknown verb's lemma from its suffix."""
    if form in PAST_TO_LEMMA:
        return PAST_TO_LEMMA[form], "past"
    if form.endswith("ies") and len(form) > 4:
        return form[:-3] + "y", "present"
    if form.endswith(("sses", "shes", "ches", "xes", "zes", "oes")):
        return form[:-2], "present"
    if form.endswith("s") and not form.endswith("ss") and len(form) > 3:
        return form[:-1], "present"
    if form.endswith("ied") and len(form) > 4:
        return form[:-3] + "y", "past"
    if form.endswith("ed") and len(form) > 4:
        stem = form[:-2]
        if stem[-1] == stem[-2] and stem[-1] not in "lsz":
            return stem[:-1], "past"
        if stem[-1] in "vcuz" or (len(stem) == 3 and stem[-2] in VOWELS and stem[0] not in VOWELS):
            return stem + "e", "past"
        return stem, "past"
    return form, "present"
