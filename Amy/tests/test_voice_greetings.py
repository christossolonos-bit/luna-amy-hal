import pytest

from app.conversation import Conversation
from db.repository import Repository
from voice.service import VoiceService
from voice.wake import WakeWord
from tests.test_voice import FakeSpeaker, FakeTranscriber, speak


@pytest.mark.parametrize('text,expected', [
    ('Hi Amy','Hi'),('Hello Amy','Hello'),('Hey, Amy!','Hey'),
    ('Hello, Aimee.','Hello'),('Amy, hello','Hello'),('Good morning, Amy','Good morning'),
])
def test_greeting_survives_wake_gate(text,expected):
    gate=WakeWord('Amy').check(text,'Travis')
    assert gate.addressed and not gate.wake_only
    assert gate.text == expected


def test_name_alone_still_means_attention():
    assert WakeWord('Amy').check('Amy?','Travis').wake_only


def test_full_voice_greeting_reaches_dialogue_and_is_remembered(tmp_path):
    repo=Repository(str(tmp_path/'memory.db'))
    try:
        c=Conversation(repo,self_name='Amy')
        sent,stt=[],FakeTranscriber()
        service=VoiceService(c,sent.append,stt,FakeSpeaker(),WakeWord('Amy'),log=lambda _:None)
        speak(service,stt,'Hello Amy')
        reply=sent[0]['content'].split('\n',1)[1]
        assert reply not in ('Yes?',"Yes? I'm here.")
        assert c.state.last_trace['intent']=='social'
        assert c.repo.conn.execute("SELECT COUNT(*) FROM dialogue_turns WHERE role='engine'").fetchone()[0] == 1
        assert sent[1]['type'] == 'play'
    finally:
        repo.close()
