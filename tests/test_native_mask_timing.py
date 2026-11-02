"""Native irregular PTS must survive automatic SAM-backed rendering."""
import json,subprocess,threading
from pathlib import Path
import numpy as np
import pytest
from fastapi.testclient import TestClient
from takeone import sam,evidence
from takeone import rendering,timing
from takeone.contracts import GradeRecipe,GradeProposal
from takeone.media import probe
from takeone.errors import Problem
from takeone.app import create_app
from tests.test_backend import settings,start,upload
from tests.test_local_grading import Planner,generate,pixels,parameters
from tests.test_sam import parsed


@pytest.fixture(scope='module',params=['vfr','jitter'])
def variable_scene(tmp_path_factory,request):
    path=tmp_path_factory.mktemp('native-masks')/'source.mp4'
    frames=[]
    for i in range(12):
        frame=np.full((108,192,3),120,np.uint8);frame[24:64,20+6*i:52+6*i]=[25,35,190];frames.append(frame)
    expression='10000+3000*N+9000*gte(N,6)' if request.param=='vfr' else '10000+3000*N+9*sin(N*PI/2)'
    subprocess.run(['ffmpeg','-v','error','-y','-f','rawvideo','-pixel_format','bgr24','-video_size','192x108',
        '-framerate','30','-i','-','-f','lavfi','-i','sine=duration=0.8',
        '-vf',f"settb=1/90000,setpts='{expression}',scale=in_range=pc:out_range=tv,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
        '-c:v','libx264','-crf','14','-pix_fmt','yuv420p','-enc_time_base:v','1/90000','-fps_mode','passthrough',
        '-c:a','aac','-video_track_timescale','90000','-movie_timescale','720000',str(path)],input=b''.join(f.tobytes() for f in frames),check=True)
    return path


class MovingMasks:
    calls=0
    def available(self):return True
    def segment(self,path,concept,remote,remember,cancel,progress,raw_path,on_usage,on_request):
        from meta_sam_parser._mask_codec import _encode_raster
        from takeone.config import Settings
        self.calls+=1;on_request();remember('mock-native-tracking')
        info=sam.frame_info(path,Settings(path.parent),cancel)
        assert len(info['pts'])==12 and info['cfr']
        payload=_encode_raster(bytes([1])*32*40,32,40)
        result=parsed([f'<{i}f>0<|box;x1={20+6*i};y1=24;x2={51+6*i};y2=63;w=192;h=108|><|mask;x=0;y=0;data=40,32,{payload}|>' for i in range(12)])
        raw_path.write_text(result.raw_output);progress(result);on_usage({'video_frames_processed':12});return result


def test_native_vfr_masks_follow_moving_subject_and_preserve_all_timestamps(settings,variable_scene):
    masks=MovingMasks();before=evidence.frame_manifest(variable_scene,settings,threading.Event())
    planner=Planner(local=True,end=.5)
    with TestClient(create_app(settings,planner,masks),base_url='http://127.0.0.1') as c:
        state=start(c,upload(c,variable_scene),'explicit darker cool surroundings, preserve red subject')
        planner.end=state['source']['duration']
        result=generate(c,state)
        assert result['status']=='render_ready',result.get('error')
        candidate=result['candidates'][-1];directory=settings.workspace/result['id'];output=directory/'candidates'/candidate['id']/'grade.mp4'
        after=evidence.frame_manifest(output,settings,threading.Event())
        assert after['times']==before['times'] and len(after['pts'])==12
        assert candidate['validation']['timing']['master']['max_normalized_error_ms']==0
        mapping=candidate['recipe']['timeline']['frame_map']
        assert mapping['schema_version']==3 and mapping['proxy_filename']=='tracking.mp4'
        assert [f['source_tick'] for f in mapping['frames']]==before['pts']
        assert sam.checksum(directory/'source.bin')==sam.checksum(variable_scene)
        for i in [0,5,6,11]:
            original,graded=pixels(variable_scene,i),pixels(output,i);x=20+6*i
            assert np.abs(original[30:58,x+5:x+25]-graded[30:58,x+5:x+25]).mean()<6
            # A one-frame-old mask ends six pixels too early here. Check
            # that moving edge explicitly, not only the overlapping core.
            assert np.abs(original[32:55,x+28:x+30]-graded[32:55,x+28:x+30]).mean()<5
            assert graded[5:15,5:40].mean()<original[5:15,5:40].mean()-10
        assert masks.calls==1
        second=generate(c,result);assert second['status']=='render_ready',second.get('error')
        assert masks.calls==1


def test_large_native_pts_map_uses_filter_file_and_executes(settings,tmp_path):
    source=tmp_path/'source.mp4';matte=tmp_path/'matte.mkv'
    for path,codec in [(source,'libx264'),(matte,'ffv1')]:
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=s=96x64:r=30:d=0.1',
            '-c:v',codec,str(path)],check=True)
    plan=GradeProposal(mode='localized',parameters=parameters(),local={'concept':'fixture','selection':'single_instance',
        'intervals':[{'start':0,'end':1}],'foreground':parameters(),'background':parameters(brightness_gain=.8),
        'preserve_subject_color':True,'feather_px':1},summary='large mapping fixture',
        decisions=[{'parameter':'local_treatment','reason':'Fixture','source_ids':['user_request']}],limitations=[])
    frames=[{'source_tick':i*3000,'source_pts':i/30,'time':i/30,'index':i} for i in range(7000)]
    recipe=GradeRecipe(source_sha256='fixture',brief_sha256='fixture',model='mock',proposal=plan,
        source_samples=[],timeline={'active_frame_ranges':[[0,6999]],'cut_frames':[0],
        'frame_map':{'schema_version':3,'source':{'time_base':'1/90000','format_start':'0'},'proxy':{'rate':'30'},'frame_count':7000,'frames':frames}})
    args=rendering.render_args(source,tmp_path,recipe,settings,None,timing_policy=timing.encoding_policy(probe(source,settings,threading.Event())))
    assert '-/filter_complex' in args or '-filter_complex_script' in args
    assert (tmp_path/'native-mask-timing.ffscript').stat().st_size>60000
    from takeone.media import command
    command(args,threading.Event(),30,cwd=tmp_path)
    assert (tmp_path/'grade.partial.mp4').is_file()


def test_ambiguous_mask_scheduling_is_rejected_before_proxy_or_sam(settings,tmp_path,monkeypatch):
    from types import SimpleNamespace
    source=tmp_path/'source.bin';source.write_bytes(b'fixture')
    monkeypatch.setattr(sam,'frame_info',lambda *a,**k:{'cfr':False,'ticks':[0,1,2]})
    with pytest.raises(Problem) as caught:
        sam.mapping(tmp_path,SimpleNamespace(sha256=sam.checksum(source),rotation=0),settings,threading.Event())
    assert caught.value.code=='SAM_TIMING_RESOLUTION'
    assert not (tmp_path/'tracking.mp4').exists()
