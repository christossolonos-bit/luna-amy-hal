from dataclasses import dataclass, field

from nlp.semantic_frames import Frame

YES, NO, CONFLICTING, UNKNOWN = "yes", "no", "conflicting", "unknown"

Spec = tuple  # ('e', id) | ('c', id) | ('kind', concept_id, modifiers) | ('any',)


@dataclass
class Query:
    relation: str                       # is_a | property | rel | describe
    subject: Spec | None                # None = the slot being asked for
    obj: Spec | None = None
    verb: str | None = None
    properties: tuple[int, ...] = ()    # concept ids
    args: tuple = ()                    # (role, Spec | literal str)
    slot: str | None = None


@dataclass
class Proof:
    steps: list[int]                    # assertion ids, in reading order
    rules: list[str] = field(default_factory=list)
    confidence: float = 1.0
    learned_rules: list[int] = field(default_factory=list)   # learned_rules ids applied
    derived: tuple | None = None    # (conclusion pattern, subject term) when a rule produced it


@dataclass
class Answer:
    status: str
    question: Frame | None
    support: list[Proof]
    against: list[Proof]
    missing: list[str]
    budget_exhausted: bool = False
    steps_used: int = 0
    query: Query | None = None
