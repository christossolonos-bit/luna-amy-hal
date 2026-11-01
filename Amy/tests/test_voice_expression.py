import base64
import json
import random

import pytest

from app.conversation import Conversation
from db.repository import Repository
from tools.voice_expression_preview import STATES
from voice.expression import CATALOG, VoiceExpressionEngine
from voice.service import VoiceService
from voice.tts import FishSpeaker
from voice.wake import WakeWord
from tests.test_voice import FakeTranscriber, _wav, speak


@pytest.mark.parametrize("state,emotion", [("excited", "delighted"), ("sad", "sad"), ("stressed", "nervous"),
                                         ("curious", "curious"), ("tired", "relaxed"), ("warm", "happy")])
def test_numerical_states_direct_delivery(state, emotion):
    plan = VoiceExpressionEngine().plan("Hello there.", STATES[state])
    assert plan.segments[0].emotion == emotion
    assert f"[{emotion}]" in plan.render("s2.1-pro-free")
    assert 0.82 <= plan.speed <= 1.18 and -5 <= plan.volume <= 2


def test_sensitive_context_overrides_excitement_and_all_effects():
    engine = VoiceExpressionEngine(rng=random.Random(1))
    plan = engine.plan("I'm sorry to hear that.", STATES['excited'], "My friend died.", preview_effect='laughing')
    assert plan.sensitive
    assert plan.segments[0].emotion == 'empathetic'
    assert plan.segments[0].delivery == 'soft tone'
    assert not any(s.effect for s in plan.segments)
    assert plan.volume < 0 and plan.speed < 1


def test_laughter_is_contextual_and_has_a_cooldown():
    engine = VoiceExpressionEngine(rng=random.Random(1))
    state = {'energy': .9, 'valence': .7, 'emotions': {'excitement': .7}}
    first = engine.plan("You're a charmer.", state, 'I was joking.')
    assert first.segments[0].effect == 'chuckling'
    for _ in range(3):
        assert engine.plan("You're a charmer.", state, 'I was joking.').segments[0].effect is None


def test_every_catalog_sound_has_model_correct_rendering():
    for effect in CATALOG['sounds']:
        plan = VoiceExpressionEngine().plan('This is a preview.', STATES['neutral'], preview_effect=effect)
        assert '[' + effect + ']' in plan.render('s2-pro')
        assert '[' not in plan.render('s1')
        assert plan.render('unsupported') == 'This is a preview.'


def test_incoming_tags_are_cleaned_then_trusted_tags_survive():
    plan = VoiceExpressionEngine().plan('[shouting] **Hello** <|speaker:99|> [debug #3]', STATES['sad'])
    text = plan.render('s2.1-pro-free')
    assert '[sad]' in text
    assert 'shouting' not in text and 'speaker:99' not in text and 'debug' not in text
    assert plan.segments[0].text == 'Hello'


def test_paragraph_pauses_breaths_and_final_question():
    plan = VoiceExpressionEngine().plan('Here is a fact.\n\nWhat do you think?', STATES['curious'], preview_effect='breath')
    assert plan.segments[1].pause_before
    assert plan.segments[1].effect == 'breath'
    assert plan.segments[1].emotion == 'curious'
    assert '[break]' in plan.render('s2.1-pro-free')


def test_numeric_controls_smooth_and_never_become_nonfinite():
    engine = VoiceExpressionEngine()
    fast = engine.plan('Hello.', STATES['excited'])
    slow = engine.plan('Hello.', STATES['sad'])
    assert abs(fast.speed - slow.speed) < .15
    bad = engine.plan('Hello.', {'energy': float('nan'), 'stress': 'oops', 'chemicals': None, 'emotions': None})
    assert .82 <= bad.speed <= 1.18
    with pytest.raises(ValueError):
        VoiceExpressionEngine(strength=float('nan'))


def test_chemical_changes_influence_prosody():
    neutral = VoiceExpressionEngine().plan('Hello.', {'energy': .8})
    stimulated = VoiceExpressionEngine().plan('Hello.', {'energy': .8, 'chemicals': {'dopamine': 30, 'norepinephrine': 22}})
    assert stimulated.speed > neutral.speed
    assert stimulated.segments[0].emotion in ('excited', 'delighted')


def test_fish_payload_keeps_reference_and_generated_tags(monkeypatch):
    seen = {}
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self): return _wav(seconds=.01)
    def fake(request, timeout):
        seen.update(json.loads(request.data))
        return Response()
    monkeypatch.setattr('voice.tts.urllib.request.urlopen', fake)
    speaker = FishSpeaker('fake-key', voice_id='chosen-voice', expression_engine=VoiceExpressionEngine())
    speaker.discord_pcm_with_state('**Hello** [debug #3]', STATES['sad'])
    assert seen['reference_id'] == 'chosen-voice'
    assert '[sad]' in seen['text'] and '[soft tone]' in seen['text']
    assert 'debug' not in seen['text']
    assert seen['prosody']['speed'] < 1


def test_service_passes_live_state_but_posts_only_original_text(monkeypatch, tmp_path):
    class Speaker:
        last_plan = None
        def discord_pcm_with_state(self, text, state, context='', intent=None):
            self.args = text, state, context, intent
            self.last_plan = VoiceExpressionEngine().plan(text, state, context, intent)
            return b'\0\0'
    repo = Repository(str(tmp_path/'memory.db'))
    try:
        convo = Conversation(repo, self_name='Amy')
        speaker, stt, sent = Speaker(), FakeTranscriber(), []
        service = VoiceService(convo, sent.append, stt, speaker, WakeWord('Amy'), log=lambda _:None)
        speak(service, stt, 'Amy, Howard owns a truck.')
        text, state, context, intent = speaker.args
        assert 'chemicals' in state and 'emotions' in state
        assert context == 'Howard owns a truck.'
        assert sent[0]['content'].endswith(text)
        assert '[happy]' not in sent[0]['content'] and '[calm]' not in sent[0]['content']
        assert service.last_voice_plan is not None
        service.on_command({'command':'voice', 'channel':'t1'})
        assert 'speed' in sent[-1]['content'] and 'fake-key' not in sent[-1]['content']
    finally:
        repo.close()
