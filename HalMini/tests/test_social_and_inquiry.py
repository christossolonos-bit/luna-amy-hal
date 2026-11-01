import pytest

from app.conversation import Conversation
from db.repository import Repository
from dialogue.social import REPLIES


@pytest.fixture
def convo():
    repo = Repository(":memory:")
    c = Conversation(repo, speaker="Travis")
    c.social.rng.seed(0)
    yield c
    repo.close()


def say(convo, text, speaker=None):
    return convo.handle(text, speaker=speaker).text


def bank(key, **fields):
    """Every phrasing in one bank, with the speaker's name filled in."""
    values = {"name": " Travis", **fields}
    return {line.format(**values) for line in REPLIES[key]}


# --- greetings and small talk -----------------------------------------------------

def test_first_greeting_introduces_itself(convo):
    assert say(convo, "Hi Hal!") in bank("greet_first", self_name="Hal")
    assert say(convo, "Hello") in bank("greet_morning") | bank("greet_afternoon") | bank("greet_evening")
    assert say(convo, "Good morning, Hal") in bank("greet_morning")


def test_greeting_plus_content(convo):
    text = say(convo, "Hey Hal, Howard owns a red truck.")
    assert text.endswith(": Howard owns a red truck.")


def test_how_are_you_reads_state_and_asks_back(convo):
    say(convo, "Howard owns a truck.")
    text = say(convo, "How are you?")
    assert "1 new thing" in text
    assert text.endswith(tuple(REPLIES["how_are_you_tail"]))
    assert say(convo, "I'm good, thanks") in bank("wellbeing_good")
    assert "feeling it" in say(convo, "How do you feel?")


def test_state_variables_move(convo):
    before = convo.self_model.current()["valence"]
    say(convo, "Thanks!")
    say(convo, "You're awesome.")
    assert convo.self_model.current()["valence"] > before
    say(convo, "You're useless.")
    assert say(convo, "What's your mood?").startswith("Mood ")


def test_are_you_state_questions(convo):
    for _ in range(4):
        say(convo, "Thank you")
    assert say(convo, "Are you happy?") in bank("are_you_yes", word="happy")
    mood = convo.self_model.mood_word()
    assert say(convo, "Are you tired?") in bank(
        "are_you_no", word="tired", mood_word=mood, Mood=mood.capitalize())


def test_thanks_and_farewell(convo):
    assert say(convo, "thanks") in bank("thanks")
    assert say(convo, "bye") in bank("farewell")


def test_acknowledgement_is_not_a_parse_failure(convo):
    assert say(convo, "OK.") in bank("ack")
    assert say(convo, "Mm-hmm") in bank("ack")
    assert convo.repo.open_questions() == []  # "OK." is not a question the bot failed to understand


# --- self-model -----------------------------------------------------------------

def test_what_are_you(convo):
    text = say(convo, "What are you?")
    assert text.startswith("I'm Hal.") and "fact" in text and "software" not in text
    bot = say(convo, "Are you a bot?")
    assert "software" not in bot and "lookup table" not in bot and bot.endswith("?")
    conscious = say(convo, "Are you conscious?")
    assert "Checking myself:" in conscious  # reasoned from his own record
    assert say(convo, "What's your name?") in bank("name_reply", self_name="Hal", extra="")


def test_teaching_it_about_itself(convo):
    assert say(convo, "You like pizza.").endswith(": I like pizza.")
    assert say(convo, "Do you like pizza?") == "Yes. I like pizza."
    assert "blue" in say(convo, "Your favorite color is blue.")
    assert say(convo, "What's your favorite color?") == "My favorite color is blue."
    assert "I like pizza" in say(convo, "Tell me about yourself.")
    assert say(convo, "Who made you?") in bank("made_unknown")
    say(convo, "I made you.")
    assert say(convo, "Who made you?") in bank("made_known", maker="Travis")


def test_talking_about_the_listener(convo):
    say(convo, "I own a guitar.")
    assert say(convo, "Do I own a guitar?") == "Yes. You own a guitar."
    say(convo, "My favorite food is pizza.")
    assert say(convo, "What's my favorite food?") == "Your favorite food is pizza."


def test_taught_state_overrides_variables(convo):
    say(convo, "You are not tired.")
    assert say(convo, "Are you tired?").startswith("No. I am not tired")


def test_discord_name_alias(convo):
    convo.add_self_alias("MiniMe")
    assert say(convo, "What's your name?") in bank("name_reply", self_name="Hal", extra=" You can also call me MiniMe.")
    assert say(convo, "MiniMe, thanks") in bank("thanks")


# --- asking questions to learn ----------------------------------------------------

def test_asks_about_unknown_word_and_learns(convo):
    say(convo, "Howard owns a truck.")
    text = say(convo, "Whereupon the flibbet notwithstanding.")
    assert "What does 'flibbet' mean?" in text
    assert say(convo, "It's a kind of bird.") == "Got it: a flibbet is a bird."
    assert convo.handle("Is a flibbet a bird?").trace["status"] == "yes"


def test_asks_for_missing_word_then_answers_original_question(convo):
    say(convo, "Howard owns a motorcycle.")
    say(convo, "Trucks are vehicles.")
    text = say(convo, "Does Howard own a zibble?")  # not a dictionary word: ask what it is
    assert text.endswith("What is a zibble?")
    reply = say(convo, "A vehicle.")
    assert reply.startswith("Got it: a zibble is a vehicle. So: I don't know whether Howard owns a zibble.")


def test_dictionary_words_are_not_asked_about(convo):
    say(convo, "Howard owns a truck.")
    text = say(convo, "Does Howard own a scooter?")
    assert "what a scooter is" not in text and "What is a scooter" not in text
    assert text.startswith("I don't know whether Howard owns a scooter.")
    assert text.endswith("Do you know? If you tell me, I'll remember it.")
    assert say(convo, "Yes.") == "Thanks, I'll remember that Howard owns a scooter."


def test_asks_do_you_know_and_learns_yes_no(convo):
    say(convo, "Howard owns a truck.", )
    say(convo, "A boat is a vehicle.")
    text = say(convo, "Does Howard own a boat?")
    assert text.endswith("Do you know? If you tell me, I'll remember it.")
    assert say(convo, "Yes, he does.") == "Thanks, I'll remember that Howard owns a boat."
    assert convo.handle("Does Howard own a boat?").trace["status"] == "yes"
    say(convo, "A canoe is a boat.")
    say(convo, "Does Bob own a canoe?")  # Bob unknown -> asks nothing about yes/no (missing name)
    say(convo, "Bob owns a truck.")
    assert say(convo, "Does Bob own a canoe?").endswith("Do you know? If you tell me, I'll remember it.")
    assert say(convo, "No idea.") == "No problem."
    assert convo.handle("Does Bob own a canoe?").trace["status"] == "unknown"


def test_does_not_repeat_questions(convo):
    say(convo, "Howard owns a truck.")
    say(convo, "A boat is a vehicle.")
    assert say(convo, "Does Howard own a boat?").endswith("I'll remember it.")
    say(convo, "Howard lives in Ohio.")  # moves on
    assert "Do you know" not in say(convo, "Does Howard own a boat?")


def test_asks_who_a_pronoun_means(convo):
    assert "Who or what do you mean by 'he'?" in say(convo, "He owns a truck.")
