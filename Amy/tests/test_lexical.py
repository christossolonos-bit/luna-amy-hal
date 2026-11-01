"""WordNet relation reasoning, against the installed Open English WordNet (skipped if not installed)."""

import pytest

from app.conversation import Conversation
from db.repository import Repository
from nlp.wordnet import WordNet

pytestmark = pytest.mark.skipif(not WordNet().available, reason="WordNet database not installed")


@pytest.fixture
def convo():
    repo = Repository(":memory:")
    yield Conversation(repo, speaker="Travis", self_name="Adam")
    repo.close()


def say(convo, text):
    return convo.handle(text).text


def test_compare_picks_related_meanings(convo):
    text = say(convo, "How is a dog like a cat?")
    assert "both kinds of carnivore" in text and "CAT" not in text and "X rays" not in text
    text = say(convo, "What's the difference between a car and a bus?")
    assert "both kinds of" in text and "a car is a motor vehicle" in text


def test_parts_and_wholes(convo):
    text = say(convo, "What are the parts of a car?")
    assert text.startswith("A car has parts such as") and "machine" not in text
    assert say(convo, "Does a car have an engine?").startswith("Yes. A car has an engine")
    assert "wheeled vehicle" in say(convo, "What is a wheel part of?")


def test_kinds_synonyms_meaning(convo):
    assert say(convo, "What kinds of dog are there?").startswith("Kinds of dog include")
    assert "“large”" in say(convo, "What's another word for big?")
    assert say(convo, "What does listen mean?").startswith("To listen means to hear with intention.")


def test_usual_meaning_instead_of_a_quiz(convo):
    text = say(convo, "What is a dog?")
    assert "domesticated" in text and "usual meaning" in text and "Which meaning" not in text
    assert "unpleasant" in say(convo, "Tell me about the second one.")


def test_taught_kinds_reach_wordnet_categories(convo):
    say(convo, "Howard owns a beagle.")
    reply = convo.handle("Does Howard own an animal?")
    assert reply.trace["status"] == "yes"
    assert "because Howard owns a beagle, and a beagle is a kind of animal" in reply.text
    assert convo.handle("Does Howard own a blue animal?").trace["status"] != "yes"


def test_everyday_words_get_a_connected_reply(convo):
    text = say(convo, "Listening to you means you are getting input.")
    assert "listening" in text and "What does" not in text  # the gist, not a definition quiz
    assert "Yes, I'm listening" in say(convo, "Hey Adam, are you listening?")
    feelings = say(convo, "Do you have feelings?")
    assert "I can't tell whether I feel" in feelings and feelings.endswith("?")
