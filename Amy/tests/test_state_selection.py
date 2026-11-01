from copy import deepcopy

import pytest

from dialogue.state_selection import Fact, choose


def f(text, subject="Howard", source="said:Travis", confidence=0.9, about_self=False):
    return Fact(text, subject, source, confidence, about_self)


FACTS = [f("Howard owns a dog."), f("Howard lives in Columbus."), f("Howard likes pizza."),
         f("A dog is an animal.", "dog", "wordnet:dog"),
         f("Howard works at a bakery."), f("Howard drives a truck.")]


@pytest.mark.parametrize("state", [{"energy": 0.1, "curiosity": 1, "valence": 1},
                                  {"stress": 0.9, "curiosity": 1, "valence": 1}])
def test_low_energy_and_stress_limit_to_most_relevant_fact(state):
    result = choose(FACTS, state, "Howard")
    assert result.max_sentences == 1 and len(result.facts) == 1
    assert not result.ask_back
    assert result.facts[0].subject == "Howard"


def test_curiosity_reserves_room_for_a_question():
    result = choose(FACTS, {"curiosity": 0.9, "energy": 0.8}, "Howard")
    assert result.ask_back
    assert len(result.facts) <= result.max_sentences - 1


def test_good_mood_adds_relevant_information_not_unrelated_padding():
    neutral = choose(FACTS, {}, "Howard")
    good = choose(FACTS, {"valence": 0.8, "energy": 0.9, "curiosity": 0.2}, "Howard")
    assert len(good.facts) > len(neutral.facts)
    assert good.max_sentences > neutral.max_sentences
    assert FACTS[3] in good.facts  # the dog's category connects to Howard's ownership
    assert all(fact.subject in ("Howard", "dog") for fact in good.facts)


def test_self_relevant_reading_connects_to_open_questions_without_new_claims():
    facts = [f("Adam remembers people.", "Adam", about_self=True),
             f("Consciousness concerns awareness.", "consciousness", "wikipedia:Consciousness"),
             f("The Sun is a star.", "Sun", "wikipedia:Sun")]
    state = {"curiosity": 0.9, "open_questions": ["Am I conscious?"], "valence": 0.8}
    result = choose(facts, state, "consciousness")
    assert facts[0] in result.facts and facts[1] in result.facts
    assert facts[2] not in result.facts
    assert all(item in facts for item in result.facts)
    assert not any("Adam is conscious" in item.text for item in result.facts)


def test_direct_relevance_beats_a_high_confidence_unrelated_fact():
    exact = f("Howard owns a dog.", confidence=0.6)
    irrelevant = f("Sam owns a boat.", "Sam", confidence=1)
    assert choose([irrelevant, exact], {"energy": 0.1}, "Howard").facts == [exact]


def test_rank_is_stable_and_confidence_breaks_relevance_ties():
    low = f("Howard owns a dog.", confidence=0.6)
    high = f("Howard owns a boat.", confidence=1.0)
    assert choose([low, high], {"energy": 0.1}, "Howard").facts == [high]


def test_state_and_fact_objects_are_never_mutated_or_rewritten():
    state = {"energy": 0.8, "valence": 0.8, "open_questions": ["Am I alive?"]}
    before = deepcopy(state)
    facts = list(FACTS)
    result = choose(facts, state, "Howard")
    assert state == before and facts == FACTS
    assert all(any(item is original for original in facts) for item in result.facts)


def test_empty_and_unrelated_inputs_return_no_invented_facts():
    assert choose([], {"curiosity": 1}, "Howard").facts == []
    assert choose(FACTS, {}, "Saturn").facts == []


def test_duplicate_and_nonfinite_fact_confidence_are_handled():
    fact = f("Howard owns a dog.")
    bad = f("Howard owns a boat.", confidence=float("nan"))
    assert choose([fact, fact, bad], {"valence": 1}, "Howard").facts == [fact]


def test_malformed_state_uses_finite_defaults():
    result = choose(FACTS, {"energy": float("nan"), "stress": "broken", "valence": None,
                            "curiosity": float("inf"), "emotions": None}, "Howard")
    assert result.max_sentences == 3 and not result.ask_back


def test_sadness_changes_length_but_does_not_filter_negative_facts():
    facts = [f("Howard does not own a boat."), f("Howard lives in Dayton.")]
    result = choose(facts, {"emotions": {"sadness": 0.9}, "curiosity": 1}, "Howard")
    assert result.max_sentences == 2 and not result.ask_back
    assert result.facts == facts


def test_full_self_model_snapshot_shape_is_accepted():
    snapshot = {"energy": 0.9, "stress": 0.2, "curiosity": 0.7, "valence": 0.8,
                "emotions": {"sadness": 0.0, "excitement": 0.9}, "counts": {"learned": 42}}
    assert choose(FACTS, snapshot, "Howard").max_sentences == 10
