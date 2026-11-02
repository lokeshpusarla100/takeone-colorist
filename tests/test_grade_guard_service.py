"""Small real-encoder service checks; providers remain local mocks."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import numpy as np
import pytest

from takeone import local_grading,rendering,sam
from takeone.app import create_app
from takeone.contracts import CreativeReview
from takeone.errors import Problem
from tests.test_backend import settings,start,upload
from tests.test_local_grading import Planner,Masks,generate,done
from tests.test_phase2 import dark_clip


class CutoffPlanner(Planner):
    def grade(self,*args,**kwargs):
        proposal,usage=super().grade(*args,**kwargs)
        proposal.parameters.black_point=.1
        proposal.execution_limits.max_new_black_fraction=0
        proposal.intent.shadow_policy='intentional_darkness'
        self.original_proposal=proposal.model_dump(mode='json')
        return proposal,usage


def test_correction_preserves_plan_bounds_brief_and_only_encodes_child(settings,dark_clip,monkeypatch):
    calls=[];actual=rendering.render
    def counted(*args,**kwargs):calls.append(args[2].model_copy(deep=True));return actual(*args,**kwargs)
    monkeypatch.setattr(rendering,'render',counted)
    planner=CutoffPlanner();masks=Masks()
    with TestClient(create_app(settings,planner,masks),base_url='http://127.0.0.1') as client:
        state=start(client,upload(client,dark_clip),'explicit preserve dark detail')
        result=generate(client,state)
        assert result['status']=='render_ready',result.get('error')
        parent,child=result['candidates']
        assert parent['recipe']['proposal']==planner.original_proposal
        assert child['parent_id']==parent['id'] and child['brief_sha256']==parent['brief_sha256']==result['brief_sha256']
        assert child['recipe']['proposal']['execution_limits']==parent['recipe']['proposal']['execution_limits']
        assert child['recipe']['proposal']['parameters']['black_point']==0
        assert child['recipe']['proposal']['parameters']['gamma']==parent['recipe']['proposal']['parameters']['gamma']
        assert child['recipe']['proposal']['parameters']['brightness_gain']==parent['recipe']['proposal']['parameters']['brightness_gain']
        original=settings.workspace/result['id']/'candidates'/parent['id']/'plan.json'
        assert json.loads(original.read_text())==planner.original_proposal
        assert len(calls)==1 and calls[0].proposal.parameters.black_point==0
        assert planner.grade_calls==1 and planner.analysis_calls==1 and planner.proposal_calls==1 and masks.calls==0


@pytest.fixture(scope='module')
def shadow_edge(tmp_path_factory):
    path=tmp_path_factory.mktemp('shadow-edge')/'edge.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=0x040404:s=192x108:r=12','-t','1',
        '-vf','setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709',
        '-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True)
    return path


def test_impossible_bound_fails_before_full_encode_without_brightening(settings,shadow_edge,monkeypatch):
    class Impossible(Planner):
        def grade(self,*args,**kwargs):
            proposal,usage=super().grade(*args,**kwargs)
            proposal.parameters.gamma=.8;proposal.parameters.brightness_gain=.65
            proposal.execution_limits.max_new_black_fraction=0
            return proposal,usage
    calls=[]
    def forbidden(*args,**kwargs):calls.append(True);raise AssertionError('Full encode must not start')
    monkeypatch.setattr(rendering,'render',forbidden)
    planner=Impossible();masks=Masks()
    with TestClient(create_app(settings,planner,masks),base_url='http://127.0.0.1') as client:
        result=generate(client,start(client,upload(client,shadow_edge),'explicit preserve dark detail'))
        assert result['status']=='failed' and result['error']['code']=='GRADE_PREFLIGHT_FAILED',result.get('error')
        candidate=result['candidates'][0]
        assert candidate['recipe']['source_samples'][0]['black_fraction']==0
        assert candidate['recipe']['proposal']['parameters']['gamma']==.8
        assert candidate['recipe']['proposal']['parameters']['brightness_gain']==.65
        assert candidate['recipe']['proposal']['execution_limits']['max_new_black_fraction']==0
        assert not calls and planner.grade_calls==2 and masks.calls==0
        assert client.app.state.service.store.get(result['id']).active_job is None
        again=client.post('/api/projects/'+result['id']+'/retry',json={'revision':result['revision'],'command_id':str(uuid4())})
        assert again.status_code==202
        assert done(client,result['id'])['status']=='failed'
        assert planner.grade_calls==2 # No new paid replan on repeated retry.


def test_incompatible_creative_controls_trigger_one_measured_muse_replan(settings,shadow_edge):
    class Repair(Planner):
        def grade(self,*args,**kwargs):
            proposal,usage=super().grade(*args,**kwargs)
            if self.grade_calls==1:
                proposal.parameters.gamma=.8;proposal.parameters.brightness_gain=.65
                proposal.execution_limits.max_new_black_fraction=0
            else:
                context=kwargs['technical_context']
                assert context['preflight_failure']['failed_plan']['parameters']['gamma']==.8
                assert context['preflight_failure']['evidence']['preflight']['encoded_checks']
                assert args[4] # Actual source visual evidence, not parameter-only advice.
                proposal.execution_limits.max_new_black_fraction=None # Cannot erase prior bound.
            return proposal,usage
    planner=Repair();masks=Masks()
    with TestClient(create_app(settings,planner,masks),base_url='http://127.0.0.1') as client:
        result=generate(client,start(client,upload(client,shadow_edge),'explicit preserve dark detail'))
        assert result['status']=='render_ready',result.get('error')
        parent,child=result['candidates']
        assert parent['status']=='failed' and child['parent_id']==parent['id']
        assert parent['recipe']['proposal']['parameters']['gamma']==.8
        assert child['recipe']['proposal']['parameters']['gamma']==1
        assert child['recipe']['proposal']['execution_limits']['max_new_black_fraction']==0
        assert parent['preflight_replan_attempted'] and child['preflight_replan_attempted']
        assert planner.grade_calls==2 and masks.calls==0


def test_correction_obeys_candidate_budget_before_full_encode(settings,dark_clip,monkeypatch):
    settings=replace(settings,max_render_iterations=1)
    calls=[]
    def forbidden(*args,**kwargs):calls.append(True);raise AssertionError('Full encode must not start')
    monkeypatch.setattr(rendering,'render',forbidden)
    planner=CutoffPlanner()
    with TestClient(create_app(settings,planner,Masks()),base_url='http://127.0.0.1') as client:
        result=generate(client,start(client,upload(client,dark_clip),'explicit preserve dark detail'))
        assert result['status']=='failed' and result['error']['code']=='RENDER_BUDGET',result.get('error')
        assert len(result['candidates'])==1 and result['candidates'][0]['recipe']['proposal']['parameters']['black_point']==.1
        assert not calls


def test_nonzero_local_start_is_normalized_by_mapping_not_rejected(settings,tmp_path,monkeypatch):
    # timeline() must not reject on the container start hint; it must ask
    # sam.mapping to verify the decoded frame sequences themselves.
    (tmp_path/'analysis.mp4').write_bytes(b'proxy')
    mapping={'frame_count':3,'proxy':{'rate':'12/1'},
             'frames':[{'index':i,'source_pts':2.0+i/12,'proxy_pts':i/12,'time':i/12} for i in range(3)],
             'normalization':{'method':'first_decoded_frame_pts','source_offset_seconds':2.0,'proxy_offset_seconds':0.0}}
    monkeypatch.setattr(sam,'mapping',lambda *args,**kwargs:mapping)
    monkeypatch.setattr(local_grading,'command',lambda *args,**kwargs:'')
    result=local_grading.timeline(tmp_path,SimpleNamespace(start_time=2.0,sha256='source'),settings,threading.Event())
    assert result['frame_map']['normalization']['source_offset_seconds']==2.0
    assert [f['time'] for f in result['frame_map']['frames']]==[0,1/12,2/12]
    assert result['shots'][0]['first_frame']==0


def test_critic_cannot_remove_same_brief_execution_limits(settings,dark_clip):
    class Critic(Planner):
        def __init__(self):super().__init__();self.review_calls=0
        def grade(self,*args,**kwargs):
            proposal,usage=super().grade(*args,**kwargs)
            proposal.review_required=True
            proposal.execution_limits.max_new_black_fraction=0
            proposal.execution_limits.max_new_white_fraction=.01
            return proposal,usage
        def review(self,brief,candidate,data,previous=None,feedback=()):
            self.review_calls+=1
            proposal=candidate.recipe.proposal.model_copy(deep=True)
            proposal.parameters.brightness_gain=1.01
            proposal.execution_limits.max_new_black_fraction=None
            proposal.execution_limits.max_new_white_fraction=1.
            return CreativeReview(decision='revise' if self.review_calls==1 else 'sufficient',summary='Fixture opinion.',
                issues=[{'id':'change','description':'Fixture targeted change.','evidence_ids':['candidate:'+str(data['candidate_images'][0]['frame_index'])],'correctable':True}] if self.review_calls==1 else [],
                revision_plan=proposal if self.review_calls==1 else None),{}
    planner=Critic()
    with TestClient(create_app(settings,planner,Masks()),base_url='http://127.0.0.1') as client:
        result=generate(client,start(client,upload(client,dark_clip),'explicit maintain dark atmosphere'))
        assert result['status']=='render_ready',result.get('error')
        assert len(result['candidates'])==2 and planner.grade_calls==1 and planner.review_calls==2
        first,second=result['candidates']
        assert second['recipe']['proposal']['execution_limits']==first['recipe']['proposal']['execution_limits']
        assert second['brief_sha256']==first['brief_sha256']


@pytest.fixture(scope='module')
def two_shadow_bands(tmp_path_factory):
    path=tmp_path_factory.mktemp('two-shadows')/'bands.mp4'
    frame=np.full((108,192,3),120,np.uint8);frame[:6]=9;frame[6:12]=20
    subprocess.run(['ffmpeg','-v','error','-y','-f','rawvideo','-pixel_format','rgb24','-video_size','192x108',
        '-framerate','12','-i','-','-vf','scale=in_range=pc:out_range=tv,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709',
        '-c:v','libx264','-crf','14','-pix_fmt','yuv420p',str(path)],input=frame.tobytes()*12,check=True)
    return path


def test_dense_failure_gets_one_neutral_level_fallback_within_three_candidates(settings,two_shadow_bands,monkeypatch):
    class BandsPlanner(CutoffPlanner):
        def grade(self,*args,**kwargs):
            proposal,usage=super().grade(*args,**kwargs)
            proposal.execution_limits.max_new_black_fraction=.06
            self.original_proposal=proposal.model_dump(mode='json')
            return proposal,usage
    calls=[];actual=rendering.render
    def dense(*args,**kwargs):
        recipe=args[2];calls.append(recipe.model_copy(deep=True))
        if len(calls)==1:
            assert recipe.execution_preflight and 0<recipe.proposal.parameters.black_point<.1
            raise Problem('GRADE_EXECUTION_LIMIT','Fixture dense context exceeded the sparse probe.',
                          'Keep the original bound.',422,frame_index=1)
        return actual(*args,**kwargs)
    monkeypatch.setattr(rendering,'render',dense)
    planner=BandsPlanner();masks=Masks()
    with TestClient(create_app(settings,planner,masks),base_url='http://127.0.0.1') as client:
        result=generate(client,start(client,upload(client,two_shadow_bands),'explicit preserve readable dark bands'))
        assert result['status']=='render_ready',result.get('error')
        first,second,third=result['candidates']
        assert [c['status'] for c in result['candidates']]==['failed','failed','ready']
        assert second['parent_id']==first['id'] and third['parent_id']==second['id']
        assert third['recipe']['proposal']['parameters']['black_point']==0
        assert third['recipe']['proposal']['parameters']['white_point']==1
        assert all(c['recipe']['proposal']['execution_limits']['max_new_black_fraction']==.06 for c in result['candidates'])
        assert len(calls)==2 and planner.grade_calls==1 and masks.calls==0


def test_retry_of_known_neutral_dense_failure_fails_before_another_encode(settings,dark_clip,monkeypatch):
    class Neutral(Planner):
        def grade(self,*args,**kwargs):
            proposal,usage=super().grade(*args,**kwargs)
            proposal.execution_limits.max_new_black_fraction=.03
            return proposal,usage
    calls=[]
    def dense(*args,**kwargs):
        calls.append(True)
        raise Problem('GRADE_EXECUTION_LIMIT','Fixture dense clipping failure.',
                      'Revise creative controls.',422,frame_index=1)
    monkeypatch.setattr(rendering,'render',dense)
    planner=Neutral()
    with TestClient(create_app(settings,planner,Masks()),base_url='http://127.0.0.1') as client:
        failed=generate(client,start(client,upload(client,dark_clip),'explicit dark unchanged endpoints'))
        assert failed['status']=='failed' and failed['error']['code']=='GRADE_EXECUTION_LIMIT'
        assert failed['candidates'][0]['recipe']['execution_preflight']
        response=client.post('/api/projects/'+failed['id']+'/retry',json={'revision':failed['revision'],'command_id':str(uuid4())})
        assert response.status_code==202
        result=done(client,failed['id'])
        assert result['status']=='failed' and result['error']['code']=='GRADE_PREFLIGHT_FAILED',result.get('error')
        assert len(calls)==1 and len(result['candidates'])==1 and planner.grade_calls==1
        assert result['candidates'][0]['recipe']['proposal']['execution_limits']['max_new_black_fraction']==.03


# ---- Highlight feasibility gate, end to end -------------------------------------------------

@pytest.fixture(scope='module')
def hazy_sky_clip(tmp_path_factory):
    path=tmp_path_factory.mktemp('hazy-sky')/'hazy.mp4'
    frame=np.full((108,192,3),120,np.uint8);frame[:44]=np.linspace(205,235,192,dtype=np.uint8)[None,:,None]
    subprocess.run(['ffmpeg','-v','error','-y','-f','rawvideo','-pixel_format','rgb24','-video_size','192x108',
        '-framerate','12','-i','-','-vf','scale=in_range=pc:out_range=tv,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709',
        '-c:v','libx264','-crf','14','-pix_fmt','yuv420p',str(path)],input=frame.tobytes()*12,check=True)
    return path


SUBDUE=dict(contrast=1.25,saturation=1.,gamma=.8,brightness_gain=.78,cooling=.15,black_point=.05,white_point=.88)
GENTLE=dict(contrast=1.,saturation=1.,gamma=.85,brightness_gain=.85,cooling=.05,black_point=0.,white_point=1.)


def set_controls(proposal,values):
    for key,value in values.items():setattr(proposal.parameters,key,value)
    proposal.intent.highlight_policy='preserve_detail'
    return proposal


class SkyPlanner(Planner):
    """First plan tries to subdue a pale sky through white_point/contrast; later plans follow `second`."""
    def __init__(self,second=None,unfulfilled=()):super().__init__();self.second=second or GENTLE;self.unfulfilled=list(unfulfilled)
    def grade(self,*args,**kwargs):
        proposal,usage=super().grade(*args,**kwargs)
        if self.grade_calls==1:set_controls(proposal,SUBDUE)
        else:
            set_controls(proposal,self.second);proposal.unfulfilled_requests=self.unfulfilled
        return proposal,usage


def test_highlight_lifting_plan_is_replanned_once_with_measured_cause_before_any_encode(settings,hazy_sky_clip,monkeypatch):
    calls=[];actual=rendering.render
    def counted(*args,**kwargs):calls.append(args[2].model_copy(deep=True));return actual(*args,**kwargs)
    monkeypatch.setattr(rendering,'render',counted)
    planner=SkyPlanner();masks=Masks()
    with TestClient(create_app(settings,planner,masks),base_url='http://127.0.0.1') as client:
        result=generate(client,start(client,upload(client,hazy_sky_clip),'explicit dark moody sky, controlled highlights'))
        assert result['status']=='render_ready',result.get('error')
        parent,child=result['candidates']
        assert parent['status']=='failed' and parent['error']['code']=='GRADE_PREFLIGHT_FAILED'
        assert parent['recipe']['proposal']['parameters']['white_point']==.88 # the original plan is preserved, not edited
        assert child['parent_id']==parent['id'] and child['recipe']['proposal']['parameters']['white_point']==1
        assert len(calls)==1 and calls[0].proposal.parameters.white_point==1 # only the repaired plan was encoded
        failure=planner.contexts[1]['preflight_failure']
        assert 'BRIGHTEN' in failure['instruction'] and 'unfulfilled_requests' in failure['instruction']
        measured=failure['evidence']['preflight']['math']['highlights']
        assert measured['violations']==['parameters'] and 'white_point' in measured['layers']['parameters']['drivers']
        assert planner.grade_calls==2 and masks.calls==0


def test_unattainable_darker_highlights_are_reported_as_unfulfilled_without_rendering(settings,hazy_sky_clip,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('Full encode must not start')
    monkeypatch.setattr(rendering,'render',forbidden)
    planner=SkyPlanner(unfulfilled=['Dark steel-blue sky from a near-white sky cannot be produced by the supported controls.'])
    with TestClient(create_app(settings,planner,Masks()),base_url='http://127.0.0.1') as client:
        result=generate(client,start(client,upload(client,hazy_sky_clip),'explicit dark moody sky'))
        assert result['status']=='failed' and result['error']['code']=='GRADE_PREFLIGHT_FAILED',result.get('error')
        details=result['error']['details']
        assert 'Dark steel-blue sky' in details['unfulfilled_instructions'] and 'Dark steel-blue sky' in result['error']['action']
        assert planner.grade_calls==2 and len(result['candidates'])==1
        again=client.post('/api/projects/'+result['id']+'/retry',json={'revision':result['revision'],'command_id':str(uuid4())})
        assert again.status_code==202 and done(client,result['id'])['status']=='failed'
        assert planner.grade_calls==2 # a repeated retry makes no new paid planning call


def test_a_replan_that_still_lifts_highlights_fails_instead_of_looping(settings,hazy_sky_clip,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('Full encode must not start')
    monkeypatch.setattr(rendering,'render',forbidden)
    planner=SkyPlanner(second=dict(SUBDUE,white_point=.85))
    with TestClient(create_app(settings,planner,Masks()),base_url='http://127.0.0.1') as client:
        result=generate(client,start(client,upload(client,hazy_sky_clip),'explicit dark moody sky'))
        assert result['status']=='failed' and result['error']['code']=='GRADE_PREFLIGHT_FAILED'
        assert planner.grade_calls==2 and len(result['candidates'])==2 # one replan, never a third attempt


def test_highlight_intent_declared_as_lift_is_rendered_not_blocked(settings,hazy_sky_clip):
    class HighKey(SkyPlanner):
        def grade(self,*args,**kwargs):
            proposal,usage=Planner.grade(self,*args,**kwargs)
            set_controls(proposal,SUBDUE);proposal.intent.highlight_policy='intentional_lift'
            return proposal,usage
    planner=HighKey()
    with TestClient(create_app(settings,planner,Masks()),base_url='http://127.0.0.1') as client:
        result=generate(client,start(client,upload(client,hazy_sky_clip),'explicit high contrast bright sky is fine'))
        assert result['status']=='render_ready',result.get('error')
        assert len(result['candidates'])==1 and planner.grade_calls==1


def test_critic_revision_that_would_lift_highlights_stops_cleanly_and_keeps_the_candidate(settings,hazy_sky_clip,monkeypatch):
    calls=[];actual=rendering.render
    def counted(*args,**kwargs):calls.append(True);return actual(*args,**kwargs)
    monkeypatch.setattr(rendering,'render',counted)
    class Critic(SkyPlanner):
        def __init__(self):super().__init__();self.review_calls=0
        def grade(self,*args,**kwargs):
            proposal,usage=Planner.grade(self,*args,**kwargs)
            set_controls(proposal,GENTLE);proposal.review_required=True
            return proposal,usage
        def review(self,brief,candidate,data,previous=None,feedback=()):
            self.review_calls+=1
            revision=candidate.recipe.proposal.model_copy(deep=True);set_controls(revision,SUBDUE)
            return CreativeReview(decision='revise',summary='Sky still looks bright; lower the white point.',
                issues=[{'id':'sky','description':'Sky is not subdued.','evidence_ids':['candidate:'+str(data['candidate_images'][0]['frame_index'])],
                         'correctable':True}],revision_plan=revision),{}
    planner=Critic()
    with TestClient(create_app(settings,planner,Masks()),base_url='http://127.0.0.1') as client:
        result=generate(client,start(client,upload(client,hazy_sky_clip),'explicit dark moody sky'))
        assert result['status']=='render_ready',result.get('error')
        assert len(result['candidates'])==1 and len(calls)==1 and planner.review_calls==1 and planner.grade_calls==1
        first=result['candidates'][0]
        assert first['status']=='ready' and first['revision_stop_reason']=='revision_would_lift_highlights'
        assert result['selected_candidate_id']==first['id']
        event=next(e for e in result['events'] if e['title']=='Revision not rendered')
        assert 'white_point' in event['reason'] and event['details']['violating_layers']=='parameters'


def test_highlight_ceiling_darkens_the_rendered_sky_through_the_real_lut(settings,hazy_sky_clip):
    import cv2
    class SkyDown(Planner):
        def grade(self,*args,**kwargs):
            proposal,usage=super().grade(*args,**kwargs)
            set_controls(proposal,dict(GENTLE,contrast=1.15,highlight_ceiling=.62))
            return proposal,usage
    with TestClient(create_app(settings,SkyDown(),Masks()),base_url='http://127.0.0.1') as client:
        result=generate(client,start(client,upload(client,hazy_sky_clip),'explicit dark subdued sky'))
        assert result['status']=='render_ready',result.get('error')
        candidate=result['candidates'][0]
        assert candidate['recipe']['proposal']['parameters']['highlight_ceiling']==.62
        def frame(path):
            video=cv2.VideoCapture(str(path));ok,image=video.read();video.release();assert ok
            return image.astype(np.float32)[...,::-1]/255
        from takeone.color_processing import apply_rgb
        from takeone.contracts import GradeParameters
        weights=np.array([.2126,.7152,.0722],np.float32)
        source=frame(hazy_sky_clip);rendered=frame(settings.workspace/result['id']/'candidates'/candidate['id']/'grade.mp4')
        predicted=apply_rgb(source,GradeParameters(**candidate['recipe']['proposal']['parameters']))
        sky,ground=(slice(4,40),slice(None)),(slice(60,104),slice(None))
        before,after,model=source@weights,rendered@weights,predicted@weights
        for region in [sky,ground]: # the encoded LUT render matches the operator, within 8-bit/yuv420 encoding error
            assert abs(np.median(after[region])-np.median(model[region]))<.02
        sky_drop=np.median(before[sky])-np.median(after[sky]);ground_drop=np.median(before[ground])-np.median(after[ground])
        assert np.median(before[sky])>.8 and sky_drop>.25 and sky_drop>ground_drop+.1 # the pale sky darkens most
        assert np.ptp(np.quantile(after[sky],[.05,.95]))>.25*np.ptp(np.quantile(before[sky],[.05,.95])) # sky gradient survives
