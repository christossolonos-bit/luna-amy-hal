import random

from app.conversation import Conversation
from db.repository import Repository
from dialogue.phrasing import PhraseDeck
from dialogue.repetition import WordingHistory
from reasoning.thought_bridge import ThoughtBridge
from tests.test_voice import FakeSpeaker, FakeTranscriber, speak
from voice.service import VoiceService
from voice.wake import WakeWord


class Ordered:
    def shuffle(self,lines):pass


def test_core_sentence_padding_and_cross_bank_reuse_are_rejected(tmp_path):
    repo=Repository(str(tmp_path/'memory.db'))
    try:
        deck=PhraseDeck(Ordered());deck.history=WordingHistory(repo)
        bank=('What shall we talk about?',"What shall we talk about? I'm listening.",
              'Which idea would you like to explore?')
        assert deck.pick('first',bank)==bank[0]
        assert deck.pick('different-key',bank)==bank[2]
        assert deck.pick('first',bank)==''
    finally:repo.close()


def test_history_survives_restart_and_has_no_refill(tmp_path):
    path=str(tmp_path/'memory.db')
    bank=('Could you explain the connection between those ideas?',
          'Which part of your thought should I examine first?')
    used=[]
    for _ in range(3):
        repo=Repository(path)
        try:
            deck=PhraseDeck(random.Random(3));deck.history=WordingHistory(repo)
            used.append(deck.pick('clarify',bank))
        finally:repo.close()
    assert len({x for x in used if x})==2 and used[-1]==''


def test_renamed_bot_keeps_variation_and_spoken_investigations_differ(tmp_path):
    repo=Repository(str(tmp_path/'memory.db'))
    try:
        convo=Conversation(repo,self_name='Bella',persona='amy')
        assert convo.social.phrases.history is not None
        convo.thinking=ThoughtBridge(tmp_path/'memory.db',tmp_path/'queue.db')
        sent=[];stt=FakeTranscriber()
        service=VoiceService(convo,sent.append,stt,FakeSpeaker(),WakeWord('Bella'),log=lambda _:None)
        replies=[]
        for _ in range(10):
            speak(service,stt,'Bella, think about friendship.')
            replies.append([m['content'].split('\n',1)[1] for m in sent if m['type']=='say'][-1])
        assert len(set(replies))==10
        assert sum(m['type']=='play' for m in sent)==10
    finally:repo.close()


def test_final_audio_guard_catches_inline_replies_and_old_history(tmp_path):
    repo=Repository(str(tmp_path/'memory.db'))
    try:
        uid=repo.log_utterance('Travis','How are you?')
        repo.log_reply(uid,'An existing reply that was already heard.')
        convo=Conversation(repo,self_name='Amy')
        sent=[]
        service=VoiceService(convo,sent.append,FakeTranscriber(),FakeSpeaker(),log=lambda _:None)
        assert service._fresh_spoken('An existing reply that was already heard.') is None
        assert service._fresh_spoken('An entirely different reply is ready.')
        service.wording.delivered('An entirely different reply is ready.')
        assert service._fresh_spoken('An entirely different reply is ready.') is None
    finally:repo.close()
