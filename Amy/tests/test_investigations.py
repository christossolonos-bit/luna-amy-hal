import json

import pytest

from app.conversation import Conversation
from db.repository import Repository
from nlp.wordnet import Sense
from reasoning.investigations import Investigations, InvestigationController, fingerprint
from reasoning.thoughts import ThoughtStore
from reasoning.thought_bridge import ThoughtBridge
from reasoning.narration import pending, delivered
from voice.service import VoiceService
from voice.wake import WakeWord
from tests.test_voice import FakeSpeaker, FakeTranscriber, speak


class LocalMeanings:
    def lookup(self, word, pos='n'):
        return [Sense('test:1',word,'n','a behavioral account'),Sense('test:2',word,'n','a subjective account')]


@pytest.fixture
def rig(tmp_path):
    repo=Repository(str(tmp_path/'memory.db'))
    convo=Conversation(repo,self_name='Amy')
    store=ThoughtStore(tmp_path/'queue.db')
    convo.thinking=ThoughtBridge(tmp_path/'memory.db',tmp_path/'queue.db',clock=lambda:10000)
    controller=InvestigationController(repo,store,wordnet=LocalMeanings())
    yield repo,convo,store,controller
    store.close()
    repo.close()


def finish(controller, ident, limit=60):
    for _ in range(limit):
        controller.tick()
        state=controller.sessions.get(ident)
        if state['phase']=='limited' or (state['phase'] in ('waiting','complete') and state['revision']==fingerprint(controller.repo)):
            return state
    raise AssertionError('Investigation did not stop within its work budget')


def teach(convo, extra=()):
    for text in ('Rex is a dog.','Rex is playful.','Luna is a dog.','Luna is playful.',*extra):
        convo.handle(text,speaker='Travis')


def test_persistent_passes_rule_testing_and_no_promotion(rig):
    repo,c,store,controller=rig
    teach(c)
    before=repo.conn.total_changes
    state=controller.sessions.open('Are dogs playful?','Travis')
    # Reopening the adapter preserves the ongoing objective rather than restarting.
    controller.tick()
    controller=InvestigationController(repo,store,wordnet=LocalMeanings())
    assert controller.sessions.get(state['id'])['passes']==1
    result=finish(controller,state['id'])
    assert result['phase']=='waiting'  # a pattern does not establish a universal rule
    assert result['hypotheses'][0]['status']=='provisional-supported'
    assert result['hypotheses'][0]['retained']
    assert result['hypotheses'][0]['tests'][0]['proofs']
    assert result['concepts'][0]['retained']
    assert result['interpretations']
    assert repo.conn.total_changes==before
    assert not repo.active_rules()
    event_count=store.conn.execute('SELECT COUNT(*) FROM investigation_events').fetchone()[0]
    assert controller.tick()==[]
    assert store.conn.execute('SELECT COUNT(*) FROM investigation_events').fetchone()[0]==event_count


def test_counterexamples_and_revision_reopen(rig):
    repo,c,store,controller=rig
    teach(c)
    state=controller.sessions.open('Are dogs playful?','Travis')
    assert finish(controller,state['id'])['hypotheses'][0]['retained']
    c.handle('Milo is a dog.',speaker='Travis')
    c.handle('Milo is not playful.',speaker='Travis')
    result=finish(controller,state['id'])
    assert result['hypotheses'][0]['status']=='rejected'
    assert result['hypotheses'][0]['counterexamples']
    assert not result['hypotheses'][0]['retained']
    # The useful intersection can survive a refuted generalization.
    assert result['concepts'][0]['retained']


def test_unknown_is_not_negative_and_no_unchecked_rule_survivor(rig):
    _,c,_,controller=rig
    teach(c,('Milo is a dog.',))
    state=controller.sessions.open('Are dogs playful?','Travis')
    result=finish(controller,state['id'])
    h=result['hypotheses'][0]
    assert any(t['status']=='unknown' for t in h['tests'])
    assert not h['counterexamples']
    controller=InvestigationController(c.repo,controller.store,wordnet=LocalMeanings(),max_queries=0)
    other=controller.sessions.open('Investigate playful dogs','Travis')
    result=finish(controller,other['id'])
    assert not result['hypotheses'][0]['retained']
    assert result['hypotheses'][0]['status']=='insufficient-evidence'


def pattern(relation, obj=None, props=()):
    return dict(relation=relation,subject='x',obj=obj,verb=None,props=list(props),args=[],polarity=True)


def test_missing_premise_opens_child_and_returns_after_learning(rig):
    repo,c,_,controller=rig
    c.handle('Rex is a dog.',speaker='Travis')
    happy=repo.concept('happy');playful=repo.concept('playful')
    uid=repo.log_utterance('Travis','If a dog is playful, it is happy.')
    repo.add_rule([pattern('is_a', ['c',repo.find_concept('dog')]),pattern('property',props=[playful])],
                  [pattern('property',props=[happy])],{},1.,uid)
    state=controller.sessions.open('Is Rex happy?','Travis')
    result=finish(controller,state['id'])
    assert result['phase']=='waiting'
    assert result['gaps'][0]['kind']=='premise'
    assert result['children']
    c.handle('Rex is playful.',speaker='Travis')
    result=finish(controller,state['id'])
    assert result['phase']=='complete'
    assert result['conclusions'][0]['status']=='yes'
    assert result['conclusions'][0]['proofs'][0]['learned_rules']


def test_budget_and_retractions(rig):
    repo,c,_,controller=rig
    teach(c)
    initial=fingerprint(repo)
    row=repo.conn.execute("SELECT id FROM assertions WHERE predicate='has_property' LIMIT 1").fetchone()
    repo.retract(row['id'],None,'test correction')
    assert fingerprint(repo)!=initial
    state=controller.sessions.open('Investigate dogs','Travis')
    state['limit_passes']=1
    controller.sessions._save(state,'set-limit',{})
    assert finish(controller,state['id'])['phase']=='limited'


def test_capability_question_and_progress_inspection(rig):
    _,c,_,controller=rig
    reply=c.handle('Can you love?',speaker='Travis')
    assert reply.trace['intent']=='thinking'
    ident=reply.trace['thinking']['investigation_id']
    result=finish(controller,ident)
    assert result['mechanisms']['state_variables']
    report=c.handle('Show your investigation.',speaker='Travis')
    for key in result['mechanisms']['state_variables']:
        assert key+'=' in report.text
    assert report.trace['thinking']['investigation']['gaps']
    assert c.handle('Show your investigation.',speaker='Stranger').trace['thinking']['status']=='no-investigation'


def test_narration_coalesces_cooldown_ack_and_final_once(rig):
    _,c,store,controller=rig
    teach(c)
    state=controller.sessions.open('Are dogs playful?','Travis')
    controller.tick();controller.tick()
    notice=pending(store,'Travis',10000)
    assert notice and 'competing meanings' in notice['text']
    # Failed delivery preserves the event cursor.
    assert not c.thinking.narration(c,'Travis',lambda text:False)
    assert pending(store,'Travis',10000)['event']==notice['event']
    delivered(store,notice,10000)
    assert pending(store,'Travis',10010) is None
    finish(controller,state['id'])
    notice=pending(store,'Travis',10030)
    assert 'evidence limit' in notice['text']
    delivered(store,notice,10030)
    assert pending(store,'Travis',10100) is None
    c.handle('Pause progress updates.',speaker='Travis')
    controller.sessions.open('Something else','Travis')
    assert pending(store,'Travis',10200) is None


def test_progress_voice_delivery_obeys_pause_and_playback(rig,monkeypatch):
    _,c,_,_=rig
    sent=[]
    stt=FakeTranscriber()
    service=VoiceService(c,sent.append,stt,FakeSpeaker(),WakeWord('Amy'),log=lambda _:None)
    speak(service,stt,'Amy, investigate dogs.')
    assert service.progress_targets[c.state.speaker]=={'guild':'g1','channel':'t1'}
    sent.clear()
    target={'guild':'g1','channel':'t1'}
    assert not service.progress('Checking evidence.',target)
    service.handle({'type':'playback','guild':'g1','state':'finished'})
    service.last_activity=0
    assert service.progress('Checking evidence.',target)
    assert [m['type'] for m in sent]==['play','say']
    assert not service.progress('Another update.',target)
    service.handle({'type':'left','guild':'g1'})
    assert not service.progress_targets
