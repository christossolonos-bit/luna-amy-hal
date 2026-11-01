"""Recursive-descent parser for plain English teaching sentences and questions.

Coverage (each sentence may combine these with 'and', 'but', commas, 'because', ...):
    NP VERB [NP (and NP)*] [PREP NP | TIME]*     any verb, known or new ("Howard drives a truck to work")
    NP do/does/did not VERB ...                  negation, tense
    NP can/will/should VERB ...                  modality
    NP is/are [not] a N | ADJ (and ADJ)* | PREP NP
    NP, who VP, VP                               appositive relative clause
    ... NP that/which/who VP                     relative clause on an object
    Does/Did/Can NP VERB ...?   Is NP ...?       yes/no questions
    What/Who does NP VERB?   Who VERBs NP?   Where/When does NP VERB?   What is NP?
    Tell me about NP / What do you know about NP
    If/Whenever CLAUSES, (then) CLAUSES     CLAUSES if CLAUSES     Anyone/Every N who VP VP
    ... NP, not NP                           contrast ("drives a bus, not a truck")

Anything outside the grammar raises ParseError naming where parsing stopped, so the engine
asks instead of inventing a meaning.
"""

from dataclasses import replace

from nlp.discourse import strip_discourse
from nlp.lexicon import (
    ADVERBS, AUX_WORDS, BE, BOUNDARY, CLAUSE_LINKS, CONJUNCTIONS, DAYS, DETERMINERS, DO,
    HAVE_FORMS, LY_NOT_ADVERBS, MODALS, MONTHS, NEG_BE, NEG_DO, NEG_MODALS, NEGATORS,
    NUMBER_WORDS, OBJECT_PRONOUNS, POSSESSIVE_DETS, PREPOSITIONS, QUANTIFIERS, RELATIVES,
    RULE_STARTERS, SUBJECT_PRONOUNS, TIME_LEAD, TIME_UNITS, TIME_WORDS, WH, Lexicon,
)
from nlp.morphology import PARTICIPLE_TO_LEMMA, lemmatize_noun
from nlp.semantic_frames import Frame, NounPhrase, RuleFrame
from nlp.tokenizer import Token, tokenize

SUPPORTED = ("Try plain sentences like 'Howard drives a red truck', 'Trucks are vehicles', "
             "'He lives in Ohio', or questions like 'What does Howard drive?'.")

QUESTION_STARTERS = set(BE) | set(NEG_BE) | set(DO) | set(NEG_DO) | MODALS | set(NEG_MODALS) | WH
END_PUNCT = {"?", ".", "!", ";"}


class ParseError(Exception):
    pass


class _Parser:
    def __init__(self, tokens: list[Token], lex: Lexicon) -> None:
        self.toks = tokens
        self.i = 0
        self.limit = len(tokens)
        self.lex = lex
        self.last_subject: NounPhrase | None = None

    # --- cursor -------------------------------------------------------------------

    def tok(self, k: int = 0) -> Token | None:
        j = self.i + k
        return self.toks[j] if 0 <= j < self.limit else None

    def low(self, k: int = 0) -> str | None:
        t = self.tok(k)
        return t.low if t else None

    def at_end(self) -> bool:
        return self.i >= self.limit

    def fail(self, message: str | None = None):
        t = self.tok()
        raise ParseError(message or (f"I got lost at '{t.text}'." if t
                                     else "The sentence ended before I expected."))

    # --- word classes -------------------------------------------------------------

    def is_verb_form(self, w: str) -> bool:
        return w in AUX_WORDS or self.lex.verb(w) is not None

    def time_len(self, k: int = 0) -> int:
        w, nxt = self.low(k), self.low(k + 1)
        if w is None:
            return 0
        if w in TIME_WORDS:
            return 1
        if w in TIME_LEAD and nxt and lemmatize_noun(nxt)[0] in TIME_UNITS:
            return 2
        if (w in NUMBER_WORDS or w.isdigit()) and nxt and lemmatize_noun(nxt)[0] in TIME_UNITS \
                and self.low(k + 2) == "ago":
            return 3
        if (w.isdigit() and len(w) == 4) or w in DAYS or w in MONTHS:
            return 1
        return 0

    def skip_adverbs(self, ly: bool = False) -> bool:
        """Skip adverbs; return True if a negator ('not', 'never') was among them."""
        negated = False
        while (t := self.tok()) is not None:
            w = t.low
            if w == "no" and self.low(1) == "longer":
                negated = True
                self.i += 2
                continue
            if w in NEGATORS:
                negated = True
            elif w in ADVERBS:
                pass
            elif ly and w.endswith("ly") and len(w) > 4 and t.text.islower() and w not in LY_NOT_ADVERBS:
                pass
            else:
                break
            self.i += 1
        return negated

    def starts_np(self, k: int = 0) -> bool:
        t = self.tok(k)
        if t is None or self.time_len(k):
            return False
        w = t.low
        if (w in OBJECT_PRONOUNS or w in SUBJECT_PRONOUNS or w in POSSESSIVE_DETS
                or w in DETERMINERS or w in NUMBER_WORDS or w.isdigit()):
            return True
        if t.text[:1].isupper():
            return True
        if w in BOUNDARY or w in ADVERBS or self.lex.inflected_verb(w):
            return False
        return w.replace("-", "").isalpha()

    def verb_ahead(self, k: int = 0) -> bool:
        while self.low(k) in ADVERBS or self.low(k) in NEGATORS:
            k += 1
        t = self.tok(k)
        if t is None or t.text[:1].isupper():
            return False
        return t.low in AUX_WORDS or self.lex.verb(t.low) is not None

    def clause_ahead(self, k: int) -> bool:
        """Does a new clause (subject + verb) start k tokens ahead?"""
        save = self.i
        try:
            self.i += k
            self.np("subj")
            if self.verb_ahead():
                return True
            t = self.tok()
            return bool(t and t.text.islower() and t.low.endswith(("s", "ed"))
                        and t.low not in BOUNDARY)
        except ParseError:
            return False
        finally:
            self.i = save

    # --- noun phrases -------------------------------------------------------------

    def np(self, role: str) -> NounPhrase:
        t = self.tok()
        if t is None:
            self.fail("I expected a noun phrase.")
        w = t.low
        if w in QUANTIFIERS:
            self.i += 1
            return NounPhrase(head=w, surface=w, quantifier=w)
        if w == "her" and self._nominal_start(1):
            self.i += 1
            return self._nominal("the", NounPhrase(head="she", pronoun="she", surface="her"))
        if w in POSSESSIVE_DETS and w != "her":
            self.i += 1
            owner = POSSESSIVE_DETS[w]
            return self._nominal("the", NounPhrase(head=owner, pronoun=owner, surface=w))
        if w in SUBJECT_PRONOUNS or w in OBJECT_PRONOUNS:
            self.i += 1
            canon = OBJECT_PRONOUNS.get(w, w)
            return NounPhrase(head=canon, pronoun=canon, surface=t.text)
        if w in DETERMINERS or w in NUMBER_WORDS or w.isdigit():
            self.i += 1
            return self._nominal(w)
        if t.text[:1].isupper() or (role == "subj" and self.i == 0):
            if role == "subj" and self._generic_plural_start():
                return self._nominal(None)
            return self._proper()
        if w in BOUNDARY:
            self.fail(f"I expected a noun phrase at '{t.text}'.")
        nxt = self.low(1)
        if role == "subj" and nxt and (nxt in AUX_WORDS or self.lex.verb(nxt)) and not lemmatize_noun(w)[1]:
            self.i += 1  # "does omar own ...": the name is one word, the next word is the verb
            return NounPhrase(head=t.text.lower(), proper=True, surface=t.text)
        phrase = self._nominal(None)
        if role == "subj" and not phrase.plural:  # a bare singular subject is a name
            return NounPhrase(head=phrase.surface.lower(), proper=True, surface=phrase.surface)
        return phrase

    def _nominal_start(self, k: int) -> bool:
        t = self.tok(k)
        return bool(t and t.text[:1].islower() and t.low not in BOUNDARY
                    and t.low not in DETERMINERS and not self.lex.inflected_verb(t.low)
                    and not self.time_len(k))

    def _generic_plural_start(self) -> bool:
        """'Dogs chase cats' (generic plural) as opposed to 'James likes cats' (a name)."""
        nxt = self.tok(1)
        if nxt is None or nxt.text[:1].isupper() or not lemmatize_noun(self.low())[1]:
            return False
        n = nxt.low
        if n in ("are", "were", "aren't", "weren't", "do", "don't", "have") or n in MODALS or n in NEG_MODALS:
            return True
        found = self.lex.verb(n)
        return found is not None and found[0] == n

    def _proper(self) -> NounPhrase:
        parts: list[str] = []
        while (t := self.tok()) is not None:
            if parts and (not t.text[:1].isupper() or t.low in BOUNDARY):
                break
            possessive = t.low.endswith("'s")
            parts.append(t.text[:-2] if possessive else t.text)
            self.i += 1
            if possessive:
                name = " ".join(parts)
                return self._nominal("the", NounPhrase(head=name.lower(), proper=True, surface=name))
        name = " ".join(parts)
        return NounPhrase(head=name.lower(), proper=True, surface=name)

    def _verb_starts_here(self, words: list[str]) -> bool:
        w = self.low()
        if w in AUX_WORDS:
            return True
        found = self.lex.verb(w)
        if found is None:
            return False
        if found[0] == w:  # bare form: a verb only after a plural noun ("the dogs chase")
            return lemmatize_noun(words[-1].lower())[1]
        return True

    def _nominal(self, det: str | None, possessor: NounPhrase | None = None) -> NounPhrase:
        words: list[str] = []
        while (t := self.tok()) is not None:
            w = t.low
            if w == "very":
                self.i += 1
                continue
            if w in BOUNDARY or w in DETERMINERS or (words and (w in ADVERBS or self.time_len())):
                break
            if words and (self._verb_starts_here(words) or t.text[:1].isupper() != words[-1][:1].isupper()):
                break
            if words and w.endswith("ly") and len(w) > 4 and w not in LY_NOT_ADVERBS:
                break
            words.append(t.text)
            self.i += 1
        if not words:
            self.fail(f"'{det}' needs a noun after it." if det else None)
        if det is None and possessor is None and len(words) == 1 and words[0].islower():
            name = self.lex.proper_form(words[0])
            if name:  # "lives in texas" is the place Texas, not some texas
                return NounPhrase(head=name.lower(), proper=True, surface=name)
        lows = [x.lower() for x in words]
        head, plural = lemmatize_noun(lows[-1])
        if det and (det in NUMBER_WORDS or det.isdigit()) and det != "one":
            plural = True
        if det in ("these", "those", "all"):
            plural = True
        return NounPhrase(head=head, modifiers=tuple(lows[:-1]), determiner=det, plural=plural,
                          surface=" ".join(words), possessor=possessor)

    # --- clauses ------------------------------------------------------------------

    def statements(self) -> list:
        if self.low() in RULE_STARTERS:
            return [self.rule_sentence()]
        frames = self.clause(None)
        while not self.at_end():
            w = self.low()
            if w == "if":  # "Howard is happy if he owns a dog"
                self.i += 1
                conditions = self._clause_list(stop=set())
                return [RuleFrame(tuple(conditions), tuple(frames))]
            if w == ",":
                self.i += 1
                if self.at_end():
                    break
                if self.low() not in CONJUNCTIONS and self.low() not in CLAUSE_LINKS and self.low() != "if":
                    frames += self.clause(self.last_subject)
                    continue
                w = self.low()
            if w in CONJUNCTIONS or w in CLAUSE_LINKS:
                self.i += 1
                if self.low() == "then":
                    self.i += 1
                if self.at_end():
                    break
                frames += self.clause(self.last_subject)
                continue
            self.fail()
        return frames

    def rule_sentence(self) -> RuleFrame:
        self.i += 1  # if / whenever / when
        conditions = self._clause_list(stop={",", "then"})
        if self.low() == ",":
            self.i += 1
        if self.low() == "then":
            self.i += 1
        conclusions = self._clause_list(stop=set())
        return RuleFrame(tuple(conditions), tuple(conclusions))

    def _clause_list(self, stop: set[str]) -> list[Frame]:
        frames = self.clause(None)
        while not self.at_end():
            if self.low() == "and":
                self.i += 1
            elif self.low() == "," and "," not in stop:
                self.i += 2 if self.low(1) == "and" else 1
            else:
                break
            frames += self.clause(self.last_subject)
        return frames

    @staticmethod
    def var_like(np: NounPhrase) -> bool:
        """Could this noun phrase stand for 'any such thing' in a rule?"""
        return bool(np.quantifier or np.determiner in ("a", "an", "every", "all", "each", "any")
                    or (np.determiner is None and np.plural and not np.proper and not np.pronoun
                        and np.possessor is None))

    def clause(self, prev_subject: NounPhrase | None) -> list[Frame]:
        lead_time = None
        n = self.time_len()
        if not n and self.low() in PREPOSITIONS and self.time_len(1):
            n = self.time_len(1) + 1
        if n:
            lead_time = " ".join(t.low for t in self.toks[self.i:self.i + n])
            self.i += n
            if self.low() == ",":
                self.i += 1

        if prev_subject is not None and self.verb_ahead():
            subjects = [prev_subject]
        else:
            subjects = [self.np("subj")]
            while self.low() == "and" and self.starts_np(1):
                self.i += 1
                subjects.append(self.np("subj"))
        self.last_subject = subjects[0]

        if len(subjects) == 1 and self.low() in ("who", "that", "which"):
            self.i += 1
            restriction = self.vp([subjects[0]])
            if self.low() == ",":
                self.i += 1
            main = self.vp([subjects[0]])
            if self.var_like(subjects[0]):  # "Anyone who owns a dog is a pet owner"
                return [RuleFrame(tuple(restriction), tuple(main))]
            return restriction + main  # "The man who owns a truck is tall": two facts

        extra: list[Frame] = []
        if self.low() == "," and self.low(1) in ("who", "which"):
            self.i += 2
            extra = self.vp([subjects[-1]])
            if self.low() == ",":
                self.i += 1
        frames = self.vp(subjects)
        if lead_time:
            frames = [replace(f, args=f.args + (("time", lead_time),)) for f in frames]
        return extra + frames

    def _base_verb(self) -> str:
        t = self.tok()
        if t is None or t.text[:1].isupper() or (t.low in BOUNDARY and not (t.low in PREPOSITIONS and self.lex.verb(t.low))):
            self.fail("I expected a verb there.")
        found = self.lex.verb(t.low, allow_unknown=True)
        self.i += 1
        return found[0] if found else t.low

    def vp(self, subjects: list[NounPhrase | None]) -> list[Frame]:
        negated = self.skip_adverbs(ly=True)
        w = self.low()
        if w is None:
            self.fail("I expected a verb.")
        if w in BE or w in NEG_BE:
            tense, number = BE.get(w) or NEG_BE[w]
            negated |= w in NEG_BE
            self.i += 1
            negated |= self.skip_adverbs()
            return self.be_complement(subjects, negated, tense, number, None)

        modality, tense = None, "present"
        if w in DO or w in NEG_DO:
            tense = (DO.get(w) or NEG_DO[w])[0]
            negated |= w in NEG_DO
            self.i += 1
            negated |= self.skip_adverbs(ly=True)
            lemma = self._base_verb()
        elif w in MODALS or w in NEG_MODALS:
            modality = NEG_MODALS.get(w, w)
            negated |= w in NEG_MODALS
            self.i += 1
            negated |= self.skip_adverbs(ly=True)
            if self.low() == "be":
                self.i += 1
                negated |= self.skip_adverbs()
                return self.be_complement(subjects, negated, "present", None, modality)
            lemma = self._base_verb()
            tense = "future" if modality == "will" else "present"
        elif w in HAVE_FORMS:
            self.i += 1
            negated |= self.skip_adverbs()
            nxt = self.low()
            if nxt == "been":
                self.fail("I can't handle 'has been' yet.")
            if nxt == "to":
                self.fail("I can't handle obligations like 'has to' yet.")
            found = self.lex.verb(nxt) if nxt else None
            if nxt in PARTICIPLE_TO_LEMMA or (found and found[1] == "past" and nxt.endswith("ed")):
                self.i += 1
                lemma, tense = (PARTICIPLE_TO_LEMMA.get(nxt) or found[0]), "past"
            else:
                lemma, tense = "own", HAVE_FORMS[w]
        else:
            t = self.tok()
            found = self.lex.verb(w, allow_unknown=True)
            if found is None or t.text[:1].isupper() or w in DETERMINERS                     or (w in BOUNDARY and not (w in PREPOSITIONS and self.lex.verb(w))):  # "like" is both
                self.fail(f"I couldn't find the verb (stopped at '{t.text}').")
            lemma, tense = found
            self.i += 1
        if lemma == "have":
            lemma = "own"

        objs, args, extra, contrasts = self.complements()
        frames = [Frame("assert", "rel", s, o, verb=lemma, polarity=pol, tense=tense,
                        modality=modality, args=tuple(args), correction=bool(contrasts))
                  for s in subjects
                  for objects, pol in ((objs or [None], not negated), (contrasts, negated))
                  for o in objects]
        return frames + extra

    def be_complement(self, subjects, negated, tense, number, modality) -> list[Frame]:
        t = self.tok()
        if t is None:
            self.fail("I expected something after 'is'.")
        w = t.low
        base = dict(act="assert", polarity=not negated, tense=tense, modality=modality)

        if w in DETERMINERS or w in NUMBER_WORDS or w in POSSESSIVE_DETS:
            preds = [self.np("pred")]
            while self.low() == "and" and self.low(1) in DETERMINERS:
                self.i += 1
                preds.append(self.np("pred"))
            contrasts = []
            if self.low() == "," and self.low(1) == "not" and self.low(2) in DETERMINERS:
                self.i += 2
                contrasts.append(self.np("pred"))
            _, args, extra, _ = self.complements(objects=False)
            frames = [Frame(relation="is_a", subject=s, obj=p, args=tuple(args),
                            correction=bool(contrasts), **base)
                      for s in subjects for p in preds]
            frames += [Frame(relation="is_a", subject=s, obj=p, correction=True, **{**base, "polarity": negated})
                       for s in subjects for p in contrasts]
            return frames + extra

        if w in PREPOSITIONS:
            _, args, extra, _ = self.complements(objects=False)
            return [Frame(relation="rel", subject=s, verb="be", args=tuple(args), **base)
                    for s in subjects] + extra

        if t.text[:1].isupper():
            self.fail("I can't learn that two names refer to the same thing yet.")

        plural_subject = number == "pl" or any(s is not None and s.plural for s in subjects)
        if plural_subject and lemmatize_noun(w)[1]:
            preds = [self._nominal(None)]
            while self.low() == "and" and self._nominal_start(1):
                self.i += 1
                preds.append(self._nominal(None))
            _, args, extra, _ = self.complements(objects=False)
            return [Frame(relation="is_a", subject=s, obj=p, args=tuple(args), **base)
                    for s in subjects for p in preds] + extra

        props: list[str] = []
        while (t := self.tok()) is not None:
            w = t.low
            if w in ADVERBS:
                self.i += 1
                continue
            if w in BOUNDARY or w in DETERMINERS or self.time_len() or t.text[:1].isupper():
                break
            props.append(w)
            self.i += 1
            if self.low() in (",", "and") and self._adjective_ahead(1):
                self.i += 1
            elif self.low() == "," and self.low(1) == "and" and self._adjective_ahead(2):
                self.i += 2
        if not props:
            self.fail()
        _, args, extra, _ = self.complements(objects=False)
        return [Frame(relation="property", subject=s, properties=tuple(props), args=tuple(args), **base)
                for s in subjects] + extra

    def _adjective_ahead(self, k: int) -> bool:
        t, after = self.tok(k), self.tok(k + 1)
        if t is None or not t.text.islower() or t.low in BOUNDARY or t.low in DETERMINERS \
                or t.low in SUBJECT_PRONOUNS or self.is_verb_form(t.low):
            return False
        return after is None or after.low in BOUNDARY or after.low in (",",)

    def complements(self, objects: bool = True):
        objs: list[NounPhrase] = []
        contrasts: list[NounPhrase] = []  # "a bus, not a truck"
        args: list = []
        extra: list[Frame] = []
        self.skip_adverbs(ly=True)
        if objects and self.starts_np() and not (self.low() in RELATIVES and self.low() != "that"):
            objs.append(self.np("obj"))
            if self.starts_np() and self.low() not in RELATIVES:
                # Ditransitive: "gave him a book" -> object "a book", recipient "to him"
                args.append(("to", objs.pop()))
                objs.append(self.np("obj"))
            while self.low() == "and" and self.starts_np(1) and not self.clause_ahead(1):
                self.i += 1
                objs.append(self.np("obj"))
            if self.low() == "," and self.low(1) == "not" and self.starts_np(2):
                self.i += 2
                contrasts.append(self.np("obj"))
        while not self.at_end():
            self.skip_adverbs(ly=True)
            w = self.low()
            if w is None:
                break
            n = self.time_len()
            if n:
                args.append(("time", " ".join(t.low for t in self.toks[self.i:self.i + n])))
                self.i += n
                continue
            if w in PREPOSITIONS:
                self.i += 1
                n = self.time_len()
                if n:
                    args.append(("time", w + " " + " ".join(t.low for t in self.toks[self.i:self.i + n])))
                    self.i += n
                elif self.starts_np():
                    args.append((w, self.np("obj")))
                else:
                    args.append((w, None))  # dangling: "Who does he live with?"
                continue
            if w in RELATIVES and (objs or args):
                target = objs[-1] if objs else next((v for _, v in reversed(args) if isinstance(v, NounPhrase)), None)
                if target is None:
                    break
                self.i += 1
                extra += self.vp([target])
                continue
            break
        return objs, args, extra, contrasts

    # --- questions ----------------------------------------------------------------

    def question(self) -> list[Frame]:
        w = self.low()
        if w in WH:
            return self.wh()
        if w == "tell" and self.low(1) == "me" and self.low(2) == "about":
            self.i += 3
            return [Frame("ask", "describe", self.np("subj"))]
        if w in BE or w in NEG_BE:
            tense, number = BE.get(w) or NEG_BE[w]
            self.i += 1
            subject = self._be_question_subject()
            frames = self.be_complement([subject], False, tense, number, None)
            return [replace(f, act="ask", polarity=True) for f in frames]
        if w in DO or w in NEG_DO:
            self.i += 1
            return self._aux_question((DO.get(w) or NEG_DO[w])[0], None)
        if w in MODALS or w in NEG_MODALS:
            modality = NEG_MODALS.get(w, w)
            self.i += 1
            return self._aux_question("future" if modality == "will" else "present", modality)
        self.fail("I couldn't work out that question.")

    def _be_question_subject(self) -> NounPhrase:
        save = self.i
        subject = self.np("subj")
        swallowed_name = subject.proper and " " in subject.surface and subject.surface.islower()  # "chen tall"
        if self.at_end() and subject.pronoun is None and (not subject.proper or swallowed_name):
            # The noun phrase swallowed the predicate ("Is the truck red"): give the last word back.
            self.i, end = save, self.limit
            self.limit = end - 1
            try:
                subject = self.np("subj")
            finally:
                self.limit = end
        return subject

    def _aux_question(self, tense, modality, slot=None, wh=None) -> list[Frame]:
        subject = self.np("subj")
        self.skip_adverbs(ly=True)
        if modality and self.low() == "be":
            self.i += 1
            frames = self.be_complement([subject], False, tense, None, modality)
            return [replace(f, act="ask", polarity=True, slot=slot if slot != "object" else None, wh=wh)
                    for f in frames]
        lemma = self._base_verb()
        if lemma == "have":
            lemma = "own"
        objs, args, _, _ = self.complements()
        dangling = [role for role, value in args if value is None]
        args = tuple(a for a in args if a[1] is not None)
        if slot == "object" and dangling:
            slot = dangling[0]
        elif slot == "object" and objs:
            slot = None
        return [Frame("ask", "rel", subject, objs[0] if objs else None, verb=lemma, tense=tense,
                      modality=modality, args=args, slot=slot, wh=wh)]

    def wh(self) -> list[Frame]:
        wh = self.low()
        self.i += 1
        w = self.low()
        if w in ("do", "does") and self.low(1) == "you" and self.low(2) == "know" and self.low(3) == "about":
            self.i += 4
            return [Frame("ask", "describe", self.np("subj"), wh=wh)]
        if w in BE or w in NEG_BE:
            tense, number = BE.get(w) or NEG_BE[w]
            self.i += 1
            if wh == "where":
                return [Frame("ask", "rel", self.np("subj"), verb="be", tense=tense, slot="where", wh=wh)]
            t = self.tok()
            if t is None:
                self.fail()
            if wh == "who" and (t.low in DETERMINERS or (t.text.islower() and t.low not in SUBJECT_PRONOUNS
                                                        and t.low not in OBJECT_PRONOUNS)):
                frames = self.be_complement([None], False, tense, number, None)
                return [replace(f, act="ask", polarity=True, slot="subject", wh=wh) for f in frames]
            return [Frame("ask", "describe", self.np("subj"), wh=wh)]
        if w in DO or w in NEG_DO or w in MODALS or w in NEG_MODALS:
            if w in DO or w in NEG_DO:
                tense, modality = (DO.get(w) or NEG_DO[w])[0], None
            else:
                modality = NEG_MODALS.get(w, w)
                tense = "future" if modality == "will" else "present"
            self.i += 1
            slot = wh if wh in ("where", "when") else "object"
            return self._aux_question(tense, modality, slot, wh)
        if wh in ("who", "what") and self.verb_ahead():
            frames = self.vp([None])
            return [replace(f, act="ask", polarity=True, slot="subject", wh=wh) for f in frames]
        self.fail("I couldn't work out that question.")


def parse_sentence(text: str, lexicon: Lexicon | None = None,
                   names: set[str] = frozenset()) -> list[Frame]:
    """Parse one sentence into one or more frames (coordination yields several)."""
    from nlp.discourse import expand_contractions, unshout
    tokens = tokenize(unshout(expand_contractions(text)))
    is_question = any(t.low == "?" for t in tokens)
    words = [t for t in tokens if t.low not in END_PUNCT]
    words, confidence, correction = strip_discourse(words, names)
    if not words:
        raise ParseError("I didn't catch any words.")
    parser = _Parser(words, lexicon or Lexicon())
    first = words[0].low
    if first in ("how", "why", "which", "whose"):
        raise ParseError(f"I can't answer '{first}' questions about that yet.")
    if first in ("when", "whenever") and parser.low(1) not in AUX_WORDS:
        frames = parser.statements()  # "When someone is hungry, they eat" is a rule
    elif first in QUESTION_STARTERS or (first == "tell" and parser.low(1) == "me"):
        frames = parser.question()
    else:
        frames = parser.statements()
        if is_question:  # "Howard owns a truck?"
            if any(isinstance(f, RuleFrame) for f in frames):
                parser.fail("I can't answer questions with 'if' in them yet.")
            frames = [replace(f, act="ask", polarity=True) for f in frames]
    if not parser.at_end():
        parser.fail()
    return [replace(f, confidence=confidence, correction=correction or f.correction) if f.act == "assert" else f
            for f in frames]


def parse_noun_phrase(text: str, lexicon: Lexicon | None = None) -> tuple[NounPhrase, bool]:
    """Parse a bare fragment such as 'No, Dayton.' -> (noun phrase, is_correction)."""
    words = [t for t in tokenize(text) if t.low not in END_PUNCT]
    words, _, correction = strip_discourse(words)
    if not words:
        raise ParseError("I didn't catch any words.")
    parser = _Parser(words, lexicon or Lexicon())
    phrase = parser.np("obj")
    if not parser.at_end():
        parser.fail()
    return phrase, correction


def parse(text: str, lexicon: Lexicon | None = None) -> Frame:
    return parse_sentence(text, lexicon)[0]
