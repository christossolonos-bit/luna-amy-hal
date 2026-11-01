"""When she can't answer, reason out exactly why, and turn that into the one question that gets her the data.

Her job is collecting knowledge, so an unknown answer is an opportunity, not a dead end. For a question
she can't settle, she links together:

  known     what she does know about the subject, preferring facts that bear on the question
  rule      a rule she holds that would decide it, and which of its conditions she can already prove
  gap       the precise condition (or fact) still missing
  ask       a question for exactly that gap

She keeps the open gap per speaker. When a later turn teaches her something, she re-asks the original
question herself, so what she just learned is reused straight away: "That fills the gap: ...".
"""

import time
from dataclasses import dataclass

from dialogue import realizer
from reasoning.proofs import CONFLICTING, NO, UNKNOWN, YES, Query

OPEN_SECONDS = 15 * 60


@dataclass
class Gap:
    query: Query
    frame: object
    subject_text: str | None
    opened: float


class GapChain:
    def __init__(self, convo) -> None:
        self.convo = convo
        self.repo = convo.repo
        self.open: dict[str, Gap] = {}

    def pick(self, key, bank, **fields):
        return self.convo.social.phrases.pick("gap:" + key, bank, **fields)

    # --- reasoning about the gap ----------------------------------------------------

    def _rule_gaps(self, query: Query) -> list[dict]:
        """Rules that would answer this, split into the conditions she can prove and the ones she can't."""
        engine = self.convo.engine
        engine.ask(query)  # loads the active rules and resets the budget
        out = []
        for rule in engine.rules:
            for pattern in rule["conclusions"]:
                try:
                    bound = engine._unify(pattern, query)
                except Exception:
                    bound = None
                if bound is None or bound[0] is None or bound[0][0] not in ("e", "c"):
                    continue
                subject = bound[0]
                name = realizer.term_text(self.repo, subject[0], subject[1], definite=True)
                proven, missing = [], []
                for condition in rule["conditions"]:
                    text = realizer.pattern_text(self.repo, condition, name)
                    status = engine.ask(engine._instantiate(condition, subject)).status
                    if status == YES:
                        proven.append(text)
                    elif status in (NO, CONFLICTING):
                        missing = None  # that rule is ruled out, not merely incomplete
                        break
                    else:
                        missing.append(text)
                if missing:
                    out.append({"rule": realizer.rule_text(self.repo, rule), "proven": proven, "missing": missing,
                                "conclusion": realizer.pattern_text(self.repo, pattern, name),
                                "rule_id": rule["id"]})
        out.sort(key=lambda g: (len(g["missing"]), -len(g["proven"])))
        return out

    def _known(self, query: Query, limit: int = 3) -> list[str]:
        subject = query.subject
        if subject is None or subject[0] not in ("e", "c"):
            return []
        rows = []
        for predicate in ("instance_of", "subclass_of", "has_property", "rel:%"):
            rows += self.repo.assertions(predicate, subj=subject)
        verb = f"rel:{query.verb}" if query.verb else None
        rows.sort(key=lambda r: (r["predicate"] != verb, -r["id"]))  # closest to the question first
        texts = []
        for row in rows:
            if row["status"] == "active" and (t := realizer.realize_assertion(self.repo, row)) and t not in texts:
                texts.append(t)
            if len(texts) >= limit:
                break
        return texts

    def explain(self, query: Query | None, frame, subject_text: str | None, lead: str) -> tuple[str, dict] | None:
        """The linked account of what she knows, what's missing, and the question that fills it."""
        if query is None or query.subject is None or query.relation == "describe":
            return None
        known = self._known(query)
        gaps = self._rule_gaps(query)
        if not known and not gaps:
            return None
        parts = [lead]
        if known:
            parts.append(realizer.sentence(self.pick("known", (
                "What I do have is this: {facts}", "Here's what I've got: {facts}",
                "I can tell you {facts}", "So far I know {facts}", "My notes say {facts}",
                "On record, {facts}", "I've learned {facts}", "Everything I hold on it: {facts}",
            ), facts=realizer.join_clauses(known))))
        ask = None
        if gaps:
            g = gaps[0]
            missing = realizer.join_clauses(g["missing"])
            if g["proven"]:
                parts.append(realizer.sentence(self.pick("rule_partial", (
                    "{proven}, and {rule}, so the piece I'm missing is whether {missing}",
                    "Since {proven} and {rule}, everything hinges on whether {missing}",
                    "I know {proven} and that {rule}; the gap is whether {missing}",
                    "Half the chain is there: {proven}, plus {rule}. Missing link: whether {missing}",
                ), proven=realizer.join_clauses(g["proven"]), rule=g["rule"], missing=missing)))
            else:
                parts.append(realizer.sentence(self.pick("rule_open", (
                    "I hold a rule that {rule}, but I can't tell whether {missing}",
                    "There's a rule that would settle it, {rule}, if only I knew whether {missing}",
                    "{rule}, and that would decide it, except whether {missing} is unknown to me",
                    "It comes down to whether {missing}, because {rule}",
                ), rule=g["rule"], missing=missing)))
            # Asked so the answer comes back as a statement she can learn and connect.
            ask = self.pick("ask_premise", (
                "If you know whether {missing}, tell me and I'll work out the rest.",
                "That's the one thing I need: tell me whether {missing}.",
                "Fill in whether {missing}, and I'll connect it up.",
                "Let me know whether {missing}; the rest follows from there.",
                "Tell me about whether {missing}, and I can finish the reasoning.",
            ), missing=missing)
        else:
            what = realizer._wh_clause(frame, subject_text) if frame.slot \
                else "whether " + realizer.realize_frame(frame, True, subject_text)
            parts.append(realizer.sentence(self.pick("not_linked", (
                "None of that tells me {what}, though",
                "But nothing there settles {what}",
                "Still, that doesn't get me to {what}",
                "That isn't enough to know {what}",
            ), what=what)))
        speaker = self.convo.state.speaker
        self.open[speaker] = Gap(query, frame, subject_text, time.time())
        trace = {"known": known, "rule_gaps": gaps[:3]}
        text = " ".join(parts)
        return (f"{text} {ask}" if ask else text), {**trace, "asked": bool(ask)}

    def ask_directly(self, frame, subject_text: str | None) -> str:
        """For a wh-question nothing else asks about: invite the missing fact itself."""
        what = realizer._wh_clause(frame, subject_text)
        return self.pick("ask_direct", (
            "Do you know {what}?", "Could you tell me {what}?", "Any idea {what}?",
            "If you know {what}, I'd like to learn it.", "Would you fill me in on {what}?",
        ), what=what)

    # --- reusing what she learns --------------------------------------------------------

    def revisit(self, speaker: str) -> str | None:
        """After learning something, try the open question again; if it's settled now, say so."""
        gap = self.open.get(speaker)
        if gap is None:
            return None
        if time.time() - gap.opened > OPEN_SECONDS:
            self.open.pop(speaker, None)
            return None
        answer = self.convo.engine.ask(gap.query, gap.frame)
        if answer.status == UNKNOWN:
            return None
        self.open.pop(speaker, None)
        text = realizer.realize_answer(self.repo, answer, gap.subject_text)
        return self.pick("filled", (
            "That fills the gap. {answer}", "Now I can answer what you asked before. {answer}",
            "Which settles the earlier question. {answer}", "Putting that together: {answer}",
            "So, back to your question. {answer}",
        ), answer=text)
