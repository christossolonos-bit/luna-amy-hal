"""Being asked the same thing again, by meaning, by the same person."""

import pytest

from app.conversation import Conversation
from db.repository import Repository


@pytest.fixture
def convo():
    repo = Repository(":memory:")
    c = Conversation(repo, speaker="Travis", self_name="Adam")
    yield c
    repo.close()


def say(convo, text, speaker=None):
    return convo.handle(text, speaker=speaker).text


def test_escalates_instead_of_repeating_itself(convo):
    say(convo, "Howard lives in Dayton.")
    first = say(convo, "Where does Howard live?")
    assert first == "Howard lives in Dayton."
    second = say(convo, "Where does Howard live?")
    assert second == "I just told you: Howard lives in Dayton."
    third = say(convo, "where does howard live")
    assert third.startswith("That's the third time you've asked. Howard lives in Dayton.")
    fourth = say(convo, "Where does he live?")  # same meaning, different words
    assert fourth.startswith("I keep telling you: Howard lives in Dayton. You've asked me 4 times now.")
    assert len({first, second, third, fourth}) == 4


def test_yes_no_repeats_use_the_answer_itself(convo):
    say(convo, "Howard owns a red truck.")
    say(convo, "Trucks are vehicles.")
    assert say(convo, "Does Howard own a vehicle?").startswith("Yes.")
    assert say(convo, "Does Howard own a vehicle?") == "I just told you: Howard owns a vehicle."


def test_unknown_repeats_and_no_question_back(convo):
    first = say(convo, "Does Bob own a boat?")
    assert first.startswith("I don't know")
    again = say(convo, "Does Bob own a boat?")
    assert again.startswith("Same as") and "?" not in again


def test_other_people_and_other_questions_are_fresh(convo):
    say(convo, "Howard lives in Dayton.")
    say(convo, "Where does Howard live?")
    assert say(convo, "Where does Howard live?", speaker="Dana") == "Howard lives in Dayton."
    assert say(convo, "Where does Bob live?", speaker="Travis").startswith("I don't know")


def test_pestering_raises_stress(convo):
    say(convo, "Howard lives in Dayton.")
    before = convo.self_model.current()["stress"]
    for _ in range(5):
        say(convo, "Where does Howard live?")
    assert convo.self_model.current()["stress"] > before


def test_never_the_same_sentence_twice_in_a_row(convo):
    say(convo, "Howard lives in Dayton.")
    replies = [say(convo, "Where does Howard live?") for _ in range(8)]
    assert all(a != b for a, b in zip(replies, replies[1:]))
