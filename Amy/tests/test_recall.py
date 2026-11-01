"""Teach him a simple fact, then ask for it back the ordinary ways people ask. Nothing ships if this fails."""

import os
import tempfile

import pytest

from app.conversation import Conversation
from db.repository import Repository

# (what he's told, the question, a word the answer must contain)
CASES = [
    ("You are from Alabama.", "Where are you from?", "Alabama"),
    ("You are from Alabama.", "Where do you come from?", "Alabama"),
    ("You are from Alabama.", "Are you from Alabama?", "yes"),
    ("I am from Ohio.", "Where am I from?", "Ohio"),
    ("I live in Dayton.", "Where do I live?", "Dayton"),
    ("I live in Dayton.", "Do you know where I live?", "Dayton"),
    ("I live in Dayton.", "Where's my home?", "Dayton"),
    ("My dog is named Chance.", "What's my dog's name?", "Chance"),
    ("My dog is named Chance.", "What is my dog called?", "Chance"),
    ("I have a dog named Chance.", "Do I have a dog?", "yes"),
    ("Jen lives in Denver.", "Where does Jen live?", "Denver"),
    ("Jen lives in Denver.", "Where is Jen?", "Denver"),
    ("Jen lives in Denver.", "What do you know about Jen?", "Denver"),
    ("Jen lives in Denver.", "Tell me about Jen.", "Denver"),
    ("Jen works at a hospital.", "Where does Jen work?", "hospital"),
    ("Jen is a nurse.", "What is Jen?", "nurse"),
    ("Jen is a nurse.", "What does Jen do?", "nurse"),
    ("Jen is a nurse.", "Is Jen a nurse?", "yes"),
    ("My favorite color is blue.", "What's my favorite color?", "blue"),
    ("Your favorite color is green.", "What's your favorite color?", "green"),
    ("My name is Travis.", "What's my name?", "Travis"),
    ("Bob owns a red truck.", "What does Bob own?", "truck"),
    ("Bob owns a red truck.", "What color is Bob's truck?", "red"),
    ("Bob owns a red truck.", "Does Bob have a truck?", "yes"),
    ("I like pizza.", "What do I like?", "pizza"),
    ("I like pizza.", "Do I like pizza?", "yes"),
    ("You like music.", "What do you like?", "music"),
    ("Chance is two years old.", "How old is Chance?", "two"),
    ("I was born in Texas.", "Where was I born?", "Texas"),
    ("You were created today.", "When were you created?", "created"),
    ("Sam is my brother.", "Who is Sam?", "brother"),
    ("Sam is my brother.", "Who's my brother?", "Sam"),
]


@pytest.mark.parametrize("fact,question,word", CASES)
def test_teach_then_ask(fact, question, word):
    repo = Repository(os.path.join(tempfile.mkdtemp(), "t.db"))
    convo = Conversation(repo, speaker="Travis", self_name="Adam")
    convo.curiosity.rng.random = lambda: 1.0
    convo.handle(fact, speaker="Travis")
    convo.handle("Okay.", speaker="Travis")  # something in between, like real talk
    reply = convo.handle(question, speaker="Travis").text
    repo.close()
    assert word.lower() in reply.lower(), f"{fact!r} then {question!r} -> {reply!r}"
