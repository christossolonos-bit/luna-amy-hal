"""Every ability he has, used end to end the way a person would use it. One conversation per ability:
what's said, and what the reply has to contain. If an ability is wired in, it's tested here."""

import os
import tempfile

import pytest

from app.conversation import Conversation
from db.repository import Repository


def fresh(path=None):
    repo = Repository(path or os.path.join(tempfile.mkdtemp(), "t.db"))
    convo = Conversation(repo, speaker="Travis", self_name="Adam")
    convo.curiosity.rng.random = lambda: 1.0
    return convo


def run(convo, turns):
    for said, want in turns:
        reply = convo.handle(said, speaker="Travis").text
        for w in want or []:
            if w.startswith("!"):
                assert w[1:].lower() not in reply.lower(), f"{said!r} -> {reply!r} (should not say {w[1:]!r})"
            else:
                assert w.lower() in reply.lower(), f"{said!r} -> {reply!r} (missing {w!r})"


ABILITIES = {
    "remembers facts about the speaker": [
        ("I live in Dayton.", None), ("I work at a bakery.", None), ("Okay.", None),
        ("Where do I work?", ["bakery"]), ("Where do I live?", ["Dayton"])],
    "remembers facts about himself": [
        ("You are from Alabama.", None), ("Your favorite color is green.", None),
        ("Where are you from?", ["Alabama"]), ("What's your favorite color?", ["green"])],
    "remembers other people": [
        ("My sister is named Jen.", None), ("She lives in Denver.", None), ("She is a nurse.", None),
        ("Where does Jen live?", ["Denver"]), ("What does Jen do?", ["nurse"])],
    "reasons with rules": [
        ("If something is a dog, it is an animal.", None), ("Chance is a dog.", None),
        ("Is Chance an animal?", ["yes"]), ("How do you know?", ["dog"])],
    "reasons with the dictionary": [
        ("Chance is a dog.", None), ("Is Chance an animal?", ["yes"])],
    "takes corrections": [
        ("Jen lives in Denver.", None), ("No, Jen lives in Boston.", None),
        ("Where does Jen live?", ["Boston", "!Denver"])],
    "takes 'that's wrong'": [
        ("Bob owns a boat.", None), ("That's wrong.", None), ("Does Bob own a boat?", ["!Yes. Bob owns a boat"])],
    "forgets on request": [
        ("Bob owns a boat.", None), ("Forget that Bob owns a boat.", None),
        ("Does Bob own a boat?", ["!Yes. Bob owns a boat"])],
    "learns names": [
        ("I have a dog. His name is Chance.", None), ("What's my dog's name?", ["Chance"])],
    "knows who you are": [("My name is Travis.", ["Travis"]), ("Who am I?", ["Travis"])],
    "recalls the conversation": [
        ("Chance is my dog.", None), ("He likes swimming.", None),
        ("What did I say about Chance?", ["swim"]), ("What have we talked about?", ["Chance"])],
    "keeps the topic across turns": [
        ("My brother is named Sam.", None), ("Sam lives in Ohio.", None), ("I like pizza.", None),
        ("He works at a bank.", None), ("Where does Sam work?", ["bank"])],
    "short follow-up questions": [
        ("Jen lives in Texas.", None), ("Sam lives in Ohio.", None),
        ("Where does Jen live?", ["Texas"]), ("And Sam?", ["Ohio"])],
    "definitions become reasoning": [
        ("Bob was born yesterday.", None), ("A birthday is the day someone is born.", None),
        ("When is Bob's birthday?", ["birthday is"])],
    "knows why he was made": [
        ("I created you to keep me company.", None), ("Why did I create you?", ["company"])],
    "answers about himself by reasoning": [("Are you conscious?", ["Checking myself"])],
    "remembers what you tell him about himself, when asked": [
        ("You are conscious.", None), ("Are you conscious?", ["told me"])],
    "the user guide": [("How do I teach you?", ["fact"]), ("Explain it like I'm five.", ["like"])],
    "explains how he works": [("How do you work?", ["grammar"])],
    "his questions": [
        ("Binance got hacked by North Korea.", None), ("What questions do you have?", ["?"]),
        ("Why do you ask?", ["because"])],
    "dictionary words": [("What is a truck?", ["vehicle"]), ("What is amazing?", ["surprising"])],
    "where is a place": [("Where is Alabama?", ["United States"])],
    "pleasantries": [("It's nice to see you again.", ["nice to see you"]), ("Thank you.", None)],
    "feelings and frustration": [
        ("I can't stand you", ["!I've stored", "frustrat"]), ("That's not correct", ["wrong"])],
    "offers and requests": [
        ("Can I help you?", ["!I don't know whether"]), ("Could you take a picture of us?", ["can't"])],
    "his name alone": [("Adam.", ["here"])],
    "what he looked up is remembered": [],   # needs the network: checked separately below
    "what you told him about himself is in memory-first": [
        ("I created you to learn about people.", None), ("What do you know about why you exist?", ["learn"])],
}


@pytest.mark.parametrize("name", [n for n, t in ABILITIES.items() if t])
def test_ability(name):
    convo = fresh()
    try:
        run(convo, ABILITIES[name])
    finally:
        convo.repo.close()


def test_survives_a_restart():
    """Everything he's told is still there after he's shut down and started again."""
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    convo = fresh(path)
    run(convo, [("I live in Dayton.", None), ("You are from Alabama.", None), ("My dog is named Chance.", None)])
    convo.repo.close()
    convo = fresh(path)
    try:
        run(convo, [("Where do I live?", ["Dayton"]), ("Where are you from?", ["Alabama"]),
                    ("What's my dog's name?", ["Chance"])])
    finally:
        convo.repo.close()


def test_looked_up_notes_are_remembered():
    """What he read before is used again, without searching again (no network: a note is planted)."""
    convo = fresh()
    try:
        with convo.repo.conn:
            convo.repo.conn.execute("INSERT INTO web_notes(term, title, summary, url) VALUES "
                                    "('binance', 'Binance', 'Binance is a cryptocurrency exchange.', 'x')")
        run(convo, [("What is Binance?", ["cryptocurrency"]), ("Tell me about Binance.", ["cryptocurrency"])])
    finally:
        convo.repo.close()
