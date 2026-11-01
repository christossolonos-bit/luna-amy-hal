from dataclasses import dataclass, field

from nlp.semantic_frames import Frame

Term = tuple[str, int]


@dataclass
class DialogueState:
    speaker: str = "user"
    last_frame: Frame | None = None
    last_trace: dict | None = None
    last_person: Term | None = None          # antecedent for he / she / they
    last_thing: Term | None = None           # antecedent for it
    recent: dict[int, int] = field(default_factory=dict)  # kind concept id -> most recent instance
    last_learned: list[tuple[str, int]] = field(default_factory=list)  # ('a'|'r', id) from the last teaching turn
    last_assert_frame: Frame | None = None
