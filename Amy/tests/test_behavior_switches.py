import io
import json
from email.message import Message

import pytest

from app import behaviors
from app.conversation import Conversation
from db.repository import Repository
from interfaces import dashboard
from voice.service import VoiceService
from voice.wake import WakeWord
from tests.test_voice import FakeSpeaker, FakeTranscriber, speak


def test_saved_switches_are_read_live_and_do_not_touch_env(tmp_path,monkeypatch):
    monkeypatch.setattr(behaviors,'ROOT',tmp_path)
    switches=behaviors.BehaviorSwitches()
    repo=Repository(':memory:')
    c=Conversation(repo,self_name='Amy')
    sent=[]; stt=FakeTranscriber()
    service=VoiceService(c,sent.append,stt,FakeSpeaker(),WakeWord('Amy'),log=lambda _:None)
    try:
        switches.update({'greetings':False})
        speak(service,stt,'Hello Amy')
        assert sent==[]
        # Suppressing a greeting must preserve the question or fact after it.
        result=c.handle('Hello Amy, Rex is a dog.',speaker='Travis')
        assert 'Rex' in result.text
        switches.update({'greetings':True})
        speak(service,stt,'Hello Amy')
        assert sent[-1]['type']=='play'
        switches.update({'farewells':False})
        assert c.handle('Goodbye.',speaker='Travis').text==''
        assert not (tmp_path/'.env').exists()
    finally:repo.close()


def test_dashboard_behavior_api_validates_and_persists(tmp_path,monkeypatch):
    switches=behaviors.BehaviorSwitches(tmp_path/'switches.json')
    monkeypatch.setattr(dashboard,'BehaviorSwitches',lambda:switches)
    def post(values,origin=None):
        handler=object.__new__(dashboard.Handler)
        body=json.dumps({'values':values}).encode()
        handler.path='/api/behaviors'
        handler.headers=Message()
        handler.headers['Content-Type']='application/json'
        handler.headers['Content-Length']=str(len(body))
        handler.headers['Host']='127.0.0.1:8766'
        if origin:handler.headers['Origin']=origin
        handler.rfile=io.BytesIO(body)
        result=[]
        handler._send=lambda body,mime,status=200:result.append((status,json.loads(body)))
        handler.do_POST()
        return result[0]
    assert post({'greetings':False})[0]==200
    assert switches.enabled('greetings') is False
    assert post({'greetings':'false'})[0]==400
    assert post({'secret_token':False})[0]==400
    assert post({'greetings':True},'https://example.com')[0]==403
    assert switches.enabled('greetings') is False
