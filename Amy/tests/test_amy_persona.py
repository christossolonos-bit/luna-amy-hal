from string import Formatter

import pytest

from app.conversation import Conversation
from db.repository import Repository
from dialogue.persona import AMY_SOCIAL, social_banks
from dialogue.social import REPLIES
from voice.wake import WakeWord


def test_every_scripted_social_intent_has_amy_wording():
    assert set(REPLIES) <= set(AMY_SOCIAL)
    for bank in social_banks(REPLIES).values():
        for line in bank:
            fields = {name: 'example' for _, name, _, _ in Formatter().parse(line) if name}
            assert line.format(**fields).strip()


def test_amy_name_wake_words_and_fact_memory(tmp_path):
    repo = Repository(str(tmp_path / 'memory.db'))
    try:
        convo = Conversation(repo, speaker='Travis', self_name='Amy')
        assert 'Amy' in convo.handle("What's your name?").text
        assert convo.self_model.current()['curiosity'] >= 0.5
        convo.handle('Howard owns a red truck.')
        reply = convo.handle('What does Howard own?').text
        assert 'truck' in reply and 'red' in reply
        assert 'charmer' not in reply and 'flattery' not in reply
        assert 'Amy' not in convo.handle('What did I just say?').text
    finally:
        repo.close()
    assert WakeWord('Amy').check('Amy, hello', 'Travis').addressed
    assert WakeWord('Amy').check('Aimee, hello', 'Travis').addressed


def test_flirtation_is_limited_to_positive_social_context(tmp_path):
    repo = Repository(str(tmp_path / 'memory.db'))
    try:
        convo = Conversation(repo, self_name='Amy')
        assert any('charmer' in line or 'way with words' in line for line in convo.social.banks['compliment'])
        for key in ('wellbeing_bad', 'insult', 'mood_low', 'stress_high'):
            assert not any('charmer' in line or 'flattery' in line for line in convo.social.banks[key])
    finally:
        repo.close()
