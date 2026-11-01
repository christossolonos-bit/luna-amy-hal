import pytest

from app.conversation import Conversation
from db.repository import Repository
from nlp.wordnet import Sense
from reasoning.investigation_results import findings
from reasoning.investigations import InvestigationController, lexical_findings
from reasoning.narration import blip
from reasoning.thought_bridge import ThoughtBridge
from reasoning.thoughts import ThoughtStore
from tests.test_investigations import finish
from tests.test_voice import FakeSpeaker, FakeTranscriber
from voice.service import VoiceService


class PhraseDictionary:
    def lookup(self, word, pos='n'):
        meanings={'fall in love':'begin to experience feelings of love towards',
                  'fall':'the season when the leaves fall from the trees'}
        return [Sense('test:'+word,word,'v' if word=='fall in love' else 'n',meanings[word])] if word in meanings else []


def state(phase):
    return dict(id=1,objective='falling in love',phase=phase,definitions=[dict(term='fall in love',scope='phrase',
                definition='begin to experience feelings of love towards')],
                evidence=[dict(text='Amy remembers people',source='Travis')],conclusions=[],connections=[],hypotheses=[],
                gaps=[dict(question='Does the available evidence establish the required experience?')])


@pytest.mark.parametrize('phase,action',[('waiting','synthesize'),('complete','synthesize'),('limited','budget-limit')])
def test_every_stopping_report_contains_findings_and_uncertainty(phase,action):
    report=blip(state(phase),dict(action=action,detail={}))
    assert 'begin to experience feelings of love' in report
    assert 'Amy remembers people' in report
    assert 'required experience' in report
    assert 'not by itself establish' in report
    assert 'Ask me to show' not in report
    if phase=='limited':assert 'partial findings' in report


def test_multiword_expression_does_not_become_autumn():
    definitions=lexical_findings(PhraseDictionary(),'falling in love',['fall','love'])
    assert definitions[0]['term']=='fall in love'
    assert not any('season' in d['definition'] for d in definitions)


def test_actual_transcribed_requests_and_followups(tmp_path):
    repo=Repository(':memory:')
    c=Conversation(repo,self_name='Amy')
    c.thinking=ThoughtBridge(tmp_path/'memory.db',tmp_path/'queue.db')
    store=ThoughtStore(tmp_path/'queue.db')
    try:
        reply=c.handle('I want you to think about falling in love.',speaker='Travis')
        ident=reply.trace['thinking']['investigation_id']
        controller=InvestigationController(repo,store,wordnet=PhraseDictionary())
        finish(controller,ident)
        for question in ('What did you find out?', 'What have you learned?', 'Tell me your findings.'):
            reply=c.handle(question,speaker='Travis')
            assert reply.trace['intent']=='thinking'
            assert 'begin to experience feelings of love' in reply.text
            assert 'season' not in reply.text
        reply=c.handle('Are you able to fall in love?',speaker='Travis')
        assert reply.trace['thinking']['status']=='investigation-queued'
    finally:
        store.close();repo.close()


def test_long_findings_are_not_lost_to_discord_message_limit():
    sent=[]
    service=VoiceService(None,sent.append,FakeTranscriber(),FakeSpeaker())
    text=('A supported fact with its source.\n\n'*160)+'Here is the unresolved question.'
    service.send(dict(type='say',channel='t1',content=text))
    assert len(sent)>1
    assert all(len(m['content'])<=2000 for m in sent)
    assert ''.join(m['content'] for m in sent)==text


def test_no_results_does_not_invent_a_conclusion():
    report=findings(dict(phase='limited',definitions=[],evidence=[],conclusions=[],connections=[],hypotheses=[],gaps=[]))
    assert 'not established a supported finding' in report
    assert 'partial findings' in report


def test_saved_interpretations_are_not_injected_into_state_reports():
    saved=state('waiting')
    saved['mechanisms']={'state_variables':{'curiosity':.8,'valence':.6},
                         'interpretation':'An obsolete fixed conclusion about emotions.'}
    report=findings(saved)
    assert 'curiosity=0.8' in report and 'valence=0.6' in report
    assert saved['mechanisms']['interpretation'] not in report
