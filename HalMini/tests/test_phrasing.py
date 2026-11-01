import random
from string import Formatter

import pytest

from app.conversation import Conversation
from db.repository import Repository
from dialogue.phrasing import BANKS, PhraseDeck
from dialogue.social import REPLIES


@pytest.mark.parametrize("intent", ["misunderstood", "misunderstood_targeted"])
def test_thousands_of_unique_replies_before_repeating(intent):
    bank = BANKS[intent]
    assert len(bank) == len(set(bank)) >= 4000
    deck = PhraseDeck(random.Random(42))
    replies = [deck.pick(intent) for _ in bank]
    assert set(replies) == set(bank)
    assert len(replies) == len(set(replies))
    # Crossing into another shuffled cycle must not repeat the last response.
    assert deck.pick(intent) != replies[-1]


def test_total_variation_does_not_count_names_or_fact_substitutions():
    templates = {line for bank in (*BANKS.values(), *REPLIES.values()) for line in bank}
    assert len(templates) >= 10_000


def test_all_templates_format_cleanly():
    for bank in (*BANKS.values(), *REPLIES.values()):
        for line in bank:
            fields = {name: "example" for _, name, _, _ in Formatter().parse(line) if name}
            rendered = line.format(**fields)
            assert rendered.strip() == rendered
            assert "  " not in rendered


def test_seeded_decks_are_reproducible():
    a, b = PhraseDeck(random.Random(17)), PhraseDeck(random.Random(17))
    assert [a.pick("misunderstood") for _ in range(50)] == [b.pick("misunderstood") for _ in range(50)]


def test_learning_varies_without_changing_the_fact():
    repo = Repository(":memory:")
    try:
        convo = Conversation(repo)
        replies = [convo.handle("Howard owns a truck.").text for _ in range(25)]
        assert len(set(replies)) == len(replies)
        assert all(reply.endswith(": Howard owns a truck.") for reply in replies)
        assert convo.handle("Does Howard own a truck?").trace["status"] == "yes"
        assert all(any(reply.endswith(line.format(fact="Howard owns a truck")) for line in BANKS["learned"])
                   for reply in replies)
    finally:
        repo.close()


def test_unparsed_conversation_varies_and_preserves_targeted_question():
    repo = Repository(":memory:")
    try:
        convo = Conversation(repo)
        replies = [convo.handle("Whereupon the zorbly notwithstanding.") for _ in range(40)]
        assert "What does 'zorbly' mean?" in replies[0].text
        assert all(a.text != b.text for a, b in zip(replies, replies[1:])) or len({r.text for r in replies}) > 1
        assert all(reply.trace["intent"] == "clarify" for reply in replies)
        assert repo.open_questions()
    finally:
        repo.close()


def test_anonymous_social_replies_do_not_repeat_after_name_substitution():
    repo = Repository(":memory:")
    try:
        convo = Conversation(repo)
        bank = {line.replace("{name}", "") for line in REPLIES["thanks"]}
        replies = [convo.handle("Thanks.").text for _ in bank]
        assert len(set(replies)) == len(bank)
    finally:
        repo.close()
