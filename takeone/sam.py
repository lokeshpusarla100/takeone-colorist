"""Hosted SAM tracking, explicit source mapping and diagnostic overlays. No grading."""
import asyncio
from contextlib import aclosing
from dataclasses import asdict
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
from uuid import uuid4
from importlib.metadata import version

import cv2
import numpy as np
from openai import AsyncOpenAI, APIError
from meta_sam_parser import parse_responses_stream, video_segmentation_format, decode_mask_to_raster

from .errors import Problem
from .media import command, probe
from .provider import MuseProvider


def atomic(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.'+path.name+'.'+str(uuid4())+'.tmp')
    try:
        with temporary.open('w') as stream:
            json.dump(value,stream,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    except OSError as error:
        raise Problem('SAM_WRITE_FAILED','The segmentation artifact could not be saved.',
                      'Check local storage. Completed streams can be decoded again without another SAM call.',507,
                      artifact=path.name,exception_type=type(error).__name__)
    finally:
        temporary.unlink(missing_ok=True)


def checksum(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def processing_config(model):
    return {'model':model,'parser':version('meta-sam-parser'),'mask_encoding':'one_bit','prompt':'concept'}


def check(cancel):
    if cancel.is_set():
        raise Problem('JOB_CANCELLED','Segmentation cancelled.','Saved source, brief and masks are retained.',409)


def frame_info(path,settings,cancel,*,interval_tolerance=.00002):
    raw=json.loads(command([settings.ffprobe,'-v','error','-select_streams','v:0','-show_frames',
        '-show_entries','frame=best_effort_timestamp_time:stream=width,height,avg_frame_rate',
        '-of','json',str(path)],cancel,90))
    try:
        pts=[float(f['best_effort_timestamp_time']) for f in raw['frames']]
        stream=raw['streams'][0]
        rate=Fraction(stream['avg_frame_rate'])
        if not pts or len(pts)>15000 or rate<=0 or not all(math.isfinite(t) for t in pts):raise ValueError()
        normalized=[t-pts[0] for t in pts]
        if any(abs((b-a)-float(1/rate))>interval_tolerance for a,b in zip(normalized,normalized[1:])):raise ValueError()
    except (KeyError,ValueError,ZeroDivisionError,IndexError):
        raise Problem('SAM_TIMING_UNSUPPORTED','SAM preview currently requires a constant-frame-rate clip with valid frame timestamps.',
                      'Use a CFR source. No segmentation request was made.',422)
    return {'width':stream['width'],'height':stream['height'],'rate':str(rate),'pts':pts,'normalized':normalized}


def mapping(directory,source,settings,cancel):
    original=directory/'source.bin'; proxy=directory/'analysis.mp4'
    if checksum(original)!=source.sha256:
        raise Problem('SOURCE_CHANGED','The original no longer matches the prepared source.','Re-import the source.',409)
    if source.rotation!=0:
        raise Problem('SAM_GEOMETRY_UNSUPPORTED','Rotated source metadata is not verified for SAM mapping yet.',
                      'Use an upright source with zero rotation metadata.',422)
    a,b=frame_info(original,settings,cancel),frame_info(proxy,settings,cancel)
    # Strict frame identity with a nonzero start: neither file's timestamps
    # are reset or snapped to a nominal rate. Each frame keeps its absolute
    # source/proxy PTS; only the *comparison* is made on each file's own
    # first-frame-relative timeline, and every relative instant must agree.
    # A shifted container start is therefore supported exactly when the two
    # decoded frame sequences still correspond one to one, and rejected when
    # their relative timing drifts, however small the absolute offset is.
    if len(a['pts'])!=len(b['pts']) or a['rate']!=b['rate'] or any(abs(x-y)>0.002 for x,y in zip(a['normalized'],b['normalized'])):
        raise Problem('SAM_FRAME_MAPPING_FAILED','Source and proxy frames do not align one to one.',
                      'Prepare a frame-preserving proxy before segmentation. No SAM request was made.',422)
    if (a['width'],a['height'])!=(source.width,source.height) or abs(a['width']/a['height']-b['width']/b['height'])>0.005:
        raise Problem('SAM_GEOMETRY_UNSUPPORTED','Source/proxy geometry is not a verified scale-only mapping.',
                      'Use upright square-pixel footage.',422)
    return {'schema_version':2,'source_sha256':source.sha256,'proxy_sha256':checksum(proxy),
        'source':{k:a[k] for k in ['width','height','rate']},'proxy':{k:b[k] for k in ['width','height','rate']},
        'normalization':{'method':'first_decoded_frame_pts','source_offset_seconds':a['pts'][0],
                          'proxy_offset_seconds':b['pts'][0],
                          'max_relative_error_seconds':max((abs(x-y) for x,y in zip(a['normalized'],b['normalized'])),default=0.)},
        'frame_count':len(a['pts']),'frames':[{'index':i,'source_pts':x,'proxy_pts':y,'time':a['normalized'][i]}
                                             for i,(x,y) in enumerate(zip(a['pts'],b['pts']))]}


async def normalized_events(stream,cancel,raw_path,on_usage):
    closed=set(); size=0
    with raw_path.open('w') as raw:
        async for event in stream:
            check(cancel)
            data=event.model_dump() if hasattr(event,'model_dump') else dict(event)
            kind=data.get('type');lane=tuple(data.get(k) for k in ['item_id','output_index','content_index'])
            if kind=='response.output_text.delta':
                delta=data.get('delta','');size+=len(delta.encode())
                if size>128*1024*1024:
                    raise Problem('SAM_OUTPUT_LIMIT','Segmentation output exceeded the local processing limit.','Use a shorter clip.',422)
                raw.write(delta);raw.flush()
            response=data.get('response',{})
            if kind in {'response.completed','response.incomplete','response.failed'} and isinstance(response,dict):
                on_usage(response.get('usage') or {})
            if kind=='response.output_text.done':
                if lane in closed:continue
                closed.add(lane)
            yield data
            part=data.get('part')
            if kind=='response.content_part.done' and lane not in closed and isinstance(part,dict) and part.get('type')=='output_text':
                closed.add(lane)
                yield dict(type='response.output_text.done',text=part.get('text',''),
                           **{k:data.get(k) for k in ['item_id','output_index','content_index']})


class SamProvider:
    def __init__(self,settings):self.settings=settings

    def available(self):return bool(MuseProvider(self.settings).key())

    def segment(self,path,concept,remote_id,remember,cancel,progress,raw_path,on_usage,on_request):
        events_path=raw_path.with_name('events.ndjson')
        receipt_path=raw_path.with_name('stream-complete.json')
        async def execute():
            if receipt_path.is_file():
                receipt=json.loads(receipt_path.read_text())
                if receipt!={'events_sha256':checksum(events_path),'input_sha256':checksum(path),'concept':concept}:
                    raise Problem('SAM_CACHE_CHANGED','The cached SAM response does not match its input.','Start a new mask run.',409)
            else:
                await download()
            check(cancel);progress(None)
            async def replay():
                with events_path.open() as stored:
                    for row in stored:
                        check(cancel)
                        yield json.loads(row)
                        await asyncio.sleep(0)
            async with parse_responses_stream(replay(),video_segmentation_format()) as parsed:
                last=0
                async for snapshot in parsed:
                    check(cancel)
                    if time.monotonic()-last>1:
                        progress(snapshot);last=time.monotonic()
                return await parsed.final_result()

        async def download():
            for old in [raw_path,events_path]:
                if old.exists():old.rename(old.with_name(old.name+'.previous-'+str(uuid4())))
            async with AsyncOpenAI(api_key=MuseProvider(self.settings).key(),base_url=self.settings.api_base,
                                   timeout=self.settings.model_timeout,max_retries=0) as client:
                if not remote_id:
                    check(cancel)
                    with path.open('rb') as source:
                        uploaded=await client.files.create(file=(path.name,source,'video/mp4'),purpose='user_data')
                    file_id=uploaded.id;remember(file_id)
                else:file_id=remote_id
                check(cancel);on_request()
                stream=await client.responses.create(model=self.settings.sam_model,stream=True,store=False,
                    input=[{'role':'user','content':[{'type':'input_text','text':concept},
                                                  {'type':'input_video','file_id':file_id}]}],
                    metadata={'mask_encoding':'one_bit'})
                async with stream, aclosing(normalized_events(stream,cancel,raw_path,on_usage)) as events:
                    completed=False;received=set();last=0
                    with events_path.open('w') as stored:
                        async for data in events:
                            stored.write(json.dumps(data)+'\n')
                            if data.get('type')=='response.output_text.delta':
                                received.update(int(i) for i in re.findall(r'<(\d+)f>',data.get('delta','')))
                            if time.monotonic()-last>1:
                                stored.flush();progress({'received_frames':len(received)});last=time.monotonic()
                            if data.get('type')=='response.completed':completed=True
                        stored.flush();os.fsync(stored.fileno())
                    if not completed:
                        raise Problem('SAM_INCOMPLETE','The SAM stream ended without completion.','Partial output is preserved; retry only explicitly.',502)
                    atomic(receipt_path,{'events_sha256':checksum(events_path),'input_sha256':checksum(path),'concept':concept})
        async def supervised():
            work=asyncio.create_task(execute())
            try:
                deadline=time.monotonic()+1800
                while not work.done():
                    check(cancel)
                    if time.monotonic()>deadline:raise TimeoutError()
                    await asyncio.wait({work},timeout=.15)
                return await work
            finally:
                if not work.done():
                    work.cancel()
                    try:await work
                    except asyncio.CancelledError:pass
        try:
            return asyncio.run(supervised())
        except Problem:raise
        except APIError as error:
            raise Problem('SAM_API_FAILED','The SAM API request failed.','Check model access, limits and credentials before an explicit retry.',
                          502,provider_status=getattr(error,'status_code',None),provider_request_id=getattr(error,'request_id',None))
        except TimeoutError:
            raise Problem('SAM_TIMEOUT','SAM exceeded the local request time limit.','Inspect partial artifacts before retrying; usage may have occurred.',504)
        except Exception as error:
            raise Problem('SAM_STREAM_INVALID','SAM output could not be parsed completely.',
                          'Inspect the saved stream and parser version before retrying.',502,exception_type=type(error).__name__)


def persist_result(result,frame_map,directory,cancel):
    atomic(directory/'parsed.json',{'outcome':asdict(result.outcome),'diagnostics':[asdict(d) for d in result.diagnostics],
                                   'records':[asdict(r) for r in result.records]})
    if result.outcome.status!='completed' or any(d.severity=='error' for d in result.diagnostics):
        raise Problem('SAM_INCOMPLETE','SAM did not produce a complete, valid result.',
                      'Partial records have been retained; they are not published as a usable mask run.',502)
    w,h=frame_map['proxy']['width'],frame_map['proxy']['height']
    for token in re.findall(r'<\|box;([^|]*)\|>',result.raw_output):
        fields=dict(part.split('=',1) for part in token.split(';') if '=' in part)
        if int(fields.get('w',0))!=w or int(fields.get('h',0))!=h:
            raise Problem('SAM_MASK_GEOMETRY','SAM returned a different coordinate space.','Inspect input and returned geometry.',422)
    records=[]; seen=set(); tracks={}
    (directory/'masks').mkdir(exist_ok=True)
    for record in result.records:
        if record.kind!='mask':continue
        check(cancel)
        i=record.frame.frame_index if record.frame else -1
        if not 0<=i<frame_map['frame_count'] or not re.fullmatch(r'[0-9]{1,12}',record.object_id):
            raise Problem('SAM_FRAME_INVALID','A mask has an invalid frame or object reference.','Inspect the preserved raw result.',422)
        key=(i,record.object_id)
        if key in seen:raise Problem('SAM_MASK_DUPLICATE','SAM returned conflicting masks for one frame/object.','Inspect the raw result.',422)
        seen.add(key)
        x,y,x2,y2=[int(getattr(record.bounds,k)) for k in ['left','top','right','bottom']]
        if not(0<=x<x2<=w and 0<=y<y2<=h) or record.mask.width*record.mask.height>w*h:
            raise Problem('SAM_MASK_GEOMETRY','Mask bounds exceed the proxy frame.','Inspect the returned geometry.',422)
        raster=np.frombuffer(decode_mask_to_raster(record.mask),np.uint8).reshape(record.mask.height,record.mask.width)
        name=f'masks/f{i:05d}-o{record.object_id}.png'
        if not cv2.imwrite(str(directory/name),raster*255):
            raise Problem('SAM_MASK_WRITE_FAILED','A decoded mask could not be saved.','Check storage, then retry local decoding.',507)
        records.append({'frame_index':i,'time':frame_map['frames'][i]['time'],'object_id':record.object_id,
                        'bounds':[x,y,x2,y2],'mask':name,'confidence':record.confidence})
        tracks.setdefault(record.object_id,[]).append(i)
    summaries=[]
    for object_id,frames in sorted(tracks.items(),key=lambda v:int(v[0])):
        frames=sorted(frames); intervals=[];first=last=frames[0]
        for value in frames[1:]:
            if value!=last+1:intervals.append([first,last]);first=value
            last=value
        intervals.append([first,last])
        summaries.append({'object_id':object_id,'visible_frames':len(frames),'intervals':intervals})
    manifest={'frame_map':frame_map,'records':records,'tracks':summaries,
              'frames_with_masks':len({r['frame_index'] for r in records}),'mask_count':len(records),
              'unmatched_frames':frame_map['frame_count']-len({r['frame_index'] for r in records}),
              'review_status':'needs_user_review','selected_object_id':None,
              'limitations':['Unmatched frames may mean absence or missed segmentation; no mask is carried across gaps.',
                             'Object IDs are not guaranteed through full occlusion, exits/reentry or scene cuts.',
                             'Overlay is diagnostic only. Regional grading is not enabled.']}
    atomic(directory/'manifest.json',manifest)
    return manifest


def overlay(proxy,directory,manifest,settings,cancel):
    geometry=manifest['frame_map']['proxy'];w,h=geometry['width'],geometry['height']
    by_frame={}
    for r in manifest['records']:by_frame.setdefault(r['frame_index'],[]).append(r)
    decode=[settings.ffmpeg,'-v','error','-i',str(proxy),'-map','0:v:0','-fps_mode','passthrough','-f','rawvideo','-pix_fmt','bgr24','-']
    output=directory/'overlay.partial.webm'
    encode=[settings.ffmpeg,'-v','error','-y','-f','rawvideo','-pixel_format','bgr24','-video_size',f'{w}x{h}',
            '-framerate',geometry['rate'],'-i','-','-an','-c:v','libvpx-vp9','-deadline','realtime','-cpu-used','6',
            '-threads','2','-crf','30','-b:v','0','-g','48','-keyint_min','48',str(output)]
    with (directory/'decode.log').open('wb') as dl,(directory/'encode.log').open('wb') as el:
        a=subprocess.Popen(decode,stdout=subprocess.PIPE,stderr=dl)
        b=subprocess.Popen(encode,stdin=subprocess.PIPE,stderr=el)
        try:
            for i in range(manifest['frame_map']['frame_count']):
                check(cancel);data=a.stdout.read(w*h*3)
                if len(data)!=w*h*3:raise ValueError('incomplete proxy decode')
                pixels=np.frombuffer(data,np.uint8).reshape(h,w,3).copy()
                for r in by_frame.get(i,[]):
                    x,y,x2,y2=r['bounds'];mask=cv2.imread(str(directory/r['mask']),cv2.IMREAD_GRAYSCALE)
                    if mask is None:raise ValueError('missing mask')
                    selected=cv2.resize(mask,(x2-x,y2-y),interpolation=cv2.INTER_NEAREST)>0
                    color=np.array([(int(r['object_id'])*67+70)%200+40,200,(int(r['object_id'])*113+90)%200+40],np.float32)
                    region=pixels[y:y2,x:x2];region[selected]=(region[selected]*0.6+color*0.4).astype(np.uint8)
                    cv2.putText(pixels,'ID '+r['object_id'],(x,max(14,y)),cv2.FONT_HERSHEY_SIMPLEX,0.45,(255,255,255),1)
                b.stdin.write(pixels.tobytes())
            if a.stdout.read(1):raise ValueError('extra proxy frames')
            b.stdin.close()
            if a.wait(timeout=20)!=0 or b.wait(timeout=60)!=0:raise ValueError('overlay encoder failed')
        except Exception as error:
            a.kill();b.kill();a.wait();b.wait()
            if isinstance(error,Problem):raise
            raise Problem('SAM_OVERLAY_FAILED','The diagnostic overlay could not be rendered.',
                          'Masks are saved. Retry overlay generation without another SAM call.',422,exception_type=type(error).__name__)
        finally:
            a.stdout.close()
            if not b.stdin.closed:b.stdin.close()
    checked=probe(output,settings,cancel)
    video=next(s for s in checked['streams'] if s['codec_type']=='video')
    expected=manifest['frame_map']['frame_count']/float(Fraction(geometry['rate']))
    if (video['width'],video['height'])!=(w,h) or abs(float(checked['format']['duration'])-expected)>0.1:
        raise Problem('SAM_OVERLAY_TIMING','Overlay timing/geometry failed validation.','Inspect the retained masks and frame map.',422)
    command([settings.ffmpeg,'-v','error','-xerror','-i',str(output),'-f','null','-'],cancel,120)
    output.replace(directory/'overlay.webm')
