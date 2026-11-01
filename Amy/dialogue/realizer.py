"""Grammatical surface realization. Clauses are built from frames and stored assertions with
agreement, tense, negation and referring expressions — not picked from canned answers."""

from db.repository import Repository
from dialogue.phrasing import PhraseDeck
from nlp.lexicon import article
from nlp.morphology import past, third_person
from nlp.semantic_frames import Frame, NounPhrase
from reasoning.proofs import CONFLICTING, NO, YES, Answer, Proof

PROBABLE = 0.75

# Who "I" and "you" are for the sentence being built: the bot itself, and whoever it's talking to.
PERSPECTIVE: dict = {"self": None, "self_name": None, "speaker": None, "speaker_name": None}


def set_perspective(self_id, self_name, speaker_id, speaker_name) -> None:
    PERSPECTIVE.update(self=self_id, self_name=self_name, speaker=speaker_id, speaker_name=speaker_name)


def agreement(subject: str, plural: bool) -> tuple[bool, bool]:
    """(plural verb agreement, first person) for a realized subject: 'I am', 'you are', 'he is'."""
    word = subject.strip().lower()
    if word == "i":
        return True, True
    if word == "you":
        return True, False
    return plural, False


def indefinite(head: str, modifiers=()) -> str:
    words = [*modifiers, head]
    return f"{article(words[0])} {' '.join(words)}"


def display_name(surface: str) -> str:
    return surface if any(ch.isupper() for ch in surface) else surface.title()


# Pronouns are stored as the speaker said them; when the reply repeats the sentence back, first and
# second person swap ("Can you hear me?" -> "whether I can hear you").
PRONOUN_SWAP = {"i": "you", "me": "you", "myself": "you", "my": "your", "mine": "yours",
                "you": "I", "your": "my", "yours": "mine", "yourself": "you"}
POSSESSIVE_SWAP = {"i": "your", "you": "my", "he": "his", "she": "her", "it": "its",
                   "they": "their", "we": "our"}


def np_text(np: NounPhrase | None, role: str = "subject") -> str:
    if np is None:
        return "something"
    if np.pronoun:
        word = (np.surface or np.pronoun).lower()
        if word in ("my", "mine", "your", "yours"):
            return PRONOUN_SWAP.get(word, word)
        if np.pronoun == "you":
            return "I" if role == "subject" else "me"
        if np.pronoun == "i":
            return "you"
        return np.surface or np.pronoun
    if np.proper:
        return display_name(np.surface)
    words = " ".join([*np.modifiers, np.head]) if not np.plural else np.surface.lower()
    if np.possessor is not None:
        owner = (POSSESSIVE_SWAP.get(np.possessor.pronoun)
                 or f"{np_text(np.possessor)}'s")
        return f"{owner} {words}"
    if np.determiner in (None,):
        return np.surface.lower()
    if np.determiner in ("a", "an") or (np.determiner in ("some", "any") and not np.plural):
        return indefinite(np.head, np.modifiers)
    return f"{np.determiner} {np.surface.lower()}"


def conjugate(lemma: str, tense: str | None, plural: bool, polarity: bool, modality: str | None = None,
              first_person: bool = False) -> str:
    if modality:
        if polarity:
            return f"{modality} {lemma}"
        return "cannot " + lemma if modality == "can" else f"{modality} not {lemma}"
    if lemma == "be":
        if first_person:
            form = "was" if tense == "past" else "am"
        else:
            form = ("were" if plural else "was") if tense == "past" else ("are" if plural else "is")
        return form if polarity else form + " not"
    if tense == "future":
        return f"will {lemma}" if polarity else f"will not {lemma}"
    if not polarity:
        aux = "did" if tense == "past" else ("do" if plural else "does")
        return f"{aux} not {lemma}"
    if tense == "past":
        return past(lemma)
    return lemma if plural else third_person(lemma)


def sentence(text: str) -> str:
    return text[:1].upper() + text[1:] + ("" if text.endswith((".", "?", "!")) else ".")


def join_clauses(clauses: list[str]) -> str:
    if len(clauses) <= 1:
        return "".join(clauses)
    if len(clauses) == 2:
        return f"{clauses[0]} and {clauses[1]}"
    return ", ".join(clauses[:-1]) + f", and {clauses[-1]}"


# --- stored knowledge -> text ------------------------------------------------------

def entity_text(repo: Repository, entity_id: int, definite: bool = False, role: str = "subject") -> str:
    if entity_id is not None and entity_id == PERSPECTIVE["self"]:
        return "I" if role == "subject" else "me"
    if entity_id is not None and entity_id == PERSPECTIVE["speaker"]:
        return "you"
    row = repo.entity_row(entity_id)
    if row["proper"]:
        return display_name(row["name"])
    kind = repo.concept_lemma(row["kind_concept_id"])
    if definite:
        owners = repo.assertions("rel:own", obj=("e", entity_id), polarity=True)
        if not owners or owners[-1]["subj_type"] != "e":
            return f"the {kind}"
        owner = owners[-1]["subj_id"]  # "my favorite color", "your truck", "Howard's dog"
        if owner == PERSPECTIVE["self"]:
            possessive = "my"
        elif owner == PERSPECTIVE["speaker"]:
            possessive = "your"
        else:
            possessive = f"{display_name(repo.entity_row(owner)['name'])}'s"
        favorite = [p for p in repo.properties(entity_id) if p in ("favorite", "favourite")]
        return " ".join([possessive, *favorite, kind])
    return indefinite(kind, repo.properties(entity_id))


def term_text(repo: Repository, kind: str, ident: int, definite: bool = False, role: str = "object") -> str:
    return entity_text(repo, ident, definite, role) if kind == "e" else indefinite(repo.concept_lemma(ident))


def realize_assertion(repo: Repository, row) -> str | None:
    predicate = row["predicate"]
    polarity = bool(row["polarity"])
    tense = row["temporal_scope"] or "present"
    args = repo.args(row["id"])
    literals = {a["role"]: a["literal"] for a in args if a["value_type"] == "lit"}

    if predicate == "instance_of" and not repo.entity_row(row["subj_id"])["proper"]:
        return None  # "a red truck is a truck" adds nothing
    plural = False
    if row["subj_type"] == "e":
        subject = entity_text(repo, row["subj_id"], definite=predicate == "has_property")
    elif "_subj" in literals:
        plural = literals["_subj"].startswith("pl:")
        subject = literals["_subj"][3:]
    else:
        subject = indefinite(repo.concept_lemma(row["subj_id"]))

    plural, first = agreement(subject, plural)
    if predicate in ("instance_of", "subclass_of"):
        obj = indefinite(repo.concept_lemma(row["obj_id"]))
        return f"{subject} {conjugate('be', tense, plural, polarity, first_person=first)} {obj}"
    if predicate == "has_property":
        prop = repo.concept_lemma(row["obj_id"])
        return f"{subject} {conjugate('be', tense, plural, polarity, first_person=first)} {prop}"

    verb = predicate[4:]
    parts = [subject, conjugate(verb, tense, plural, polarity, literals.get("modality"), first)]
    if not (row["obj_type"] == "c" and row["obj_id"] == repo.none_concept()):
        parts.append(literals.get("_obj") or term_text(repo, row["obj_type"], row["obj_id"]))
    for a in args:
        if a["role"].startswith("_") or a["role"] in ("modality", "time"):
            continue
        if a["value_type"] == "lit":
            parts.append(f"{a['role']} {a['literal']}")
        else:
            parts.append(f"{a['role']} {term_text(repo, a['value_type'], a['value_id'])}")
    if "time" in literals:
        parts.append(literals["time"])
    return " ".join(parts)


def spec_text(repo: Repository, spec) -> str:
    if spec[0] == "kind":
        words = " ".join([*spec[2], repo.concept_lemma(spec[1])])
        bare = len(spec) > 3 and spec[3]  # mass noun or plural as taught: "pet food", "cats"
        return words if bare else indefinite(repo.concept_lemma(spec[1]), spec[2])
    return term_text(repo, spec[0], spec[1])


def pattern_text(repo: Repository, pattern: dict, var_text: str | None, var_plural: bool = False) -> str:
    """Realize one rule condition/conclusion, with the variable shown as var_text."""
    if pattern["subject"] == "x":
        subject, plural = var_text, var_plural
    else:
        subject, plural = term_text(repo, *pattern["subject"]), False
    polarity = pattern["polarity"]
    if pattern["relation"] == "is_a":
        return f"{subject} {conjugate('be', 'present', plural, polarity)} {indefinite(repo.concept_lemma(pattern['obj'][1]))}"
    if pattern["relation"] == "property":
        props = join_clauses([repo.concept_lemma(p) for p in pattern["props"]])
        return f"{subject} {conjugate('be', 'present', plural, polarity)} {props}"
    parts = [subject, conjugate(pattern["verb"], "present", plural, polarity)]
    if pattern["obj"] is not None:
        parts.append(spec_text(repo, pattern["obj"]))
    for role, value in pattern["args"]:
        parts.append(value if role == "time" else f"{role} {value if isinstance(value, str) else spec_text(repo, value)}")
    return " ".join(parts)


def rule_text(repo: Repository, rule: dict) -> str:
    var = rule["variable"]
    conditions = rule["conditions"][1:] if var["typed"] and len(rule["conditions"]) > 1 else rule["conditions"]
    pronoun_plural = var["pronoun"] == "they" or var["plural"]
    cond = join_clauses([pattern_text(repo, p, var["word"], var["plural"]) for p in conditions])
    concl = join_clauses([pattern_text(repo, p, var["pronoun"], pronoun_plural) for p in rule["conclusions"]])
    return f"if {cond}, {concl}"


def derived_text(repo: Repository, proof: Proof) -> str | None:
    """The conclusion a rule produced, stated about the thing it was proved for."""
    if proof.derived is None:
        return None
    pattern, subject = proof.derived
    subject_text = term_text(repo, subject[0], subject[1]) if subject[0] in ("e", "c") else "something"
    return pattern_text(repo, pattern, subject_text)


def explain(repo: Repository, proof: Proof) -> str:
    clauses = [c for aid in proof.steps if (c := realize_assertion(repo, repo.assertion(aid)))]
    facts = join_clauses(list(dict.fromkeys(clauses)))
    rules = [rule_text(repo, repo.rule(rid)) for rid in proof.learned_rules]
    if not rules:
        return facts
    return ", and ".join([facts, *rules] if facts else rules)


def _fact_sentence(repo: Repository, proof: Proof) -> str | None:
    """A wh-answer line: a stated fact, or a derived one with its reason."""
    derived = derived_text(repo, proof)
    if derived:
        return f"{sentence(derived)} I know this because {explain(repo, proof)}."
    text = explain(repo, proof)
    return sentence(text) if text else None


def sources(repo: Repository, proof: Proof) -> set[str]:
    return {s for aid in proof.steps if (s := repo.source_speaker(aid))}


# --- frames (questions) -> text ------------------------------------------------------

def realize_frame(frame: Frame, polarity: bool | None = None, subject_text: str | None = None) -> str:
    pol = frame.polarity if polarity is None else polarity
    subject = subject_text or np_text(frame.subject)
    plural, first = agreement(subject, bool(frame.subject and frame.subject.plural and not subject_text))
    be = conjugate("be", frame.tense, plural, pol, frame.modality, first)
    if frame.relation == "is_a":
        return f"{subject} {be} {np_text(frame.obj, 'object')}"
    if frame.relation == "property":
        return f"{subject} {be} {join_clauses(list(frame.properties))}"
    parts = [subject, conjugate(frame.verb, frame.tense, plural, pol, frame.modality, first)]
    if frame.obj is not None:
        parts.append(np_text(frame.obj, "object"))
    for role, value in frame.args:
        if role == "time":
            continue
        parts.append(f"{role} {value if isinstance(value, str) else np_text(value, 'object')}")
    parts += [value for role, value in frame.args if role == "time"]
    return " ".join(parts)


def _wh_clause(frame: Frame, subject_text: str | None) -> str:
    wh = frame.wh or "what"
    if frame.slot == "subject":
        rest = realize_frame(frame, True, subject_text="\0").split("\0 ", 1)[-1]
        return f"{wh} {rest}"
    subject = subject_text or np_text(frame.subject)
    verb = "be" if frame.relation != "rel" else frame.verb
    tense = frame.tense
    plural, first = agreement(subject, False)
    if verb == "be":
        core = f"{subject} {conjugate('be', tense, plural, True, first_person=first)}"
    else:
        core = f"{subject} {conjugate(verb, tense, plural, True, frame.modality, first)}"
    if frame.obj is not None:
        core += f" {np_text(frame.obj, 'object')}"
    tail = f" {frame.slot}" if frame.slot not in ("object", "where", "when", "subject", None) else ""
    return f"{wh} {core}{tail}"


def realize_answer(repo: Repository, answer: Answer, subject_text: str | None = None) -> str:
    frame = answer.question
    if answer.status == "unknown" and answer.missing:
        lead = (f"I don't know anything about {subject_text or np_text(frame.subject)} yet."
                if frame.relation == "describe" else
                f"I don't know {_wh_clause(frame, subject_text)}." if frame.slot else
                f"I don't know whether {realize_frame(frame, True, subject_text)}.")
        return f"{lead} I don't know {join_clauses(answer.missing)}."

    if frame.relation == "describe":
        name = subject_text or np_text(frame.subject)
        facts = list(dict.fromkeys(sentence(c) for p in answer.support if (c := explain(repo, p))))
        if not facts:
            return f"I don't know anything about {name} yet."
        return f"Here's what I know about {name}: " + " ".join(facts)

    if frame.slot:
        facts = list(dict.fromkeys(f for p in answer.support if (f := _fact_sentence(repo, p))))
        if not facts:
            return f"I don't know {_wh_clause(frame, subject_text)}."
        hedge = "I think: " if answer.support[0].confidence < PROBABLE else ""
        return hedge + " ".join(facts[:8])

    positive = realize_frame(frame, True, subject_text)
    if answer.status == YES:
        best = answer.support[0]
        probable = best.confidence < PROBABLE
        parts = [f"{'Probably' if probable else 'Yes'}. {sentence(positive)}"]
        because = explain(repo, best)
        if because and because != positive:
            parts.append(f"I {'think so' if probable else 'know this'} because {because}.")
        return " ".join(parts)
    if answer.status == NO:
        negative = realize_frame(frame, False, subject_text)
        parts = [f"No. {sentence(negative)}"]
        because = explain(repo, answer.against[0])
        if because and because != negative:
            parts.append(f"I know this because {because}.")
        return " ".join(parts)
    if answer.status == CONFLICTING:
        pro, con = answer.support[0], answer.against[0]
        pro_src, con_src = sources(repo, pro), sources(repo, con)
        attribute = pro_src != con_src
        pro_txt = explain(repo, pro) + (f" (according to {', '.join(sorted(pro_src))})" if attribute and pro_src else "")
        con_txt = explain(repo, con) + (f" (according to {', '.join(sorted(con_src))})" if attribute and con_src else "")
        return (f"I have conflicting information about whether {positive}. "
                f"On one hand, {pro_txt}. On the other hand, {con_txt}.")
    text = f"I don't know whether {positive}."
    if answer.budget_exhausted:
        text += " I ran out of inference budget before finishing."
    return text


def realize_clarification(reason: str, supported: str, phrases: PhraseDeck | None = None) -> str:
    phrases = phrases if phrases is not None else PhraseDeck()
    return " ".join(p for p in (phrases.pick('misunderstood'), reason, supported) if p)
