import asyncio
from dataclasses import replace
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from meta_sam_parser import parse_responses_stream,video_segmentation_format

from takeone.app import create_app
from takeone.errors import Problem
from takeone import sam
from tests.test_backend import FakeMuse,settings,video,upload,start


def parsed(lines,completed=True):
    async def run():
        async def events():
            for line in lines:
                yield {'type':'response.output_text.delta','item_id':'mask','output_index':0,'content_index':0,'delta':line+'\n'}
            yield {'type':'response.output_text.done','item_id':'mask','output_index':0,'content_index':0,'text':'\n'.join(lines)+'\n'}
            if completed:yield {'type':'response.completed','response':{'status':'completed'}}
        return await parse_responses_stream(events(),video_segmentation_format()).final_result()
    return asyncio.run(run())


def line(frame=0,object_id=7):
    from meta_sam_parser._mask_codec import _encode_raster
    # Test-only encoding; production decoding uses the public parser API.
    payload=_encode_raster(bytes([1,1,1,1]),2,2)
    return f'<{frame}f>{object_id}<|box;x1=4;y1=5;x2=15;y2=16;w=192;h=108|><|mask;x=0;y=0;data=2,2,{payload}|>'


class FakeSam:
    def __init__(self,empty=False,incomplete=False):self.calls=0;self.empty=empty;self.incomplete=incomplete
    def available(self):return True
    def segment(self,path,concept,remote,remember,cancel,progress,raw_path,on_usage,on_request):
        self.calls+=1;remember('fixture-sam-file');on_request()
        result=parsed([] if self.empty else [line(0),line(2),line(1,3)],not self.incomplete)
        raw_path.write_text(result.raw_output);progress(result)
        on_usage({'video_frames_processed':12})
        return result


def finished(c,id):
    for _ in range(500):
        s=c.get('/api/projects/'+id).json()
        if s['status']!='segmenting':return s
        time.sleep(.02)
    raise AssertionError('segmentation did not finish')


def request(c,state):
    body={'command_id':str(uuid4()),'revision':state['revision'],'concept':'motorcycle'}
    result=c.post('/api/projects/'+state['id']+'/segment',json=body)
    assert result.status_code==202,result.text
    assert c.post('/api/projects/'+state['id']+'/segment',json=body).status_code==202
    return finished(c,state['id'])


def test_real_parser_uses_explicit_frames_objects_and_reports_gaps(tmp_path):
    frame_map={'proxy':{'width':192,'height':108},'frame_count':4,'frames':[{'time':i/30} for i in range(4)]}
    result=parsed([line(2),line(0),line(1,3)])
    manifest=sam.persist_result(result,frame_map,tmp_path,threading.Event())
    assert [(r['frame_index'],r['object_id']) for r in manifest['records']]==[(2,'7'),(0,'7'),(1,'3')]
    assert manifest['unmatched_frames']==1 and manifest['selected_object_id'] is None
    track=next(t for t in manifest['tracks'] if t['object_id']=='7')
    assert track['intervals']==[[0,0],[2,2]]
    assert manifest['records'][0]['bounds']==[4,5,16,17] # parser converts inclusive box edges


def test_empty_is_valid_but_incomplete_is_not_published(tmp_path):
    m={'proxy':{'width':192,'height':108},'frame_count':4,'frames':[{'time':i/30} for i in range(4)]}
    assert sam.persist_result(parsed([]),m,tmp_path,threading.Event())['mask_count']==0
    with pytest.raises(Problem,match='complete'):
        sam.persist_result(parsed([line()],False),m,tmp_path,threading.Event())


def test_api_tracking_persists_manifest_playable_overlay_and_deduplicates(settings,video):
    provider=FakeSam()
    with TestClient(create_app(settings,FakeMuse(),provider),base_url='http://127.0.0.1') as c:
        state=start(c,upload(c,video),'explicit neutral')
        brief=state['brief']
        done=request(c,state)
        assert done['status']=='brief_ready',done.get('error')
        run=done['segmentations'][-1]
        assert run['status']=='ready' and run['mask_count']==3 and provider.calls==1
        assert done['brief']==brief and done['sam_calls']==1
        assert 'sam_upload' not in done
        manifest=c.get(run['manifest_url']).json()
        assert manifest['frame_map']['frame_count']==12 and manifest['frames_with_masks']==3
        preview=c.get(run['preview_url'],headers={'Range':'bytes=0-31'})
        assert preview.status_code==206 and len(preview.content)==32
        assert done['events'][-1]['title']=='Subject masks ready'


def test_overlay_retry_reuses_completed_masks_without_another_sam_request(settings,video,monkeypatch):
    provider=FakeSam();original=sam.overlay
    def fail(*args):raise Problem('SAM_OVERLAY_FAILED','Fixture overlay failure','Retry',422)
    with TestClient(create_app(settings,FakeMuse(),provider),base_url='http://127.0.0.1') as c:
        state=start(c,upload(c,video),'explicit neutral')
        monkeypatch.setattr(sam,'overlay',fail)
        failed=request(c,state)
        assert failed['status']=='failed' and provider.calls==1
        monkeypatch.setattr(sam,'overlay',original)
        monkeypatch.setattr(provider,'available',lambda:False) # Cached local work requires no remote key.
        r=c.post('/api/projects/'+state['id']+'/retry',json={'command_id':str(uuid4()),'revision':failed['revision']})
        assert r.status_code==202
        done=finished(c,state['id'])
        assert done['segmentations'][-1]['status']=='ready' and provider.calls==1


def test_zero_matches_return_empty_success(settings,video):
    with TestClient(create_app(settings,FakeMuse(),FakeSam(empty=True)),base_url='http://127.0.0.1') as c:
        done=request(c,start(c,upload(c,video),'explicit neutral'))
        assert done['segmentations'][-1]['status']=='empty' and done['error'] is None
        assert done['segmentations'][-1]['preview_url'] is None


def test_mapping_rejects_frame_loss_before_any_remote_request(tmp_path,settings,monkeypatch):
    (tmp_path/'source.bin').write_bytes(b'original');(tmp_path/'analysis.mp4').write_bytes(b'proxy')
    source=SimpleNamespace(sha256=sam.checksum(tmp_path/'source.bin'),rotation=0,width=192,height=108)
    def info(path,*_):
        times=[0,.033333,.066667] if path.name=='source.bin' else [0,.066667]
        return dict(width=192,height=108,rate='30',pts=times,normalized=times)
    monkeypatch.setattr(sam,'frame_info',info)
    with pytest.raises(Problem,match='align'):sam.mapping(tmp_path,source,settings,threading.Event())


def test_vfr_is_rejected_explicitly(settings,monkeypatch):
    monkeypatch.setattr(sam,'command',lambda *_:json.dumps({'streams':[{'width':192,'height':108,'avg_frame_rate':'30/1'}],
        'frames':[{'best_effort_timestamp_time':str(t)} for t in [0,.033333,.2]]}))
    with pytest.raises(Problem,match='constant-frame-rate'):sam.frame_info(Path('fixture'),settings,threading.Event())


def test_network_is_saved_before_parsing_and_completed_stream_replays_without_api(tmp_path,settings,monkeypatch):
    counts={'requests':0,'usage':0};text=line()+'\n'
    class Stream:
        async def __aenter__(self):return self
        async def __aexit__(self,*_):pass
        def __aiter__(self):return self.events()
        async def events(self):
            yield {'type':'response.output_text.delta','item_id':'mask','output_index':0,'content_index':0,'delta':text}
            yield {'type':'response.content_part.done','item_id':'mask','output_index':0,'content_index':0,
                   'part':{'type':'output_text','text':text}}
            yield {'type':'response.completed','response':{'status':'completed','usage':{'video_frames_processed':12}}}
    class Client:
        def __init__(self,**_):self.files=SimpleNamespace(create=self.upload);self.responses=SimpleNamespace(create=self.create)
        async def __aenter__(self):return self
        async def __aexit__(self,*_):pass
        async def upload(self,**_):return SimpleNamespace(id='fixture-file')
        async def create(self,**kw):
            assert kw['model']=='sam-3.1' and kw['input'][0]['content'][1]['type']=='input_video'
            assert 'instructions' not in kw # No profile or model-generated creative context.
            counts['requests']+=1
            return Stream()
    monkeypatch.setattr(sam,'AsyncOpenAI',Client)
    provider=sam.SamProvider(settings);path=tmp_path/'input.mp4';path.write_bytes(b'fixture')
    def usage(_):counts['usage']+=1
    for _ in range(2):
        result=provider.segment(path,'motorcycle',None,lambda _:None,threading.Event(),lambda _:None,tmp_path/'raw.txt',usage,lambda:None)
        assert result.outcome.status=='completed'
        assert len([r for r in result.records if r.kind=='mask'])==1
    assert counts=={'requests':1,'usage':1}
    assert (tmp_path/'stream-complete.json').is_file()


def test_cancel_interrupts_a_waiting_network_request(settings,tmp_path,monkeypatch):
    class Client:
        def __init__(self,**_):self.responses=SimpleNamespace(create=self.create)
        async def __aenter__(self):return self
        async def __aexit__(self,*_):pass
        async def create(self,**_):await asyncio.sleep(10)
    monkeypatch.setattr(sam,'AsyncOpenAI',Client)
    path=tmp_path/'input.mp4';path.write_bytes(b'fixture');cancel=threading.Event()
    timer=threading.Timer(.05,cancel.set);timer.start();started=time.monotonic()
    try:
        with pytest.raises(Problem) as error:
            sam.SamProvider(settings).segment(path,'motorcycle','existing',lambda _:None,cancel,lambda _:None,tmp_path/'raw.txt',lambda _:None,lambda:None)
        assert error.value.code=='JOB_CANCELLED'
        assert time.monotonic()-started<2
    finally:timer.cancel()
