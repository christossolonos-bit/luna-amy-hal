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
from reasoning.proofs import CONFLICTING, NO, UNKNOWN, YES, Answer, Query

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
    r"(?:wrong|not right|not true|incorrect|false|mistaken)"
    r"|scratch that|never ?mind|undo(?: that)?|forget (?:it|that)|wrong)\W*$", re.IGNORECASE)
FORGET_RE = re.compile(r"^\W*" + LEADING_FILLER + r"(?:please\s+)?(?:forget|disregard|ignore)\s+(?:that\s+|about\s+)?(.+?)\W*$",
                       re.IGNORECASE)


@dataclass
class Reply:
    text: str
    trace: dict


class Conversation:
    def __init__(self, repo: Repository, speaker: str = "user", self_name: str = "Hal",
                 max_depth: int = 16, budget: int = 5000, wordnet: WordNet | None = None,
                 curious: bool = False, elaborate: bool | None = None) -> None:
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
        self.social = Social(self)
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
        self.world = WorldQuestions(self)
        self.web.world = self.world
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
        self._sensitive_turn = bool(SENSITIVE.search(text))
        trace: dict = {"heard": text, "speaker": self.state.speaker, "sentences": []}
        learned: list[str] = []
        conflicts: list[str] = []
        answers: list[str] = []
        failed: list[tuple[str, str]] = []
        dropped: list[str] = []
        self.turn_learned = []

        text, direct = self.thread.resolve(text, uid)  # read against what we've been talking about
        self._resolved = text
        if direct is not None:
            return self._finish(uid, trace, direct, started, intent="thread")
        self.world.hear(text)  # what people bring up is what he gets curious about
        routed = intent_router().route(text)
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
        if self.curious:
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
                     if dropped else "There's nothing recent for me to take back.")
            return self._finish(uid, trace, lead + reply, started, intent="retract", dropped=dropped)
        forget = FORGET_RE.match(text)
        if forget:
            reply = self._forget(forget.group(1), uid, trace, started)
            if greeting:
                reply.text = lead + reply.text
            return reply
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
            return not re.search(r"(?:news|headlines?|rss|feeds?|look ?up|google|search|research)", text, re.I)
        except Exception:
            return False

    def _order_or_remark(self, text: str) -> str | None:
        """'Welcome back', 'Turn off free speed', 'You're passing here': said to him, not facts to store."""
        if re.match(r"^\W*you and me\W*$", text, re.I):
            return f"You and me, {self.state.speaker}."
        parts = [p.strip() for p in re.split(r",|\band\b", getattr(self, "_raw_text", text).strip(" .!?")) if p.strip()]
        mine = {"you", "me", self.state.speaker.lower(), self.self_name.lower()}
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
        text = re.sub(r",\s*(?:buddy|bud|man|dude|bro|pal|friend|my friend|sweetheart|babe|mate|kid)(?=\W*$)", "", text,
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
        if self.thread.prefix and reply:
            reply, self.thread.prefix = f"{self.thread.prefix} {reply}", None
        self.thread.after(getattr(self, "_resolved", trace.get("heard", "")))
        filled = self.web.fill_gap(self.state.speaker, reply, trace.get("heard", "")) if reply else None  # didn't know it: read up on it, keep it
        if filled:
            reply, extra["web"] = filled, True
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

    def _unlearnable(self, frame: Frame, sentence: str) -> str | None:
        """Why this statement shouldn't become a fact, or None. Conversation is full of things that
        parse but aren't claims: thanks, apologies, orders, slang, pasted code and long technical prose."""
        if JUNK.search(sentence):
            return "contains code, markup, links or emoji"
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
            question = self.inquiry.ask_about_unknown(frame, missing)
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
