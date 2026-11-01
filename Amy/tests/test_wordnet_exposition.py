import json
import random

import pytest

from app.conversation import Conversation
from db.repository import Repository
from dialogue.self_model import SelfModel
from nlp.wordnet import WordNet, import_xml


@pytest.fixture
def words(tmp_path):
    source = tmp_path / "fixture.xml"
    source.write_text('''<LexicalResource><Lexicon id="fixture">
      <LexicalEntry><Lemma writtenForm="terrier" partOfSpeech="n"/><Sense synset="t"/></LexicalEntry>
      <LexicalEntry><Lemma writtenForm="dog" partOfSpeech="n"/><Sense synset="d"/></LexicalEntry>
      <LexicalEntry><Lemma writtenForm="animal" partOfSpeech="n"/><Sense synset="a"/></LexicalEntry>
      <LexicalEntry><Lemma writtenForm="bank" partOfSpeech="n"/><Sense synset="b1"/><Sense synset="b2"/></LexicalEntry>
      <LexicalEntry><Lemma writtenForm="place" partOfSpeech="n"/><Sense synset="p"/></LexicalEntry>
      <Synset id="t" partOfSpeech="n"><Definition>a small hunting dog</Definition><SynsetRelation relType="hypernym" target="d"/><Example>A terrier flew to Mars.</Example></Synset>
      <Synset id="d" partOfSpeech="n"><Definition>a domesticated animal</Definition><SynsetRelation relType="hypernym" target="a"/></Synset>
      <Synset id="a" partOfSpeech="n"><Definition>a living creature</Definition></Synset>
      <Synset id="b1" partOfSpeech="n"><Definition>land beside a river</Definition><SynsetRelation relType="hypernym" target="p"/></Synset>
      <Synset id="b2" partOfSpeech="n"><Definition>a financial institution for deposits and money</Definition><SynsetRelation relType="hypernym" target="p"/></Synset>
      <Synset id="p" partOfSpeech="n"><Definition>a location</Definition></Synset>
    </Lexicon></LexicalResource>''', encoding="utf-8")
    target = tmp_path / "wordnet.db"
    import_xml(source, target)
    return WordNet(target)


@pytest.fixture
def c(words):
    repo = Repository(":memory:")
    convo = Conversation(repo, wordnet=words)
    yield convo
    repo.close()


def test_senses_stay_separate_and_use_context(words):
    assert words.select("bank").sense is None
    assert words.select("bank", "river water").sense.id == "b1"
    assert words.select("bank", "money deposits").sense.id == "b2"
    assert words.path_to("t", "animal") == ["t", "d", "a"]
    assert words.verify_path(["t", "a"]) is False


def test_paragraphs_combine_memory_definitions_and_supported_deductions(c):
    c.handle("Howard owns a terrier.")
    c.handle("He lives in Columbus.")
    c.handle("He likes pizza.")
    reply = c.handle("Tell me about Howard.")
    assert "\n\n" in reply.text
    assert "Howard lives in Columbus" in reply.text
    assert "Howard owns an animal" in reply.text
    assert "Mars" not in reply.text
    claims = reply.trace["paragraph_plan"]["claims"]
    assert {"memory", "definition", "taxonomy", "deduction"} <= {claim["kind"] for claim in claims}
    assert not reply.trace["paragraph_plan"]["rejected"]
    assert c.handle("Is a terrier an animal?").trace["status"] == "yes"


def test_definition_choices_and_follow_up(c):
    reply = c.handle("Explain bank.")
    assert "land beside a river" in reply.text and "usual meaning" in reply.text  # answers, doesn't quiz
    reply = c.handle("money and deposits")
    assert "financial institution" in reply.text and "land beside" not in reply.text
    again = c.handle("Tell me more.")
    assert again.trace["paragraph_plan"]["exhausted"]


def test_retractions_and_conflicts_are_not_promoted_to_deductions(c):
    c.handle("Howard owns a terrier.", speaker="Alice")
    c.handle("Howard doesn't own a terrier.", speaker="Bob")
    reply = c.handle("Tell me about Howard.")
    assert "conflicting" in reply.text
    assert not any(x["kind"] == "deduction" for x in reply.trace["paragraph_plan"]["claims"])
    c.handle("Forget that Howard owns a terrier.")
    reply = c.handle("Tell me about Howard.")
    assert "That means Howard owns" not in reply.text


def test_learned_rules_join_paragraph_reasoning(c):
    c.handle("If someone owns a terrier, they are happy.")
    c.handle("Howard owns a terrier.")
    reply = c.handle("Explain Howard.")
    assert "Howard is happy" in reply.text
    assert any(x["kind"] == "inference" for x in reply.trace["paragraph_plan"]["claims"])


def test_emotion_changes_expression_not_evidence(c):
    c.handle("Howard owns a terrier.")
    for _ in range(6):
        c.self_model.event("complimented")
    c.social.phrases.remaining.clear()
    happy = c.handle("Explain Howard.")
    assert happy.trace["paragraph_plan"]["expression"]["tone"] == "excited"
    for _ in range(15):
        c.self_model.event("insulted")
    sad = c.handle("Explain Howard.")
    assert sad.trace["paragraph_plan"]["expression"]["tone"] in ("subdued", "measured")
    assert happy.text != sad.text
    assert happy.trace["paragraph_plan"]["claims"] == sad.trace["paragraph_plan"]["claims"]
    assert "Howard owns an animal" in happy.text and "Howard owns an animal" in sad.text
    assert c.repo.conn.execute("SELECT COUNT(*) FROM emotion_events").fetchone()[0] > 0
    persisted = SelfModel(c.repo)
    assert persisted.emotions()["sadness"] > 0.35
    assert all(0 <= value <= 1 for value in persisted.emotions().values())
    assert persisted.expression(sensitive=True)["tone"] == "gentle"


def test_missing_wordnet_still_works(tmp_path):
    repo = Repository(":memory:")
    try:
        convo = Conversation(repo, wordnet=WordNet(tmp_path / "absent.db"))
        convo.handle("Howard owns a terrier.")
        reply = convo.handle("Explain Howard.")
        assert "Howard owns a terrier" in reply.text
        assert not reply.trace["paragraph_plan"]["rejected"]
        assert convo.handle("Is a terrier an animal?").trace["status"] == "unknown"
    finally:
        repo.close()


def test_continuation_is_per_speaker_and_teaching_is_not_swallowed(c):
    c.handle("Explain bank.", speaker="Alice")
    c.handle("Howard owns a terrier.", speaker="Bob")
    assert c.handle("Does Howard own a terrier?").trace["status"] == "yes"
    assert "What would you like" in c.handle("Tell me more.", speaker="Bob").text
    assert "financial institution" in c.handle("2", speaker="Alice").text
