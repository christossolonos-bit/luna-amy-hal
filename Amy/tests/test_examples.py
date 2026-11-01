import random

from dialogue.examples import EXAMPLES, capabilities, hint


def test_hints_vary_and_span_categories():
    rng = random.Random(1)
    seen = {hint("whereupon the quixotic", rng=rng) for _ in range(20)}
    assert len(seen) > 10  # not the same line every time
    every = {e for group in EXAMPLES.values() for e in group}
    for text in seen:
        quoted = [q for q in every if f"“{q}”" in text]
        assert len(quoted) == 3


def test_question_shaped_input_leads_with_a_question_example():
    text = hint("What is going on with that thing?", rng=random.Random(2))
    first = text.split("“")[1].split("”")[0]
    assert first in EXAMPLES["question"]


def test_capabilities_shows_one_of_each():
    text = capabilities(random.Random(3))
    for label in ("teach me facts", "ask me questions", "give me rules", "correct me", "ask what we said"):
        assert label in text
