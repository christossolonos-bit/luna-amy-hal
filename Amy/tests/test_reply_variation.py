import json
from pathlib import Path

from app.conversation import Conversation
from db.repository import Repository
from dialogue.persona import social_banks, learning_banks
from dialogue.phrasing import BANKS
from dialogue.social import REPLIES


def test_all_social_and_learning_banks_have_at_least_fifteen_variants():
    for name, bank in {**social_banks(REPLIES), **learning_banks(BANKS)}.items():
        assert len(set(bank)) >= 15, name
    assert len(learning_banks(BANKS)['misunderstood']) >= 4000


def test_every_inventoried_canned_reply_has_fifteen_variants():
    data=json.loads((Path(__file__).resolve().parents[1]/'dialogue'/'scripted_replies.json').read_text(encoding='utf-8'))
    assert len(data) >= 60
    assert all(len(set(bank)) >= 15 for bank in data.values())
    from tools.build_scripted_replies import inventory
    assert inventory() <= set(data)


def test_live_fallback_path_uses_large_deck_without_repeats(tmp_path):
    repo=Repository(str(tmp_path/'memory.db'))
    try:
        c=Conversation(repo,self_name='Amy')
        replies=[c.understanding.make_sense('Where are we from?') for _ in range(40)]
        assert len(set(replies)) == 40
        assert all("I didn't catch what that was about. Say it a different way" not in r for r in replies)
    finally:
        repo.close()


def test_unknown_origin_is_not_an_unparsed_question(tmp_path):
    repo=Repository(str(tmp_path/'memory.db'))
    try:
        c=Conversation(repo,self_name='Amy')
        result=c.understanding.make_sense('Where are you from?')
        assert 'origin' in result or "where I'm from" in result or 'that about myself' in result
        c.handle('You are from Alabama.')
        assert 'Alabama' in c.handle('Where are you from?').text
    finally:
        repo.close()


def test_variation_does_not_rewrite_facts_or_quoted_memories(tmp_path):
    repo=Repository(str(tmp_path/'memory.db'))
    try:
        c=Conversation(repo,self_name='Amy')
        statement='Howard owns a red truck.'
        assert c.scripted_replies.render(statement) == statement
        quoted='I said: “Okay. Go ahead.”'
        assert c.scripted_replies.render(quoted) == quoted
        c.handle(statement)
        assert 'Howard owns a red truck' in c.handle('What does Howard own?').text
    finally:
        repo.close()
