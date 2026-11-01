import pytest

from app.conversation import Conversation
from db.repository import Repository
from nlp.parser import ParseError, parse


@pytest.fixture
def convo():
    repo = Repository(":memory:")
    yield Conversation(repo)
    repo.close()


def teach(convo, *lines):
    for line in lines:
        reply = convo.handle(line)
        assert any(s.get("stored") for s in reply.trace["sentences"]), line


# --- spec acceptance cases ------------------------------------------------------

def test_owns_vehicle_is_yes(convo):
    teach(convo, "Howard owns a red truck.", "A truck is a vehicle.")
    reply = convo.handle("Does Howard own a vehicle?")
    assert reply.trace["status"] == "yes"
    assert reply.text == ("Yes. Howard owns a vehicle. "
                          "I know this because Howard owns a red truck and a truck is a vehicle.")
    assert reply.trace["validator"]["ok"]


def test_owns_motorcycle_is_unknown(convo):
    teach(convo, "Howard owns a red truck.", "A truck is a vehicle.", "A motorcycle is a vehicle.")
    reply = convo.handle("Does Howard own a motorcycle?")
    assert reply.trace["status"] == "unknown"
    assert reply.text.startswith("I don't know whether Howard owns a motorcycle.")


def test_explicit_negative_is_no(convo):
    teach(convo, "Howard owns a red truck.", "Howard does not own a motorcycle.")
    reply = convo.handle("Does Howard own a motorcycle?")
    assert reply.trace["status"] == "no"
    assert reply.text.startswith("No. Howard does not own a motorcycle.")


def test_contradictory_claims_are_conflicting(convo):
    teach(convo, "Howard owns a boat.")
    ack = convo.handle("Howard doesn't own a boat.")
    assert ack.trace["conflict"]
    reply = convo.handle("Does Howard own a boat?")
    assert reply.trace["status"] == "conflicting"
    assert "On one hand, Howard owns a boat" in reply.text
    assert "On the other hand, Howard does not own a boat" in reply.text


# --- taxonomy reasoning ---------------------------------------------------------

def test_negative_inherits_down_to_subkinds(convo):
    teach(convo, "Howard does not own a boat.", "A canoe is a boat.")
    reply = convo.handle("Does Howard own a canoe?")
    assert reply.trace["status"] == "no"
    assert "a canoe is a boat" in reply.text


def test_multi_hop_subclass(convo):
    teach(convo, "A pickup is a truck.", "Trucks are vehicles.", "A vehicle is a machine.")
    assert convo.handle("Is a pickup a machine?").trace["status"] == "yes"
    assert convo.handle("Are machines pickups?").trace["status"] == "unknown"


def test_instance_inheritance_and_disjointness(convo):
    teach(convo, "Rex is a dog.", "A dog is an animal.", "A dog is not a cat.")
    assert convo.handle("Is Rex an animal?").trace["status"] == "yes"
    assert convo.handle("Is Rex a cat?").trace["status"] == "no"


def test_modifiers_are_required_but_absence_is_not_negative(convo):
    teach(convo, "Howard owns a red truck.")
    assert convo.handle("Does Howard own a red truck?").trace["status"] == "yes"
    assert convo.handle("Does Howard own a blue truck?").trace["status"] == "unknown"


def test_cycles_terminate(convo):
    teach(convo, "A foo is a bar.", "A bar is a foo.", "A baz is a thing.")
    assert convo.handle("Is a foo a baz?").trace["status"] == "unknown"


def test_unknown_entity_reports_gap(convo):
    reply = convo.handle("Does Bob own a truck?")
    assert reply.trace["status"] == "unknown"
    assert "anyone or anything called Bob" in reply.text


def test_unknown_questions_persist(convo):
    convo.handle("Does Howard own a truck?")
    assert len(convo.repo.open_questions()) == 1


def test_knowledge_persists_across_sessions(tmp_path):
    path = str(tmp_path / "kb.db")
    repo = Repository(path)
    teach(Conversation(repo), "Howard owns a red truck.", "A truck is a vehicle.")
    repo.close()
    repo = Repository(path)
    assert Conversation(repo).handle("Does Howard own a vehicle?").trace["status"] == "yes"
    repo.close()


# --- extraction from free-form teaching -------------------------------------------

def test_paragraph_from_another_bot_is_extracted(convo):
    reply = convo.handle(
        "So basically, Howard drove his red truck to Ohio yesterday. He lives in Columbus "
        "and works at a bakery. Columbus is a city. The bakery is famous.",
        speaker="TutorBot",
    )
    assert "I learned" in reply.text
    assert convo.handle("Where does Howard live?").text == "Howard lives in Columbus."
    assert convo.handle("Does Howard work at a bakery?").trace["status"] == "yes"
    assert convo.handle("Did Howard drive a truck?").trace["status"] == "yes"
    assert convo.handle("Is the bakery famous?").trace["status"] == "yes"
    assert "Ohio" in convo.handle("Where did Howard drive his truck?").text
    assert convo.handle("When did Howard drive to Ohio?").text == \
        "Howard drove a red truck to Ohio yesterday."


def test_unparsed_sentences_are_reported_not_dropped(convo):
    reply = convo.handle("Howard owns a truck. Whereupon the quixotic notwithstanding. Bob owns a boat.")
    assert "I learned 2 things" in reply.text
    assert "Howard owns a truck" in reply.text and "Bob owns a boat" in reply.text
    assert "notwithstanding" in reply.text or "didn't" in reply.text  # says part didn't land, briefly
    assert "(I " not in reply.text  # parser diagnostics stay in the trace
    assert any("quixotic" in row["frame_json"] for row in convo.repo.open_questions())


def test_single_unparseable_sentence_asks_for_clarification(convo):
    reply = convo.handle("Whereupon the zorbly notwithstanding.")
    assert "What does 'zorbly' mean?" in reply.text
    assert reply.trace["intent"] == "clarify"


def test_new_verbs_are_learned_and_reused(convo):
    teach(convo, "Howard yeets rocks.")
    assert convo.handle("Does Howard yeet rocks?").trace["status"] == "yes"
    assert "verb" in {r["pos"] for r in convo.repo.conn.execute("SELECT pos FROM lexemes WHERE lemma='yeet'")}


def test_pronouns_and_possessives(convo):
    teach(convo, "Rex is a dog.", "He is very loyal.", "Howard's cat is grumpy.")
    assert convo.handle("Is Rex loyal?").trace["status"] == "yes"
    assert convo.handle("Does Howard own a cat?").trace["status"] == "yes"
    assert convo.handle("Is Howard's cat grumpy?").trace["status"] == "yes"


def test_relative_and_appositive_clauses(convo):
    teach(convo, "Howard owns a truck that is red.", "Bob, who is a doctor, drives a bus.")
    assert convo.handle("Does Howard own a red truck?").trace["status"] == "yes"
    assert convo.handle("Is Bob a doctor?").trace["status"] == "yes"
    assert convo.handle("Does Bob drive a bus?").trace["status"] == "yes"


def test_coordination(convo):
    reply = convo.handle("Howard and Bob own trucks, and Howard owns a truck and a boat.")
    assert "I learned" in reply.text
    assert convo.handle("Does Bob own trucks?").trace["status"] == "yes"
    assert convo.handle("Does Howard own a boat?").trace["status"] == "yes"


def test_generic_facts_are_inherited_as_probable(convo):
    teach(convo, "Dogs chase cats.", "Rex is a dog.")
    reply = convo.handle("Does Rex chase cats?")
    assert reply.trace["status"] == "yes"
    assert reply.text.startswith("Probably. Rex chases cats.")
    assert "Rex is a dog and dogs chase cats" in reply.text


def test_hedged_teaching_lowers_confidence(convo):
    convo.handle("I think Howard owns a boat.")
    reply = convo.handle("Does Howard own a boat?")
    assert reply.text.startswith("Probably.")
    convo.handle("Howard owns a boat.")
    assert convo.handle("Does Howard own a boat?").text.startswith("Yes.")


def test_wh_questions(convo):
    teach(convo, "Howard drives a red truck.", "Bob drives a bus.", "Howard is tall.", "Rex is a dog.")
    assert convo.handle("What does Howard drive?").text == "Howard drives a red truck."
    who = convo.handle("Who drives a bus?").text
    assert who == "Bob drives a bus."
    assert convo.handle("Who is tall?").text == "Howard is tall."
    assert convo.handle("Who is a dog?").text == "Rex is a dog."
    assert convo.handle("What does Bob own?").text == "I don't know what Bob owns."


def test_describe(convo):
    teach(convo, "Howard is a baker.", "Howard is tall.", "Howard drives a red truck.")
    text = convo.handle("Tell me about Howard.").text
    assert "Howard is a baker" in text and "Howard is tall" in text
    assert "Howard drives a red truck" in text
    assert "\n\n" in text


def test_provenance_is_reported_when_sources_disagree(convo):
    convo.handle("Howard owns a boat.", speaker="alice")
    convo.handle("Howard doesn't own a boat.", speaker="bob")
    text = convo.handle("Does Howard own a boat?", speaker="user").text
    assert "(according to alice)" in text and "(according to bob)" in text


def test_negation_variants(convo):
    teach(convo, "Howard never eats meat.", "Howard can't swim.")
    assert convo.handle("Does Howard eat meat?").trace["status"] == "no"
    assert convo.handle("Can Howard swim?").trace["status"] == "no"


def test_first_person_refers_to_speaker(convo):
    convo.handle("I own a guitar.", speaker="Travis")
    assert convo.handle("Does Travis own a guitar?").trace["status"] == "yes"


# --- parser ---------------------------------------------------------------------

@pytest.mark.parametrize("text, relation, verb, subj, obj, pol, act", [
    ("Howard owns a red truck.", "rel", "own", "howard", "truck", True, "assert"),
    ("Howard does not own a boat", "rel", "own", "howard", "boat", False, "assert"),
    ("Howard has a dog", "rel", "own", "howard", "dog", True, "assert"),
    ("Trucks are vehicles.", "is_a", None, "truck", "vehicle", True, "assert"),
    ("A dog isn't a cat", "is_a", None, "dog", "cat", False, "assert"),
    ("Does Howard own a vehicle?", "rel", "own", "howard", "vehicle", True, "ask"),
    ("Is Howard Smith a person?", "is_a", None, "howard smith", "person", True, "ask"),
    ("She gave him a book", "rel", "give", "she", "book", True, "assert"),
    ("The dog chases cats", "rel", "chase", "dog", "cat", True, "assert"),
])
def test_parser_frames(text, relation, verb, subj, obj, pol, act):
    frame = parse(text)
    assert (frame.relation, frame.verb, frame.subject.head, frame.obj.head, frame.polarity, frame.act) == \
        (relation, verb, subj, obj, pol, act)


def test_parser_strips_filler_and_hedges():
    frame = parse("Well, you know, I think Howard probably drives a truck, right?")
    assert (frame.subject.head, frame.verb, frame.obj.head, frame.act) == ("howard", "drive", "truck", "ask")
    frame = parse("Hey Hal, I believe that Howard drives a truck.")
    assert frame.confidence == 0.7 and frame.verb == "drive"


def test_parser_rejects_unknown_structure():
    with pytest.raises(ParseError):
        parse("Whereupon the quixotic notwithstanding.")
