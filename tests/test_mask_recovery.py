"""Real parser/FFmpeg recovery tests with mocked SAM only."""
from pathlib import Path
from uuid import uuid4
import numpy as np
from fastapi.testclient import TestClient
from takeone import sam
from takeone.app import create_app
from takeone.config import Settings
from takeone.errors import Problem
from tests.test_backend import settings,start,upload
from tests.test_local_grading import scene,Planner,generate,done
from tests.test_sam import parsed
from tests.test_native_mask_timing import variable_scene


class PrefixMasks:
    def __init__(self,wrong=False,fail=False,prefix=3,dynamic=False,thin_anchor=False,wrong_id=False):self.calls=0;self.wrong=wrong;self.fail=fail;self.frames=[];self.prefix=prefix;self.dynamic=dynamic;self.thin_anchor=thin_anchor;self.wrong_id=wrong_id
    def available(self):return True
    def segment_prompted(self,*args,object_prompt,propagation_direction):
        import re
        assert propagation_direction=='both' and '<|box;' in object_prompt and '<|point;' in object_prompt
        self.seed_frames=[int(n) for n in re.findall(r'<(\d+)f>',object_prompt)]
        self.seeded=True
        return self.segment(*args)
    def segment(self,path,concept,remote,remember,cancel,progress,raw_path,on_usage,on_request):
        from meta_sam_parser._mask_codec import _encode_raster
        self.calls+=1;on_request();remember('mock-repair')
        count=len(sam.frame_info(path,Settings(path.parent),cancel)['pts']);self.frames.append(count)
        boxes=[]
        if self.dynamic:
            import cv2
            capture=cv2.VideoCapture(str(path))
            while True:
                ok,p=capture.read()
                if not ok:break
                yy,xx=np.where((p[:,:,2]>150)&(p[:,:,1]<80));boxes.append((int(xx.min()),int(yy.min()),int(xx.max()),int(yy.max())))
            capture.release()
        rows=[]
        order=list(range(count))
        if getattr(self,'seeded',False):
            first=min(self.seed_frames);order=list(range(first,count))+list(reversed(range(first)))
        for i in order:
            if self.calls==1 and i<self.prefix or self.calls>1 and self.fail:continue
            if self.calls>1 and self.thin_anchor and i>=self.prefix+2:continue
            left,top,right,bottom=boxes[i] if self.dynamic else (5 if self.calls>1 and self.wrong else 60,24,131 if not(self.calls>1 and self.wrong) else 76,83)
            width,height=right-left+1,bottom-top+1;payload=_encode_raster(bytes([1])*width*height,width,height)
            rows.append(f'<{i}f>{1 if self.calls>1 and self.wrong_id else 0}<|box;x1={left};y1={top};x2={right};y2={bottom};w=192;h=108|><|mask;x=0;y=0;data={height},{width},{payload}|>')
        result=parsed(rows);raw_path.write_text(result.raw_output);progress(result);on_usage({'video_frames_processed':count});return result


def test_prefix_recovery_preserves_original_masks_and_reuses_verified_repair(settings,scene):
    masks=PrefixMasks()
    with TestClient(create_app(settings,Planner(local=True),masks),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit dark background, preserve red subject'))
        assert result['status']=='render_ready',result.get('error')
        assert masks.calls==2 and len(result['candidates'][0]['mask_run_ids'])==2
        parent,repair=result['segmentations']
        assert parent['frames_with_masks']==9 and repair['reverse_tracking'] is False
        assert repair['object_prompt'] and repair['propagation_direction']=='both'
        root=settings.workspace/result['id'];original=root/'segmentations'/parent['id']/'manifest.json'
        digest=sam.checksum(original)
        import json
        mapping=json.loads((root/'segmentations'/repair['id']/'frame-map.json').read_text())
        assert [f['index'] for f in mapping['frames']]==list(range(12))
        check=json.loads((root/'segmentations'/repair['id']/'coverage-verification.json').read_text())
        assert check['added_source_frames']==[0,1,2]
        assert check['method']=='native_object_prompt_v1'
        raw=(root/'segmentations'/repair['id']/'raw.txt').read_text()
        assert raw.startswith('<3f>') and raw.splitlines()[-1].startswith('<0f>')
        again=generate(c,result)
        assert again['status']=='render_ready',again.get('error')
        assert masks.calls==2 and len(again['candidates'][-1]['mask_run_ids'])==2
        assert sam.checksum(original)==digest


def test_wrong_subject_recovery_stops_without_render_or_another_paid_retry(settings,scene):
    masks=PrefixMasks(wrong=True)
    with TestClient(create_app(settings,Planner(local=True),masks),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit preserve red subject'))
        assert result['status']=='failed' and result['error']['code']=='LOCAL_MASK_GAP'
        assert 'disagree' in result['error']['details']['recovery_reason']
        assert masks.calls==2 and not list((settings.workspace/result['id']).glob('candidates/*/grade.mp4'))
        retry=c.post('/api/projects/'+result['id']+'/retry',json={'revision':result['revision'],'command_id':str(uuid4())})
        assert retry.status_code==202
        assert done(c,result['id'])['status']=='failed' and masks.calls==2


def test_old_reverse_repair_is_preserved_but_not_replayed_as_native_prompt(settings,scene):
    masks=PrefixMasks()
    with TestClient(create_app(settings,Planner(local=True),masks),base_url='http://127.0.0.1') as c:
        first=generate(c,start(c,upload(c,scene),'explicit preserve red subject'))
        store=c.app.state.service.store;state=store.get(first['id']);old=state.segmentations[-1]
        old.reverse_tracking=True;old.object_prompt=None;old.status='failed';store.save(state)
        old_id=old.id
        fresh=c.get('/api/projects/'+first['id']).json();second=generate(c,fresh)
        assert second['status']=='render_ready',second.get('error')
        assert masks.calls==3
        parent,legacy,native=second['segmentations']
        assert legacy['id']==old_id and legacy['reverse_tracking']
        assert native['id']!=old_id and native['object_prompt'] and not native['reverse_tracking']
        assert parent['recovery_run_ids']==[old_id,native['id']]


def test_still_missing_recovery_is_not_filled_with_stale_masks(settings,scene):
    masks=PrefixMasks(fail=True)
    with TestClient(create_app(settings,Planner(local=True),masks),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit preserve red subject'))
        assert result['status']=='failed' and result['error']['code']=='LOCAL_MASK_GAP'
        assert masks.calls==2 and result['candidates'][0]['preview_url'] is None


def test_no_recovery_request_without_enough_visible_anchor_frames(settings,scene):
    masks=PrefixMasks(prefix=11)
    with TestClient(create_app(settings,Planner(local=True),masks),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit preserve red subject'))
        assert result['status']=='failed' and result['error']['code']=='LOCAL_MASK_GAP'
        assert 'overlap' in result['error']['details']['recovery_reason']
        assert masks.calls==1


def test_native_propagation_still_requires_three_returned_anchor_frames(settings,scene):
    masks=PrefixMasks(thin_anchor=True)
    with TestClient(create_app(settings,Planner(local=True),masks),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit preserve red subject'))
        assert result['status']=='failed' and result['error']['code']=='LOCAL_MASK_GAP'
        assert 'enough anchor' in result['error']['details']['recovery_reason']
        assert masks.calls==2 and result['candidates'][0]['preview_url'] is None


def test_native_propagation_rejects_an_unrequested_object_id(settings,scene):
    masks=PrefixMasks(wrong_id=True)
    with TestClient(create_app(settings,Planner(local=True),masks),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit preserve red subject'))
        assert result['status']=='failed' and 'prompted object ID' in result['error']['details']['recovery_reason']
        assert masks.calls==2


def test_failed_repair_network_request_is_not_reissued_automatically(settings,scene):
    class Broken(PrefixMasks):
        def segment(self,*args):
            if self.calls==0:return super().segment(*args)
            self.calls+=1;args[-1]()
            raise Problem('SAM_API_FAILED','Fixture recovery network failure','Check the provider connection.',502)
    masks=Broken()
    with TestClient(create_app(settings,Planner(local=True),masks),base_url='http://127.0.0.1') as c:
        result=generate(c,start(c,upload(c,scene),'explicit preserve red subject'))
        assert result['status']=='failed' and result['error']['code']=='SAM_API_FAILED'
        assert result['segmentations'][-1]['request_count']==1
        retry=c.post('/api/projects/'+result['id']+'/retry',json={'revision':result['revision'],'command_id':str(uuid4())})
        assert retry.status_code==202
        stopped=done(c,result['id'])
        assert stopped['error']['code']=='LOCAL_MASK_GAP' and masks.calls==2


def test_prompted_recovery_keeps_native_timing_and_moving_subject(settings,variable_scene):
    from takeone import evidence
    from tests.test_local_grading import pixels
    import threading
    masks=PrefixMasks(dynamic=True);planner=Planner(local=True)
    with TestClient(create_app(settings,planner,masks),base_url='http://127.0.0.1') as c:
        state=start(c,upload(c,variable_scene),'explicit dark background preserve red subject');planner.end=state['source']['duration']
        result=generate(c,state);assert result['status']=='render_ready',result.get('error')
        candidate=result['candidates'][-1];path=settings.workspace/result['id']/'candidates'/candidate['id']/'grade.mp4'
        before=evidence.frame_manifest(variable_scene,settings,threading.Event());after=evidence.frame_manifest(path,settings,threading.Event())
        assert before['times']==after['times'] and masks.calls==2
        for i in [0,1,2,3,11]:
            a,b=pixels(variable_scene,i),pixels(path,i);x=20+6*i
            assert np.abs(a[32:55,x+28:x+30]-b[32:55,x+28:x+30]).mean()<5
