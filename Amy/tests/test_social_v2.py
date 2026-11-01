"""Content checks for Amy's authored social rewrite, independent of runtime wiring."""

from collections import defaultdict
import json
from pathlib import Path
import re
from string import Formatter


PATH = Path(__file__).resolve().parents[1] / "dialogue" / "banks" / "social_v2.json"


def load():
    return json.loads(PATH.read_text(encoding="utf-8"))


def test_every_social_family_has_a_full_rewrite():
    from dialogue.social import REPLIES
    banks = load()
    assert set(banks) == set(REPLIES)
    assert min(map(len, banks.values())) >= 45
    assert sum(len(banks[key]) for key in (
        "greet_first", "greet", "greet_morning", "greet_afternoon", "greet_evening")) == 288


def test_authored_entries_do_not_repeat_substantive_sentences():
    for key, bank in load().items():
        assert len(bank) == len(set(bank)), key
        clauses = defaultdict(list)
        for index, text in enumerate(bank):
            for clause in re.split(r"(?<=[.!?;])\s+", text):
                clause = re.sub(r"\{[^}]+\}", "FIELD", clause).lower().strip(" .!?;")
                if len(clause.split()) >= 5:
                    clauses[clause].append(index)
        assert not {clause: ids for clause, ids in clauses.items() if len(ids) > 1}, key


def test_social_templates_format_without_punctuation_artifacts():
    values = {"name": " Travis", "self_name": "Amy", "maker": "Travis", "noun": "color",
              "possessive": "your", "span": "two days", "facts": "12 facts", "rules": "3 rules",
              "people": "4 people", "extra": " You can also call me Amie.", "word": "curious",
              "mood_word": "okay", "count": "one question"}
    for key, bank in load().items():
        for text in bank:
            fields = {name for _, name, _, _ in Formatter().parse(text) if name}
            assert fields <= values.keys(), (key, fields)
            assert text.count("?") <= 1, (key, text)
            rendered = text.format(**values)
            assert "  " not in rendered, (key, text)
            assert "{name}" not in rendered


def test_no_old_shared_prefix_expansion_or_tail_products():
    banned = ("Let me put it this way:", "To put it plainly:", "Here's what I mean:",
              "In other words:", "Here's the short version:", "The way I'd put it is:")
    for key, bank in load().items():
        for text in bank:
            assert not text.startswith(banned), (key, text)
            assert not text.endswith((" I'm listening.", " We can take our time.",
                                      " What else would you like to explore?")), (key, text)


def test_sensitive_banks_have_no_flirtation_or_pressure_to_stay():
    for key in ("wellbeing_bad", "mood_low", "stress_high", "insult", "lonely"):
        for text in load()[key]:
            assert not re.search(r"\b(?:charmer|flattery|flirt|sexy|don't leave|need you|stay with me)\b", text, re.I)
