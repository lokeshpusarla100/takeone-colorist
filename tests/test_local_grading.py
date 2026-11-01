import json
from pathlib import Path
import subprocess
import time
import threading
from uuid import uuid4

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from takeone.app import create_app
from takeone.contracts import GradeProposal
from takeone.errors import Problem
from takeone import sam,local_grading,rendering
from tests.test_backend import FakeMuse,settings,upload,start
from tests.test_sam import parsed


@pytest.fixture(scope='module')
def scene(tmp_path_factory):
    path=tmp_path_factory.mktemp('local-scene')/'scene.mp4'
    frames=[]
    for i in range(12):
        frame=np.full((108,192,3),120,np.uint8)
        frame[24:84,60:132]=[25,35,190] # BGR red subject, stable mask.
        frames.append(frame)
    subprocess.run(['ffmpeg','-v','error','-y','-f','rawvideo','-pixel_format','bgr24','-video_size','192x108',
        '-framerate','12','-i','-','-an','-vf','setparams=range=full:color_primaries=bt709:color_trc=bt709:colorspace=bt709,scale=in_range=pc:out_range=tv',
        '-c:v','libx264','-crf','14','-pix_fmt','yuv420p','-color_range','tv','-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709',str(path)],
        input=b''.join(f.tobytes() for f in frames),check=True)
    return path


def parameters(**changes):return {**dict(contrast=1.,saturation=1.,gamma=1.,brightness_gain=1.,cooling=0.),**changes}


class Planner(FakeMuse):
    def __init__(self,local=False,end=1.,begin=0,fore_gain=1.):super().__init__();self.local=local;self.end=end;self.begin=begin;self.fore_gain=fore_gain;self.grade_calls=0;self.briefs=[];self.contexts=[]
    def grade(self,brief,analysis,source,stats,images,technical_context=None):
        self.grade_calls+=1;self.briefs.append(brief.model_copy(deep=True));self.contexts.append(technical_context)
        local={'concept':'red subject','selection':'single_instance','intervals':[{'start':self.begin,'end':self.end}],
               'foreground':parameters(brightness_gain=self.fore_gain),'background':parameters(brightness_gain=.7,cooling=.12,saturation=.9),
               'preserve_subject_color':True,'feather_px':1.}
        return GradeProposal(mode='localized' if self.local else 'global',parameters=parameters(),local=local if self.local else None,
            summary='Fixture localized treatment' if self.local else 'Fixture global correction',
            decisions=[{'parameter':'local_treatment' if self.local else 'contrast','reason':'Fixture plan','source_ids':['user_request']}],limitations=[]),{}


class Masks:
    def __init__(self,gap=False,invalid=False,jitter=False,ambiguous=False):
        self.calls=0;self.gap=gap;self.invalid=invalid;self.jitter=jitter;self.ambiguous=ambiguous;self.input_frames=[]
    def available(self):return True
    def segment(self,path,concept,remote,remember,cancel,progress,raw_path,on_usage,on_request):
        self.calls+=1;on_request();remember('fixture-upload')
        from takeone.config import Settings
        info=sam.frame_info(path,Settings(path.parent),cancel);self.input_frames.append(len(info['pts']))
        from meta_sam_parser._mask_codec import _encode_raster
        payload=_encode_raster(bytes([1])*72*60,72,60)
        lines=[]
        for i in range(len(info['pts'])):
            if self.gap and i==2:continue
            left=999 if self.invalid else 5 if self.jitter and i%2 else 60
            right=131 if self.invalid else left+71
            record=f'<|box;x1={left};y1=24;x2={right};y2=83;w=192;h=108|><|mask;x=0;y=0;data=60,72,{payload}|>'
            lines.append(f'<{i}f>0'+record+(',1'+record if self.ambiguous else ''))
        result=parsed(lines);raw_path.write_text(result.raw_output);progress(result);on_usage({'video_frames_processed':len(info['pts'])})
        return result


def done(c,id):
    for _ in range(1500):
        state=c.get('/api/projects/'+id).json()
        if state['status'] not in {'rendering','analyzing','segmenting','ingesting','reviewing'}:return state
        time.sleep(.02)
    raise AssertionError('Fixture job did not finish')


def generate(c,state):
    result=c.post('/api/projects/'+state['id']+'/render',json={'command_id':str(uuid4()),'revision':state['revision']})
    assert result.status_code==202,result.text
    return done(c,state['id'])


def pixels(path,index=3):
    video=cv2.VideoCapture(str(path));video.set(cv2.CAP_PROP_POS_FRAMES,index);ok,frame=video.read();video.release();assert ok
    return frame.astype(float)


def test_global_only_does_not_call_sam(settings,scene):
    planner=Planner();masks=Masks()
    with TestClient(create_app(settings,planner,masks),base_url='http://127.0.0.1') as c:
        state=start(c,upload(c,scene),'explicit increase overall contrast')
        result=generate(c,state)
        assert result['status']=='render_ready',result.get('error')
        assert masks.calls==0 and not result['segmentations']
        assert result['candidates'][-1]['validation']['mode']=='global'


def test_localized_pixels_differ_and_subject_is_preserved_with_cache_reuse(settings,scene):
    planner=Planner(local=True);masks=Masks()
    with TestClient(create_app(settings,planner,masks),base_url='http://127.0.0.1') as c:
        state=start(c,upload(c,scene),'explicit dark cool background, preserve red subject')
        result=generate(c,state)
        assert result['status']=='render_ready',result.get('error')
        assert masks.calls==1 and result['candidates'][-1]['mask_run_ids']
        output=settings.workspace/state['id']/'candidates'/result['candidates'][-1]['id']/'grade.mp4'
        before,after=pixels(scene),pixels(output)
        assert np.abs(after[35:72,75:118]-before[35:72,75:118]).mean()<4
        assert after[10:20,10:40].mean()<before[10:20,10:40].mean()-10
        assert after[10:20,10:40,0].mean()>after[10:20,10:40,2].mean()+5 # cooler background
        validation=result['candidates'][-1]['validation']
        assert validation['source_frames']==validation['output_frames']=='12'
        assert validation['subject_protection']['checked_subject_frames']==12
        assert validation['review_status']=='needs_user_review'
        again=generate(c,result)
        assert again['status']=='render_ready',again.get('error')
        assert masks.calls==1 and len(again['candidates'])==2


@pytest.mark.parametrize('invalid',[False,True])
def test_invalid_or_missing_masks_cannot_publish_a_localized_candidate(settings,scene,invalid):
    with TestClient(create_app(settings,Planner(local=True),Masks(gap=not invalid,invalid=invalid)),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit local treatment'))
        assert result['status']=='failed' and result['candidates'][-1]['status']=='failed'
        assert result['candidates'][-1]['preview_url'] is None
        assert result['error']['code'] in {'LOCAL_MASK_GAP','SAM_INCOMPLETE'}


def test_latest_feedback_is_part_of_next_candidate_plan(settings,scene):
    planner=Planner();masks=Masks()
    with TestClient(create_app(settings,planner,masks),base_url='http://127.0.0.1') as c:
        state=start(c,upload(c,scene),'explicit initial global correction')
        first=generate(c,state);planner.local=True
        text='Make surroundings dark and cold while keeping the red subject visible.'
        feedback=c.post('/api/projects/'+state['id']+'/feedback',json={'command_id':str(uuid4()),'revision':first['revision'],
            'evidence':{'kind':'feedback','original_wording':text}})
        assert feedback.status_code==200
        second=generate(c,feedback.json())
        assert second['status']=='render_ready',second.get('error')
        assert any(d.outcome==text for d in planner.briefs[-1].directives)
        assert planner.contexts[-1]['current_video_feedback'][-1]['text']==text
        assert second['candidates'][-1]['brief_sha256']!=second['candidates'][0]['brief_sha256']
        assert masks.calls==1


def test_sam_and_ffmpeg_errors_are_actionable_without_fallback(settings,scene,monkeypatch):
    class Broken(Masks):
        def segment(self,*args):raise Problem('SAM_API_FAILED','Fixture SAM failure','Check SAM access and retry explicitly.',502)
    with TestClient(create_app(settings,Planner(local=True),Broken()),base_url='http://127.0.0.1') as c:
        state=start(c,upload(c,scene),'explicit local')
        result=generate(c,state)
        assert result['error']['code']=='SAM_API_FAILED' and result['error']['action']
        assert result['candidates'][-1]['preview_url'] is None
    with TestClient(create_app(settings,Planner(),Masks()),base_url='http://127.0.0.1') as c:
        # Existing workspace persists; use another source project.
        state=start(c,upload(c,scene),'explicit global')
        monkeypatch.setattr(rendering,'render',lambda *_:(_ for _ in ()).throw(Problem('MEDIA_TOOL_FAILED','Fixture FFmpeg error','Inspect encoder diagnostic; retry saved recipe.',422)))
        result=generate(c,state)
        assert result['error']['code']=='MEDIA_TOOL_FAILED' and result['error']['action']


def test_bounded_curve_keeps_dark_detail_with_previous_problem_parameters(settings,scene):
    from takeone.contracts import GradeParameters
    p=GradeParameters(contrast=1.12,saturation=1.28,gamma=1.03)
    text=rendering.treatment_filter(p)
    assert '-0.5' not in text
    # Real pixels exercise the curve independently of model decisions.
    source=settings.workspace.parent/'dark.mp4';output=source.with_name('safe.mp4')
    command=['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=0x0a0a0a:s=64x64:r=12','-t','1','-c:v','libx264',str(source)]
    subprocess.run(command,check=True)
    subprocess.run(['ffmpeg','-v','error','-y','-i',str(source),'-vf','format=gbrp16le,'+text,'-c:v','libx264','-pix_fmt','yuv420p',str(output)],check=True)
    assert pixels(output).mean()>1


def test_only_requested_middle_interval_is_segmented_and_changed(settings,scene):
    masks=Masks()
    with TestClient(create_app(settings,Planner(local=True,begin=.25,end=.75),masks),base_url='http://127.0.0.1') as c:
        state=start(c,upload(c,scene),'explicit local middle interval')
        result=generate(c,state)
        assert result['status']=='render_ready',result.get('error')
        assert masks.input_frames==[6]
        candidate=result['candidates'][-1];output=settings.workspace/state['id']/'candidates'/candidate['id']/'grade.mp4'
        for i in [1,10]:assert np.abs(pixels(output,i)-pixels(scene,i)).mean()<4
        assert pixels(output,5)[10:20,10:40].mean()<pixels(scene,5)[10:20,10:40].mean()-10
        manifest=json.loads((settings.workspace/state['id']/'segmentations'/candidate['mask_run_ids'][0]/'manifest.json').read_text())
        assert [f['index'] for f in manifest['frame_map']['frames']]==list(range(3,9))


def test_shot_cut_splits_tracking_and_resets_refinement(settings,scene,tmp_path):
    # A strong luminance cut, retaining the same subject geometry on both sides.
    capture=cv2.VideoCapture(str(scene));frames=[]
    for i in range(12):
        ok,frame=capture.read();assert ok
        if i>=6:
            frame[:24]=210;frame[84:]=210;frame[:, :60]=210;frame[:,132:]=210
        frames.append(frame)
    capture.release();cut=tmp_path/'cuts.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','rawvideo','-pixel_format','bgr24','-video_size','192x108','-framerate','12','-i','-',
        '-vf','setparams=range=full:color_primaries=bt709:color_trc=bt709:colorspace=bt709,scale=in_range=pc:out_range=tv',
        '-c:v','libx264','-pix_fmt','yuv420p','-color_range','tv','-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709',str(cut)],input=b''.join(f.tobytes() for f in frames),check=True)
    masks=Masks()
    with TestClient(create_app(settings,Planner(local=True),masks),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,cut),'explicit full interval with cut'))
        assert result['status']=='render_ready',result.get('error')
        assert masks.calls==2 and masks.input_frames==[6,6]
        assert 6 in result['candidates'][-1]['validation']['mask_validation']['cut_frames']


@pytest.mark.parametrize('flag,code',[('jitter','LOCAL_MASK_UNSTABLE'),('ambiguous','LOCAL_SUBJECT_AMBIGUOUS')])
def test_unreliable_or_ambiguous_masks_stop_local_render(settings,scene,flag,code):
    with TestClient(create_app(settings,Planner(local=True),Masks(**{flag:True})),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit local'))
        assert result['status']=='failed' and result['error']['code']==code
        assert result['candidates'][-1]['preview_url'] is None


def test_deleted_cached_mask_fails_without_retracking_or_changing_previous_version(settings,scene):
    masks=Masks()
    with TestClient(create_app(settings,Planner(local=True),masks),base_url='http://127.0.0.1') as c:
        first=generate(c,start(c,upload(c,scene),'explicit local'))
        assert first['status']=='render_ready',first.get('error')
        directory=settings.workspace/first['id'];old=directory/'candidates'/first['candidates'][0]['id']/'grade.mp4'
        before=sam.checksum(old)
        mask=next((directory/'segmentations').glob('*/masks/*.png'));mask.unlink()
        second=generate(c,first)
        assert second['status']=='failed' and second['error']['code']=='LOCAL_MASK_MISSING'
        assert masks.calls==1 and sam.checksum(old)==before


def test_protected_subject_gain_lifts_detail_without_recoloring(settings,scene):
    with TestClient(create_app(settings,Planner(local=True,fore_gain=1.1),Masks()),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit local gain'))
        assert result['status']=='render_ready',result.get('error')
        output=settings.workspace/result['id']/'candidates'/result['candidates'][-1]['id']/'grade.mp4'
        before,after=pixels(scene)[40:70,80:110],pixels(output)[40:70,80:110]
        assert after.mean()>before.mean()+3
        a=after/after.sum(axis=2,keepdims=True);b=before/before.sum(axis=2,keepdims=True)
        assert np.abs(a-b).mean()<.02


def test_region_adjustment_can_protect_unmasked_buildings_or_other_background(settings,scene):
    class RegionPlanner(Planner):
        def grade(self,*args,**kw):
            proposal,usage=super().grade(*args,**kw)
            value=proposal.model_dump(mode='json');value['local']['preserve_subject_color']=False
            value['local']['foreground']=parameters(brightness_gain=.7,cooling=.1)
            value['local']['background']=parameters()
            return GradeProposal.model_validate(value),usage
    with TestClient(create_app(settings,RegionPlanner(local=True),Masks()),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit adjust selected region, preserve surroundings'))
        assert result['status']=='render_ready',result.get('error')
        output=settings.workspace/result['id']/'candidates'/result['candidates'][-1]['id']/'grade.mp4'
        before,after=pixels(scene),pixels(output)
        assert np.abs(after[10:20,10:40]-before[10:20,10:40]).mean()<4
        assert np.abs(after[40:70,80:110]-before[40:70,80:110]).mean()>5
        assert result['candidates'][-1]['validation']['subject_protection']['background_checked_frames']==12


def test_declared_unfulfilled_request_never_renders_global_approximation(settings,scene):
    class UnsupportedPlanner(Planner):
        def grade(self,*args,**kwargs):
            plan,usage=super().grade(*args,**kwargs)
            plan.unfulfilled_requests=['Physical relighting is unavailable.']
            return plan,usage
    masks=Masks()
    with TestClient(create_app(settings,UnsupportedPlanner(),masks),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit unsupported request'))
        assert result['status']=='failed' and result['error']['code']=='GRADE_REQUEST_UNFULFILLED'
        assert result['candidates'][-1]['preview_url'] is None and masks.calls==0


def test_unavailable_sam_stops_required_local_plan(settings,scene):
    class Unavailable(Masks):
        def available(self):return False
    masks=Unavailable()
    with TestClient(create_app(settings,Planner(local=True),masks),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit local required'))
        assert result['status']=='failed' and result['error']['code']=='SAM_NOT_CONFIGURED'
        assert masks.calls==0 and result['segmentations'][-1]['status']=='failed'


def test_legacy_recipe_keeps_its_original_operator():
    from takeone.contracts import GradeRecipe
    plan=GradeProposal(parameters=parameters(contrast=1.12),summary='Legacy fixture',decisions=[
        {'parameter':'contrast','reason':'Fixture','source_ids':['user_request']}],limitations=[])
    old=GradeRecipe(source_sha256='source',brief_sha256='brief',model='fixture',proposal=plan,source_samples=[])
    assert old.tone_operator=='legacy_fixed_pivot' and '-0.5' in rendering.filter_graph(plan.parameters,recipe=old)
    new=old.model_copy(update={'tone_operator':'bounded_sigmoid_v2','schema_version':'takeone-grade-v2'})
    assert '-0.5' not in rendering.filter_graph(plan.parameters,recipe=new)


@pytest.mark.parametrize('case',['same_layers','protected_colour_change','mode_mismatch'])
def test_local_contract_rejects_unexecutable_or_unnecessary_plans(case):
    from pydantic import ValidationError
    local={'concept':'subject','intervals':[{'start':0,'end':1}],
           'foreground':parameters(),'background':parameters(brightness_gain=.7),'preserve_subject_color':True}
    if case=='same_layers':local['background']=parameters()
    if case=='protected_colour_change':local['foreground']=parameters(saturation=1.2)
    with pytest.raises(ValidationError):
        GradeProposal(mode='global' if case=='mode_mismatch' else 'localized',parameters=parameters(),local=local,
            summary='Invalid fixture',decisions=[{'parameter':'local_treatment','reason':'Fixture','source_ids':['user_request']}],limitations=[])


def test_subtle_local_changes_are_measured_without_universal_aesthetic_failure(settings,scene):
    class TinyPlanner(Planner):
        def grade(self,*args,**kwargs):
            plan,usage=super().grade(*args,**kwargs)
            value=plan.model_dump(mode='json');value['local']['background']=parameters(brightness_gain=.9999)
            return GradeProposal.model_validate(value),usage
    with TestClient(create_app(settings,TinyPlanner(local=True),Masks()),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit local tiny fixture'))
        assert result['status']=='render_ready',result.get('error')
        assert result['candidates'][-1]['validation']['review_status']=='needs_user_review'
        path=settings.workspace/result['id']/'candidates'/result['candidates'][-1]['id']/'validation.json'
        assert 'change_below_encoding_resolution' in json.loads(path.read_text())['subject_protection']
