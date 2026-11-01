import pytest

from app.conversation import Conversation
from db.repository import Repository


@pytest.fixture
def convo():
    repo = Repository(":memory:")
    yield Conversation(repo)
    repo.close()


def test_repeat_last_reply(convo):
    original = convo.handle("Howard owns a red truck.").text
    reply = convo.handle("Can you repeat that?")
    assert reply.text == f"I said: “{original}”"
    assert convo.handle("What did you say?").text == reply.text  # repeating a repeat stays stable


def test_what_did_i_say(convo):
    convo.handle("Howard owns a red truck.")
    convo.handle("Repeat that.")
    assert convo.handle("What did I just say?").text == "You said: “Howard owns a red truck.”"


def test_what_did_i_tell_you_about(convo):
    convo.handle("Howard drove his truck to Ohio.")
    convo.handle("Bob owns a boat.")
    text = convo.handle("What did I tell you about Howard?").text
    assert "you said: “Howard drove his truck to Ohio.”" in text and "Bob" not in text
    assert convo.handle("What did I tell you about cheese?").text == "I don't have anything you said about cheese."


def test_when_did_i_tell_you(convo):
    convo.handle("Howard owns a boat.")
    reply = convo.handle("When did I tell you that Howard owns a boat?")
    assert reply.text.startswith("You told me that today at ")
    assert "“Howard owns a boat.”" in reply.text


def test_did_i_tell_you_yes_no(convo):
    convo.handle("Howard owns a boat.")
    yes = convo.handle("Did I tell you that Howard owns a boat?")
    assert yes.trace["status"] == "yes" and yes.text.startswith("Yes. You told me today at")
    no = convo.handle("Did I tell you that Bob owns a boat?")
    assert no.trace["status"] == "no" and no.text == "No, I don't have a record of you saying that."


def test_did_i_tell_you_matches_meaning_not_words(convo):
    convo.handle("Howard has a little red boat.")
    assert convo.handle("Did I tell you Howard owns a boat?").trace["status"] == "yes"
    convo.handle("Rex is a dog.", )
    convo.handle("A dog is an animal.")
    assert convo.handle("Did I say Rex is a dog?").trace["status"] == "yes"


def test_search_is_fuzzy(convo):
    convo.handle("Howard drove his red truck to Ohio yesterday.")
    convo.handle("Bob owns a boat.")
    # different word forms, different order, a typo, and an extra word that never appeared
    assert convo.handle("Did I mention Howard driving trucks?").trace["status"] == "yes"
    assert convo.handle("Have we talked about Haward in Ohio?").trace["status"] == "yes"
    assert convo.handle("Have we talked about volcanoes?").trace["status"] == "no"
    found = convo.handle("Search for trucks driven to Ohio").text
    assert found.startswith("I found 1 mention:") and "Ohio" in found


def test_did_you_say(convo):
    convo.handle("Howard owns a boat.")
    assert convo.handle("Did you say Howard owns a boat?").trace["status"] == "yes"
    assert convo.handle("Did you say Bob is tall?").trace["status"] == "no"


def test_remember_this_teaches(convo):
    reply = convo.handle("Remember this: Howard's birthday is in May.")
    assert reply.text.endswith(": a birthday is in May.")
    convo.handle("Remember that Bob lives in Dayton.")
    assert convo.handle("Where does Bob live?").text == "Bob lives in Dayton."


def test_recall_respects_speaker(convo):
    convo.handle("Howard owns a boat.", speaker="alice")
    assert convo.handle("Did I tell you that Howard owns a boat?", speaker="bob").trace["status"] == "no"
    text = convo.handle("What did alice tell you about Howard?", speaker="bob").text
    assert "alice said" in text


def test_meta_questions_are_not_search_results(convo):
    convo.handle("Howard owns a boat.")
    convo.handle("Did I tell you about boats?")
    text = convo.handle("Search for boats").text
    assert text.startswith("I found 1 mention:")


def test_repeat_survives_spoken_lead_in(convo):
    original = convo.handle("Howard owns a red truck.").text
    reply = convo.handle("Okay, so what did you just say?")
    assert reply.text == f"I said: “{original}”"
    # the same question with no lead-in answers the same way
    assert convo.handle("Um, what did you just say").text == reply.text


@pytest.mark.parametrize("question", [
    "What have we been talking about?",
    "what have we been talking about",
    "What did we talk about?",
    "What were we talking about?",
    "Um, what have we been talking about?",
    "Can you summarize our conversation?",
    "Where were we?",
])
def test_recalls_what_we_have_been_talking_about(convo, question):
    convo.handle("Howard owns a red truck.")
    convo.handle("Bob lives in Dayton.")
    text = convo.handle(question).text
    assert text.startswith("We've talked about 2 things so far.")
    assert "Howard owns a red truck" in text and "Bob lives in Dayton" in text
    assert "what have we" not in text.lower()


def test_summary_ignores_acknowledgements_and_itself(convo):
    convo.handle("Howard owns a red truck.")
    convo.handle("OK.")
    convo.handle("Thanks.")
    text = convo.handle("What have we been talking about?").text
    assert text.startswith("We've talked about 1 thing so far.")
    assert "Howard owns a red truck" in text
    assert convo.handle("What have we been talking about?").text == text  # the question is not a topic


def test_echoed_questions_keep_perspective(convo):
    assert "whether I can hear you" in convo.handle("Can you hear me?").text
    assert "whether I remember your name" in convo.handle("Do you remember my name?").text
