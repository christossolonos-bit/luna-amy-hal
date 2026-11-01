"""Second and third passes, on the exact messages that came back as 'I need your help'."""

import pytest

from app.conversation import Conversation
from db.repository import Repository
from nlp.wordnet import WordNet


@pytest.fixture
def convo():
    repo = Repository(":memory:")
    c = Conversation(repo, speaker="Travis", self_name="Adam")
    yield c
    repo.close()


def say(convo, text):
    return convo.handle(text).text


NOT_HELP = ("I need your help", "could use your help", "I'm not following", "I didn't follow the whole sentence")


@pytest.mark.skipif(not WordNet().available, reason="WordNet not installed")
def test_conditional_question_is_reasoned_and_not_stored(convo):
    reply = say(convo, "Am I an animal if I'm human?")
    assert reply.startswith("Yes. If you are a human, then you are an animal")
    assert "haven't stored that" in reply
    assert convo.repo.conn.execute("SELECT COUNT(*) FROM assertions WHERE status='active' "
                                   "AND source_utterance_id IS NOT NULL").fetchone()[0] == 0


@pytest.mark.parametrize("text", ["That's very good.", "You are being very good right now.", "That's great!"])
def test_praise_is_recognised(convo, text):
    say(convo, "What is a human")
    reply = say(convo, text)
    assert not any(p in reply for p in NOT_HELP)
    assert reply.startswith("Thank you.")


def test_criticism_is_recognised(convo):
    reply = say(convo, "That's awful.")
    assert reply.startswith("I hear that you think it was awful.")


def test_did_you_understand(convo):
    say(convo, "Howard lives in Dayton.")
    reply = say(convo, "Do you understand what I said?")
    assert reply == "I think so. As I understood it, you were telling me that Howard lives in Dayton."


def test_encouragement_to_continue(convo):
    say(convo, "Howard lives in Dayton.")
    assert say(convo, "Okay, that's got to continue.").startswith("Then I'll keep going the same way.")


def test_simplifies_before_giving_up(convo):
    say(convo, "Howard is really tall right now.")
    assert convo.handle("Is Howard tall?").trace["status"] == "yes"


@pytest.mark.skipif(not WordNet().available, reason="WordNet not installed")
def test_unclear_sentence_connects_to_the_topic(convo):
    say(convo, "What is a signal?")
    reply = say(convo, "The signal thing over there was wild, honestly, whatever.")
    assert "signal" in reply and not any(p in reply for p in NOT_HELP)


def test_no_raw_numbers_in_conversation(convo):
    reply = say(convo, "That's very good.")
    assert not __import__("re").search(r"[+-]\d\.\d\d", reply)
