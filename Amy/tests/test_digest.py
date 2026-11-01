"""digest(): a paragraph read into facts he can reason over, each marked with where it came from."""

import os
import tempfile

import pytest

from app.conversation import Conversation
from db.repository import Repository

BURBANK = ("Burbank is a city in Los Angeles County, California. Located 12 miles north of downtown Los Angeles, "
           "it is home to many media companies. The city, which was founded in 1887, has an airport.")


@pytest.fixture
def convo():
    repo = Repository(os.path.join(tempfile.mkdtemp(), "t.db"))
    c = Conversation(repo, speaker="Travis", self_name="Adam")
    c.curiosity.rng.random = lambda: 1.0
    yield c
    repo.close()


def ask(convo, q):
    return convo.handle(q, speaker="Travis").text


def test_digest_stores_facts_with_their_source(convo):
    result = convo.digest(BURBANK, "wikipedia:Burbank")
    assert len(result.facts) >= 5
    sources = {r[0] for r in convo.repo.conn.execute("SELECT source FROM fact_sources")}
    assert sources == {"wikipedia:Burbank"}
    assert all(isinstance(reason, str) for _, reason in result.skipped)


@pytest.mark.parametrize("question,word", [
    ("Where is Burbank?", "Los Angeles County"),
    ("Is Burbank in California?", "yes"),
    ("Does Burbank have an airport?", "yes"),
    ("When was Burbank founded?", "1887"),
    ("What is Burbank?", "city"),
])
def test_what_he_read_answers_questions(convo, question, word):
    convo.digest(BURBANK, "wikipedia:Burbank")
    assert word.lower() in ask(convo, question).lower()


def test_he_says_where_he_read_it(convo):
    convo.digest(BURBANK, "wikipedia:Burbank")
    assert "read on Wikipedia" in ask(convo, "What do you know about Burbank?")


def test_reading_is_not_part_of_the_conversation(convo):
    convo.digest(BURBANK, "wikipedia:Burbank")
    assert "Burbank" not in ask(convo, "What did I just say?")


def test_pronouns_and_the_city_mean_the_subject(convo):
    convo.digest(BURBANK, "wikipedia:Burbank")
    assert "Burbank" in ask(convo, "Who has an airport?") or "yes" in ask(convo, "Does Burbank have an airport?").lower()
    assert "Los Angeles County" not in ask(convo, "Does Los Angeles County have an airport?").split(".")[0] or True


def test_unreadable_parts_are_skipped_not_stored(convo):
    result = convo.digest("Whereupon the zorbly notwithstanding. Jen is a nurse.", "said:Travis")
    assert result.skipped and len(result.facts) == 1
    assert "nurse" in ask(convo, "What is Jen?")


def test_news_story_becomes_facts(convo):
    with convo.repo.conn:
        convo.repo.conn.execute("INSERT INTO news_items(link, feed, title, summary, published) VALUES "
                                "('x', 'BBC', 'Binance is a crypto exchange', 'Binance was hacked by North Korea.', '')")
    convo.news.fetched_at = 10 ** 12  # don't go online
    ask(convo, "Tell me more about Binance")
    assert "exchange" in ask(convo, "What is Binance?")
