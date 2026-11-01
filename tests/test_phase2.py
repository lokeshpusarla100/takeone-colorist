from dataclasses import replace
import base64
import json
from pathlib import Path
from types import SimpleNamespace
import threading
from uuid import uuid4

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from takeone.app import create_app
from takeone.config import Settings
from takeone.contracts import CreativeReview,GradeProposal,GradeParameters
from takeone.provider import MuseProvider
from takeone import evidence,color_processing,sam
from tests.test_backend import settings,start,upload
from tests.test_local_grading import scene,Planner,Masks,generate,done,parameters,pixels


@pytest.fixture(scope='module')
def dark_clip(tmp_path_factory):
    import subprocess
    path=tmp_path_factory.mktemp('dark-scene')/'dark.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=0x080808:s=192x108:r=12','-t','1',
        '-vf','setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709',
        '-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True)
    return path


def test_dark_footage_is_not_brightened_for_a_histogram_target(settings,dark_clip):
    with TestClient(create_app(settings,Planner(),Masks()),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,dark_clip),'explicit maintain a very dark atmosphere'))
        assert result['status']=='render_ready',result.get('error')
        candidate=result['candidates'][-1];output=settings.workspace/result['id']/'candidates'/candidate['id']/'grade.mp4'
        assert pixels(output).mean()<=pixels(dark_clip).mean()+2
        assert candidate['validation']['source_samples'][0]['median_encoded_luma']<.05
        assert candidate['validation']['review_status']=='needs_user_review'


def test_declared_clipping_bound_is_enforced_without_forcing_brightness(settings,dark_clip):
    class BlackPlanner(Planner):
        def grade(self,*a,**kw):
            p,u=super().grade(*a,**kw);p.parameters.black_point=.1
            p.intent.shadow_policy='intentional_black';p.execution_limits.max_new_black_fraction=0
            return p,u
    with TestClient(create_app(settings,BlackPlanner(),Masks()),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,dark_clip),'explicit preserve detail'))
        assert result['status']=='failed' and result['error']['code']=='GRADE_EXECUTION_LIMIT'


def test_intentional_black_regions_are_reported_without_aesthetic_rejection(settings,dark_clip):
    class Intentional(Planner):
        def grade(self,*a,**kw):
            p,u=super().grade(*a,**kw);p.parameters.black_point=.1;p.intent.shadow_policy='intentional_black'
            return p,u
    with TestClient(create_app(settings,Intentional(),Masks()),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,dark_clip),'explicit intentional black shadows'))
        assert result['status']=='render_ready',result.get('error')
        diagnostic=result['candidates'][-1]['validation']['diagnostics']
        assert max(p['new_black_fraction'] for p in diagnostic['paired_measurements'])>.5
        assert diagnostic['intent']['shadow_policy']=='intentional_black'
        assert result['candidates'][-1]['validation']['review_status']=='needs_user_review'


def test_luminance_tone_preserves_ratios_and_ocio_matches_reference():
    import PyOpenColorIO as ocio
    rgb=np.array([[.3,.08,.02],[.02,.02,.02],[1,1,1]],np.float32)
    p=GradeParameters(**parameters(gamma=1.1,contrast=1.1,brightness_gain=1.1))
    transformed=color_processing.apply_rgb(rgb,p,True)
    assert np.allclose(transformed[0]/transformed[0].sum(),rgb[0]/rgb[0].sum(),atol=1e-6)
    p=GradeParameters(**parameters(saturation=.6))
    expected=rgb.copy();cdl=ocio.CDLTransform();cdl.setSat(.6)
    ocio.Config.CreateRaw().getProcessor(cdl).getDefaultCPUProcessor().applyRGB(expected)
    assert np.allclose(color_processing.apply_rgb(rgb,p),expected,atol=1e-6)


def test_adaptive_selection_covers_shots_lighting_and_has_reproducible_cache(settings,scene,monkeypatch):
    source=SimpleNamespace(sha256=sam.checksum(scene),duration=1.)
    shots=[{'start':0,'end':.5},{'start':.5,'end':1}]
    selection=evidence.plan(scene,source,replace(settings,sample_count=4,sample_scan_frames=8),threading.Event(),shots=shots,cache=settings.workspace.parent/'evidence.json')
    assert selection['scanned_frames']<=8 and len(selection['selected_indices'])<=4
    times=[selection['manifest']['times'][i] for i in selection['selected_indices']]
    assert any(t<.5 for t in times) and any(t>=.5 for t in times)
    monkeypatch.setattr(evidence,'extract',lambda *_args,**_kwargs:(_ for _ in ()).throw(AssertionError('unchanged scan repeated')))
    cached=evidence.plan(scene,source,replace(settings,sample_count=4,sample_scan_frames=8),threading.Event(),shots=shots,cache=settings.workspace.parent/'evidence.json')
    assert cached['cache_hit'] and cached['selected_indices']==selection['selected_indices']
    features=[dict(frame_index=i,time=i,p05_encoded_luma=v,p95_encoded_luma=v,median_encoded_luma=v,temporal_change=0) for i,v in enumerate([.3,.01,.5,.99,.4])]
    chosen=evidence.select(features,[{'start':0,'end':5}],[],3)
    assert 1 in chosen and 3 in chosen


class ReviewPlanner(Planner):
    def __init__(self,decisions=None):super().__init__();self.review_calls=0;self.evidence_seen=[];self.decisions=decisions or ['revise','sufficient']
    def grade(self,*args,**kwargs):
        plan,usage=super().grade(*args,**kwargs)
        plan.review_required=True;plan.parameters=GradeParameters(**parameters(brightness_gain=.85))
        return plan,usage
    def review(self,brief,candidate,data,previous=None,feedback=()):
        self.review_calls+=1;self.evidence_seen.append(data)
        decision=self.decisions[min(self.review_calls-1,len(self.decisions)-1)]
        revision=candidate.recipe.proposal.model_copy(deep=True)
        revision.parameters=GradeParameters(**parameters(brightness_gain=.75 if self.review_calls==1 else .7))
        return CreativeReview(decision=decision,summary='Fixture creative opinion, not approval.',
            issues=[{'id':'brightness','description':'Background differs from requested darkness.','evidence_ids':['candidate:'+str(data['candidate_images'][0]['frame_index'])],'correctable':True}] if decision=='revise' else [],
            revision_plan=revision if decision=='revise' else None),{'prompt_tokens':10,'completion_tokens':5}


def test_review_uses_rendered_pixels_and_revision_preserves_original_and_prior(settings,scene):
    planner=ReviewPlanner()
    with TestClient(create_app(settings,planner,Masks()),base_url='http://127.0.0.1') as c:
        state=start(c,upload(c,scene),'explicit dark atmosphere')
        result=generate(c,state)
        assert result['status']=='render_ready',result.get('error')
        assert planner.grade_calls==1 and planner.review_calls==2 and len(result['candidates'])==2
        first,second=result['candidates'];assert second['parent_id']==first['id'] and second['iteration']==2
        root=settings.workspace/result['id'];assert sam.checksum(root/'source.bin')==state['source']['sha256']
        assert (root/'candidates'/first['id']/'grade.mp4').exists()
        assert (root/'candidates'/second['id']/'recipe.json').exists() and (root/'candidates'/second['id']/'plan.json').exists()
        assert second['validation']['review_status']=='needs_user_review'
        data=planner.evidence_seen[0]
        assert data['source_images'][0]['frame_index']==data['candidate_images'][0]['frame_index']
        decode=lambda u:cv2.imdecode(np.frombuffer(base64.b64decode(u.split(',')[1]),np.uint8),cv2.IMREAD_COLOR)
        assert np.abs(decode(data['source_images'][0]['url']).astype(float)-decode(data['candidate_images'][0]['url']).astype(float)).mean()>2
        again=c.post('/api/projects/'+state['id']+'/review',json={'command_id':str(uuid4()),'revision':result['revision'],'candidate_id':second['id']})
        assert again.status_code==202 and planner.review_calls==2


def test_unchanged_revision_does_not_render_again(settings,scene):
    class Same(ReviewPlanner):
        def review(self,brief,candidate,data,*args):
            self.review_calls+=1
            return CreativeReview(decision='revise',summary='Fixture',issues=[{'id':'a','description':'Same plan proposed','evidence_ids':['candidate:'+str(data['candidate_images'][0]['frame_index'])],'correctable':True}],
                revision_plan=candidate.recipe.proposal),{}
    planner=Same()
    with TestClient(create_app(settings,planner,Masks()),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit fixture'))
        assert len(result['candidates'])==1 and planner.review_calls==1
        assert result['candidates'][0]['revision_stop_reason']=='unchanged_or_repeated_execution_plan'


def test_invalid_technical_output_never_calls_critic(settings,scene,monkeypatch):
    from takeone import rendering
    from takeone.errors import Problem
    planner=ReviewPlanner()
    with TestClient(create_app(settings,planner,Masks()),base_url='http://127.0.0.1') as c:
        state=start(c,upload(c,scene),'explicit fixture')
        monkeypatch.setattr(rendering,'render',lambda *_args,**_kwargs:(_ for _ in ()).throw(Problem('RENDER_VALIDATION_FAILED','Fixture corrupt media','Inspect encoder output.',422)))
        result=generate(c,state)
        assert result['status']=='failed' and planner.review_calls==0


def test_current_request_epoch_excludes_old_feedback_but_preserves_evidence(settings,scene):
    with TestClient(create_app(settings,Planner(),Masks()),base_url='http://127.0.0.1') as c:
        state=start(c,upload(c,scene),'explicit first direction')
        feedback=c.post('/api/projects/'+state['id']+'/feedback',json={'command_id':str(uuid4()),'revision':state['revision'],
            'evidence':{'kind':'feedback','original_wording':'Old dark direction'}}).json()
        updated=start(c,feedback,'explicit new bright direction')
        assert updated['status']=='brief_ready'
        assert not any(d['outcome']=='Old dark direction' for d in updated['brief']['directives'])
        assert any(f['text']=='Old dark direction' for f in updated['feedback'])


def test_revision_budget_is_three_and_no_technical_approval_is_inferred(settings,scene):
    class Always(ReviewPlanner):
        def review(self,brief,candidate,data,*a):
            self.review_calls+=1
            plan=candidate.recipe.proposal.model_copy(deep=True);plan.parameters.brightness_gain=.85-self.review_calls*.05
            return CreativeReview(decision='revise',summary='Fixture still insufficient',issues=[{
                'id':'change','description':'Needs another supported adjustment','evidence_ids':['candidate:'+str(data['candidate_images'][0]['frame_index'])],
                'correctable':True}],revision_plan=plan),{}
    p=Always()
    with TestClient(create_app(settings,p,Masks()),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit look'))
        assert len(result['candidates'])==3 and p.grade_calls==1 and p.review_calls==3
        assert result['candidates'][-1]['revision_stop_reason']=='render_iteration_limit'
        assert all(x['validation']['review_status']=='needs_user_review' for x in result['candidates'])


def test_worse_reviewed_revision_keeps_previous_candidate_selected(settings,scene):
    class PreferPrevious(ReviewPlanner):
        def review(self,brief,candidate,data,previous=None,feedback=()):
            if previous:
                self.review_calls+=1
                return CreativeReview(decision='needs_user',summary='Prior version is preferable in the supplied pairs.',preferred_candidate_id=previous['id']),{}
            return super().review(brief,candidate,data,previous,feedback)
    p=PreferPrevious()
    with TestClient(create_app(settings,p,Masks()),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit look'))
        assert len(result['candidates'])==2 and result['selected_candidate_id']==result['candidates'][0]['id']


def test_review_failure_keeps_render_and_explicit_retry_is_bounded(settings,scene):
    from takeone.errors import Problem
    class Broken(ReviewPlanner):
        def review(self,*a):self.review_calls+=1;raise Problem('MUSE_TIMEOUT','Fixture review timeout','Retry creative review explicitly.',504)
    p=Broken()
    with TestClient(create_app(settings,p,Masks()),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit look'))
        assert result['status']=='render_ready' and result['candidates'][0]['status']=='ready'
        assert result['candidates'][0]['review_error']['code']=='MUSE_TIMEOUT'
        candidate=result['candidates'][0]
        for _ in range(2):
            r=c.post('/api/projects/'+result['id']+'/review',json={'command_id':str(uuid4()),'revision':result['revision'],'candidate_id':candidate['id']})
            assert r.status_code==202
            result=done(c,result['id'])
        assert p.review_calls==2 and len(result['candidates'])==1


def test_corrupted_review_candidate_is_rejected_before_model_call(settings,scene):
    p=ReviewPlanner(decisions=['sufficient'])
    with TestClient(create_app(settings,p,Masks()),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit look'));candidate=result['candidates'][0]
        path=settings.workspace/result['id']/'candidates'/candidate['id']/'grade.mp4';path.write_bytes(b'corrupted')
        r=c.post('/api/projects/'+result['id']+'/review',json={'command_id':str(uuid4()),'revision':result['revision'],'candidate_id':candidate['id']})
        assert r.status_code==409 and p.review_calls==1


def test_review_sdk_receives_actual_evidence_and_capture_only_gate_excludes_memory(settings,scene):
    from takeone_rubric_v1.rubric import Directive
    from takeone import creative_review
    captured={}
    with TestClient(create_app(settings,Planner(),Masks()),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit direction'))
        svc=c.app.state.service;state=svc.store.get(result['id']);candidate=state.candidates[-1]
        data,previous,_=creative_review.collect(svc,state,candidate,threading.Event())
        state.brief.directives.append(Directive(dimension='appearance',scope='clip',outcome='MEMORY_SENTINEL_CRITIC',
            provenance='memory_default',strength='optional',source_id='memory:private:1',verification_hint='Default'))
        def parse(**kw):
            captured.update(kw)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=CreativeReview(decision='needs_user',summary='Fixture opinion.')))],usage=None)
        class SDK:
            beta=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
            def __enter__(self):return self
            def __exit__(self,*_):pass
        provider=MuseProvider(settings,svc.prompt_gate);provider.client=lambda:SDK()
        provider.review(state.brief,candidate,data,previous,[])
        messages=captured['messages'];assert 'MEMORY_SENTINEL_CRITIC' not in json.dumps(messages)
        images=[p for p in messages[1]['content'] if p['type']=='image_url']
        assert len(images)>=2 and images[0]['image_url']['url'].startswith('data:image/jpeg;base64,')
        assert data['candidate_images'][0]['url'] in [p['image_url']['url'] for p in images]


def test_live_phase2_verifier_requires_explicit_opt_in(monkeypatch):
    from takeone import verify
    monkeypatch.setattr('sys.argv',['verify','--phase2'])
    with pytest.raises(SystemExit) as error:verify.main()
    assert error.value.code==2


def test_adaptive_math_covers_real_synthetic_lighting_cuts(settings,tmp_path):
    import subprocess
    from takeone import local_grading
    path=tmp_path/'lighting.mp4'
    frames=[np.full((108,192,3),level,np.uint8) for level in [8]*8+[220]*8+[64]*8]
    subprocess.run(['ffmpeg','-v','error','-y','-f','rawvideo','-pixel_format','bgr24','-video_size','192x108','-framerate','12','-i','-',
        '-vf','setparams=range=full:color_primaries=bt709:color_trc=bt709:colorspace=bt709,scale=in_range=pc:out_range=tv',
        '-c:v','libx264','-pix_fmt','yuv420p',str(path)],input=b''.join(f.tobytes() for f in frames),check=True)
    with TestClient(create_app(settings,Planner(),Masks()),base_url='http://127.0.0.1') as c:
        state=upload(c,path);service=c.app.state.service;source=service.store.get(state['id']).source
        directory=settings.workspace/state['id'];timeline=local_grading.timeline(directory,source,settings,threading.Event())
        budget=replace(settings,sample_scan_frames=12,sample_count=6)
        selection=evidence.plan(directory/'source.bin',source,budget,threading.Event(),proxy=directory/'analysis.mp4',shots=timeline['shots'])
        records,_=evidence.collect(directory/'source.bin',source,budget,threading.Event(),selection=selection)
        assert {8,16}<=set(timeline['cut_frames'])
        assert selection['scanned_frames']<=12 and len(records)<=6 and not selection['uncovered_shots']
        assert min(r['median_encoded_luma'] for r in records)<.1 and max(r['median_encoded_luma'] for r in records)>.8


def test_local_creative_review_includes_aligned_subject_regions(settings,scene):
    p=ReviewPlanner(decisions=['sufficient']);p.local=True
    with TestClient(create_app(settings,p,Masks()),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit local look'))
        assert result['status']=='render_ready',result.get('error')
        data=p.evidence_seen[0]
        assert data['source_region_images'] and data['candidate_region_images']
        assert {x['frame_index'] for x in data['source_region_images']}=={x['frame_index'] for x in data['candidate_region_images']}
        total=sum(len(i['url'].split(',')[1]) for key in ['source_images','candidate_images','source_region_images','candidate_region_images'] for i in data[key])
        assert total<=settings.sample_image_bytes


def test_automatic_revision_cannot_drop_existing_subject_protection(settings,scene):
    class DropProtection(ReviewPlanner):
        def review(self,brief,candidate,data,*_):
            self.review_calls+=1
            value=candidate.recipe.proposal.model_dump(mode='json');value['mode']='global';value['local']=None
            return CreativeReview(decision='revise',summary='Fixture unsupported fallback',issues=[{
                'id':'edge','description':'Change local treatment','evidence_ids':['candidate:'+str(data['candidate_images'][0]['frame_index'])],'correctable':True}],
                revision_plan=GradeProposal.model_validate(value)),{}
    p=DropProtection();p.local=True
    with TestClient(create_app(settings,p,Masks()),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit preserve subject'))
        assert len(result['candidates'])==1 and result['candidates'][0]['revision_stop_reason']=='revision_would_weaken_subject_protection'


def test_base_only_revision_is_skipped_when_local_plan_covers_every_frame(settings,scene):
    p=ReviewPlanner();p.local=True
    with TestClient(create_app(settings,p,Masks()),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit local look'))
        assert len(result['candidates'])==1 and p.review_calls==1
        assert result['candidates'][0]['revision_stop_reason']=='unchanged_or_repeated_execution_plan'


def test_cancellation_retains_completed_review_without_starting_revision(settings,scene):
    class Slow(ReviewPlanner):
        entered=threading.Event();release=threading.Event()
        def review(self,*args):
            self.entered.set();assert self.release.wait(5)
            return super().review(*args)
    p=Slow()
    with TestClient(create_app(settings,p,Masks()),base_url='http://127.0.0.1') as c:
        state=start(c,upload(c,scene),'explicit look')
        r=c.post('/api/projects/'+state['id']+'/render',json={'command_id':str(uuid4()),'revision':state['revision']})
        assert r.status_code==202 and p.entered.wait(5)
        c.post('/api/projects/'+state['id']+'/cancel');p.release.set()
        result=done(c,state['id'])
        assert result['status']=='cancelled' and len(result['candidates'])==1
        assert result['candidates'][0]['status']=='ready' and result['candidates'][0]['creative_review']
        retry=c.post('/api/projects/'+state['id']+'/retry',json={'command_id':str(uuid4()),'revision':result['revision']})
        assert retry.status_code==202
        result=done(c,state['id']);assert result['status']=='render_ready' and p.review_calls==1 and p.grade_calls==1


def test_derived_colour_lut_cache_detects_corruption(tmp_path,monkeypatch):
    p=GradeParameters(**parameters(cooling=.08))
    color_processing.lut(tmp_path,p)
    path=next(tmp_path.glob('*.cube'));original=sam.checksum(path)
    implementation=color_processing.apply_rgb
    monkeypatch.setattr(color_processing,'apply_rgb',lambda *_:(_ for _ in ()).throw(AssertionError('valid LUT recomputed')))
    color_processing.lut(tmp_path,p)
    path.write_text('invalid LUT')
    monkeypatch.setattr(color_processing,'apply_rgb',implementation)
    color_processing.lut(tmp_path,p)
    assert sam.checksum(path)==original
