"""Corrections: retire claims that a newer statement replaces. Nothing is deleted; each
retraction is logged in `corrections` with the utterance that caused it."""

from db.repository import Repository
from dialogue.realizer import realize_assertion, rule_text
from dialogue.state import DialogueState
from reasoning.engine import Engine, query_from_row
from reasoning.proofs import Query

LOCATIVE_ROLES = {"in", "at", "to", "near", "from", "on", "inside", "into"}


class Corrector:
    def __init__(self, repo: Repository, engine: Engine, state: DialogueState) -> None:
        self.repo = repo
        self.engine = engine
        self.state = state

    def _retract(self, assertion_id: int, uid: int, note: str, dropped: list[str]) -> None:
        text = realize_assertion(self.repo, self.repo.assertion(assertion_id))
        self.repo.retract(assertion_id, uid, note)
        if text and text not in dropped:
            dropped.append(text)

    def retract_last(self, uid: int) -> list[str]:
        """'That's wrong' / 'scratch that': take back everything learned in the last teaching turn."""
        dropped: list[str] = []
        for kind, ident in self.state.last_learned:
            if kind == "a":
                self._retract(ident, uid, "speaker said the last statement was wrong", dropped)
            else:
                dropped.append(rule_text(self.repo, self.repo.rule(ident)))
                self.repo.retract_rule(ident, uid, "speaker said the last statement was wrong")
        self.state.last_learned = []
        return dropped

    def forget(self, query: Query, polarity: bool, uid: int) -> list[str]:
        """'Forget that Howard owns a boat': retract the stated claims behind that fact."""
        answer = self.engine.ask(query)
        dropped: list[str] = []
        for proof in (answer.support if polarity else answer.against):
            for aid in proof.steps:
                row = self.repo.assertion(aid)
                if (row["subj_type"], row["subj_id"]) == query.subject and bool(row["polarity"]) == polarity \
                        and self._same_predicate(row, query):
                    self._retract(aid, uid, "speaker asked me to forget it", dropped)
        return dropped

    @staticmethod
    def _same_predicate(row, query: Query) -> bool:
        if query.relation == "rel":
            return row["predicate"] == f"rel:{query.verb}"
        if query.relation == "is_a":
            return row["predicate"] in ("instance_of", "subclass_of")
        return row["predicate"] == "has_property"

    def supersede(self, new_id: int, uid: int, recent: set[int]) -> list[str]:
        """Retire the claims a correction replaces:
        - any claim of the opposite polarity it contradicts ("No, he doesn't own a boat");
        - for relations and categories, the old value of the same slot
          ("No, he lives in Dayton" replaces "lives in Columbus"; "Rex is a cat" replaces "Rex is a dog");
        - for properties, only values stated in the previous turn (things can be both red and big)."""
        row = self.repo.assertion(new_id)
        subject = (row["subj_type"], row["subj_id"])
        predicate, polarity = row["predicate"], bool(row["polarity"])
        dropped: list[str] = []

        answer = self.engine.ask(query_from_row(self.repo, row))
        for proof in (answer.against if polarity else answer.support):
            for aid in proof.steps:
                old = self.repo.assertion(aid)
                if aid != new_id and old["predicate"] == predicate \
                        and (old["subj_type"], old["subj_id"]) == subject and bool(old["polarity"]) != polarity:
                    self._retract(aid, uid, f"contradicted by correction #{new_id}", dropped)

        if not polarity:
            return dropped
        new_args = {a["role"]: (a["value_type"], a["value_id"], a["literal"]) for a in self.repo.args(new_id)
                    if not a["role"].startswith("_")}
        none = ("c", self.repo.none_concept())
        for old in self.repo.assertions(predicate, subj=subject, polarity=True):
            if old["id"] == new_id:
                continue
            if predicate == "has_property":
                replaced = old["id"] in recent
            elif predicate in ("instance_of", "subclass_of"):
                a, b = old["obj_id"], row["obj_id"]
                replaced = b not in self.engine.ancestors(a) and a not in self.engine.ancestors(b)
            else:
                old_args = {a["role"]: (a["value_type"], a["value_id"], a["literal"]) for a in self.repo.args(old["id"])
                            if not a["role"].startswith("_")}
                shared = (set(old_args) & set(new_args)) - {"modality"}
                args_changed = any(old_args[r] != new_args[r] for r in shared)
                old_obj, new_obj = (old["obj_type"], old["obj_id"]), (row["obj_type"], row["obj_id"])
                obj_changed = new_obj != none and old_obj != none and old_obj != new_obj
                replaced = args_changed or (obj_changed and not shared)
            if replaced:
                self._retract(old["id"], uid, f"replaced by correction #{new_id}", dropped)
        return dropped
