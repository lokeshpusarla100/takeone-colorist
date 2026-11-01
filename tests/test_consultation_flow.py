from dataclasses import replace
from uuid import uuid4

from takeone_rubric_v1.rubric import Candidate, Proposal
from tests.test_backend import FakeMuse, client, settings, start, upload, video, wait


def answer(c,state,response):
    result=c.post('/api/projects/'+state['id']+'/answers',json={
        'command_id':str(uuid4()),'revision':state['revision'],
        'answer':{'question_id':state['question']['id'],'response_type':'option','response':response}})
    assert result.status_code==202,result.text
    return wait(c,state['id'])


class AdaptiveMuse(FakeMuse):
    def __init__(self):
        super().__init__()
        self.histories=[]
        self.template=None

    def propose(self,session):
        self.histories.append(session.answered_decisions())
        result=super().propose(session)
        if not session.answers:
            first=Candidate.model_validate({**result.candidates[0].model_dump(mode='json'),
                'target_key':'contrast_texture','preference_family':'contrast_texture',
                'question':'Should contrast transitions feel soft or punchy?',
                'decision_fork':'Soft tonal transitions versus punchy global contrast.'})
            self.template=first.model_dump(mode='json')
            return Proposal(candidates=[first])
        if len(session.answers)==1:
            restrained=session.answers[0].response=='natural'
            question='Keep colors restrained too, or retain strong color?' if restrained else 'Pair stronger contrast with muted or vivid color?'
            return Proposal(candidates=[Candidate.model_validate({**self.template,
                'id':'q2','target_key':'color_intensity','preference_family':'color_intensity',
                'question':question,'decision_fork':'Muted versus vivid global saturation.',
                'options':[{'id':'muted','label':'Muted','intended_outcome':'Reduce global saturation','required_operations':['global_grade']},
                           {'id':'vivid','label':'Vivid','intended_outcome':'Keep vivid global saturation','required_operations':['global_grade']}]})])
        return Proposal(completion_reason='Treatment strength and saturation were chosen; no further supported preference fork remains.')


def test_one_question_then_reconsider_actual_answer_before_followup(settings,video):
    muse=AdaptiveMuse()
    with client(replace(settings,max_questions=5),muse) as c:
        first=start(c,upload(c,video))
        assert first['status']=='awaiting_answer' and first['question']['id']=='q1'
        assert muse.proposal_calls==1 and muse.histories==[[]]
        # Polling cannot advance to the next question before an answer.
        assert c.get('/api/projects/'+first['id']).json()['question']['id']=='q1'
        assert muse.proposal_calls==1
        second=answer(c,first,'natural')
        assert second['question']['id']=='q2'
        assert second['question']['question'].startswith('Keep colors restrained')
        assert muse.histories[1][0]['selected_outcome']=='Restrained treatment'
        assert muse.histories[1][0]['question']==first['question']['question']
        assert second['answered_count']==1 and muse.proposal_calls==2
        done=answer(c,second,'muted')
        assert done['status']=='brief_ready' and done['question'] is None
        assert done['answered_count']==2 and muse.analysis_calls==1
        assert done['consultation']['reason']=='no_candidates'
        assert 'Treatment strength' in done['consultation']['model_explanation']
        assert len(muse.histories[-1])==2


def test_zero_questions_has_explicit_saved_reason_without_filling_budget(settings,video):
    class ClearMuse(FakeMuse):
        def propose(self,session):
            self.proposal_calls+=1
            return Proposal(completion_reason='The request explicitly specifies the supported global treatment and preservation limits.')
    with client(replace(settings,max_questions=5),ClearMuse()) as c:
        state=start(c,upload(c,video),'Keep contrast and gamma neutral; reduce saturation slightly.')
        assert state['answered_count']==0 and state['max_questions']==5
        assert state['consultation']['reason']=='no_candidates'
        assert state['consultation']['model_explanation'].startswith('The request explicitly')
        assert state['model_calls']==2
        saved=c.get('/api/projects/'+state['id']).json()
        assert saved['consultation']==state['consultation']


def test_invalid_tool_contract_fails_explicitly_instead_of_completing_consultation(settings,video):
    muse=FakeMuse();muse.operation='unsupported_mask'
    with client(settings,muse) as c:
        state=start(c,upload(c,video))
        assert state['status']=='failed' and state['brief'] is None
        assert state['error']['code']=='RUBRIC_PROTOCOL_ERROR'
        assert 'unavailable' in state['error']['details']['contract_error']
        attempt=state['question_proposals'][-1]
        assert attempt['proposal']['candidates'][0]['id']=='q1'
        assert attempt['selected_id'] is None and attempt['error']
        muse.operation='global_grade'
        retried=c.post('/api/projects/'+state['id']+'/retry',json={'command_id':str(uuid4()),'revision':state['revision']})
        assert retried.status_code==202
        ready=wait(c,state['id'])
        assert ready['status']=='awaiting_answer' and muse.analysis_calls==1
        assert len(ready['question_proposals'])==2


def test_same_topic_followup_is_shown_and_full_proposals_survive_export_state(settings,video):
    class Followup(FakeMuse):
        def propose(self,session):
            if not session.answers:return super().propose(session)
            self.proposal_calls+=1
            first=session.questions[0]
            return Proposal(candidates=[Candidate.model_validate({**first.model_dump(mode='json'),
                'id':'followup','preference_family':'contrast_texture',
                'question':'Keep dark shadow detail with gentle contrast, or use punchier texture?',
            })])
    with client(settings,Followup()) as c:
        first=start(c,upload(c,video))
        second=answer(c,first,'natural')
        assert second['status']=='awaiting_answer' and second['question']['id']=='followup'
        assert second['question']['target_key']==first['question']['target_key']
        proposals=c.get('/api/projects/'+first['id']).json()['question_proposals']
        assert len(proposals)==2 and proposals[-1]['selected_id']=='followup'
        assert proposals[-1]['proposal']['candidates'][0]['question']==second['question']['question']
        assert all(p['selection_policy']=='muse_order' for p in proposals)


def test_question_limit_is_explained_without_another_call(settings,video):
    muse=FakeMuse()
    with client(replace(settings,max_questions=1),muse) as c:
        state=start(c,upload(c,video))
        done=answer(c,state,'natural')
        assert done['consultation']['reason']=='question_limit'
        assert muse.proposal_calls==1 and done['model_calls']==2
