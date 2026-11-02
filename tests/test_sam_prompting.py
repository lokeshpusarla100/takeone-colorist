import json,threading
from types import SimpleNamespace
import cv2,numpy as np,pytest
from takeone import sam
from takeone.config import Settings
from takeone.errors import Problem
from takeone.sam_prompting import seed_from_masks


def test_seed_coordinates_are_from_actual_mask_and_excerpt_indices(tmp_path):
    mask=np.zeros((12,16),np.uint8);mask[:,0:4]=255;mask[-4:,:]=255
    path=tmp_path/'mask.png';cv2.imwrite(str(path),mask)
    rows={i:[{'object_id':'4','bounds':[10,20,26,32],'directory':str(tmp_path),'mask':'mask.png','proxy':{'width':64,'height':48}}] for i in [61,65,70]}
    prompt,proof=seed_from_masks(rows,[61,65,70],50,64,48,'4')
    assert '<11f>4' in prompt and '<20f>4' in prompt
    assert 'x2=25;y2=31;w=64;h=48' in prompt # Builder converts half-open bounds to inclusive.
    for item in proof:
        x,y=item['positive_point'];assert mask[y-20,x-10]>0
        assert item['excerpt_frame']==item['source_frame']-50


@pytest.mark.parametrize('seeded',[False,True])
def test_provider_sends_distinct_concept_or_native_prompt_protocol(tmp_path,monkeypatch,seeded):
    captured={};requested=[]
    class Client:
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        @property
        def responses(self):return SimpleNamespace(create=self.create)
        async def create(self,**kwargs):
            captured.update(kwargs);raise Problem('CAPTURED','Captured request','No network.',500)
    monkeypatch.setattr(sam,'AsyncOpenAI',lambda **kwargs:Client())
    monkeypatch.setattr(sam.MuseProvider,'key',lambda self:'fixture-key')
    source=tmp_path/'input.mp4';source.write_bytes(b'fixture')
    provider=sam.SamProvider(Settings(workspace=tmp_path))
    args=(source,'yellow boat','existing-file',lambda _:None,threading.Event(),lambda _:None,tmp_path/'raw.txt',lambda _:None,lambda:requested.append(True))
    with pytest.raises(Problem,match='Captured'):
        if seeded:provider.segment_prompted(*args,object_prompt='<3f>4<|point;x=10;y=20;w=64;h=48|>',propagation_direction='both')
        else:provider.segment(*args)
    text=captured['input'][0]['content'][0]['text']
    assert text.startswith('<3f>4') if seeded else text=='yellow boat'
    assert captured['metadata']==({'mask_encoding':'one_bit','propagation_direction':'both','include_confidence':'true'} if seeded else {'mask_encoding':'one_bit'})
    assert requested==[True]


def test_prompt_direction_change_invalidates_completed_cache(tmp_path):
    path=tmp_path/'input.mp4';path.write_bytes(b'fixture');events=tmp_path/'events.ndjson';events.write_text('')
    (tmp_path/'stream-complete.json').write_text(json.dumps({'events_sha256':sam.checksum(events),'input_sha256':sam.checksum(path),'concept':'boat',
        'object_prompt':'<3f>4<|point;x=10;y=20;w=64;h=48|>','propagation_direction':'forward','include_confidence':True,'model':'sam-3.1','prompt_protocol':'object-v1'}))
    provider=sam.SamProvider(Settings(workspace=tmp_path))
    with pytest.raises(Problem) as caught:
        provider.segment_prompted(path,'boat','unused',lambda _:None,threading.Event(),lambda _:None,tmp_path/'raw.txt',lambda _:None,lambda:pytest.fail('network was requested'),
            object_prompt='<3f>4<|point;x=10;y=20;w=64;h=48|>',propagation_direction='both')
    assert caught.value.code=='SAM_CACHE_CHANGED'


def test_invalid_native_direction_or_prompt_fails_before_request(tmp_path):
    provider=sam.SamProvider(Settings(workspace=tmp_path))
    args=(tmp_path/'not-uploaded','boat',None,lambda _:None,threading.Event(),lambda _:None,tmp_path/'raw.txt',lambda _:None,lambda:pytest.fail('network requested'))
    for prompt,direction in [('boat','both'),('<0f>4<|point;x=10;y=20;w=64;h=48|>','backward')]:
        with pytest.raises(Problem) as caught:provider.segment_prompted(*args,object_prompt=prompt,propagation_direction=direction)
        assert caught.value.code=='SAM_PROMPT_INVALID'
