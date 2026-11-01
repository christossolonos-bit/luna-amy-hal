"""Knowing who he's talking to: stable identity, bots, and only real conversations counted."""

import pytest

from app.conversation import Conversation
from db.repository import Repository


@pytest.fixture
def convo():
    repo = Repository(":memory:")
    c = Conversation(repo, self_name="Adam")
    yield c
    repo.close()


def say(convo, text, **who):
    return convo.handle(text, **who).text


def test_only_people_he_talked_with_count(convo):
    say(convo, "Hi", speaker="Travis", speaker_id="1")
    say(convo, "I'm online.", speaker="Nova", speaker_id="2", is_bot=True)
    convo.overhear("Nobody. No model.", "Chris", speaker_id="3")
    assert convo.people.count_people() == 1
    text = say(convo, "Who have you talked to?", speaker="Travis", speaker_id="1")
    assert text.startswith("Just you, so far.")
    assert "1 bot (Nova)" in text and "overheard 1 other voice" in text


def test_nickname_change_keeps_identity_and_facts(convo):
    say(convo, "I live in Dayton.", speaker="Travis", speaker_id="42")
    who = say(convo, "Who am I?", speaker="T-Rex", speaker_id="42")
    assert who.startswith("You're Travis, and your account shows T-Rex.")
    assert "you live in Dayton" in who
    assert say(convo, "Where do I live?", speaker="T-Rex", speaker_id="42").startswith(("You live in Dayton",
                                                                                        "I just told you"))
    assert convo.people.count_people() == 1


def test_same_name_different_people_stay_apart(convo):
    say(convo, "Hi", speaker="Sam", speaker_id="10")
    say(convo, "Hi", speaker="Sam", speaker_id="11")
    rows = convo.repo.conn.execute("SELECT COUNT(*) FROM people WHERE display = 'Sam'").fetchone()[0]
    assert rows == 2


def test_who_am_i_for_a_stranger(convo):
    text = say(convo, "Do you know who I am?", speaker="Dana", speaker_id="7")
    assert text.startswith("You're Dana.") and "haven't told me anything about yourself" in text
