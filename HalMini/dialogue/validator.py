"""Parse-back check: re-parse a generated clause and confirm actor, object, verb and polarity
survived generation."""

from dataclasses import dataclass

from db.repository import Repository
from nlp.lexicon import Lexicon
from nlp.parser import ParseError, parse
from nlp.semantic_frames import Frame

Signature = tuple


@dataclass
class ValidationResult:
    ok: bool
    detail: str


def _head(np) -> str | None:
    """A noun phrase's head, with 'I'/'me' and 'you' read as the bot and the person it's talking to."""
    from dialogue.realizer import PERSPECTIVE
    if np is None:
        return None
    if np.pronoun == "i" and PERSPECTIVE["self_name"]:
        return PERSPECTIVE["self_name"].lower()
    if np.pronoun == "you" and PERSPECTIVE["speaker_name"]:
        return PERSPECTIVE["speaker_name"].lower()
    return np.head


def frame_signature(frame: Frame, polarity: bool, subject_head: str | None = None) -> Signature:
    subject = subject_head if subject_head is not None else _head(frame.subject)
    return (frame.relation, frame.verb if frame.relation == "rel" else None, polarity, subject,
            _head(frame.obj), tuple(frame.properties))


def _term_head(repo: Repository, kind: str, ident: int) -> str:
    if kind == "c":
        return repo.concept_lemma(ident)
    row = repo.entity_row(ident)
    return row["name"].lower() if row["proper"] else repo.concept_lemma(row["kind_concept_id"])


def row_signature(repo: Repository, row) -> Signature:
    predicate = row["predicate"]
    subject = _term_head(repo, row["subj_type"], row["subj_id"])
    polarity = bool(row["polarity"])
    if predicate in ("instance_of", "subclass_of"):
        return ("is_a", None, polarity, subject, repo.concept_lemma(row["obj_id"]), ())
    if predicate == "has_property":
        return ("property", None, polarity, subject, None, (repo.concept_lemma(row["obj_id"]),))
    none = row["obj_type"] == "c" and row["obj_id"] == repo.none_concept()
    obj = None if none else _term_head(repo, row["obj_type"], row["obj_id"])
    return ("rel", predicate[4:], polarity, subject, obj, ())


def _check(generated: str, want: Signature, lexicon: Lexicon | None) -> ValidationResult:
    try:
        reparsed = parse(generated, lexicon)
    except ParseError as exc:
        return ValidationResult(False, f"could not re-parse '{generated}': {exc}")
    got = frame_signature(reparsed, reparsed.polarity)
    if want != got and want[4] and got[4] and want[:4] + want[5:] == got[:4] + got[5:] \
            and want[4].split()[-1] == got[4] and reparsed.obj is not None \
            and " ".join((*reparsed.obj.modifiers, got[4])) == want[4]:
        return ValidationResult(True, "roles and polarity match (compound object)")  # "a crypto exchange"
    if want != got:
        return ValidationResult(False, f"expected {want}, re-parsed {got}")
    return ValidationResult(True, "roles and polarity match")


def validate(generated: str, expected: Frame, polarity: bool, subject_head: str | None = None,
             lexicon: Lexicon | None = None) -> ValidationResult:
    return _check(generated, frame_signature(expected, polarity, subject_head), lexicon)


def validate_row(repo: Repository, row, generated: str, lexicon: Lexicon | None = None) -> ValidationResult:
    return _check(generated, row_signature(repo, row), lexicon)
