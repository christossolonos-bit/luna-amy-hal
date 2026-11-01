from app.conversation import Conversation
from db.repository import Repository
import pytest


@pytest.fixture
def convo():
    repo = Repository(":memory:")
    yield Conversation(repo)
    repo.close()


def test_rule_gap_names_the_missing_condition_and_reuses_the_answer(convo):
    convo.handle("If someone owns a dog, they are a pet owner.")
    convo.handle("Howard drives a truck.")
    reply = convo.handle("Is Howard a pet owner?")
    chain = reply.trace["answers"][-1] if "answers" in reply.trace else None
    assert "owns a dog" in reply.text, reply.text
    assert "Howard drives a truck" in reply.text
    settled = convo.handle("Howard owns a dog.")
    assert "pet owner" in settled.text.split(".", 1)[1], settled.text


def test_plain_gap_asks_and_revisits(convo):
    convo.handle("Bob drives a bus.")
    asked = convo.handle("What does Bob own?")
    assert "Bob drives a bus" in asked.text
    taught = convo.handle("Bob owns a boat.")
    assert taught.text.count("Bob owns a boat") >= 2, taught.text
