"""Bounded mathematical evidence selection. No aesthetic classifier or model calls."""
import base64
import hashlib
import json
from pathlib import Path
import time
from fractions import Fraction

import cv2
import numpy as np

from .errors import Problem
from .media import command


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def frame_manifest(path,settings,cancel):
    raw=json.loads(command([settings.ffprobe,'-v','error','-select_streams','v:0','-show_frames',
        '-show_entries','frame=best_effort_timestamp,best_effort_timestamp_time,duration,pkt_duration:stream=width,height,time_base,r_frame_rate','-of','json',str(path)],cancel,90))
    try:
        times=[float(f['best_effort_timestamp_time']) for f in raw['frames']]
        if not times or len(times)>15000 or not all(np.isfinite(times)) or any(b<=a for a,b in zip(times,times[1:])):raise ValueError()
        stream=raw['streams'][0];tick=Fraction(stream['time_base'])
        pts=[int(f['best_effort_timestamp']) for f in raw['frames']]
        if tick<=0 or any(b<=a for a,b in zip(pts,pts[1:])):raise ValueError()
        return {'times':[float((p-pts[0])*tick) for p in pts],'width':stream['width'],'height':stream['height'],
                'start':float(pts[0]*tick),'pts':pts,'time_base':str(tick),'nominal_rate':stream.get('r_frame_rate','0/1'),
                'durations':[f.get('duration',f.get('pkt_duration')) for f in raw['frames']]}
    except (KeyError,ValueError,IndexError,ZeroDivisionError):
        raise Problem('EVIDENCE_TIMING_INVALID','Visual evidence has missing or unsupported frame timestamps.',
                      'Use a clip within the 15,000-frame analysis limit. No model call was made.',422)


def extract(path,manifest,indices,settings,cancel,width=256):
    indices=sorted(set(indices));scale=min(width/manifest['width'],width/manifest['height'],1)
    w=max(2,int(manifest['width']*scale)//2*2);h=max(2,int(manifest['height']*scale)//2*2)
    expected=len(indices)*w*h*3
    if expected>settings.sample_memory_bytes:
        raise Problem('EVIDENCE_MEMORY_BUDGET','Requested visual evidence exceeds the configured memory budget.',
                      'Reduce sample count or dimensions.',422)
    selection='+'.join(fr'eq(n\,{i})' for i in indices)
    raw=command([settings.ffmpeg,'-v','error','-nostdin','-i',str(path),'-an','-vf',f'select={selection},scale={w}:{h}',
        '-frames:v',str(len(indices)),'-fps_mode','passthrough','-pix_fmt','rgb24','-f','rawvideo','-'],cancel,60,binary=True)
    if len(raw)!=expected:raise Problem('EVIDENCE_DECODE_FAILED','Decoded evidence does not match the selected frame identities.',
                                       'Inspect frame timing/decoder diagnostics; no model input was accepted.',422)
    pixels=np.frombuffer(raw,np.uint8).reshape(len(indices),h,w,3)
    return {i:frame for i,frame in zip(indices,pixels)}


def measure(pixels):
    rgb=pixels.astype(np.float32)/255;y=rgb@np.array([.2126,.7152,.0722],np.float32)
    hist=np.histogram(y,bins=16,range=(0,1))[0]/y.size
    grey=cv2.cvtColor(pixels,cv2.COLOR_RGB2GRAY)
    return {'median_encoded_luma':float(np.median(y)),'p05_encoded_luma':float(np.quantile(y,.05)),
            'p95_encoded_luma':float(np.quantile(y,.95)),'black_fraction':float(np.mean(y<=1/255)),
            'white_fraction':float(np.mean(y>=254/255)),
            'near_black_fraction':float(np.mean(y<=.02)),'near_white_fraction':float(np.mean(y>=.98)),
            'median_rgb':[float(v) for v in np.median(rgb,axis=(0,1))],
            'local_contrast':float(np.abs(cv2.Laplacian(grey,cv2.CV_32F)).mean()/255),'luma_histogram':hist.tolist()}


def select(features,shots,focus,max_count):
    """Coverage + distribution extremes, then measured temporal novelty; no look target."""
    selected={};by_id={f['frame_index']:f for f in features}
    def add(f,reason):
        if f['frame_index'] not in selected and len(selected)>=max_count:return
        selected.setdefault(f['frame_index'],[]).append(reason)
    for a,b in focus:
        candidates=[f for f in features if a<=f['time']<b]
        if candidates:add(min(candidates,key=lambda f:abs(f['time']-(a+b)/2)),'brief_region_interval')
    add(min(features,key=lambda f:f['p05_encoded_luma']),'shadow_extreme')
    add(max(features,key=lambda f:f['p95_encoded_luma']),'highlight_extreme')
    for shot in sorted(shots,key=lambda s:s['end']-s['start'],reverse=True):
        if any(shot['start']<=by_id[i]['time']<shot['end'] for i in selected):continue
        candidates=[f for f in features if shot['start']<=f['time']<shot['end']]
        if candidates:
            median=np.median([f['median_encoded_luma'] for f in candidates])
            add(min(candidates,key=lambda f:abs(f['median_encoded_luma']-median)),'shot_representative')
    add(max(features,key=lambda f:f.get('temporal_change',0)),'motion_or_lighting_change')
    while len(selected)<min(max_count,len(features)):
        remaining=[f for f in features if f['frame_index'] not in selected]
        f=max(remaining,key=lambda f:min(abs(f['time']-by_id[i]['time']) for i in selected))
        add(f,'temporal_coverage')
    return selected


def plan(path,source,settings,cancel,*,proxy=None,shots=None,focus=(),cache=None):
    started=time.monotonic()
    scan_path=Path(proxy) if proxy and Path(proxy).exists() else Path(path)
    with scan_path.open('rb') as stream:file_hash=hashlib.file_digest(stream,'sha256').hexdigest()
    budget={'scan':settings.sample_scan_frames,'count':settings.sample_count,'width':settings.sample_width,
            'memory':settings.sample_memory_bytes,'image_bytes':settings.sample_image_bytes}
    key=digest({'file':file_hash,'source':source.sha256,'budget':budget,'shots':shots,'focus':focus,'version':2})
    if cache and Path(cache).is_file():
        cached=json.loads(Path(cache).read_text())
        if cached.get('key')==key:return dict(cached,cache_hit=True,lookup_elapsed_seconds=time.monotonic()-started)
    manifest=frame_manifest(path,settings,cancel)
    scan_manifest=frame_manifest(scan_path,settings,cancel) if scan_path!=Path(path) else manifest
    if len(manifest['times'])!=len(scan_manifest['times']) or any(abs(a-b)>.002 for a,b in zip(manifest['times'],scan_manifest['times'])):
        scan_path=Path(path);scan_manifest=manifest
    shots=shots or [{'start':0.,'end':source.duration,'first_frame':0,'last_frame':len(manifest['times'])-1}]
    times=manifest['times'];count=len(times)
    nearest=lambda t:min(range(count),key=lambda i:abs(times[i]-t))
    desired=[nearest((s['start']+s['end'])/2) for s in shots]
    desired+=[nearest(t) for a,b in focus for t in [a,(a+b)/2,max(a,b-.001)]]
    desired+=[int(v) for v in np.linspace(0,count-1,settings.sample_scan_frames)]
    scan=[]
    bytes_per_frame=min(settings.sample_width,scan_manifest['width'])*min(settings.sample_width,scan_manifest['height'])*3
    limit=min(settings.sample_scan_frames,max(1,settings.sample_memory_bytes//max(1,bytes_per_frame)))
    for i in desired:
        if i not in scan:scan.append(i)
        if len(scan)>=limit:break
    pixels=extract(scan_path,scan_manifest,scan,settings,cancel,width=settings.sample_width)
    features=[];previous=None
    for i in sorted(pixels):
        f=measure(pixels[i]);f.update(frame_index=i,time=times[i])
        f['temporal_change']=0. if previous is None else float(np.abs(pixels[i].astype(float)-previous).mean()/255)
        previous=pixels[i].astype(float);features.append(f)
    chosen=select(features,shots,focus,settings.sample_count)
    uncovered=[s for s in shots if not any(s['start']<=times[i]<s['end'] for i in chosen)]
    result={'key':key,'manifest':manifest,'features':features,'selected_indices':sorted(chosen),
            'selection_reasons':{str(i):v for i,v in chosen.items()},'budget':budget,'scanned_frames':len(scan),
            'uncovered_shots':uncovered,'elapsed_seconds':time.monotonic()-started,
            'cache_hit':False,'uncertainty':['Bounded proxy measurements can miss brief events and do not establish semantic subject importance.']}
    if cache:
        from .sam import atomic
        atomic(cache,result)
    return result


def collect(path,source,settings,cancel,*,selection):
    frames=extract(path,selection['manifest'],selection['selected_indices'],settings,cancel,width=256)
    records=[];images=[];used=0
    for i,pixels in frames.items():
        metric=measure(pixels);metric.update(frame_index=i,requested_time=selection['manifest']['times'][i],
            selection_reasons=selection['selection_reasons'].get(str(i),[]))
        ok,jpeg=cv2.imencode('.jpg',pixels[:,:,::-1],[cv2.IMWRITE_JPEG_QUALITY,78])
        if not ok:raise Problem('EVIDENCE_IMAGE_FAILED','Could not encode visual evidence.','Retry local evidence preparation.',500)
        size=4*((len(jpeg)+2)//3)+len('data:image/jpeg;base64,')
        if used+size>settings.sample_image_bytes:
            metric['image_omitted']='image_payload_budget';records.append(metric);continue
        used+=size;records.append(metric)
        images.append({'id':f'frame:{i}','frame_index':i,'time':metric['requested_time'],
                       'url':'data:image/jpeg;base64,'+base64.b64encode(jpeg).decode()})
    if not images:raise Problem('EVIDENCE_IMAGE_BUDGET','No visual sample fits the image payload budget.','Increase the configured image budget.',422)
    return records,images


def compare(source_records,candidate_records,intent=None):
    by_id={r['frame_index']:r for r in source_records};pairs=[]
    for result in candidate_records:
        before=by_id[result['frame_index']]
        pairs.append({'frame_index':result['frame_index'],'time':result['requested_time'],
            'new_black_fraction':result['black_fraction']-before['black_fraction'],
            'new_white_fraction':result['white_fraction']-before['white_fraction'],
            'median_luma_change':result['median_encoded_luma']-before['median_encoded_luma'],
            'local_contrast_ratio':result['local_contrast']/before['local_contrast'] if before['local_contrast']>1/255 else None,
            'luma_distribution_distance':float(np.abs(np.array(result['luma_histogram'])-before['luma_histogram']).sum()/2)})
    ordered=sorted(candidate_records,key=lambda r:r['frame_index']);temporal=[]
    for a,b in zip(ordered,ordered[1:]):
        x,y=by_id[a['frame_index']],by_id[b['frame_index']]
        temporal.append({'from_frame':a['frame_index'],'to_frame':b['frame_index'],'seconds_between':b['requested_time']-a['requested_time'],
            'source_luma_delta':y['median_encoded_luma']-x['median_encoded_luma'],
            'render_luma_delta':b['median_encoded_luma']-a['median_encoded_luma'],
            'additional_rgb_delta':[float(v) for v in (np.array(b['median_rgb'])-a['median_rgb'])-(np.array(y['median_rgb'])-x['median_rgb'])]})
    return {'paired_measurements':pairs,'sampled_temporal_changes':temporal,'intent':intent or {},'interpretation':'Measurements and declared intent; no aesthetic score.',
            'uncertainty':['Sparse samples do not establish whole-video aesthetic quality or semantic preservation.',
                           'Contrast ratios are undefined when source contrast is at the one-code measurement floor.']}


def paired(original,rendered,source,settings,cancel,selection,cache=None):
    def file_hash(path):
        with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
    key=digest({'source':file_hash(original),'rendered':file_hash(rendered),'indices':selection['selected_indices'],
                'image_budget':settings.sample_image_bytes,'version':1})
    if cache and Path(cache).exists():
        value=json.loads(Path(cache).read_text())
        if value.get('key')==key:return value
    manifest=frame_manifest(rendered,settings,cancel)
    if len(manifest['times'])!=len(selection['manifest']['times']) or any(abs(a-b)>.002 for a,b in zip(manifest['times'],selection['manifest']['times'])):
        raise Problem('REVIEW_TIMING_INVALID','Source/rendered review samples do not align.','Technical validation must pass before creative review.',422)
    from dataclasses import replace
    bounded=replace(settings,sample_image_bytes=settings.sample_image_bytes//2)
    source_records,source_images=collect(original,source,bounded,cancel,selection=selection)
    output_records,output_images=collect(rendered,source,bounded,cancel,selection=selection)
    a=extract(original,selection['manifest'],selection['selected_indices'],settings,cancel)
    b=extract(rendered,selection['manifest'],selection['selected_indices'],settings,cancel)
    details=[]
    for i in a:
        x=a[i].astype(np.float32)@np.array([.2126,.7152,.0722],np.float32)/255
        y=b[i].astype(np.float32)@np.array([.2126,.7152,.0722],np.float32)/255
        shadow=x<=np.quantile(x,.25);high=x>=np.quantile(x,.95)
        spread=lambda v:float(np.quantile(v,.9)-np.quantile(v,.1))
        details.append({'frame_index':i,'source_shadow_spread':spread(x[shadow]),'render_shadow_spread':spread(y[shadow]),
            'source_shadow_clipped_fraction':float(np.mean(x[shadow]<=1/255)),
            'render_shadow_clipped_fraction':float(np.mean(y[shadow]<=1/255)),
            'source_highlight_spread':spread(x[high]),'render_highlight_spread':spread(y[high])})
    common={i['frame_index'] for i in source_images}&{i['frame_index'] for i in output_images}
    if not common:raise Problem('REVIEW_IMAGE_BUDGET','No aligned source/rendered images fit the budget.','Increase the review image budget.',422)
    result={'key':key,'source_images':[i for i in source_images if i['frame_index'] in common],
            'candidate_images':[i for i in output_images if i['frame_index'] in common],
            'source_records':source_records,'candidate_records':output_records,
            'measurements':compare(source_records,output_records),'detail_retention':details,
            'uncertainty':['Source-defined shadow/highlight regions measure changes, not whether darkness or clipping was artistically intended.']}
    if cache:
        from .sam import atomic
        atomic(cache,result)
    return result
