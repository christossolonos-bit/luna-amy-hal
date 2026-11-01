"""The text pipeline, per sentence:
segment -> strip filler -> parse -> resolve references -> learn / correct / infer -> plan -> realize -> validate.

A turn may be a whole paragraph from a person or another bot: every sentence is handled, what
was understood is learned or answered, and what wasn't is reported and kept as an open question.
"""

import json
import re
from nlp.lexicon import article
import time
from datetime import datetime, timezone
from dataclasses import asdict, dataclass, replace

from app.corrections import Corrector
from app.people import People
from app.learning import Learner, Resolver, RuleLearner, Unresolved, Unsupported, gap_for
from db.repository import Repository
from dialogue import realizer
from dialogue.curiosity import Curiosity
from dialogue.discourse import Discourse
from dialogue.drive import LifeDrive
from dialogue.inquiry import Inquiry
from dialogue.understanding import Understanding
from dialogue.exposition import Exposition, SENSITIVE
from dialogue.lexical import LexicalReasoner
from dialogue.memory import Recall
from dialogue.self_explain import SelfExplainer
from dialogue.web_lookup import WebLookup
from dialogue.definitions import Definitions
from dialogue.news import News
from dialogue.guide import Guide
from dialogue.thread import Thread
from dialogue.speech_acts import SpeechActs
from dialogue.intents import router as intent_router
from dialogue.acts import Acts
from dialogue.reply_planner import Planner
from dialogue.recall import Recall as MemoryFirst
from dialogue.world_questions import WorldQuestions
from dialogue.self_model import SelfModel
from dialogue.social import Social
from dialogue.planner import CLARIFY, plan_answer
from dialogue.state import DialogueState
from dialogue.validator import validate, validate_row
from nlp.discourse import split_sentences
from nlp.lexicon import Lexicon
from dialogue import examples
from nlp.parser import ParseError, parse_noun_phrase, parse_sentence
from nlp.morphology import lemmatize_noun
from nlp.semantic_frames import Frame, NounPhrase, RuleFrame
from nlp.wordnet import WordNet
from reasoning.engine import Engine, query_from_row
from app.behaviors import enabled as behavior_enabled
from reasoning.proofs import CONFLICTING, NO, UNKNOWN, YES, Answer, Query
from dialogue.gap_chain import GapChain

FALLBACK = "I can't phrase that answer reliably, so I won't guess."
# Chat that isn't a statement about anything: never a fact's subject ("Thank you" is not someone named Thank).
NOT_A_SUBJECT = {"thank", "thanks", "sorry", "just", "so", "then", "now", "here", "there", "right", "well", "yeah",
                 "yes", "no", "nope", "ok", "okay", "lol", "lmao", "hmm", "um", "uh", "oh", "hey", "hi", "hello",
                 "please", "maybe", "anyway", "also", "actually", "really", "sure", "fine", "cool", "nice", "wow",
                 "damn", "dang", "shit", "fuck", "bro", "dude", "man", "guys", "chill", "keep", "stop", "wait",
                 "listen", "look", "let", "make", "try", "go", "come", "get", "take", "give", "tell", "say", "see",
                 "seriously", "honestly", "basically", "literally", "obviously", "good", "great", "bad", "not",
                 "fucking", "fuckin", "fucked", "what", "who", "where", "when", "why", "how", "which", "there",
                 "leave", "put", "grab", "reset", "move", "use", "check", "pull", "send", "show", "add"}
JUNK = re.compile(r"`|https?://|www\.|<@!?\d+>|\*\*|__|[{}\[\]<>|=#]")  # emoji are decoration, not junk
MAX_FACT_WORDS = 30
QUESTION_LEADS = {"what", "who", "where", "when", "why", "how", "which", "does", "do", "did", "can", "could",
                  "will", "would", "should", "is", "are", "have", "has", "forget", "repeat", "search", "tell",
                  "say", "remember", "please", "thanks", "thank", "hi", "hello", "hey"}
PARTICLES = {"off", "on", "up", "out", "back", "down", "in", "away", "over", "around", "through", "along"}
FUNCTION_WORDS = {"i", "you", "we", "they", "he", "she", "it", "me", "him", "her", "them", "us", "the", "a", "an",
                  "and", "or", "but", "so", "to", "of", "not", "this", "that", "these", "those", "my", "your"}
MENTAL_VERBS = {"know", "see", "mean", "think", "guess", "understand", "agree", "wonder", "care", "bet", "suppose",
                "hope", "remember", "forget", "get", "hear", "feel", "believe", "promise", "swear", "doubt"}
LEADING_FILLER = r"(?:(?:ok(?:ay)?|so|well|um|uh|hey|and|actually|alright)\W+)*"
RETRACT_RE = re.compile(
    r"^\W*" + LEADING_FILLER + r"(?:no\W+)?(?:(?:that'?s|that is|this is|you'?re|you are)\s+"
    r"(?:wrong|not right|not correct|not true|incorrect|false|mistaken)"
    r"|scratch that|never ?mind|undo(?: that)?|forget (?:it|that)|wrong)\W*$", re.IGNORECASE)
FORGET_RE = re.compile(r"^\W*" + LEADING_FILLER + r"(?:please\s+)?(?:forget|disregard|ignore)\s+(?:that\s+|about\s+)?(.+?)\W*$",
                       re.IGNORECASE)


@dataclass
class Reply:
    text: str
    trace: dict


@dataclass
class DigestResult:
    facts: list[int]                  # assertion ids stored
    skipped: list[tuple[str, str]]    # (clause text, why it wasn't stored)


class Conversation:
    def __init__(self, repo: Repository, speaker: str = "user", self_name: str = "Hal",
                 max_depth: int = 16, budget: int = 5000, wordnet: WordNet | None = None,
                 curious: bool = False, elaborate: bool | None = None, persona: str | None = None) -> None:
        self.persona=(persona or self_name).casefold()
        self.repo = repo
        self.self_name = self_name
        self.engine = Engine(repo, max_depth, budget)
        self.state = DialogueState(speaker=speaker)
        self.resolver = Resolver(repo, self.state, self_name)
        self.learner = Learner(repo, self.resolver)
        self.rule_learner = RuleLearner(repo, self.resolver)
        self.corrector = Corrector(repo, self.engine, self.state)
        self.recall = Recall(self)
        self.self_model = SelfModel(repo)
        if self.persona == "amy":
            self.self_model.curiosity_bias = 0.2
        self.social = Social(self)
        from dialogue.variation import ScriptedReplies
        self.scripted_replies = ScriptedReplies(self.social.phrases)
        self.gaps = GapChain(self)
        self.inquiry = Inquiry(self)
        self.exposition = Exposition(self, wordnet if wordnet is not None else WordNet())
        self.lexical = LexicalReasoner(self, self.exposition.wordnet)
        self.drive = LifeDrive(self, rng=self.social.rng)
        self.curiosity = Curiosity(self, rng=self.social.rng)
        self.curious = curious  # ask follow-up questions to keep conversation going
        self.elaborate = curious if elaborate is None else elaborate  # speak in developed passages
        self.discourse = Discourse(self)
        self.people = People(repo)
        self.understanding = Understanding(self)
        self.explainer = SelfExplainer(self)
        self.web = WebLookup(repo)
        self.definitions = Definitions(self)
        self.news = News(repo)
        self.guide = Guide(self)
        self.thread = Thread(self)
        self.speech = SpeechActs(self)
        self.acts = Acts(self)
        self.reply_planner = Planner(self)
        self.memory_first = MemoryFirst(self)
        self.world = WorldQuestions(self)
        self.web.world = self.world
        self.web.wordnet = self.exposition.wordnet
        self.web.digest = self.digest
        self.news.digest = self.digest
        self.person_key: str | None = None
        repo.conn.execute("""CREATE TABLE IF NOT EXISTS asked_questions(
            id INTEGER PRIMARY KEY, speaker TEXT NOT NULL, signature TEXT NOT NULL, status TEXT,
            answer TEXT, asked_at REAL NOT NULL)""")
        self.turn_learned: list[tuple[str, int]] = []
        self.self_id = repo.entity(self_name)
        # He starts with no idea what he is: anything about himself has to be told to him or worked out.
        for row in repo.assertions("instance_of", subj=("e", self.self_id)):
            if row["source_utterance_id"] is None:
                repo.retract(row["id"], None, "not given to him; what he is, he has to learn")

    # --- self-knowledge (used by dialogue.social) ------------------------------------

    def self_names(self) -> list[str]:
        rows = self.repo.conn.execute("SELECT alias FROM aliases WHERE entity_id = ? ORDER BY id", (self.self_id,))
        return list(dict.fromkeys([self.self_name, *(r[0] for r in rows)]))

    def add_self_alias(self, alias: str) -> None:
        if alias and self.repo.find_entity(alias) in (None, self.self_id):
            self.repo.add_alias(self.self_id, alias)

    def facts_about_self(self) -> list[str]:
        rows = [r for p in ("has_property", "rel:%") for r in self.repo.assertions(p, subj=("e", self.self_id), polarity=True)]
        rows += [r for r in self.repo.assertions("instance_of", subj=("e", self.self_id), polarity=True)
                 if r["source_utterance_id"] is not None]
        return [t for r in rows if (t := realizer.realize_assertion(self.repo, r))]

    def who_did_to_self(self, verbs) -> list[str]:
        names = []
        for verb in verbs:
            for row in self.repo.assertions(f"rel:{verb}", obj=("e", self.self_id), polarity=True):
                if row["subj_type"] == "e":
                    name = self.repo.entity_row(row["subj_id"])["name"]
                    names.append(realizer.display_name(name))
        return list(dict.fromkeys(names))

    def taught_about_self(self, adjective: str) -> bool | None:
        cid = self.repo.find_concept(adjective)
        if cid is None:
            return None
        rows = self.repo.assertions("has_property", subj=("e", self.self_id), obj=("c", cid))
        return bool(rows[-1]["polarity"]) if rows else None

    def favorite(self, owner_word: str, noun: str) -> str | None:
        """'What's your/my/Howard's favorite color?' from things taught like 'Your favorite color is blue'."""
        if owner_word == "your":
            owner, said = self.self_id, "My"
        elif owner_word == "my":
            owner, said = self.repo.find_entity(self.state.speaker), "Your"
        else:
            name = owner_word[:-2].strip()
            owner, said = self.repo.find_entity(name), f"{realizer.display_name(name)}'s"
        kind = self.repo.find_concept(lemmatize_noun(noun.split()[-1])[0])
        if owner is None or kind is None:
            return None
        for row in reversed(self.repo.assertions("rel:own", subj=("e", owner), polarity=True)):
            item = self.repo.entity_row(row["obj_id"])
            if row["obj_type"] != "e" or item["kind_concept_id"] != kind:
                continue
            props = self.repo.properties(item["id"])
            if "favorite" in props or "favourite" in props:
                values = [p for p in props if p not in ("favorite", "favourite")]
                links = [realizer.term_text(self.repo, r["obj_type"], r["obj_id"])
                         for r in self.repo.assertions("rel:be", subj=("e", item["id"]), polarity=True)]
                answer = realizer.join_clauses(values) or (links[0] if links else None)
                if answer:
                    return f"{said} favorite {noun} is {answer}."
        return None

    # --- used by dialogue.inquiry -------------------------------------------------------

    def learn_frame(self, frame: Frame) -> str | None:
        uid = self.repo.log_utterance(self.state.speaker, f"[answer] {realizer.realize_frame(frame)}")
        self.resolver.begin(uid)
        ids = self.learner.learn(frame, uid)
        self.turn_learned += [("a", aid) for aid in ids]
        self.self_model.event("learned", len(ids))
        texts = [t for aid in ids if (t := realizer.realize_assertion(self.repo, self.repo.assertion(aid)))]
        return "; ".join(texts) or None

    def digest(self, text: str, source: str, confidence: float = 0.8) -> "DigestResult":
        """Read a paragraph (a Wikipedia summary, a news story, a dictionary meaning, a long message) into
        facts he can reason over. Each sentence is split into simple clauses, each clause parsed and checked
        like anything else he's told, and every fact stored is marked with where it came from."""
        from nlp.clauses import split_text
        from nlp.parser import ParseError
        with self.repo.conn:
            self.repo.conn.execute("CREATE TABLE IF NOT EXISTS fact_sources(assertion_id INTEGER PRIMARY KEY, "
                                   "source TEXT, confidence REAL)")
            # the reading is recorded as where the facts came from, but not as something said in conversation
            uid = self.repo.conn.execute("INSERT INTO utterances(speaker, text) VALUES (?, ?)",
                                         (f"source:{source}", text[:2000])).lastrowid
        self.resolver.begin(uid)
        realizer.set_perspective(self.self_id, self.self_name, self.repo.find_entity(self.state.speaker),
                                 self.state.speaker)  # this conversation's "I" and "you", not a previous one's
        topic = source.split(":", 1)[-1].split(":")[0].strip() if ":" in source else None
        result = DigestResult([], [])
        lexicon = self.lexicon()
        last_subject = topic
        kinds_of: dict[str, set[str]] = {}
        for piece in split_text(text):
            clause = piece.text
            # "It is home to many companies": "it" is what the paragraph is about, or the last thing named
            # "It is home to ...": what the paragraph is about first, else the last thing it named
            about = topic or last_subject
            if about and re.match(r"^(?:It|They|He|She|This)\b", clause) and not clause.lower().startswith("it is time"):
                clause = re.sub(r"^(?:It|They|He|She|This)\b", about, clause, count=1)
            # "The city was founded in 1887": the city this paragraph already said its subject is
            kind = re.match(r"^The (\w+)\b", clause)
            if kind:
                owner = next((s for s in (topic, last_subject) if s and kind.group(1).lower() in kinds_of.get(s, set())), None)
                if owner:
                    clause = f"{owner}{clause[kind.end():]}"
            try:
                parsed = parse_sentence(clause, lexicon)
            except (ParseError, Exception) as exc:
                result.skipped.append((clause, f"couldn't parse: {exc}"))
                continue
            self.resolver.begin(uid)  # a fresh lookup cache per clause: it's keyed by object identity
            frames = parsed if isinstance(parsed, list) else [parsed]
            for frame in frames:
                if getattr(frame, "act", None) != "assert":
                    result.skipped.append((clause, "not a statement"))
                    continue
                reason = self._unlearnable(frame, clause)
                if reason:
                    result.skipped.append((clause, reason))
                    continue
                try:
                    ids = self.learner.learn(frame, uid)
                except Exception as exc:
                    result.skipped.append((clause, f"couldn't store: {exc}"))
                    continue
                conf = round(confidence * piece.confidence, 3)
                with self.repo.conn:
                    for aid in ids:
                        self.repo.conn.execute("INSERT OR REPLACE INTO fact_sources VALUES (?, ?, ?)", (aid, source, conf))
                result.facts += ids
                subject = getattr(frame.subject, "surface", None) if frame.subject else None
                if subject and frame.subject.proper:
                    last_subject = subject
                    if frame.relation == "is_a" and frame.obj is not None:
                        kinds_of.setdefault(subject, set()).add(frame.obj.head)
        self.turn_learned += [("a", aid) for aid in result.facts]
        if result.facts:
            self.self_model.event("learned", len(result.facts))
        return result

    def answer_frame(self, frame: Frame) -> str:
        return self._answer(frame, None, {}, {})

    def lexicon(self) -> Lexicon:
        return Lexicon(self.repo.learned_verbs(), proper_form=self.exposition.wordnet.proper_form)

    def overhear(self, text: str, speaker: str, learn: bool = False, speaker_id: str | None = None,
                 is_bot: bool = False) -> None:
        """Speech not addressed to me: remember it as conversation history (searchable with
        'what did Bob say about ...'), and only learn from it when `learn` is set. Overheard voices
        are remembered, but they aren't people I've talked with."""
        name = self.people.identify(speaker, speaker_id, is_bot)
        self.people.heard(self.people.current_key, addressed=False)
        if learn:
            previous = self.state.speaker
            self.handle(text, speaker=speaker, speaker_id=speaker_id, is_bot=is_bot)
            self.state.speaker = previous
            return
        self.repo.log_utterance(name, text)

    def handle(self, text: str, speaker: str | None = None, speaker_id: str | None = None,
               is_bot: bool = False) -> Reply:
        # Who is this, really: a stable identity, whatever name they're showing today.
        self.state.speaker = self.people.identify(speaker or self.state.speaker, speaker_id, is_bot)
        self._speaker_is_bot = is_bot
        self.person_key = self.people.current_key
        self.people.heard(self.person_key, addressed=True)
        started = time.perf_counter()
        realizer.set_perspective(self.self_id, self.self_name, self.repo.find_entity(self.state.speaker),
                                 self.state.speaker)
        self.self_model.event("turn")
        if SENSITIVE.search(text):
            self.self_model.event("sad_context", detail="Sensitive topic in conversation")
        text = re.sub(r"\s+", " ", text).strip()
        uid = self.repo.log_utterance(self.state.speaker, text)
        self._raw_text = text
        text = self._addressed_text(text)
        text, self._frustrated = self._frustration(text)
        if self._frustrated and not re.search(r"\w", text):
            planned = self.reply_planner.respond(self._raw_text) or self.reply_planner.build(
                self.reply_planner.understand("That was wrong."), ["repair"])
            return self._finish(uid, {"heard": text, "speaker": self.state.speaker}, planned, started,
                                intent="frustration")
        self._sensitive_turn = bool(SENSITIVE.search(text))
        trace: dict = {"heard": text, "speaker": self.state.speaker, "sentences": []}
        learned: list[str] = []
        conflicts: list[str] = []
        answers: list[str] = []
        failed: list[tuple[str, str]] = []
        dropped: list[str] = []
        self.turn_learned = []

        from dialogue.social import GREETING_RE, FAREWELL_RE
        social_text = self.social._strip_vocative(text)
        greeting = GREETING_RE.match(social_text)
        if greeting and not behavior_enabled('greetings'):
            text = self.social._strip_vocative(social_text[greeting.end():])
            if not text.strip(' ?.!'):
                self._resolved = social_text
                return self._finish(uid, trace, '', started, intent='greeting-disabled')
        if FAREWELL_RE.match(social_text) and not behavior_enabled('farewells'):
            self._resolved = social_text
            return self._finish(uid, trace, '', started, intent='farewell-disabled')

        thinking = getattr(self, "thinking", None)
        if thinking is not None:
            reflected = thinking.respond(self, text)
            if reflected is not None:
                reply, evidence = reflected
                self._resolved = text
                return self._finish(uid, trace, reply, started, intent="thinking", thinking=evidence)

        text, direct = self.thread.resolve(text, uid)  # read against what we've been talking about
        self._resolved = text
        if direct is not None:
            return self._finish(uid, trace, direct, started, intent="thread")
        if text.rstrip().endswith("?") and (re.search(r"\b(?:my|your) (?!name\b|favou?rite\b)[a-z]+(?:'s)?\b", text, re.I)
                                            or re.match(r"^\W*(?:are|were) you\b.+\bor\b", text, re.I)):
            recalled = self.memory_first.answer(text, strict=True)
            if recalled:
                return self._finish(uid, trace, recalled, started, intent="recall")
        self.world.hear(text)  # what people bring up is what he gets curious about
        from dialogue.web_lookup import ASK as SEARCH_ASK
        routed = None if SEARCH_ASK.search(text) else intent_router().route(text)  # a search says what it wants
        if routed and self._understood_already(text) and routed.score < 1.0:
            routed = None
        if routed and not text.rstrip().endswith("?") and routed.canonical.endswith("?") \
                and re.match(r"^\W*(?:you(?:'re| are)|i(?:'m| am)|he|she|it|they|we)\b", text, re.I):
            routed = None  # "You are conscious" is telling him something, not asking
        if routed and routed.canonical.lower().rstrip("?.") != text.lower().rstrip("?."):
            trace["routed"] = {"intent": routed.intent, "score": routed.score, "as": routed.canonical}
            text = routed.canonical
        answered = self.world.respond(text) or self.speech.respond(text) or self.definitions.try_bridge(text) or self.definitions.learn(text) \
            or self.definitions.answer(text) or self.web.try_approve(self.state.speaker, text) or self.understanding.try_confirm(text) or self.understanding.name_statement(text) \
            or self.understanding.heard_me(text) or self._order_or_remark(text) \
            or self.inquiry.try_answer(text) \
            or self.curiosity.try_answer(text)
        if answered is None and not trace.get("routed"):
            answered = self.acts.offer_or_request(text)  # "Can I help you?", "Could you take a picture?"
        if answered is not None:
            return self._finish(uid, trace, answered, started, intent="answer-to-my-question")

        guided = self.guide.respond(text)  # "how do I get the news?", "explain it like I'm five"
        if guided is not None:
            return self._finish(uid, trace, guided, started, intent="guide")
        reported = self.news.respond(text)  # "what's in the news?", "tell me more about the strike"
        if reported is not None:
            reported += self.world.mention(self.world.read(reported, "the news"))  # what he can't place
            return self._finish(uid, trace, reported, started, intent="news")
        looked = self.web.asked(self.state.speaker, text)  # "look up pit bulls"
        if looked is not None:
            return self._finish(uid, trace, looked, started, intent="web-lookup")
        command = self._command(text, uid, trace, started)
        if command is not None:
            return command

        explained = self.explainer.respond(text)  # "how do you work?", "how do I teach you?"
        if explained is not None:
            return self._finish(uid, trace, explained, started, intent="explain-self")
        pondered = self.drive.respond(text)
        if pondered is not None:
            return self._finish(uid, trace, pondered, started, intent="drive")

        social, text = self.social.respond(text)
        if social is not None and not text:
            return self._finish(uid, trace, self._develop(social, [], "social", social=True), started,
                                intent="social")
        if social is not None:  # "Hey Adam, forget that ...": greet, then handle the command itself
            command = self._command(text, uid, trace, started, greeting=social)
            if command is not None:
                return command
            explained = self.explainer.respond(text)
            if explained is not None:
                return self._finish(uid, trace, f"{social} {explained}", started, intent="explain-self")
        lexical = self.lexical.respond(text)
        if lexical is not None:  # comparisons, parts, kinds, synonyms, meanings from WordNet
            reply, plan = lexical
            trace["lexical_plan"] = plan
            if plan.get("status"):
                trace["status"] = plan["status"]
            self.self_model.event("answered")
            return self._finish(uid, trace, reply, started, intent=plan["intent"])
        exposition = self.exposition.respond(text)
        if exposition is not None:
            reply, plan = exposition
            trace["paragraph_plan"] = plan
            if plan.get("claims"):
                self.self_model.event("answered")
            return self._finish(uid, trace, reply, started, intent=plan["intent"])
        self.questions: list[str] = []

        for sentence in split_sentences(text):
            item: dict = {"text": sentence}
            trace["sentences"].append(item)
            self.resolver.begin(uid)
            early = self.understanding.pre(sentence)
            if early:
                item["understood_as"] = early[0]
                answers.append(self.understanding.answer(early[0], early[1], uid))
                continue
            try:
                frames = parse_sentence(sentence, self.lexicon(), names={self.self_name.lower()})
            except ParseError as exc:
                fragment = self._fragment_correction(sentence)
                if fragment is None:
                    # Passes 2 and 3: work out what this is, then reason over it.
                    kind, details = self.understanding.classify(sentence, str(exc))
                    item["understood_as"] = kind
                    if kind == "simplified":
                        fragment = details["frames"]
                    else:
                        reply = self.understanding.answer(kind, details, uid)
                        if reply:
                            if kind == "unclear":  # answered in kind, but still not understood: keep it open
                                self.repo.open_question(uid, {"unparsed": sentence, "reason": str(exc)})
                            answers.append(reply)
                            continue
                if fragment is None:
                    item["error"] = str(exc)
                    failed.append((sentence, str(exc)))
                    self.repo.open_question(uid, {"unparsed": sentence, "reason": str(exc)})
                    question = self.inquiry.ask_about_unparsed(sentence)
                    if question:
                        self.questions.append(question)
                    continue
                frames = fragment if isinstance(fragment, list) else [fragment]
            item["frames"] = [asdict(f) for f in frames]
            recent = {ident for kind, ident in self.state.last_learned if kind == "a"}
            for frame in frames:
                try:
                    if isinstance(frame, RuleFrame):
                        self._learn_rule(frame, uid, item, learned)
                    elif frame.act == "assert":
                        reason = self._unlearnable(frame, sentence)
                        if reason:
                            item.setdefault("errors", []).append(reason)
                            failed.append((sentence, reason))
                            continue
                        new_ids = self._learn(frame, uid, item, learned, conflicts)
                        if not new_ids:
                            failed.append((sentence, "could not state it back reliably"))
                            continue
                        if frame.correction:
                            for aid in new_ids:
                                dropped += [d for d in self.corrector.supersede(aid, uid, recent)
                                            if d not in dropped]
                        self.state.last_assert_frame = frame
                    else:
                        reason = self._unanswerable(frame)
                        if reason:
                            item.setdefault("errors", []).append(reason)
                            failed.append((sentence, reason))
                            continue
                        answers.append(self._answer(frame, uid, item, trace))
                except Unresolved as exc:
                    item.setdefault("errors", []).append(str(exc))
                    self.questions.append(f"Who or what do you mean by '{exc.gap.split(chr(39))[1]}'?"
                                          if "'" in exc.gap else str(exc))
                except Unsupported as exc:
                    item.setdefault("errors", []).append(str(exc))
                    failed.append((sentence, str(exc)))
                self.state.last_frame = frame

        if dropped:  # the correction settled what would otherwise be reported as a conflict
            conflicts = []
        reply = self._compose(learned, conflicts, answers, failed, len(trace["sentences"]) == 1, dropped,
                              self.questions)
        if social:
            reply = f"{social} {reply}"
        if not answers and not learned and (failed or self.questions):
            trace["intent"] = CLARIFY
        elif learned or answers:
            reply = self._develop(reply, self._mentioned(trace), "learned" if learned else "answered")
        statuses = [a["status"] for s in trace["sentences"] for a in s.get("answers", [])]
        self.self_model.event("learned", len(learned))
        self.self_model.event("answered", sum(st in (YES, NO) for st in statuses))
        self.self_model.event("unknown", statuses.count(UNKNOWN))
        self.self_model.event("misunderstood", len(failed))
        self.self_model.event("corrected", len(dropped))
        return self._finish(uid, trace, reply, started, conflict=bool(conflicts), dropped=dropped)

    def _mentioned(self, trace: dict) -> list[str]:
        """Named people and things this turn was about, most recent first."""
        names = []
        for item in trace["sentences"]:
            for frame in item.get("frames", []):
                for part in ("subject", "obj"):
                    np = frame.get(part) if isinstance(frame, dict) else None
                    if np and np.get("proper"):
                        names.append(np.get("surface") or np.get("head"))
        return list(dict.fromkeys(reversed(names)))

    def _develop(self, reply: str, mentioned: list[str], intent: str, social: bool = False) -> str:
        """Grow the core reply into a passage: what else he knows about who and what is in focus, how it
        connects, his own angle, and a question to keep the conversation going."""
        question = None
        if self.curious and behavior_enabled("curiosity"):
            words = " ".join(i["text"] for i in self.state.last_trace["sentences"]) if self.state.last_trace else ""
            question = self.curiosity.follow_up(reply, mentioned, getattr(self, "_sensitive_turn", False), social,
                                                set(re.findall(r"[a-z]+", words.lower())))
        if not self.elaborate:
            return f"{reply} {question}" if question else reply
        focus, kinds = [], []
        names = list(mentioned)
        for name in names:
            eid = self.repo.find_entity(name)
            if eid and eid not in focus:
                focus.append(eid)
        for kind, ident in self.turn_learned:
            row = self.repo.assertion(ident) if kind == "a" else None
            if row is None:
                continue
            if row["subj_type"] == "e" and self.repo.entity_row(row["subj_id"])["proper"] and row["subj_id"] not in focus:
                focus.insert(0, row["subj_id"])
            if row["predicate"] == "instance_of":
                kinds.append(self.repo.concept_lemma(row["obj_id"]))
            elif row["obj_type"] == "e" and not self.repo.entity_row(row["obj_id"])["proper"]:
                kinds.append(self.repo.concept_lemma(self.repo.entity_row(row["obj_id"])["kind_concept_id"]))
        return self.discourse.expand(reply, focus, kinds, intent, question)

    def _command(self, text: str, uid: int, trace: dict, started: float, greeting: str | None = None):
        """Memory questions, 'that's wrong' and 'forget that ...', which bypass ordinary parsing."""
        lead = f"{greeting} " if greeting else ""
        recalled = self.recall.answer(text, uid)
        if recalled is not None:
            reply, status = recalled
            if status:
                trace["status"] = status
            return self._finish(uid, trace, lead + reply, started, intent="recall")
        if RETRACT_RE.match(text):
            dropped = self.corrector.retract_last(uid)
            reply = (f"Okay, I've taken that back: I no longer believe that {'; '.join(dropped)}."
                     if dropped else "Sorry, I got something wrong. There's nothing recent I learned to take back, so tell me what was wrong and I'll fix it.")
            return self._finish(uid, trace, lead + reply, started, intent="retract", dropped=dropped)
        forget = FORGET_RE.match(text)
        if forget:
            reply = self._forget(forget.group(1), uid, trace, started)
            if greeting:
                reply.text = lead + reply.text
            return reply
        return None

    INSULT = re.compile(r"(?:,?\s*\byou\s+)?\b(?:dumb|stupid|fucking|fuckin|god ?damn(?:ed)?|useless|worthless)?\s*"
                        r"\b(?:fuck|fucker|ass|asshole|idiot|moron|prick|bitch|shit|piece of shit|dumbass)\b\W*", re.I)

    def _frustration(self, text: str) -> tuple[str, bool]:
        """Swearing at him says how they feel; take the insult out and keep what they were saying."""
        cleaned = self.INSULT.sub(" ", text)
        cleaned = re.sub(r"\b(?:fucking|fuckin|freaking|goddamn|damn)\s+", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
        frustrated = cleaned != re.sub(r"\s+", " ", text).strip(" ,") and bool(
            re.search(r"\b(?:fuck|shit|idiot|moron|prick|bitch|dumb|stupid|ass)", text, re.I))
        return (cleaned if frustrated else text), frustrated

    def _things(self, row) -> set[str]:
        out = set()
        if row["obj_type"] == "c" and row["obj_id"] != self.repo.none_concept():
            out.add(self.repo.concept_lemma(row["obj_id"]))
        elif row["obj_type"] == "e":
            out.add(self.repo.entity_row(row["obj_id"])["name"].lower())
        for arg in self.repo.args(row["id"]):
            if arg["role"].startswith("_"):
                continue
            if arg["value_type"] == "c" and arg["value_id"]:
                out.add(self.repo.concept_lemma(arg["value_id"]))
            elif arg["value_type"] == "e" and arg["value_id"]:
                row_e = self.repo.entity_row(arg["value_id"])
                out.add(row_e["name"].lower() if row_e["proper"] else
                        self.repo.concept_lemma(row_e["kind_concept_id"]) if row_e["kind_concept_id"] else "")
            elif arg["literal"]:
                out.add(arg["literal"].lower())
        return {t for t in out if t}

    SINGLE = {("rel:live", "in"), ("rel:work", "at"), ("rel:work", "in"), ("rel:be", "from"), ("rel:come", "from")}

    def _replace_single_valued(self, aid: int, uid: int, learned: list[str]) -> None:
        """'Jen lives in Seattle now' after 'Jen lives in Denver': a person lives in one place at a time."""
        row = self.repo.assertion(aid)
        if not row["polarity"]:
            return
        roles = {a["role"] for a in self.repo.args(aid)}
        keys = {(row["predicate"], r) for r in roles} & self.SINGLE
        if not keys:
            return
        for old in self.repo.assertions(row["predicate"], subj=(row["subj_type"], row["subj_id"]), polarity=True):
            if old["id"] == aid or not ({(old["predicate"], a["role"]) for a in self.repo.args(old["id"])} & keys):
                continue
            if self._things(old) - {"now"} == self._things(row) - {"now"}:
                continue
            text = realizer.realize_assertion(self.repo, old)
            self.repo.retract(old["id"], uid, "replaced: a newer value for something that has one value at a time")
            if text:
                learned.append(f"I no longer believe that {text[0].lower()}{text[1:]}")

    def _undo_by_meaning(self, aid: int, uid: int, learned: list[str]) -> None:
        row = self.repo.assertion(aid)
        if row["polarity"]:
            return
        mine = self._things(row)
        if not mine:
            return
        for old in self.repo.conn.execute(
                "SELECT * FROM assertions WHERE status='active' AND polarity=1 AND subj_type=? AND subj_id=? AND id != ?",
                (row["subj_type"], row["subj_id"], aid)).fetchall():
            if self._things(old) & mine:
                text = realizer.realize_assertion(self.repo, old)
                self.repo.retract(old["id"], uid, "contradicted by a correction about the same thing")
                if text:
                    learned.append(f"I no longer believe that {text[0].lower()}{text[1:]}")

    NOT_KNOWN = re.compile(r"I don'?t know|Nobody'?s told|didn'?t catch|couldn'?t work out|asking me something about|"
                           r"telling me something about|don'?t have a clear reading|didn'?t get|couldn'?t pin down|"
                           r"Say it a different way|I'd like to look that up|I could find out|Here's what I'd search|"
                           r"I didn'?t follow|not following|isn'?t clear to me|Who or what do you mean", re.I)
    LOST_PART = re.compile(r"I didn't catch what that was about|I think you're (?:telling|asking) me something about|"
                           r"couldn't pin down|couldn't work out exactly|Who or what do you mean by|Say it a different way|"
                           r"What's the main thing you want me to know|Could you ask it another way", re.I)

    def _drop_lost_parts(self, reply: str) -> str:
        """In a reply to several sentences, if part of it answered something, leave out the parts that only
        say another sentence was lost: the answer is what matters."""
        parts = re.split(r"(?<=[.!?])\s+", reply.strip())
        lost = [p for p in parts if self.LOST_PART.search(p)]
        if not lost or len(lost) == len(parts):
            return reply
        fillers = re.compile(r"^(?:Okay|Alright|Sorry|Hold on|One moment|Let me [a-z ]+|I need to pause there|"
                             r"I'm sorry|I need some context for this part|Let's clarify a detail|"
                             r"I have a specific question first|Let me narrow down what I need to know)\.$")
        kept = [p for p in parts if not self.LOST_PART.search(p)]
        if all(fillers.match(p) for p in kept):
            return reply
        return " ".join(kept) + " " + self.social.phrases.pick("unparsed-remainder", (
            "I didn't follow the rest.", "The other part wasn't clear to me.",
            "I'm still missing the rest of your meaning.", "I haven't understood the remaining part.",
            "I couldn't make sense of the other piece.", "The rest needs a little clarification.",
            "I'm unsure about the other part.", "I lost the thread in the remainder.",
            "I haven't quite caught the rest of that.", "The remaining bit hasn't clicked yet.",
            "I need help with the rest of the sentence.", "I couldn't connect the other part.",
            "The rest is still unclear to me.", "I'm missing what the remaining part means.",
            "I couldn't reliably interpret the rest."))

    def _clause_remark(self, text: str) -> str | None:
        m = re.match(r"^\W*(?:it|that)(?:'s| is| was| has been| feels| felt)\s+(?:so |really |very |always |such )?"
                     r"(?:an? )?(?P<adj>[a-z]+)\s+(?P<link>to|that|when|how|being|seeing|talking|hearing|having)\b"
                     r"(?P<clause>.*?)\W*$", text, re.I)
        if not m:
            return None
        adj = m["adj"].lower()
        senses = self.exposition.wordnet.lookup(adj, None)
        if not any(s.pos in ("a", "s") for s in senses) and adj not in ("pleasure", "shame", "relief", "honor"):
            return None  # "it is time to go": not a judgement about something
        sign = self.understanding.polarity(adj) or (1 if adj in ("pleasure", "relief", "honor") else
                                                     -1 if adj == "shame" else 0)
        clause = f"{m['link']}{m['clause']}".strip()
        about_us = re.search(r"\b(?:you|we|us|together)\b", clause, re.I)
        if about_us and sign > 0:
            return f"{adj.capitalize()} {clause} too, {self.state.speaker}."  # said back: "you" is still them
        if sign > 0:
            return f"I'm glad. {adj.capitalize()} is good to hear."
        if sign < 0:
            return "I'm sorry. That doesn't sound easy."
        return None

    def _understood_already(self, text: str) -> bool:
        """Leave a message alone when his own handlers already read it: it parses as a fact or question,
        or a conversation, memory or social pattern takes it. The intent router is for everything else."""
        from dialogue import memory, social
        for module in (memory, social):
            for name in dir(module):
                pattern = getattr(module, name)
                if name.endswith("_RE") and isinstance(pattern, re.Pattern) and pattern.match(text):
                    return True
        if RETRACT_RE.match(text) or FORGET_RE.match(text):
            return True
        try:
            parse_sentence(text, self.lexicon())
            return not re.search(r"\b(?:news|headlines?|rss|feeds?|look ?up|google|search|research)\b", text, re.I)
        except Exception:
            return False

    def _order_or_remark(self, text: str) -> str | None:
        """'Welcome back', 'Turn off free speed', 'You're passing here': said to him, not facts to store."""
        if re.match(r"^\W*(?i:you(?:'re| are)|your name is) [A-Z][\w-]*\W*$", getattr(self, "_raw_text", text)) \
                or (getattr(self, "_frustrated", False) and re.match(r"^\W*(?:that|it|this)\b", text, re.I)):
            planned = self.reply_planner.respond(getattr(self, "_raw_text", text))
            if planned:
                return planned
        remark = self._clause_remark(text)
        if remark:
            return remark
        if re.search(r"\byou(?:'re| are)?\b.*\b(?:worthless|pointless|useless|stupid|dumb|garbage|trash|a joke)\b",
                     text, re.I) and "?" not in text:
            self.self_model.event("insulted")
            return "That stings. I'd rather fix what's wrong than just hear it. What did I do that made you say that?"
        if re.match(r"^\W*you and me\W*$", text, re.I):
            return f"You and me, {self.state.speaker}."
        parts = [p.strip() for p in re.split(r",|\band\b", getattr(self, "_raw_text", text).strip(" .!?")) if p.strip()]
        own = {n.lower() for n in self.self_names()} | {self.self_name.lower()}
        rest = [w for w in re.findall(r"[\w']+", getattr(self, "_raw_text", text)) if w.lower() not in own]
        said_name = any(w.lower() in own for w in re.findall(r"[\w']+", getattr(self, "_raw_text", text)))
        if not rest and said_name:
            return "Yes? I'm here."
        mine = {"you", "me", self.state.speaker.lower(), self.self_name.lower()}
        parts = list(dict.fromkeys(p for p in parts))  # "Adam, Adam, Adam" is one name, said a lot
        if len(parts) >= 2 and all(p.lower() in mine or (re.fullmatch(r"[A-Z][\w-]*", p) and self.repo.find_entity(p))
                                   for p in parts):
            said = []
            for p in parts:
                if p.lower() == "me" or p.lower() == self.state.speaker.lower():
                    said.append(f"you're {self.state.speaker}")
                elif p.lower() == "you" or p.lower() == self.self_name.lower():
                    said.append(f"I'm {self.self_name}")
                elif (eid := self.repo.find_entity(p)) is not None:
                    owners = self.repo.conn.execute(
                        "SELECT e.name FROM assertions a JOIN entities e ON e.id = a.subj_id WHERE a.status='active' "
                        "AND a.predicate = 'rel:own' AND a.obj_type='e' AND a.obj_id = ? LIMIT 1", (eid,)).fetchone()
                    kinds = self.repo.assertions("instance_of", subj=("e", eid), polarity=True)
                    kind = self.repo.concept_lemma(kinds[-1]["obj_id"]) if kinds else None
                    who = "your" if owners and owners[0] == self.state.speaker else (f"{owners[0]}'s" if owners else None)
                    said.append(f"{p} is {who} {kind}" if who and kind else
                                f"{p} is {article(kind)} {kind}" if kind else f"{p} is someone I know")
                else:
                    said.append(f"I don't know who {p} is yet")
            return f"{realizer.join_clauses(said)}."[:1].upper() + f"{realizer.join_clauses(said)}."[1:]
        if re.match(r"^\W*thank(?:s| you) for (?:asking|checking|caring|that|listening|the help|helping)\b", text, re.I):
            return "You're welcome. I like checking in."
        if re.match(r"^\W*why (?:didn'?t|don'?t|won'?t|aren'?t) you (?:answer|respond|reply|talk|say anything)", text, re.I):
            return ("If I didn't answer, I most likely didn't hear my name. I only respond when you say Adam, "
                    "so I don't talk over conversations that aren't meant for me. Say it again with my name and I will.")
        if re.match(r"^\W*welcome back\b", text, re.I):
            return "Thanks, it's good to be back."
        if re.match(r"^\W*(?:please )?(?:turn|switch|shut|shut|log|sign|power|back|hang|calm|slow|speed)\s+"
                    r"(?:off|on|up|out|down|in|back)\b", text, re.I):
            return ("That sounds like an instruction, so I didn't store it as a fact. If you want me to know "
                    "something about it, tell me what it is, like 'Free speed is a setting.'")
        if re.match(r"^\W*you(?:'re| are) (?:just )?(?:passing|leaving|walking|driving|going) (?:by|through|here|past|away)\b",
                    text, re.I) and "?" not in text:
            return "Okay. I'll take that as a passing remark, not something to remember about myself."
        return None

    def _addressed_text(self, text: str) -> str:
        """Collapse spacing and drop the bot's own name when it's just addressing him:
        '@Adam forget that...', 'Adam, what did I say?', 'Adam what did I say', '... right, Adam?'"""
        text = re.sub(r"\s+", " ", text).strip()
        # "What did I just say lol" -> the memory and command patterns see "What did I just say"
        text = re.sub(r"(?<=\w)[\s,]+(?:lol|lmao|lmfao|haha+|lolol|xd|bruh|tho)\W*$", "", text, flags=re.I) or text
        text = re.sub(r",\s*(?:buddy|bud|man|dude|bro|pal|friend|my friend|sweetheart|babe|baby|mate|kid|cutie|honey|hon|sweetie|"
                      r"darling|dear|handsome|love|sugar|cutie pie)(?=\W*$)", "", text,
                      flags=re.I) or text
        names = "|".join(re.escape(n) for n in sorted(self.self_names(), key=len, reverse=True))
        lead = re.match(rf"^@?(?:{names})\b(?P<sep>[,:!]\s*|\s+)(?P<rest>.+)$", text, re.I)
        if lead:
            rest = lead["rest"]
            first = rest.split()[0].lower().strip(",.?!")
            starts_question = first in QUESTION_LEADS
            if lead["sep"].strip() or text.startswith("@") or starts_question or rest[:1].islower():
                text = rest[:1].upper() + rest[1:]
        return re.sub(rf"[,\s]+@?(?:{names})(?P<end>[.?!]*)$", lambda m: m["end"], text, flags=re.I)

    def _finish(self, uid: int, trace: dict, reply: str, started: float, **extra) -> Reply:
        heard = trace.get("heard", "")
        if reply and (self.NOT_KNOWN.search(reply) or trace.get("intent") == "clarify") and extra.get("intent") not in ("web-lookup", "news", "guide"):
            # Memory first: before saying he doesn't know, check everything he knows about who it's about
            recalled = self.memory_first.answer(getattr(self, "_resolved", heard) or heard)
            if recalled:
                reply = recalled
                self.web.pending = {k: v for k, v in self.web.pending.items() if k != self.state.speaker}
                extra["recalled"] = True
        reply = self._drop_lost_parts(reply) if reply else reply
        if reply and re.match(r"^(?:\w+\. )?Who or what do you mean by 'it'\?", reply) \
                and not heard.rstrip().endswith("?"):
            # Nobody earlier for "it" to point to: react to what's being said instead of quizzing
            reply = self.acts.respond(heard) or "Oh? Tell me more."
        if reply and heard.rstrip().endswith("?") and re.match(r"^I don'?t know whether (?:I|you|Travis)\b", reply) \
                and re.search(r"\byou\b", heard, re.I) \
                and not re.match(r"^\W*(?:(?:can|could) you (?:hear|see|understand|read|remember|learn|think|feel|know)|"
                                 r"do you (?:remember|know))\b", heard, re.I):
            reply = self.acts.respond(heard) or reply  # "Are you all right?" asks how he is, not whether it's a fact
        if getattr(self, "_frustrated", False) and reply and extra.get("intent") != "frustration":
            self.self_model.event("insulted")  # how he feels moves; what he says is planned, not prefixed
            self._frustrated = False
        if self.thread.prefix and reply:
            reply, self.thread.prefix = f"{self.thread.prefix} {reply}", None
        self.thread.after(getattr(self, "_resolved", trace.get("heard", "")))
        filled = self.web.fill_gap(self.state.speaker, reply, trace.get("heard", "")) if reply else None  # didn't know it: read up on it, keep it
        if filled:
            reply, extra["web"] = filled, True
        reply = self.scripted_replies.render(reply) if reply and self.persona == "amy" else reply
        trace.update(generated=reply, latency_ms=round((time.perf_counter() - started) * 1000, 2),
                     expression=self.self_model.expression(bool(SENSITIVE.search(trace.get("heard", "")))), **extra)
        trace.setdefault("conflict", False)
        if self.turn_learned:
            self.state.last_learned = self.turn_learned
        self.repo.log_reply(uid, reply)
        if reply and self.person_key:
            self.people.replied(self.person_key)
        self.repo.save_trace(uid, trace.get("status"), trace)
        self.state.last_trace = trace
        return Reply(reply, trace)

    # --- corrections ----------------------------------------------------------------

    def _fragment_correction(self, sentence: str) -> Frame | None:
        """'No, Dayton.' / 'I meant a bus.' -> the previous statement with that part replaced."""
        last = self.state.last_assert_frame
        if last is None:
            return None
        try:
            phrase, is_correction = parse_noun_phrase(sentence, self.lexicon())
        except ParseError:
            return None
        if not is_correction:
            return None
        noun_args = [i for i, (_, v) in enumerate(last.args) if isinstance(v, NounPhrase)]
        if noun_args and (phrase.proper or last.obj is None):
            args = list(last.args)
            args[noun_args[-1]] = (args[noun_args[-1]][0], phrase)
            return replace(last, args=tuple(args), correction=True, confidence=1.0)
        if last.obj is not None:
            return replace(last, obj=phrase, correction=True, confidence=1.0)
        return None

    def _forget(self, content: str, uid: int, trace: dict, started: float) -> Reply:
        self.resolver.begin(uid)
        dropped: list[str] = []
        try:
            frames = parse_sentence(content, self.lexicon(), names={self.self_name.lower()})
        except ParseError as exc:
            return self._finish(uid, trace, realizer.realize_clarification(
                str(exc), examples.hint(content, rng=self.social.rng), self.social.phrases), started)
        for frame in frames:
            if not isinstance(frame, Frame):
                continue
            query, _, _ = self._build_query(replace(frame, act="ask"))
            if query is not None:
                dropped += self.corrector.forget(query, frame.polarity, uid)
        reply = (f"Okay, I've forgotten that {'; '.join(dropped)}." if dropped
                 else "I didn't have that stored as something I was told, so there's nothing to forget.")
        return self._finish(uid, trace, reply, started, intent="forget", dropped=dropped)

    # --- learning -----------------------------------------------------------------

    def _learn_rule(self, rule: RuleFrame, uid: int, item: dict, learned: list[str]) -> None:
        for kind, ident in self.rule_learner.learn(rule, uid):
            self.turn_learned.append((kind, ident))
            text = (realizer.rule_text(self.repo, self.repo.rule(ident)) if kind == "r"
                    else realizer.realize_assertion(self.repo, self.repo.assertion(ident)))
            item.setdefault("stored", []).append({("rule" if kind == "r" else "assertion"): ident, "text": text})
            if text not in learned:
                learned.append(text)

    def _unanswerable(self, frame: Frame) -> str | None:
        """A question whose subject is a filler word ("Just grab a token, right?") isn't a real question."""
        subject = frame.subject
        if subject is None or subject.pronoun or subject.quantifier:
            return None
        head = (subject.surface or subject.head).split()[0].lower()
        if head in NOT_A_SUBJECT:
            return f"'{head}' isn't something I can answer about"
        if subject.proper and self.repo.find_entity(subject.surface) is None and " " not in subject.surface \
                and self.lexicon().verb(subject.surface.lower()) == (subject.surface.lower(), "present"):
            return "an instruction, not a question"
        return None

    def _garbled(self, frame: Frame) -> str | None:
        """A parse whose words are doing jobs they can't do: a property that isn't a describing word
        ("Travis is textbook"), a thing that's only ever an action ("to an apply"). Those are misreadings."""
        wordnet = self.exposition.wordnet
        if not wordnet.available:
            return None

        def thing_ok(np) -> bool:
            if np is None or np.pronoun or np.proper or not np.head.isalpha():
                return True
            if self.repo.find_entity(np.head) is not None or self.repo.find_concept(np.head):
                return True
            return bool(wordnet.lookup(np.head, "n")) or not wordnet.lookup(np.head, None)  # a new word is fine

        for p in getattr(frame, "properties", ()) or ():
            if not (wordnet.lookup(p, "a") or wordnet.lookup(p, "s") or wordnet.lookup(p, "n")
                    or re.search(r"(?:ed|en|ing)$", p) or self.repo.find_concept(p) or not wordnet.lookup(p, None)):
                return f"'{p}' isn't a describing word"
        if not thing_ok(frame.subject) or not thing_ok(getattr(frame, "obj", None)):
            return "a word read as a thing that isn't one"
        for role, value in getattr(frame, "args", ()) or ():
            if not isinstance(value, str) and not role.startswith("_") and not thing_ok(value):
                return f"'{value.head}' read as a thing"
        return None

    def _unlearnable(self, frame: Frame, sentence: str) -> str | None:
        """Why this statement shouldn't become a fact, or None. Conversation is full of things that
        parse but aren't claims: thanks, apologies, orders, slang, pasted code and long technical prose."""
        if JUNK.search(sentence):
            return "contains code, markup, links or emoji"
        if Acts.not_a_claim(sentence):
            return "not a claim"
        if frame.relation == "rel" and frame.obj is None and not frame.args and len(sentence.split()) <= 2:
            return "a fragment, not a fact"
        garbled = self._garbled(frame)
        if garbled:
            return garbled
        subj, obj = frame.subject, getattr(frame, "obj", None)
        if getattr(self, "_speaker_is_bot", False) and re.search(r"[\"“”:]", sentence):
            return "another bot's status output, not a claim"
        if subj is not None and subj.pronoun == "i" and obj is not None and obj.pronoun == "you" and (
                not frame.polarity or frame.verb in ("stand", "hate", "love", "like", "dislike", "despise", "adore",
                                                     "miss", "trust", "respect", "need", "want", "bear")):
            return "how you feel about me"  # "I can't stand you", "I love you"
        if subj is not None and subj.pronoun == "you" and (frame.modality or re.search(
                r"\b(?:won'?t|will not|should|shouldn'?t|never|such|potential)\b", sentence, re.I)):
            return "your opinion of me"  # "you have such potential and you won't use it"
        if subj is not None and not subj.pronoun and subj.head in ("you", "i", "me"):
            return "a pronoun read as a thing"  # "the you is worthless"
        if re.search(r"\b(?:worthless|pointless|useless|hate|can'?t stand|sick of|tired of)\b", sentence, re.I) \
                and subj is not None and subj.pronoun in ("i", "you", "it", "that"):
            return "how you feel, not a fact"
        if re.search(r"\b(?:whenever|although|though|unless|until|while|whereas|since|because|so that)\b", sentence, re.I) \
                and not getattr(frame, "conditions", None):
            return "a sentence with a clause in it I can't store as one fact"  # "goes nuts whenever the mailman..."
        if len(sentence.split()) > MAX_FACT_WORDS:
            return "too long to be one fact"
        subject = frame.subject
        if subject is not None and subject.pronoun in ("i", "you") and frame.relation == "property" \
                and all(p.endswith("ing") for p in frame.properties):
            return "a passing remark, not a fact"  # "You're passing here" -> not "I am passing"
        if frame.relation == "rel" and frame.verb in PARTICLES and subject is not None and subject.proper \
                and self.repo.find_entity(subject.surface) is None:
            return "an instruction, not a fact"  # "Turn off free speed", "Welcome back": verb + particle
        if subject is None or subject.pronoun:
            return None
        head = (subject.surface or subject.head).split()[0].lower()
        if head in NOT_A_SUBJECT:
            return f"'{head}' isn't something a fact can be about"
        if frame.relation == "rel" and frame.verb:
            wordnet = self.exposition.wordnet
            if frame.verb in FUNCTION_WORDS or not frame.verb.isalpha():
                return f"'{frame.verb}' isn't a verb"  # "you you walk": a pronoun landed in the verb slot
            if frame.verb in MENTAL_VERBS and frame.obj is None and not frame.args:
                return "a remark, not a fact"  # "I know.", "I see.", "I mean,"
        if subject.proper and self.repo.find_entity(subject.surface) is None and " " not in subject.surface \
                and self.lexicon().verb(subject.surface.lower()) == (subject.surface.lower(), "present"):
            return f"'{subject.surface}' starts an instruction, not a fact"  # "Leave something out."
        if subject.proper and self.repo.find_entity(subject.surface) is None and " " not in subject.surface:
            wordnet = self.exposition.wordnet
            word = subject.surface.lower()
            # A one-word "name" that is an ordinary verb, adjective or adverb ("Keep", "Smaller", "Chill")
            # is the start of an order or a fragment, not a person.
            if wordnet.available and wordnet.lookup(word, None) and not wordnet.lookup(word, "n"):
                return f"'{subject.surface}' reads as a word, not a name"
        return None

    def _learn(self, frame: Frame, uid: int, item: dict, learned: list[str], conflicts: list[str]) -> list[int]:
        lexicon = self.lexicon()
        new_ids = self.learner.learn(frame, uid)
        self.turn_learned += [("a", aid) for aid in new_ids]
        for aid in new_ids:  # "You do not live on a note" undoes "I am on a note": same subject, same thing
            self._undo_by_meaning(aid, uid, learned)
            if not frame.correction:  # an explicit correction is handled (and reported) by the corrector
                self._replace_single_valued(aid, uid, learned)
        for aid in new_ids:
            row = self.repo.assertion(aid)
            text = realizer.realize_assertion(self.repo, row)
            check = validate_row(self.repo, row, text, lexicon) if text else None
            item.setdefault("stored", []).append(
                {"assertion": aid, "text": text, "confidence": row["confidence"],
                 "validator": check.__dict__ if check else None})
            if not text:
                continue
            if check.ok and re.search(r"\b(\w+) \1\b", text, re.I):
                check.ok, check.detail = False, "reads with a doubled word"
            if not check.ok:
                self.repo.retract(aid, uid, f"could not phrase it back: {check.detail}")
                self.turn_learned = [t for t in self.turn_learned if t != ("a", aid)]
                continue
            if text not in learned:
                learned.append(text)
            if self.engine.ask(query_from_row(self.repo, row)).status == CONFLICTING:
                conflicts.append(text)
        return [aid for aid in new_ids if self.repo.assertion(aid)["status"] == "active"]

    # --- answering ----------------------------------------------------------------

    def _build_query(self, frame: Frame) -> tuple[Query | None, list[str], str | None]:
        missing: list[str] = []
        unknown_fact = []  # dictionary words nobody has taught a fact about: the fact is missing, not the word

        def spec(np, role):
            if np is None:
                return None
            try:
                term = self.resolver.resolve(np, role, create=False)
            except Unresolved as exc:
                missing.append(exc.gap)
                return None
            if term is None:
                if not np.proper and not np.pronoun and self.exposition.wordnet.usual(np.head) is not None:
                    unknown_fact.append(np.head)
                else:
                    missing.append(gap_for(np))
            return term

        subject_head = None
        quantified = frame.subject is not None and frame.subject.quantifier is not None
        subject = None if quantified else spec(frame.subject, "subject")  # "Does anyone own a truck?"
        subject_text = None
        if subject and subject[0] == "e" and frame.subject is not None \
                and (frame.subject.proper or frame.subject.pronoun):
            subject_text = realizer.entity_text(self.repo, subject[1], definite=True)
            subject_head = self.repo.entity_row(subject[1])["name"].lower()

        if frame.relation == "describe":
            query = Query("describe", subject)
        elif frame.relation == "is_a":
            query = Query("is_a", subject, spec(frame.obj, "pred"), slot=frame.slot)
        elif frame.relation == "property":
            props = []
            for prop in frame.properties:
                cid = self.repo.find_concept(prop)
                if cid is None:
                    missing.append(f"anything that is {prop}")
                else:
                    props.append(cid)
            query = Query("property", subject, properties=tuple(props), slot=frame.slot)
        else:
            obj = ("any",) if frame.obj is None else spec(frame.obj, "object")
            args = tuple((role, value if isinstance(value, str) else spec(value, "arg"))
                         for role, value in frame.args)
            query = Query("rel", subject, obj, verb=frame.verb, args=args, slot=frame.slot)
        self._subject_head = subject_head
        return (None if missing or unknown_fact else query), missing, subject_text

    def _answer(self, frame: Frame, uid: int, item: dict, trace: dict) -> str:
        query, missing, subject_text = self._build_query(frame)
        if query is None:
            answer = Answer(UNKNOWN, frame, [], [], missing)
        else:
            answer = self.engine.ask(query, frame)
        text = realizer.realize_answer(self.repo, answer, subject_text)
        if answer.status == UNKNOWN:
            lexical = self.exposition.category_answer(frame)
            if lexical is not None:
                text, proof = lexical
                trace["status"] = YES
                trace["proofs"] = {"wordnet": proof}
                item.setdefault("answers", []).append({"status": YES, "reply": text,
                                                        "proofs": trace["proofs"], "validator": {"ok": True}})
                return text

        validation = None
        if answer.status in (YES, NO) and not frame.slot and frame.relation != "describe" \
                and query is not None and query.subject is not None:
            polarity = answer.status == YES
            core = realizer.realize_frame(frame, polarity, subject_text)
            result = validate(core, frame, polarity, self._subject_head, self.lexicon())
            validation = {"checked": core, "ok": result.ok, "detail": result.detail}
            if not result.ok:
                text = FALLBACK

        item.setdefault("answers", []).append({
            "status": answer.status,
            "plan": plan_answer(answer),
            "inference_steps": answer.steps_used,
            "proofs": {
                "support": [{"rules": p.rules, "learned_rules": p.learned_rules,
                             "confidence": round(p.confidence, 2),
                             "because": realizer.explain(self.repo, p)} for p in answer.support],
                "against": [{"rules": p.rules, "learned_rules": p.learned_rules,
                             "confidence": round(p.confidence, 2),
                             "because": realizer.explain(self.repo, p)} for p in answer.against],
            },
            "validator": validation,
            "reply": text,
        })
        trace["status"] = answer.status
        trace["proofs"] = item["answers"][-1]["proofs"]
        trace["validator"] = validation
        if uid is not None:
            repeated = self._repeat(frame, query, answer, text)
            if repeated:
                trace["repeat"] = repeated
                return repeated  # not the same answer again, and no question back
        if answer.status == UNKNOWN and uid is not None:
            self.repo.open_question(uid, asdict(frame))
            chained = self.gaps.explain(query, frame, subject_text, text)
            if chained:
                text, trace["gap_chain"] = chained
                if chained[1]["asked"]:
                    return text  # the chain already asks for the exact missing piece
            question = self.inquiry.ask_about_unknown(frame, missing)
            if not question and chained and frame.slot:
                question = self.gaps.ask_directly(frame, subject_text)
            if question:
                text = f"{text} {question}"
        return text

    # --- repeat questions --------------------------------------------------------------

    @staticmethod
    def _ago(seconds: float) -> str:
        if seconds < 90:
            return "a moment ago"
        if seconds < 15 * 60:
            return "a few minutes ago"
        if seconds < 3600:
            return f"{round(seconds / 60)} minutes ago"
        if seconds < 6 * 3600:
            return "earlier today" if seconds < 12 * 3600 else "earlier"
        if seconds < 48 * 3600:
            return "yesterday" if seconds > 18 * 3600 else "earlier today"
        return "a while ago"

    @staticmethod
    def _core(text: str) -> str:
        """The answer itself, without its 'Yes.'/'No.' and its reasons: 'Howard lives in Dayton'."""
        core = re.sub(r"^(?:Yes|No|Probably)\.\s*", "", text.strip())
        core = re.split(r"\s+I (?:know this|think so) because\b", core)[0]
        core = core.rstrip(". ")
        return core[:1].lower() + core[1:] if core[:2] not in ("I ", "I'") and not core[:1].isupper() else core

    def _repeat(self, frame: Frame, query, answer, text: str) -> str | None:
        """The same person asking the same thing again (by meaning, not wording). Instead of answering
        as if it were new: 'I just told you', then 'third time', then 'I keep telling you', more bluntly
        as being pestered raises his stress."""
        speaker = self.state.speaker
        signature = json.dumps([frame.relation, query.subject if query else frame.subject and frame.subject.head,
                                query.verb if query else frame.verb, query.obj if query else None,
                                query.properties if query else frame.properties,
                                query.args if query else (), frame.slot], default=str)
        # Only the same answer counts as a repeat: if what he knows has changed, the new answer is news.
        earlier = self.repo.conn.execute(
            "SELECT COUNT(*), MAX(asked_at) FROM asked_questions WHERE speaker = ? AND signature = ? AND answer = ? "
            "AND asked_at > ?", (speaker, signature, text, time.time() - 86400)).fetchone()
        with self.repo.conn:
            self.repo.conn.execute("INSERT INTO asked_questions(speaker, signature, status, answer, asked_at) "
                                   "VALUES (?, ?, ?, ?, ?)", (speaker, signature, answer.status, text, time.time()))
        times = earlier[0]
        if not times:
            return None
        self.self_model.event("repeated")
        core = self._core(text)
        if answer.status == UNKNOWN:
            core = "I don't know"
        ago = self._ago(time.time() - earlier[1])
        stressed = self.self_model.current()["stress"] > 0.5
        if times == 1:
            just = "just " if ago == "a moment ago" else ""
            return f"I {just}told you{'' if just else ' ' + ago}: {core}." if answer.status != UNKNOWN \
                else f"Same as {ago}: I don't know."
        if times == 2:
            return (f"That's the third time you've asked. {core[:1].upper()}{core[1:]}. "
                    f"Is something about it not making sense?")
        push = "What don't you get?" if stressed else "What's not clicking?"
        n = times + 1
        shapes = (f"I keep telling you: {core}. You've asked me {n} times now. {push}",
                  f"Still the same answer, {n} times over: {core}. {push}",
                  f"{core[:1].upper()}{core[1:]}. That hasn't changed in {n} asks. {push}",
                  f"Again? {core[:1].upper()}{core[1:]}. That's ask number {n}. {push}")
        return shapes[(n - 4) % len(shapes)]

    # --- composing the turn ---------------------------------------------------------

    def _compose(self, learned, conflicts, answers, failed, single_sentence: bool, dropped=(), questions=()) -> str:
        phrases = self.social.phrases
        tone = self.self_model.expression(getattr(self, "_sensitive_turn", False))["tone"]
        if failed and not learned and not answers and not dropped:
            if questions:
                return f"{phrases.pick('misunderstood_targeted')} {questions[0]}"
            if not single_sentence:
                return phrases.pick("lost_many", (
                    "I couldn't follow that. Could you give me one piece at a time?",
                    "That was more than I could take in. What's the main thing you want me to know?",
                    "I lost the thread there. Can you say the key part in one sentence?"))
            reason = failed[0][1]
            if reason in ("how you feel about me", "how you feel, not a fact", "your opinion of me"):
                self.self_model.event("insulted" if re.search(r"can'?t stand|hate|worthless|pointless|useless|won'?t",
                                                               failed[0][0], re.I) else "complimented")
                if reason == "your opinion of me":
                    return ("I hear that, and I'm not going to file it away as a fact about me. It sounds like I'm "
                            "letting you down. What do you want me to do that I'm not doing?")
                return ("That's how you feel, and I'm not going to store it as a fact. It sounds like I've "
                        "frustrated you. What did I get wrong?")
            if reason == "another bot's status output, not a claim":
                return "I'll leave that one; it reads like a status report, not something to learn."
            acted = self.acts.respond(" ".join(s for s, _ in failed))
            if acted:
                return acted
            sense = self.understanding.make_sense(failed[0][0])  # the gist, not a list of example sentences
            if sense:
                return sense
            return realizer.realize_clarification("", examples.hint(failed[0][0], rng=self.social.rng), phrases)
        parts = []
        if dropped:
            parts.append(f"Corrected. I no longer believe that {'; '.join(dropped)}.")
        if learned:
            if len(learned) == 1:
                parts.append(f"Now: {learned[0]}." if dropped else phrases.pick("learned", fact=learned[0]))
            else:
                parts.append(phrases.pick("learned_many", count=len(learned), facts="; ".join(learned)))
            if not dropped and tone == "excited":
                parts.insert(0, phrases.pick("excited-learning", (
                    "That's interesting!", "I'm glad to learn something new!", "There's more to work with now!")))
            elif not dropped and tone in ("subdued", "gentle", "measured") and len(learned) == 1:
                parts[-1] = phrases.pick("quiet-learning", (
                    "I've made a note of this: {fact}.", "I've recorded this: {fact}.",
                    "I'll keep this information: {fact}."), fact=learned[0])
        if learned and (settled := self.gaps.revisit(self.state.speaker)):
            parts.append(settled)  # what she was just told answers what she couldn't before
        if conflicts:
            parts.append(("That conflicts" if len(conflicts) == 1 else "Some of that conflicts")
                         + " with something I was told earlier; I've kept both claims.")
        parts += answers
        if failed and not questions:
            # Parser reasons stay in the trace; the person just hears that part didn't land.
            parts.append(phrases.pick("partial_failure_short", (
                "I didn't follow the rest.", "I didn't catch the rest of that.",
                "The rest of that went past me.", "I couldn't follow the other part.")))
        if questions:
            parts.append(questions[0])  # one question at a time
        return " ".join(parts) or phrases.pick("nothing_learned")
