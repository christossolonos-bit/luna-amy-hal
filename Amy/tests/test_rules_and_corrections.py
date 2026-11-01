import pytest

from app.conversation import Conversation
from db.repository import Repository


@pytest.fixture
def convo():
    repo = Repository(":memory:")
    yield Conversation(repo)
    repo.close()


def say(convo, *lines, speaker=None):
    return [convo.handle(line, speaker=speaker) for line in lines][-1]


# --- if-then rules --------------------------------------------------------------

def test_rule_fires_with_explanation(convo):
    ack = say(convo, "If someone owns a dog, they are a pet owner.")
    assert ack.text.endswith(": if someone owns a dog, they are a pet owner.")
    say(convo, "Howard owns a dog.")
    reply = convo.handle("Is Howard a pet owner?")
    assert reply.trace["status"] == "yes"
    assert reply.text == ("Yes. Howard is a pet owner. I know this because Howard owns a dog, "
                          "and if someone owns a dog, they are a pet owner.")
    assert convo.handle("Is Bob a pet owner?").trace["status"] == "unknown"


def test_rule_conditions_use_taxonomy(convo):
    say(convo, "If someone owns a vehicle, they pay insurance.", "Howard owns a truck.", "Trucks are vehicles.")
    assert convo.handle("Does Howard pay insurance?").trace["status"] == "yes"


def test_rule_forms(convo):
    say(convo, "Anyone who owns a boat is a sailor.", "Howard is happy if he owns a dog.",
        "Whenever someone is hungry, they eat food.", "Bob owns a boat.", "Howard owns a dog.", "Rex is hungry.")
    assert convo.handle("Is Bob a sailor?").trace["status"] == "yes"
    assert convo.handle("Is Howard happy?").trace["status"] == "yes"
    assert convo.handle("Does Rex eat food?").trace["status"] == "yes"


def test_typed_variable_and_negative_conclusion(convo):
    say(convo, "If a person is a vegetarian, they don't eat meat.", "Howard is a person.", "Howard is a vegetarian.")
    reply = convo.handle("Does Howard eat meat?")
    assert reply.trace["status"] == "no"
    assert "if a person is a vegetarian, they do not eat meat" in reply.text
    assert convo.handle("Does Bob eat meat?").trace["status"] == "unknown"


def test_rule_requires_all_conditions(convo):
    say(convo, "If someone is tall and strong, they are an athlete.", "Howard is tall.")
    assert convo.handle("Is Howard an athlete?").trace["status"] == "unknown"
    say(convo, "Howard is strong.")
    assert convo.handle("Is Howard an athlete?").trace["status"] == "yes"


def test_rules_chain(convo):
    say(convo, "If someone owns a dog, they are a pet owner.",
        "If someone is a pet owner, they buy pet food.", "Howard owns a dog.")
    reply = convo.handle("Does Howard buy pet food?")
    assert reply.trace["status"] == "yes"
    assert "they are a pet owner" in reply.text and "they buy pet food" in reply.text


def test_circular_rules_terminate(convo):
    say(convo, "If someone is happy, they are calm.", "If someone is calm, they are happy.")
    assert convo.handle("Is Howard happy?").trace["status"] == "unknown"
    say(convo, "Howard is calm.")
    assert convo.handle("Is Howard happy?").trace["status"] == "yes"


def test_rule_answers_who_questions(convo):
    say(convo, "If someone owns a dog, they are a pet owner.", "Howard owns a dog.", "Bob owns a cat.")
    reply = convo.handle("Who is a pet owner?")
    assert reply.text == ("Howard is a pet owner. I know this because Howard owns a dog, "
                          "and if someone owns a dog, they are a pet owner.")


def test_taxonomic_rule_becomes_subclass(convo):
    reply = say(convo, "If something is a dog, it is an animal.")
    assert reply.text.endswith(": a dog is an animal.")
    say(convo, "If X is a beagle, X is a dog.", "Snoopy is a beagle.")
    assert convo.handle("Is Snoopy an animal?").trace["status"] == "yes"


def test_rule_conflicts_with_direct_fact(convo):
    say(convo, "If someone is a vegetarian, they don't eat meat.", "Howard is a vegetarian.", "Howard eats meat.")
    assert convo.handle("Does Howard eat meat?").trace["status"] == "conflicting"


def test_anyone_questions(convo):
    say(convo, "Howard owns a truck.")
    assert convo.handle("Does anyone own a truck?").trace["status"] == "yes"
    assert convo.handle("Does anyone own a boat?").trace["status"] == "unknown"


def test_rules_persist(tmp_path):
    path = str(tmp_path / "kb.db")
    repo = Repository(path)
    Conversation(repo).handle("If someone owns a dog, they are a pet owner.")
    repo.close()
    repo = Repository(path)
    convo = Conversation(repo)
    convo.handle("Howard owns a dog.")
    assert convo.handle("Is Howard a pet owner?").trace["status"] == "yes"
    repo.close()


# --- corrections ----------------------------------------------------------------

def test_correction_replaces_relation_value(convo):
    say(convo, "Howard lives in Columbus.")
    reply = convo.handle("No, he lives in Dayton.")
    assert reply.text == "Corrected. I no longer believe that Howard lives in Columbus. Now: Howard lives in Dayton."
    assert convo.handle("Where does Howard live?").text == "Howard lives in Dayton."
    assert convo.handle("Does Howard live in Columbus?").trace["status"] == "unknown"


def test_fragment_correction(convo):
    say(convo, "Howard lives in Columbus.")
    reply = convo.handle("No, Dayton.")
    assert "no longer believe that Howard lives in Columbus" in reply.text
    assert convo.handle("Where does Howard live?").text == "Howard lives in Dayton."
    say(convo, "Bob drives a truck.")
    convo.handle("Sorry, I meant a bus.")
    assert convo.handle("What does Bob drive?").text == "Bob drives a bus."


def test_contrast_correction(convo):
    say(convo, "Howard drives a truck.")
    reply = convo.handle("Howard drives a bus, not a truck.")
    assert "no longer believe that Howard drives a truck" in reply.text
    assert convo.handle("Does Howard drive a truck?").trace["status"] == "no"
    assert convo.handle("Does Howard drive a bus?").trace["status"] == "yes"


def test_correction_to_negative_resolves_conflict(convo):
    say(convo, "Howard owns a boat.")
    reply = convo.handle("Actually, Howard doesn't own a boat.")
    assert not reply.trace["conflict"]
    assert convo.handle("Does Howard own a boat?").trace["status"] == "no"


def test_no_longer_and_anymore(convo):
    say(convo, "Howard drives a truck.", "Bob owns a cat.")
    say(convo, "Howard no longer drives a truck.")
    assert convo.handle("Does Howard drive a truck?").trace["status"] == "no"
    say(convo, "Bob doesn't own a cat anymore.")
    assert convo.handle("Does Bob own a cat?").trace["status"] == "no"


def test_category_correction_keeps_compatible_categories(convo):
    say(convo, "A dog is an animal.", "A cat is an animal.", "Rex is a dog.", "Rex is an animal.")
    convo.handle("No, Rex is a cat.")
    assert convo.handle("Is Rex a cat?").trace["status"] == "yes"
    assert convo.handle("Is Rex a dog?").trace["status"] == "unknown"
    assert convo.handle("Is Rex an animal?").trace["status"] == "yes"


def test_thats_wrong_retracts_last_turn(convo):
    say(convo, "Howard owns a red truck.", "Howard owns a boat and lives in Ohio.")
    reply = convo.handle("That's wrong.")
    assert reply.text.startswith("Okay, I've taken that back")
    assert convo.handle("Does Howard own a boat?").trace["status"] == "unknown"
    assert convo.handle("Does Howard own a truck?").trace["status"] == "yes"
    assert convo.handle("Scratch that.").text == "Sorry, I got something wrong. There's nothing recent I learned to take back, so tell me what was wrong and I'll fix it."


def test_thats_wrong_retracts_a_rule(convo):
    say(convo, "If someone owns a dog, they are a pet owner.")
    assert "if someone owns a dog" in convo.handle("That's not true.").text
    say(convo, "Howard owns a dog.")
    assert convo.handle("Is Howard a pet owner?").trace["status"] == "unknown"


def test_forget_specific_fact(convo):
    say(convo, "Howard owns a boat.", "Howard lives in Ohio.")
    reply = convo.handle("Forget that Howard owns a boat.")
    assert reply.text == "Okay, I've forgotten that Howard owns a boat."
    assert convo.handle("Does Howard own a boat?").trace["status"] == "unknown"
    assert convo.handle("Does Howard live in Ohio?").trace["status"] == "yes"


def test_retractions_are_audited_not_deleted(convo):
    say(convo, "Howard lives in Columbus.")
    convo.handle("No, he lives in Dayton.")
    rows = convo.repo.conn.execute(
        "SELECT a.status, c.note FROM corrections c JOIN assertions a ON a.id = c.assertion_id").fetchall()
    assert [(r["status"], r["note"].split(" #")[0]) for r in rows] == [("retracted", "replaced by correction")]


def test_non_correction_keeps_both_values(convo):
    say(convo, "Howard drives a truck.", "Howard drives a bus.")
    assert convo.handle("Does Howard drive a truck?").trace["status"] == "yes"
    assert convo.handle("Does Howard drive a bus?").trace["status"] == "yes"
