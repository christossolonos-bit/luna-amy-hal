"""The clause splitter: long sentences into simple ones the parser can read."""

import pytest

from nlp.clauses import split, split_text
from nlp.lexicon import Lexicon
from nlp.parser import parse_sentence

CASES = [
    ("Burbank is a city in Los Angeles County, which is 7 miles northwest of downtown.",
     ["Burbank is a city in Los Angeles County.", "Los Angeles County is 7 miles northwest of downtown."], [None, None]),
    ("Because it rained, the game was cancelled.", ["It rained.", "The game was cancelled."], [None, "cause"]),
    ("The game was cancelled because it rained.", ["The game was cancelled.", "It rained."], [None, "cause"]),
    ("Jen lives in Denver, and she is a nurse.", ["Jen lives in Denver.", "She is a nurse."], [None, None]),
    ("He moved to Dayton, where he met Jen.", ["He moved to Dayton.", "He met Jen in Dayton."], [None, None]),
    ("Burbank, a city in California, has an airport.",
     ["Burbank is a city in California.", "Burbank has an airport."], [None, None]),
    ("Burbank is a city, located 7 miles northwest of Los Angeles.",
     ["Burbank is a city.", "Burbank is located 7 miles northwest of Los Angeles."], [None, None]),
    ("It rained; the game was cancelled.", ["It rained.", "The game was cancelled."], [None, None]),
    ("Burbank (population 107,000) is a city.", ["Burbank is a city."], [None]),
    ("Although she was tired, Jen went to work.", ["She was tired.", "Jen went to work."], [None, "contrast"]),
    ("When the war ended, the soldiers went home.", ["The war ended.", "The soldiers went home."], [None, "time"]),
    ("Maria, who lives in Boston, works at a hospital.", ["Maria works at a hospital.", "Maria lives in Boston."],
     [None, None]),
    ("Paris is the capital of France, and it is known for the Eiffel Tower.",
     ["Paris is the capital of France.", "It is known for the Eiffel Tower."], [None, None]),
]
WHOLE = ["Bob owns a truck and a boat.", "Jen is a nurse.", "Chance loves playing outside."]


@pytest.mark.parametrize("sentence,texts,links", CASES)
def test_splits(sentence, texts, links):
    pieces = split(sentence)
    assert [p.text for p in pieces] == texts
    assert [p.link for p in pieces] == links
    assert all(p.source == sentence and 0 < p.confidence <= 1 for p in pieces)


@pytest.mark.parametrize("sentence", WHOLE)
def test_simple_sentences_pass_through_whole(sentence):
    pieces = split(sentence)
    assert [p.text for p in pieces] == [sentence] and pieces[0].confidence == 1.0


@pytest.mark.parametrize("sentence", ["Jen lives in Denver, and she is a nurse.",
                                      "Maria, who lives in Boston, works at a hospital.",
                                      "Burbank, a city in California, has an airport.",
                                      "Because it rained, the game was cancelled."])
def test_pieces_parse(sentence):
    lexicon = Lexicon()
    for piece in split(sentence):
        parse_sentence(piece.text, lexicon)  # raises ParseError if the parser can't read it


def test_paragraph():
    text = ("Burbank is a city in Los Angeles County, California. Located 12 miles north of downtown, it is home "
            "to many media companies. The city, which was founded in 1887, has an airport.")
    pieces = split_text(text)
    assert len(pieces) >= 4
    assert any("founded in 1887" in p.text for p in pieces)
