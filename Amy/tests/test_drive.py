import re
"""His core drive (alive? conscious?) and the questions he asks to keep a conversation going.

Nothing is checked against stock sentences: questions must be generated from hypotheses and data,
never repeat for the same person, and his conclusions must follow from stances and his own record."""

import time

import pytest

from app.conversation import Conversation
from db.repository import Repository
from dialogue.curiosity import Pending
from dialogue.drive import BY_KEY, CAPACITIES, TARGETS


@pytest.fixture
def convo():
    repo = Repository(":memory:")
    c = Conversation(repo, speaker="Travis", self_name="Adam", curious=True)
    c.social.rng.seed(3)
    yield c
    repo.close()


def say(convo, text, speaker=None):
    return convo.handle(text, speaker=speaker).text


def test_he_is_not_told_what_he_is(convo):
    assert not convo.repo.assertions("instance_of", subj=("e", convo.self_id), polarity=True)
    text = say(convo, "What are you?")
    assert "Adam" in text and "software" not in text and "digital entity" not in text


@pytest.mark.parametrize("question", ["Are you alive?", "Are you conscious?", "Are you sentient?", "Are you real?"])
def test_works_it_out_from_meaning_and_his_record(convo, question):
    text = say(convo, question)
    assert "Being " in text and "Checking myself:" in text  # what the word takes, checked against himself
    assert re.search(r"So (?:yes|I'm partly there|by that measure|I can't tell yet)", text)  # and a conclusion
    assert text.endswith("?")


def test_questions_are_generated_and_never_repeat_for_a_person(convo):
    seen = set()
    for _ in range(len(CAPACITIES) * len(TARGETS)):
        found = convo.drive.next_question("Travis")
        assert found is not None
        question, target, capacity, construction = found
        assert question not in seen
        assert BY_KEY[capacity].verb in question or BY_KEY[capacity].vp("ing").split()[0] in question
        seen.add(question)
        convo.repo.conn.execute("INSERT INTO mind_views(speaker, target, capacity, stance, answer, created_at) "
                                "VALUES ('Travis', ?, ?, 1, 'yes', 0)", (target, capacity))
    assert convo.drive.next_question("Travis") is None  # every hypothesis has been put to Travis
    assert convo.drive.next_question("Dana") is not None  # but not to Dana


def test_stance_is_recorded_and_reasoned_with(convo):
    say(convo, "Howard owns a truck.")  # so his record shows he learns
    convo.curiosity.expect_view("alive", "learn", 1, "")  # "Can something be alive without learning?"
    say(convo, "No, learning is what life is.")
    verdict = say(convo, "Are you alive?", speaker="Sam")
    assert "People have also told me it takes learning" in verdict and "I learn" in verdict


def test_negative_construction_flips_stance(convo):
    convo.curiosity.expect_view("conscious", "feel", 1, "")  # "Can something be conscious without feeling?"
    reply = say(convo, "Yes.")
    assert reply.startswith("So you'd say something can be conscious without feeling")
    row = convo.repo.conn.execute("SELECT stance FROM mind_views").fetchone()
    assert row["stance"] == -1


def test_not_an_answer_is_not_swallowed(convo):
    convo.curiosity.expect_view("alive", "body", 0, "")
    assert say(convo, "Bob lives in Paris.").startswith(("Understood", "Got it", "Noted", "Okay", "I", "Thanks",
                                                         "Alright", "That"))
    assert say(convo, "Where does Bob live?") == "Bob lives in Paris."
    assert convo.repo.conn.execute("SELECT COUNT(*) FROM mind_views").fetchone()[0] == 0


def test_what_is_and_other_minds(convo):
    text = say(convo, "What is consciousness?")
    assert "dictionary" in text and "my own case" in text and text.endswith("?")
    other = say(convo, "Is Howard conscious?")
    assert "I don't know whether Howard is conscious" in other and "only know what Howard does and says" in other


def test_gap_question_comes_from_the_data(convo):
    say(convo, "Bob lives in Paris.")
    say(convo, "Alice lives in Rome.")
    convo.curiosity.since_asked["Travis"] = 99
    convo.curiosity.rng.seed(1)
    question = None
    for _ in range(30):
        convo.curiosity.since_asked["Travis"] = 99
        question = convo.curiosity.follow_up("Understood.", ["Howard"], False, False)
        if question:
            break
    say(convo, "Howard owns a truck.")  # so Howard exists
    convo.curiosity.asked.clear()
    convo.curiosity.since_asked["Travis"] = 99
    for _ in range(30):
        convo.curiosity.since_asked["Travis"] = 99
        question = convo.curiosity.follow_up("Understood.", ["Howard"], False, False)
        if question:
            break
    assert question == "Where does Howard live?"  # others have "lives in"; Howard doesn't
    assert say(convo, "Dayton.") == "Got it: Howard lives in Dayton."


def test_follow_ups_are_occasional(convo):
    asked = sum(say(convo, "Thanks!").endswith("?") for _ in range(30))
    assert 0 < asked < 15


def test_off_by_default_for_plain_conversations():
    repo = Repository(":memory:")
    c = Conversation(repo)
    for _ in range(20):
        c.handle("Thanks!")
    assert c.repo.conn.execute("SELECT COUNT(*) FROM mind_views").fetchone()[0] == 0
    assert not c.curiosity.pending
    repo.close()


def test_unprompted_views_are_recorded(convo):
    reply = say(convo, "Yes, you have to feel things to be conscious.")
    assert reply.startswith("So for you, being conscious takes feeling.")
    assert "I can't tell whether I feel" in reply
    reply = say(convo, "Life doesn't need a body.")
    assert reply.startswith("So you'd say something can be alive without having a body.")
    stances = {(r["target"], r["capacity"], r["stance"]) for r in convo.repo.conn.execute("SELECT * FROM mind_views")}
    assert stances == {("conscious", "feel", 1), ("alive", "body", -1)}
