import json,subprocess,sys,threading
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from takeone import rendering
from takeone.app import create_app
from takeone.contracts import GradeRecipe,Source
from takeone.errors import Problem
from takeone.media import command
from tests.test_backend import settings,start,upload
from tests.test_local_grading import scene,Planner,Masks,generate


def test_advancing_encoder_has_no_elapsed_deadline(tmp_path):
    progress=tmp_path/'progress.txt'
    code="import pathlib,time; p=pathlib.Path(__import__('sys').argv[1]); [(p.write_text('frame='+str(i)+'\\n'),time.sleep(.15)) for i in range(8)]"
    command([sys.executable,'-c',code,str(progress)],threading.Event(),None,progress_path=progress,stall_timeout=.6)
    assert 'frame=7' in progress.read_text()


def test_stalled_encoder_and_cancel_are_distinct(tmp_path):
    progress=tmp_path/'progress.txt'
    code="import pathlib,time; pathlib.Path(__import__('sys').argv[1]).write_text('frame=3\\n');time.sleep(10)"
    with pytest.raises(Problem) as caught:
        command([sys.executable,'-c',code,str(progress)],threading.Event(),None,progress_path=progress,stall_timeout=.5)
    assert caught.value.code=='MEDIA_TOOL_TIMEOUT' and caught.value.details['timeout_kind']=='stalled'
    assert caught.value.details['last_encoded_frame']==3
    cancel=threading.Event();cancel.set()
    with pytest.raises(Problem) as caught:
        command([sys.executable,'-c','import time;time.sleep(10)'],cancel,None,progress_path=progress,stall_timeout=.5)
    assert caught.value.code=='JOB_CANCELLED'


def test_productive_cpu_work_can_exceed_tool_timeout():
    code='import time\nend=time.process_time()+4.2\nx=0\nwhile time.process_time()<end:x+=1'
    command([sys.executable,'-c',code],threading.Event(),2.5)


def test_localized_fast_path_matches_reference_including_fades(settings,scene,tmp_path):
    class Grade(Planner):
        def grade(self,*a,**kw):
            p,u=super().grade(*a,**kw);p.parameters.brightness_gain=1.03;return p,u
    with TestClient(create_app(settings,Grade(local=True,begin=.25,end=.9,fore_gain=1.05),Masks()),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit preserve red subject'))
        assert result['status']=='render_ready',result.get('error')
        candidate=result['candidates'][-1];folder=settings.workspace/result['id']/'candidates'/candidate['id']
        reference=tmp_path/'reference';reference.mkdir()
        import os
        os.link(folder/'matte.mkv',reference/'matte.mkv')
        rendering.render(settings.workspace/result['id']/'source.bin',reference,GradeRecipe.model_validate(candidate['recipe']),
            Source.model_validate(result['source']),settings,threading.Event(),qc_mode='reference')
        for name in ['grade.mp4','preview.webm']:
            def hashes(path):
                raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-map','0:v:0','-f','framemd5','-'])
                return [line for line in raw.splitlines() if line and not line.startswith(b'#')]
            assert hashes(folder/name)==hashes(reference/name)


def test_subject_preservation_applies_on_first_and_last_fade_frames(settings,scene):
    from tests.test_local_grading import pixels
    class Protected(Planner):
        def grade(self,*a,**kw):
            p,u=super().grade(*a,**kw)
            p.parameters.gamma=.8;p.parameters.brightness_gain=.65;p.parameters.black_point=.04
            p.execution_limits.subject_min_luma_ratio=.85
            return p,u
    with TestClient(create_app(settings,Protected(local=True,begin=.25,end=.9,fore_gain=1.05),Masks()),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit dark background preserve subject'))
        assert result['status']=='render_ready',result.get('error')
        candidate=result['candidates'][-1]
        assert candidate['validation']['subject_protection']['worst_measured']['subject_luma_ratio']>=.85
        assert candidate['validation']['local_composition_policy']=='background_fade_then_foreground_merge_v1'
        output=settings.workspace/result['id']/'candidates'/candidate['id']/'grade.mp4'
        for i in [3,4,9,10]:
            a,b=pixels(scene,i),pixels(output,i)
            assert b[32:75,70:125].mean()>=a[32:75,70:125].mean()*.9
        assert pixels(output,0)[32:75,70:125].mean()<pixels(scene,0)[32:75,70:125].mean()*.85
