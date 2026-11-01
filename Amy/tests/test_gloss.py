from types import SimpleNamespace

import pytest

from dialogue.gloss import GlossDigester, gloss_sentences
from nlp.parser import parse_sentence
from nlp.lexicon import Lexicon
from nlp.wordnet import Sense


class Words:
    def related(self, ident, relations, limit=4):
        assert relations == ("hypernym", "instance_hypernym")
        return [Sense("animal", "animal", "n", "a living thing")]
    def lookup(self, *args, **kwargs):
        raise AssertionError("compiler must not choose another meaning")


def test_dog_definition_keeps_agent_aspect_and_source_meaning():
    sense = Sense("dog.n.01", "dog", "n", "a member of the genus Canis that has been domesticated by man")
    sentences = gloss_sentences("dog", sense, Words())
    assert "A dog is an animal." in sentences
    assert "A dog is a member of the genus Canis." in sentences
    assert "A dog has been domesticated by man." in sentences
    assert not any("A dog domesticates" in text for text in sentences)


def test_blue_definition_does_not_turn_similarity_into_identity():
    sense = Sense("blue", "blue", "a", "of the color intermediate between green and violet; having a color similar to that of a clear unclouded sky")
    sentences = gloss_sentences("blue", sense, None)
    assert "Blue is a color." in sentences
    assert "Blue is between green and violet." in sentences
    assert "A clear sky is blue." not in sentences


def test_simple_compiled_sentences_reach_existing_parser():
    sense = Sense("truck", "truck", "n", "a vehicle that has wheels")
    sentences = gloss_sentences("truck", sense, None)
    assert sentences == ["A truck is a vehicle.", "A truck has wheels."]
    for text in sentences:
        assert parse_sentence(text, Lexicon())


def test_examples_never_become_events():
    sense = Sense("dog", "dog", "n", "an animal", ("Howard owns a dog.",))
    sentences = gloss_sentences("dog", sense, None)
    assert sentences == ["A dog is an animal."]
    assert "Howard" not in " ".join(sentences)


def test_relative_after_another_subject_is_not_lifted():
    sense = Sense("keeper", "keeper", "n", "a person with a dog that is hungry")
    sentences = gloss_sentences("keeper", sense, None)
    assert "A keeper is hungry." not in sentences


@pytest.mark.parametrize("definition,expected", [
    ("a bird that can swim", "A penguin can swim."),
    ("a bird that may migrate", "A penguin may migrate."),
    ("a bird that is not red", "A penguin is not red."),
    ("a bird; occurs in cold regions", "A penguin occurs in cold regions."),
    ("a bird usually having black feathers", "A penguin usually has black feathers."),
])
def test_qualifiers_and_negation_are_preserved(definition, expected):
    result = gloss_sentences("penguin", Sense("penguin", "penguin", "n", definition), None)
    assert expected in result
    assert "A penguin migrates." not in result


@pytest.mark.parametrize("definition", ["a plant or an animal", "a creature only in stories", "", "a"])
def test_unsupported_or_incomplete_gloss_is_not_fabricated(definition):
    assert gloss_sentences("thing", Sense("thing", "thing", "n", definition), None) == []


def test_verb_definition_is_a_rule_not_an_observed_event():
    sense = Sense("run", "run", "v", "move fast")
    assert gloss_sentences("run", sense, None) == ["If something runs, it moves fast."]
    assert gloss_sentences("run", Sense("r", "run", "v", "move or operate a machine"), None) == []


def test_adjective_negation_does_not_become_positive():
    result = gloss_sentences("unhappy", Sense("u", "unhappy", "a", "not happy"), None)
    assert result == ["Unhappy is not happy."]


def test_no_selected_sense_no_data_and_no_code_like_headword():
    assert gloss_sentences("dog", None, None) == []
    assert gloss_sentences("dog. Bob owns a boat", Sense("x", "dog", "n", "an animal"), None) == []


def test_on_demand_adapter_uses_digest_source_and_caches_success_only():
    calls = []
    result = SimpleNamespace(facts=[7], skipped=[])
    class Reader:
        def digest(self, text, source, confidence):
            calls.append((text, source, confidence))
            return result
    adapter = GlossDigester(Reader(), None)
    sense = Sense("dog.n.01", "dog", "n", "an animal")
    assert calls == []
    assert adapter.digest("dog", sense, 0.7) is result
    assert adapter.digest("dog", sense) is result
    assert calls == [("A dog is an animal.", "wordnet:dog.n.01", 0.7)]


def test_failed_or_fully_skipped_digestions_are_retryable():
    calls = []
    class Reader:
        def digest(self, *args, **kwargs):
            calls.append(1)
            return SimpleNamespace(facts=[], skipped=[("text", "unsupported")])
    adapter = GlossDigester(Reader(), None)
    sense = Sense("dog", "dog", "n", "an animal")
    adapter.digest("dog", sense)
    adapter.digest("dog", sense)
    assert len(calls) == 2


def test_missing_digest_is_an_explicit_integration_error():
    adapter = GlossDigester(object(), None)
    with pytest.raises(RuntimeError, match="Conversation.digest"):
        adapter.digest("dog", Sense("dog", "dog", "n", "an animal"))


@pytest.mark.parametrize("confidence", [-1, 2, float("nan"), float("inf")])
def test_invalid_confidence_rejected(confidence):
    with pytest.raises(ValueError):
        GlossDigester(object(), None).digest("dog", Sense("dog", "dog", "n", "an animal"), confidence)
