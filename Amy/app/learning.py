"""Reference resolution and learning: noun phrases -> entities/concepts, frames -> assertions."""

from db.repository import ArgRecord, Repository, Term
from dialogue.state import DialogueState
from nlp.lexicon import DEFINITE_DETS, GENERIC_DETS, PERSON_QUANTIFIERS, article
from nlp.parser import _Parser
from nlp.semantic_frames import Frame, NounPhrase, RuleFrame


class Unsupported(Exception):
    pass


class Unresolved(Exception):
    def __init__(self, message: str, gap: str) -> None:
        super().__init__(message)
        self.gap = gap


def gap_for(np: NounPhrase) -> str:
    if np.proper:
        return f"anyone or anything called {np.surface}"
    return f"what {article(np.head)} {np.head} is"


class Resolver:
    """Maps noun phrases to stored terms, tracking what 'he', 'it' and 'the truck' refer to."""

    def __init__(self, repo: Repository, state: DialogueState, self_name: str) -> None:
        self.repo = repo
        self.state = state
        self.self_name = self_name
        self.uid: int | None = None
        self.cache: dict[int, object] = {}

    def begin(self, utterance_id: int) -> None:
        self.uid = utterance_id
        self.cache = {}

    def resolve(self, np: NounPhrase, role: str, create: bool, context: tuple | None = None):
        """Return a term ('e'|'c', id); when not creating, an indefinite NP yields a
        ('kind', concept_id, modifiers) spec. None when nothing matching is known."""
        # Keyed by identity, so the noun phrase itself is kept alongside: a freed object's id can be reused by
        # a new one, which would otherwise pick up the old one's answer ("an airport" -> California)
        key = id(np)
        hit = self.cache.get(key)
        if hit is None or hit[0] is not np:
            value = self._resolve(np, role, create, context)
            self.cache[key] = (np, value)
            self._note(value, np, role)
        return self.cache[key][1]

    def _pronoun(self, word: str) -> Term:
        if word in ("i", "we"):
            return ("e", self.repo.entity(self.state.speaker))
        if word == "you":
            return ("e", self.repo.entity(self.self_name))
        ref = self.state.last_thing if word == "it" else self.state.last_person
        if ref is None:
            raise Unresolved(f"I'm not sure who or what '{word}' refers to.",
                             f"who or what '{word}' refers to")
        return ref

    def category(self, np: NounPhrase, create: bool) -> int | None:
        """The concept a predicate noun names. 'a pet owner' is its own category, filed under 'owner'."""
        if not np.modifiers:
            return self._concept(np.head, create)
        compound = " ".join([*np.modifiers, np.head])
        if not create:
            return self.repo.find_concept(compound)
        cid = self.repo.concept(compound)
        self.repo.add_assertion(("c", cid), "subclass_of", ("c", self.repo.concept(np.head)), True, self.uid)
        return cid

    def _concept(self, lemma: str, create: bool) -> int | None:
        return self.repo.concept(lemma) if create else self.repo.find_concept(lemma)

    def _new_instance(self, kind: int, mods: tuple[str, ...]) -> int:
        item = self.repo.new_instance(kind)
        self.repo.add_assertion(("e", item), "instance_of", ("c", kind), True, self.uid)
        for mod in mods:
            self.repo.note_lexeme(mod, "adj")
            self.repo.add_assertion(("e", item), "has_property", ("c", self.repo.concept(mod)), True, self.uid)
        return item

    @staticmethod
    def is_generic(np: NounPhrase, role: str) -> bool:
        if role == "pred" or np.determiner in GENERIC_DETS:
            return True
        if np.determiner is None and np.possessor is None:
            return True  # bare plural or mass noun: "dogs", "water"
        return role == "subject" and np.determiner in ("a", "an")  # "A dog barks"

    def _resolve(self, np: NounPhrase, role: str, create: bool, context: tuple | None):
        if np.pronoun:
            return self._pronoun(np.pronoun)
        if np.quantifier:
            if create:
                raise Unsupported(f"I can't store a fact about an unspecified '{np.quantifier}' yet; "
                                  "try a rule like 'If someone ..., they ...'.")
            return None
        if np.proper:
            eid = self.repo.entity(np.surface) if create else self.repo.find_entity(np.surface)
            return ("e", eid) if eid else None
        if create:
            self.repo.note_lexeme(np.head, "noun", np.surface.split()[-1].lower())
        if role == "pred":
            cid = self.category(np, create)
            return ("c", cid) if cid else None
        kind = self._concept(np.head, create)
        if kind is None:
            return None

        if np.possessor is not None:
            owner = self.resolve(np.possessor, "subject", create)
            if owner is None or owner[0] != "e":
                return None
            item = self.repo.related_instance(owner, "rel:own", kind, np.modifiers)
            if item is None:
                candidates = [r["obj_id"] for r in self.repo.assertions("rel:own", subj=owner, polarity=True)
                              if r["obj_type"] == "e" and self.repo.entity_row(r["obj_id"])["kind_concept_id"] == kind]
                item = candidates[-1] if candidates else None
            if item is None:
                if not create:
                    return None
                item = self._new_instance(kind, np.modifiers)
                self.repo.add_assertion(owner, "rel:own", ("e", item), True, self.uid)
            return ("e", item)

        if self.is_generic(np, role):
            return ("c", kind)

        if np.determiner in DEFINITE_DETS:
            item = self.state.recent.get(kind)
            if item is None:
                existing = self.repo.instances_of(kind)
                item = existing[0] if len(existing) == 1 else None
            if item is None:
                if not create:
                    return None
                item = self._new_instance(kind, np.modifiers)
            elif create:
                for mod in np.modifiers:
                    self.repo.add_assertion(("e", item), "has_property", ("c", self.repo.concept(mod)), True, self.uid)
            return ("e", item)

        if not create:
            return ("kind", kind, np.modifiers)
        item = None
        if context is not None:
            item = self.repo.related_instance(context[0], context[1], kind, np.modifiers)
        return ("e", item or self._new_instance(kind, np.modifiers))

    def _note(self, term, np: NounPhrase, role: str) -> None:
        if not term or term[0] != "e":
            return
        if np.pronoun in ("i", "you"):
            return
        if (np.proper or np.pronoun in ("he", "she", "they")) and role == "subject":
            self.state.last_person = term
        else:
            self.state.last_thing = term
            row = self.repo.entity_row(term[1])
            if row["kind_concept_id"] is not None:
                self.state.recent[row["kind_concept_id"]] = term[1]


class Learner:
    def __init__(self, repo: Repository, resolver: Resolver) -> None:
        self.repo = repo
        self.resolver = resolver

    def learn(self, frame: Frame, uid: int) -> list[int]:
        """Store a statement; returns the ids of the primary assertions it produced."""
        if frame.subject is None:
            raise Unsupported("That statement has no subject I can attach it to.")
        pol, conf, tense = frame.polarity, frame.confidence, frame.tense
        subj = self.resolver.resolve(frame.subject, "subject", True)

        if frame.relation == "is_a":
            if frame.obj.proper:
                raise Unsupported("I can't learn that two names refer to the same thing yet.")
            self.repo.note_lexeme(frame.obj.head, "noun")
            kind = self.resolver.category(frame.obj, True)
            if subj == ("c", kind):
                return []
            predicate = "instance_of" if subj[0] == "e" else "subclass_of"
            return [self.repo.add_assertion(subj, predicate, ("c", kind), pol, uid, conf, tense)]

        if frame.relation == "property":
            ids = []
            when = [(role, "lit", None, value if isinstance(value, str) else value.surface)
                    for role, value in frame.args
                    if role in ("time", "on", "in", "at") and value is not None
                    and (isinstance(value, str) or value.proper or value.head in ("today", "yesterday", "tomorrow")
                         or value.head.isdigit())]
            for prop in frame.properties:
                self.repo.note_lexeme(prop, "adj")
                ids.append(self.repo.add_assertion(subj, "has_property", ("c", self.repo.concept(prop)),
                                                   pol, uid, conf, tense, when))
            return ids

        predicate = f"rel:{frame.verb}"
        self.repo.note_lexeme(frame.verb, "verb")
        args: list[ArgRecord] = []
        if subj[0] == "c":
            surface = frame.subject.surface.lower() if frame.subject.plural else \
                f"{article(frame.subject.head)} {frame.subject.head}"
            args.append(("_subj", "lit", None, ("pl:" if frame.subject.plural else "sg:") + surface))

        if frame.obj is None:
            obj = ("c", self.repo.none_concept())
        elif not pol and not frame.obj.proper and not frame.obj.pronoun and frame.obj.possessor is None \
                and frame.obj.determiner not in DEFINITE_DETS:
            obj = ("c", self.repo.concept(frame.obj.head))  # "doesn't own a boat": no boat at all
        else:
            obj = self.resolver.resolve(frame.obj, "object", True, context=(subj, predicate))
        if obj[0] == "c" and frame.obj is not None and Resolver.is_generic(frame.obj, "object"):
            args.append(("_obj", "lit", None, frame.obj.surface.lower()))

        for role, value in frame.args:
            if isinstance(value, str):
                args.append((role, "lit", None, value))
            elif value is not None:
                term = self.resolver.resolve(value, "arg", True)
                args.append((role, term[0], term[1], None))
        if frame.modality:
            args.append(("modality", "lit", None, frame.modality))
        return [self.repo.add_assertion(subj, predicate, obj, pol, uid, conf, tense, args)]


PERSON_WORDS = {"person", "people", "man", "woman", "child", "kid", "boy", "girl", "adult",
                "student", "teacher", "doctor", "farmer", "baker", "friend", "user", "human"}


class RuleLearner:
    """Turns 'if ... then ...' frames into stored patterns over a single variable x."""

    def __init__(self, repo: Repository, resolver: Resolver) -> None:
        self.repo = repo
        self.resolver = resolver

    def learn(self, rule: RuleFrame, uid: int) -> list[tuple[str, int]]:
        conditions, conclusions = rule.conditions, rule.conclusions
        if not conditions or not conclusions or not all(isinstance(f, Frame) for f in conditions + conclusions):
            raise Unsupported("I couldn't follow the structure of that rule.")
        first = conditions[0].subject
        letter = first is not None and first.proper and len(first.surface) == 1 and first.surface.isupper()
        var = first if first is not None and (letter or _Parser.var_like(first)) else None

        def is_var(np: NounPhrase | None) -> bool:
            if var is None or np is None:
                return False
            return (np is var or np.pronoun in ("he", "she", "it", "they")
                    or (np.quantifier is not None and np.quantifier == var.quantifier)
                    or (np.proper and var.proper and np.surface == var.surface)
                    or (np.determiner in DEFINITE_DETS and not var.quantifier and np.head == var.head)
                    or (not np.proper and not np.pronoun and np.head == var.head and np.determiner == var.determiner))

        # Ground rules ("If Rex is hungry, he barks"): resolve named subjects first so pronouns bind to them.
        for frame in sorted(conditions + conclusions, key=lambda f: not (f.subject and f.subject.proper)):
            if frame.subject is not None and not is_var(frame.subject):
                self.resolver.resolve(frame.subject, "subject", True)

        typed = var is not None and not var.quantifier and not var.proper
        condition_patterns = []
        if typed:
            condition_patterns.append(self._is_a_pattern(var))
        condition_patterns += [self._pattern(f, is_var) for f in conditions]
        conclusion_patterns = [self._pattern(f, is_var) for f in conclusions]

        # "If something is a dog, it is an animal" is just taxonomy.
        only = condition_patterns[0] if len(condition_patterns) == 1 else None
        if only and only["relation"] == "is_a" and only["subject"] == "x" and only["polarity"] \
                and all(p["relation"] == "is_a" and p["subject"] == "x" for p in conclusion_patterns):
            narrower = ("c", only["obj"][1])
            return [("a", self.repo.add_assertion(narrower, "subclass_of", ("c", p["obj"][1]), p["polarity"],
                                                  uid, rule.confidence)) for p in conclusion_patterns]

        variable = self._variable_display(var, typed)
        return [("r", self.repo.add_rule(condition_patterns, conclusion_patterns, variable, rule.confidence, uid))]

    def _is_a_pattern(self, var: NounPhrase) -> dict:
        return {"relation": "is_a", "subject": "x", "verb": None, "obj": ["c", self.resolver.category(var, True)],
                "props": [], "polarity": True, "args": []}

    @staticmethod
    def _variable_display(var: NounPhrase | None, typed: bool) -> dict:
        if var is None:
            return {"word": None, "pronoun": None, "plural": False, "typed": False}
        if var.quantifier:
            person = var.quantifier in PERSON_QUANTIFIERS
            return {"word": "someone" if person else "something", "pronoun": "they" if person else "it",
                    "plural": False, "typed": False}
        if var.proper:
            return {"word": var.surface, "pronoun": var.surface, "plural": False, "typed": False}
        if var.plural:
            return {"word": var.surface.lower(), "pronoun": "they", "plural": True, "typed": typed}
        person = var.head in PERSON_WORDS
        return {"word": f"{article(var.head)} {var.head}", "pronoun": "they" if person else "it",
                "plural": False, "typed": typed}

    def _spec(self, np: NounPhrase) -> list:
        if np.quantifier:
            raise Unsupported("I can only handle rules about one unspecified thing at a time so far.")
        if np.pronoun or np.proper or np.possessor is not None or np.determiner in DEFINITE_DETS:
            return list(self.resolver.resolve(np, "object", True))
        self.repo.note_lexeme(np.head, "noun")
        return ["kind", self.repo.concept(np.head), list(np.modifiers), np.determiner is None]

    def _pattern(self, frame: Frame, is_var) -> dict:
        if frame.subject is None:
            raise Unsupported("Part of that rule has no subject.")
        if is_var(frame.subject):
            subject = "x"
        elif frame.subject.quantifier:
            raise Unsupported("I can only handle rules about one unspecified thing at a time so far.")
        else:
            subject = list(self.resolver.resolve(frame.subject, "subject", True))
        pattern = {"relation": frame.relation, "subject": subject, "verb": frame.verb,
                   "polarity": frame.polarity, "obj": None, "props": [], "args": []}
        if frame.relation == "is_a":
            pattern["obj"] = ["c", self.resolver.category(frame.obj, True)]
        elif frame.relation == "property":
            pattern["props"] = [self.repo.concept(p) for p in frame.properties]
        elif frame.relation == "rel":
            self.repo.note_lexeme(frame.verb, "verb")
            if frame.obj is not None:
                if is_var(frame.obj):
                    raise Unsupported("I can only handle rules about one thing at a time so far.")
                pattern["obj"] = self._spec(frame.obj)
            pattern["args"] = [[role, value if isinstance(value, str) else self._spec(value)]
                               for role, value in frame.args if value is not None]
        else:
            raise Unsupported("I can't use that kind of clause inside a rule.")
        return pattern
