"""Real FFmpeg regression fixtures, with encoded frame IDs and no model calls."""
import subprocess
import threading
import json
from uuid import uuid4

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from takeone import evidence,rendering,sam,timing
from takeone.config import Settings
from takeone.contracts import GradeParameters,GradeProposal,GradeRecipe
from takeone.errors import Problem
from takeone.media import inspect_source
from takeone.app import create_app
from tests.test_backend import settings,start,upload
from tests.test_local_grading import Planner,Masks,scene,generate,done


def make_clip(path,kind):
    count=741 if kind=='near_cfr' else 24
    frames=np.full((count,64,96,3),96,np.uint8)
    for i in range(count):
        for bit in range(10):frames[i,8:24,8+bit*8:16+bit*8]=255 if i&(1<<bit) else 0
    if kind=='near_cfr':expression='6912+3000*N-floor(431*N/740)+round(8*sin(N*PI/10))'
    elif kind=='vfr':expression='6912+3000*N+9000*gte(N,6)+3000*gte(N,14)'
    else:expression='186912+3000*N'
    audio='sine=sample_rate=48000:duration=24.789333' if kind=='near_cfr' else 'sine=sample_rate=48000:duration=1.3'
    filters=f"[0:v]settb=1/90000,setpts='{expression}',setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709[v]"
    if kind=='cfr':filters+=';[1:a]asetpts=PTS+2/TB[a]'
    args=['ffmpeg','-v','error','-y','-f','rawvideo','-pixel_format','rgb24','-video_size','96x64',
          '-framerate','30','-i','-','-f','lavfi','-i',audio,'-filter_complex',filters,'-map','[v]',
          '-map','[a]' if kind=='cfr' else '1:a','-c:v','libx264','-crf','16','-pix_fmt','yuv420p',
          '-enc_time_base:v','1/90000','-fps_mode','passthrough','-video_track_timescale','90000',
          '-movie_timescale','720000','-c:a','aac',str(path)]
    subprocess.run(args,input=frames.tobytes(),check=True,timeout=45)


@pytest.fixture(scope='module')
def clips(tmp_path_factory):
    directory=tmp_path_factory.mktemp('timing')
    result={}
    for kind in ['cfr','near_cfr','vfr']:
        path=directory/(kind+'.mp4');make_clip(path,kind);result[kind]=path
    return result


def ids(path):
    capture=cv2.VideoCapture(str(path));result=[]
    try:
        while True:
            ok,pixels=capture.read()
            if not ok:break
            result.append(sum((1<<bit) for bit in range(10) if pixels[11:21,10+bit*8:14+bit*8].mean()>128))
    finally:capture.release()
    return result


@pytest.mark.parametrize('kind',['cfr','near_cfr','vfr'])
def test_render_preserves_pts_frame_identity_and_audio_offset(clips,tmp_path,kind):
    path=clips[kind];settings=Settings(workspace=tmp_path/'workspace',sample_scan_frames=8,sample_count=2)
    cancel=threading.Event();original,_=inspect_source(path,path.name,sam.checksum(path),settings,cancel)
    selection=evidence.plan(path,original,settings,cancel)
    stats,_=rendering.samples(path,original,settings,cancel,selection=selection)
    plan=GradeProposal(parameters=GradeParameters(contrast=1.05,saturation=1,gamma=1,brightness_gain=.9),summary='Timing fixture',limitations=[],
        decisions=[{'parameter':'contrast','reason':'Synthetic fixture','source_ids':['user_request']}])
    recipe=GradeRecipe(schema_version='takeone-grade-v3',prompt_version='colorist-v3',tone_operator='luminance_ocio_v3',
        source_sha256=original.sha256,brief_sha256='fixture',model='mock',proposal=plan,
        source_samples=stats,evidence_selection=selection)
    # Old persisted recipes have only decimal timestamps; they must still retry.
    if kind=='near_cfr':
        for key in ['pts','time_base','nominal_rate','durations']:recipe.evidence_selection['manifest'].pop(key)
    output=tmp_path/'candidate'
    result=rendering.render(path,output,recipe,original,settings,cancel)
    measured=result['timing']
    assert measured['master']['source']['classification']==kind
    assert measured['master']['max_normalized_error_ms']<.02
    assert abs(measured['master']['first_frame_offset_ms'])<.02
    assert abs(measured['audio']['start_error_ms'])<.02
    assert abs(measured['audio']['duration_error_ms'])<.02
    assert measured['preview']['max_normalized_error_ms']<=measured['preview']['quantization_bound_ms']<=2
    assert ids(output/'grade.mp4')==ids(path)==list(range(741 if kind=='near_cfr' else 24))
    assert sam.checksum(path)==original.sha256
    if kind=='near_cfr':
        source_manifest=evidence.frame_manifest(path,settings,cancel)
        assert len(source_manifest['pts'])==741 and source_manifest['start']==.0768
        assert abs(source_manifest['times'][-1]-24.661878)<.000001
        # The old command reproduces the real failure even with passthrough.
        rejected=tmp_path/'old-default.mp4'
        subprocess.run(['ffmpeg','-v','error','-y','-copyts','-start_at_zero','-i',str(path),'-map','0:v',
            '-map','0:a','-c:v','libx264','-fps_mode','passthrough','-c:a','copy',str(rejected)],check=True,timeout=45)
        with pytest.raises(Problem) as caught:
            timing.validate(source_manifest,evidence.frame_manifest(rejected,settings,cancel))
        assert caught.value.code=='RENDER_TIMING_CHANGED'
        assert caught.value.details['max_normalized_error_ms']>4
        assert caught.value.details['first_failing_frame'] is not None


def manifest(pts,time_base='1/90000'):
    return {'pts':pts,'time_base':time_base,'nominal_rate':'30/1','durations':[3000]*len(pts)}


@pytest.mark.parametrize('defect',['missing','duplicate','reordered','drift','origin','last_duration'])
def test_timing_guard_rejects_true_mapping_and_sync_defects(defect):
    source=manifest([6912+3000*i for i in range(741)])
    result={**source,'pts':source['pts'][:],'durations':source['durations'][:]}
    if defect=='missing':result['pts'].pop(200)
    if defect=='duplicate':result['pts'][200]=result['pts'][199]
    if defect=='reordered':result['pts'][199:201]=reversed(result['pts'][199:201])
    if defect=='drift':result['pts']=[p+i for i,p in enumerate(result['pts'])]
    if defect=='origin':result['pts']=[p-900 for p in result['pts']]
    if defect=='last_duration':result['durations'][-1]+=3000
    with pytest.raises(Problem) as caught:timing.validate(source,result)
    assert caught.value.code=='RENDER_TIMING_CHANGED'


def test_container_quantization_is_bounded_without_relaxing_drift_validation():
    source=manifest([6912+3000*i-i//2 for i in range(741)])
    values=timing.instants(source)
    preview=manifest([round(t*1000)+7 for t in values],'1/1000')
    report=timing.validate(source,preview,role='preview')
    assert report['max_normalized_error_ms']<=report['quantization_bound_ms']<=2
    assert report['first_frame_offset_ms']>6 # Report shared preview codec offset.
    with pytest.raises(Problem):timing.validate(source,preview)


def test_audio_sync_guard_rejects_start_or_copied_duration_change():
    original={'streams':[{'codec_type':'audio','time_base':'1/48000','start_pts':0,'duration_ts':48000}]}
    policy={'source_origin_seconds':'0'}
    for changed in [{'start_pts':480},{'duration_ts':49000}]:
        output={'streams':[{**original['streams'][0],**changed}]}
        with pytest.raises(Problem) as caught:timing.validate_audio(original,output,policy,True)
        assert caught.value.code=='RENDER_TIMING_CHANGED'


def test_service_persists_structured_timing_failure_and_retry_reuses_recipe(settings,scene,monkeypatch):
    original_render=rendering.render;planner=Planner();masks=Masks()
    def fail(*args,**kwargs):
        raise Problem('RENDER_TIMING_CHANGED','Fixture PTS mismatch','Retry the saved recipe.',422,
                      source={'classification':'near_cfr'},first_failing_frame=310)
    monkeypatch.setattr(rendering,'render',fail)
    with TestClient(create_app(settings,planner,masks),base_url='http://127.0.0.1') as client:
        state=generate(client,start(client,upload(client,scene),'explicit global correction'))
        assert state['status']=='failed' and client.app.state.service.store.get(state['id']).active_job is None
        assert state['error']['details']['source']=={'classification':'near_cfr'}
        assert json.loads(state['events'][-1]['details']['source'])=={'classification':'near_cfr'}
        candidate=state['candidates'][-1]['id']
        monkeypatch.setattr(rendering,'render',original_render)
        response=client.post('/api/projects/'+state['id']+'/retry',json={'revision':state['revision'],'command_id':str(uuid4())})
        assert response.status_code==202
        ready=done(client,state['id'])
        assert ready['status']=='render_ready',ready.get('error')
        assert len(ready['candidates'])==1 and ready['candidates'][-1]['id']==candidate
        assert planner.grade_calls==1 and masks.calls==0
