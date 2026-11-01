"""Evidence-carrying paragraph plans assembled from memory and lexical graphs.

Definitions stay lexical descriptions, examples never become events, and only
hypernym paths license category deductions. Sentences carry inspectable evidence.
"""

from dataclasses import asdict, dataclass, field
import re

from dialogue import realizer
from nlp.lexicon import article
from nlp.morphology import lemmatize_noun
from nlp.wordnet import Sense, WordNet
from reasoning.engine import query_from_row
from reasoning.proofs import CONFLICTING
from reasoning.proofs import YES, Query

REQUEST = re.compile(
    r"^(?:please\s+)?(?:tell me (?:more )?about|explain(?: to me)?|describe|"
    r"give me (?:a |an )?(?:detailed )?(?:explanation|overview|summary) (?:of|about)|"
    r"what (?:do you know|can you tell me) about)\s+(.+?)[?.!]*$", re.I)
DEFINE = re.compile(r"^what (?:is|are) (?:a |an |the )?(.+?)[?.!]*$", re.I)
MORE = re.compile(r"^(?:tell me more|go on|keep going|continue|explain (?:that|it)(?: further)?|"
                  r"(?:can you )?(?:give|add) (?:me )?more detail)[.!?]*$", re.I)
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "one": 1, "two": 2,
            "three": 3, "four": 4, "five": 5, "six": 6, "last": -1}
ORDINAL_REPLY = re.compile(r"^(?:tell me about |i meant |i mean |the |number |#|option )*(?:the )?"
                           r"(\d+|first|second|third|fourth|fifth|sixth|last|one|two|three|four|five|six)"
                           r"(?: one| meaning| sense| option)?$", re.I)
SENSITIVE = re.compile(r"\b(?:sad|grief|grieving|died|death|funeral|depressed|heartbroken|"
                       r"mourning|devastated|suicide|bereaved)\b", re.I)
INTRO = {
    "neutral": ("Here's how the information fits together.", "Let me connect what I know about {topic}."),
    "warm": ("I'd be glad to walk through {topic} with you.", "Let's put the pieces about {topic} together."),
    "excited": ("There's an interesting connection here!", "I'm keen to explore how these pieces fit together!"),
    "subdued": ("I'll walk through what I know about {topic}.", "Let me take this one piece at a time."),
    "gentle": ("I'll take this carefully.", "Let's take this at your pace."),
    "measured": ("Let me work through the evidence carefully.", "I'll keep the explanation grounded in what I know."),
}


@dataclass
class Claim:
    text: str
    kind: str
    assertions: list[int] = field(default_factory=list)
    synsets: list[str] = field(default_factory=list)
    confidence: float = 1.0
    rules: list[int] = field(default_factory=list)
    query: dict | None = None


@dataclass
class Topic:
    name: str
    context: str = ""
    chosen: str | None = None
    emitted: set[str] = field(default_factory=set)
    bindings: dict[str, str] = field(default_factory=dict)


class Exposition:
    def __init__(self, conversation, wordnet: WordNet):
        self.convo, self.repo, self.wordnet = conversation, conversation.repo, wordnet
        self.topics: dict[str, Topic] = {}
        self.pending: dict[str, tuple[str, list[Sense], str]] = {}
        self.max_claims = 48  # safety budget, not a sentence/paragraph length target

    def _context(self, entity_id: int | None) -> str:
        if entity_id is None:
            return ""
        rows = self.repo.conn.execute("SELECT * FROM assertions WHERE status='active' "
                                      "AND subj_type='e' AND subj_id=? ORDER BY id DESC LIMIT 48", (entity_id,))
        return " ".join(t for row in rows if (t := realizer.realize_assertion(self.repo, row)))

    def _ambiguity(self, word: str, alternatives: list[Sense], context: str, topic: Topic):
        self.pending[self.convo.state.speaker] = (word, alternatives, context, topic)
        # Show a manageable choice list; numeric replies keep exact sense identities.
        shown = alternatives[:6]
        options = "; ".join(f"{i + 1}: {s.definition}" for i, s in enumerate(shown))
        tail = " There are other meanings too; you can describe the one you intend." if len(alternatives) > 6 else ""
        return f"Which meaning of '{word}' do you mean? {options}.{tail}"

    def _select(self, word, context, chosen=None):
        return self.wordnet.select(word, context, chosen=chosen)

    def respond(self, text: str) -> tuple[str, dict] | None:
        speaker = self.convo.state.speaker
        topic = None
        continuing = False
        if speaker in self.pending:
            word, alternatives, context, original = self.pending[speaker]
            reply = text.strip(" .!?")
            chosen = None
            ordinal = ORDINAL_REPLY.match(reply)
            if ordinal:  # "2", "the second one", "tell me about the first one"
                word_or_digit = ordinal.group(1).lower()
                index = int(word_or_digit) if word_or_digit.isdigit() else ORDINALS[word_or_digit]
                index = len(alternatives[:6]) if index == -1 else index
                if 1 <= index <= min(6, len(alternatives)):
                    chosen = alternatives[index - 1].id
            elif len(reply.split()) <= 20 and not REQUEST.match(text):
                # The new explanation alone must select the sense; stale context
                # must not outweigh the user's explicit clarification.
                selected = self._select(word, reply)
                chosen = selected.sense.id if selected.sense else None
            if chosen:
                self.pending.pop(speaker)
                topic = original
                topic.context += " " + text
                topic.bindings[word] = chosen
                if topic.name.lower() == word:
                    topic.chosen = chosen
            else:
                # An unrelated teaching turn must still reach the normal learner.
                self.pending.pop(speaker)
        if topic is None and MORE.match(text):
            topic = self.topics.get(speaker)
            if topic is None:
                return "What would you like me to explain further?", {"intent": "clarify", "claims": []}
            continuing = True
        if topic is None:
            match = REQUEST.match(text)
            definition = DEFINE.match(text)
            if not match and not definition:
                return None
            raw = (match or definition).group(1).strip(" .?!")
            raw = re.sub(r"\s+(?:in detail|at length|in paragraphs|in depth)$", "", raw, flags=re.I)
            raw = re.sub(r"^(?:a|an|the)\s+", "", raw, flags=re.I)
            # Explicit contexts: "bank in the river sense", "bank near a river".
            parts = re.split(r"\s+(?:in the context of|in the .*?sense of|near|beside|in relation to)\s+", raw, maxsplit=1, flags=re.I)
            name, context = parts[0], parts[1] if len(parts) > 1 else ""
            eid = self.repo.find_entity(name)
            if name.lower() in ("me", "myself"):
                name, eid = speaker, self.repo.find_entity(speaker)
            if eid is None and not self.wordnet.lookup(name) and not self.repo.find_concept(name.lower()):
                if definition:
                    return None  # keep ordinary question behavior for unknown terms
                return None
            # What is Howard? still uses ordinary entity reasoning.
            if definition and eid is not None:
                return None
            topic = Topic(name, context)
        self.topics[speaker] = topic
        return self.build(topic, continuing, text)

    def _validate(self, claim: Claim) -> bool:
        for aid in claim.assertions:
            row = self.repo.assertion(aid)
            if row is None or row["status"] != "active":
                return False
        if claim.kind == "definition":
            sense = self.wordnet.sense(claim.synsets[0]) if claim.synsets else None
            return sense is not None and sense.definition in claim.text
        if claim.kind in ("taxonomy", "deduction"):
            return self.wordnet.verify_path(claim.synsets) and (
                claim.kind != "deduction" or bool(claim.assertions))
        if claim.kind == "inference" and claim.query:
            if any(self.repo.rule(ident)["status"] != "active" for ident in claim.rules):
                return False
            query = Query(**claim.query)
            answer = self.convo.engine.ask(query)
            return answer.status == YES
        return bool(claim.assertions) if claim.kind == "memory" else False

    def _memory(self, row) -> Claim | None:
        text = realizer.realize_assertion(self.repo, row)
        if not text:
            return None
        who = self.repo.source_speaker(row["id"])
        disputed = self.convo.engine.ask(query_from_row(self.repo, row)).status == CONFLICTING
        if who in ("Wikipedia", "the dictionary"):  # something he read, not something a person said
            text = f"I read {'in the dictionary' if who == 'the dictionary' else 'on ' + who} that {text}"
        elif who:
            name = "you" if who == self.convo.state.speaker else who
            verb = "suggested" if row["confidence"] < 0.75 else "told me"
            text = f"{name[:1].upper() + name[1:]} {verb} that {text}"
        if disputed:
            text += ", although I also have a conflicting claim about this"
        return Claim(realizer.sentence(text), "memory", [row["id"]], confidence=row["confidence"])

    def _rule_claims(self, eid):
        claims = []
        for rule in self.repo.active_rules()[:24]:
            for pattern in rule["conclusions"]:
                query = self.convo.engine._instantiate(pattern, ("e", eid))
                answer = self.convo.engine.ask(query)
                if answer.status != YES:
                    continue
                for proof in answer.support[:1]:
                    conclusion = realizer.derived_text(self.repo, proof)
                    if not conclusion:
                        continue
                    hedge = "It probably follows that " if proof.confidence < 0.75 else "It follows that "
                    claims.append(Claim(hedge + conclusion + ", because " + realizer.explain(self.repo, proof) + ".",
                                        "inference", proof.steps, confidence=proof.confidence,
                                        rules=proof.learned_rules, query=asdict(query)))
        return claims

    def _lexical(self, word, context, evidence=(), chosen=None) -> tuple[list[Claim], list[Sense]]:
        selection = self._select(word, context, chosen)
        sense = selection.sense
        if sense is None and not selection.alternatives:
            return [], []
        if sense is None:
            # No context to choose by: answer with the most common meaning (WordNet ranks senses by
            # frequency) rather than making the person pick from a list, and offer to switch.
            from nlp.wordnet import Selection
            selection = Selection(selection.alternatives[0], selection.alternatives, 0.5)
            sense = selection.sense
        claims = [Claim(f"The word {word} here refers to {sense.definition}.",
                        "definition", synsets=[sense.id], confidence=selection.confidence)]
        paths = self.wordnet.ancestors(sense.id, depth=12, budget=150)
        # Follow one actual chain, not a list of unrelated neighboring definitions.
        longest = max(paths.values(), key=len)
        # Very abstract roots add little conversational value.
        informative = [ident for ident in longest[1:] if (s := self.wordnet.sense(ident))
                       and s.lemma not in ("entity", "physical entity", "object", "whole", "unit", "abstraction")]
        if informative:
            parent = self.wordnet.sense(informative[0])
            path = paths[parent.id]
            claims.append(Claim(f"In that sense, {article(word)} {word} is a type of {parent.lemma}.",
                                "taxonomy", synsets=path, confidence=selection.confidence))
            if len(informative) > 1:
                anchors = ("animal", "vehicle", "person", "plant", "food", "tool", "machine", "building")
                anchor = next((self.wordnet.sense(ident) for ident, path in paths.items()
                               if ident != sense.id and self.wordnet.sense(ident).lemma in anchors), None)
                broad = anchor or self.wordnet.sense(informative[min(2, len(informative) - 1)])
                path = paths[broad.id]
                claims.append(Claim(f"Following those category links also places this meaning of {word} "
                                    f"under {broad.lemma}.", "taxonomy", synsets=path,
                                    confidence=selection.confidence))
                if evidence:
                    row = self.repo.assertion(evidence[0])
                    if row["predicate"].startswith("rel:") and row["obj_type"] == "e" \
                            and not self.repo.args(row["id"]):
                        subject = realizer.term_text(self.repo, row["subj_type"], row["subj_id"], role="subject")
                        plural, first = realizer.agreement(subject, False)
                        verb = realizer.conjugate(row["predicate"][4:], row["temporal_scope"] or "present",
                                                 plural, True, first_person=first)
                        conclusion = f"{subject} {verb} {article(broad.lemma)} {broad.lemma}"
                        claims.append(Claim(f"That means {conclusion}, based on the category of the {word} "
                                            "you mentioned.", "deduction", list(evidence), path, selection.confidence))
        # More detail from the same dictionary: what kinds there are, and what it's made of
        kinds = [s.lemma for s in self.wordnet.related(sense.id, ("hyponym",), limit=8)][:5]
        if kinds:
            claims.append(Claim(f"There are different kinds of {word}, like {realizer.join_clauses(kinds)}.",
                                "taxonomy", synsets=[sense.id], confidence=selection.confidence))
        parts = [s.lemma for s in self.wordnet.related(sense.id, ("mero_part",), limit=8)][:5]
        if parts:
            claims.append(Claim(f"{article(word).capitalize()} {word} has parts like {realizer.join_clauses(parts)}.",
                                "taxonomy", synsets=[sense.id], confidence=selection.confidence))
        return claims, (selection.alternatives if selection.confidence <= 0.5 else [])

    def _usual_note(self, word: str, alternatives: list[Sense], context: str, topic: Topic) -> str:
        """After answering with the usual meaning, say so and let a reply pick another."""
        self.pending[self.convo.state.speaker] = (word, alternatives, context, topic)
        others = len(alternatives) - 1
        second = alternatives[1].definition if others else ""
        return (f"That's the usual meaning of '{word}'. It has {others} other{'s' if others != 1 else ''}, "
                f"such as {second}; say which one, or describe it, if you meant something else.")

    def build(self, topic: Topic, continuing: bool, prompt: str):
        eid = self.repo.find_entity(topic.name)
        context = topic.context + " " + self._context(eid)
        sensitive = bool(SENSITIVE.search(prompt + " " + context + " " + topic.name))
        style = self.convo.self_model.expression(sensitive)
        paragraphs, accepted, rejected, ambiguities = [], [], [], []

        def add(claims):
            fresh = []
            for claim in claims:
                if len(accepted) >= self.max_claims:
                    break
                if claim.text in topic.emitted and continuing:
                    continue
                if self._validate(claim):
                    fresh.append(claim.text)
                    accepted.append(asdict(claim))
                    topic.emitted.add(claim.text)
                else:
                    rejected.append(asdict(claim))
            if fresh:
                paragraphs.append(" ".join(fresh))

        if eid is None:
            # Multiword expressions are looked up whole before considering singulars.
            word = topic.name.lower()
            if not self.wordnet.lookup(word):
                word = lemmatize_noun(word)[0]
            claims, alternatives = self._lexical(word, context, chosen=topic.chosen)
            add(claims)
            if claims and alternatives:
                ambiguities.append((word, alternatives))
            # Bring in user-taught facts about this category, never dictionary examples.
            cid = self.repo.find_concept(word)
            if cid:
                rows = self.repo.conn.execute("SELECT * FROM assertions WHERE status='active' AND "
                    "((subj_type='c' AND subj_id=?) OR (predicate='instance_of' AND obj_type='c' AND obj_id=?)) "
                    "ORDER BY id DESC LIMIT 48", (cid, cid)).fetchall()
                add([c for row in rows if (c := self._memory(row))])
                owners = self.repo.conn.execute("SELECT a.* FROM assertions a JOIN entities e "
                    "ON a.obj_type='e' AND a.obj_id=e.id WHERE a.status='active' AND e.kind_concept_id=? "
                    "AND a.predicate LIKE 'rel:%' ORDER BY a.id DESC LIMIT 24", (cid,)).fetchall()
                add([c for row in owners if (c := self._memory(row))])
        else:
            rows = self.repo.conn.execute("SELECT * FROM assertions WHERE status='active' AND subj_type='e' "
                                          "AND subj_id=? ORDER BY id LIMIT 48", (eid,)).fetchall()
            direct = [c for row in rows if (c := self._memory(row))]
            # Paragraphs follow coherent groups of source facts, not a fixed total.
            for i in range(0, len(direct), 5):
                add(direct[i:i + 5])
            add(self._rule_claims(eid))
            kinds = {}
            for row in rows:
                if not row["polarity"]:
                    continue
                if row["predicate"] == "instance_of":
                    kinds.setdefault(self.repo.concept_lemma(row["obj_id"]), []).append(row["id"])
                elif row["obj_type"] == "e":
                    obj = self.repo.entity_row(row["obj_id"])
                    if obj and not obj["proper"]:
                        kinds.setdefault(self.repo.concept_lemma(obj["kind_concept_id"]), []).append(row["id"])
                        related = self.repo.conn.execute("SELECT * FROM assertions WHERE status='active' AND "
                            "subj_type='e' AND subj_id=? ORDER BY id LIMIT 24", (obj["id"],)).fetchall()
                        add([c for r in related if (c := self._memory(r))])
            for word, evidence in kinds.items():
                # Do not draw deductions from disputed or weak personal claims.
                safe = [aid for aid in evidence if self.repo.assertion(aid)["confidence"] >= 0.75
                        and self.convo.engine.ask(query_from_row(self.repo, self.repo.assertion(aid))).status != CONFLICTING]
                claims, alternatives = self._lexical(word, context, safe, topic.bindings.get(word))
                add(claims)
                if alternatives:
                    ambiguities.append((word, alternatives))
        if not paragraphs:
            message = ("I've covered the supported connections I have so far. Tell me another fact or ask about "
                       "a particular part, and we can develop it further." if continuing else
                       f"I don't have enough supported information to explain {topic.name} yet.")
            return message, {"intent": "explain", "claims": [], "expression": style, "exhausted": continuing}
        intro = self.convo.social.phrases.pick("explain-intro:" + style["tone"],
                                             INTRO[style["tone"]], topic=topic.name)
        paragraphs[0] = intro + " " + paragraphs[0]
        if ambiguities:
            word, alternatives = ambiguities[0]
            paragraphs.append(self._usual_note(word, alternatives, context, topic))
        elif style["follow_up"]:
            paragraphs.append(f"Which part of {topic.name} would you like to explore further?")
        return "\n\n".join(paragraphs), {"intent": "explain", "topic": topic.name,
            "claims": accepted, "rejected": rejected, "expression": style,
            "paragraphs": len(paragraphs), "wordnet": "Open English WordNet 2025+"}

    def relation_answer(self, frame):
        """'Does Howard own an animal?' when he was told 'Howard owns a beagle': the stored fact plus a
        verified WordNet path from the thing's kind to the asked kind."""
        if frame.relation != "rel" or frame.slot or not frame.subject or not frame.obj or frame.obj.proper:
            return None
        eid = self.repo.find_entity(frame.subject.surface or frame.subject.head)
        if eid is None:
            return None
        context = self._context(eid)
        target = frame.obj.head
        for row in self.repo.assertions(f"rel:{frame.verb}", subj=("e", eid), polarity=True):
            if row["confidence"] < 0.75 or self.convo.engine.ask(query_from_row(self.repo, row)).status == CONFLICTING:
                continue
            if row["obj_type"] == "e":
                thing = self.repo.entity_row(row["obj_id"])
                if not set(frame.obj.modifiers) <= set(self.repo.properties(thing["id"])):
                    continue  # "a blue animal" needs blue to be known, not assumed
                if thing["proper"]:  # "Howard owns Rex" + "Rex is a beagle": use Rex's stated kind
                    kinds = self.repo.assertions("instance_of", subj=("e", thing["id"]), polarity=True)
                    if not kinds:
                        continue
                    kind = self.repo.concept_lemma(kinds[0]["obj_id"])
                elif thing["kind_concept_id"] is None:
                    continue
                else:
                    kind = self.repo.concept_lemma(thing["kind_concept_id"])
            else:
                if frame.obj.modifiers:
                    continue
                kind = self.repo.concept_lemma(row["obj_id"])
            selected = self._select(kind, context)
            sense = selected.sense or next(iter(selected.alternatives), None)
            if sense is None:
                continue
            path = self.wordnet.path_to(sense.id, target)
            if not path or not self.wordnet.verify_path(path):
                continue
            labels = [self.wordnet.sense(ident).lemma for ident in path]
            core = realizer.realize_frame(frame, True)
            fact = realizer.realize_assertion(self.repo, row)
            text = (f"Yes. {realizer.sentence(core)} I know this because {fact}, and {article(kind)} {kind} "
                    f"is a kind of {target} (" + " \u2192 ".join(labels) + ").")
            return text, {"status": "yes", "assertions": [row["id"]], "synsets": path,
                          "source": "Open English WordNet 2025+"}
        return None

    def category_answer(self, frame):
        """Additional positive evidence for an otherwise unknown simple category query."""
        if frame.relation == "rel":
            return self.relation_answer(frame)
        if frame.relation != "is_a" or frame.slot or not frame.subject or not frame.obj:
            return None
        name = frame.subject.surface or frame.subject.head
        eid = self.repo.find_entity(name)
        context = self._context(eid)
        target = frame.obj.head
        evidence = []
        if eid:
            for row in self.repo.assertions("instance_of", subj=("e", eid), polarity=True):
                if row["confidence"] >= 0.75 and self.convo.engine.ask(query_from_row(self.repo, row)).status != CONFLICTING:
                    evidence.append((self.repo.concept_lemma(row["obj_id"]), [row["id"]]))
        elif not frame.subject.proper:
            evidence.append((frame.subject.head, []))
        for word, aids in evidence:
            selected = self._select(word, context)
            if not selected.sense:
                continue
            path = self.wordnet.path_to(selected.sense.id, target)
            if not path or not self.wordnet.verify_path(path):
                continue
            core = realizer.realize_frame(frame, True)
            labels = [self.wordnet.sense(ident).lemma for ident in path]
            text = f"Yes. {realizer.sentence(core)} The category connection is " + " to ".join(labels) + "."
            return text, {"status": "yes", "assertions": aids, "synsets": path,
                          "source": "Open English WordNet 2025+", "sense_confidence": selected.confidence}
        return None
