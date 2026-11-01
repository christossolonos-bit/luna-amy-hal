from dataclasses import dataclass, field
from typing import Union


@dataclass(frozen=True, eq=False)  # identity equality: two "a truck" mentions are different referents
class NounPhrase:
    head: str                       # lemma, or lowercased name for proper nouns
    modifiers: tuple[str, ...] = ()
    determiner: str | None = None
    proper: bool = False
    plural: bool = False
    surface: str = ""
    pronoun: str | None = None      # canonical subject form: i, you, he, she, it, they, we
    possessor: "NounPhrase | None" = None
    quantifier: str | None = None   # someone, anything, everyone, ...


Arg = tuple[str, Union[NounPhrase, str, None]]  # (preposition or 'time', value)


@dataclass(frozen=True)
class Frame:
    act: str                        # 'assert' | 'ask'
    relation: str                   # 'is_a' | 'property' | 'rel' | 'describe'
    subject: NounPhrase | None      # None = the slot a wh-question asks for
    obj: NounPhrase | None = None
    verb: str | None = None         # lemma, for relation == 'rel'
    polarity: bool = True
    tense: str = "present"
    modality: str | None = None
    args: tuple[Arg, ...] = ()
    properties: tuple[str, ...] = ()
    confidence: float = 1.0
    slot: str | None = None         # subject | object | where | when | <preposition>
    wh: str | None = None
    correction: bool = False        # the speaker is correcting something said earlier
    notes: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class RuleFrame:
    """'If CONDITIONS, CONCLUSIONS' — also 'Anyone who ... is ...' and 'X if Y'."""
    conditions: tuple[Frame, ...]
    conclusions: tuple[Frame, ...]
    act: str = "assert"
    confidence: float = 1.0
    correction: bool = False
