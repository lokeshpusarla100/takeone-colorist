"""Bounded mathematical evidence selection. No aesthetic classifier or model calls."""
import base64
import hashlib
import json
from pathlib import Path
import time
import tempfile
from fractions import Fraction

import cv2
import numpy as np

from .errors import Problem
from . import tonal
from .media import command, display_size


def decoded_manifest(path,settings,cancel,metadata):
    """One strict full video/audio decode, retaining every presented video PTS.

    framecrc consumes decoded raw video/audio; it is not a packet-only probe.
    Its encoder time base is explicit so validation cannot itself snap VFR PTS.
    """
    video=next(s for s in metadata['streams'] if s['codec_type']=='video')
    raw=command([settings.ffmpeg,'-v','error','-xerror','-nostdin','-copyts','-threads','4','-i',str(path),
        '-map','0:v:0','-map','0:a:0?','-c:v','rawvideo','-threads:v','1','-enc_time_base:v',video['time_base'],
        '-fps_mode','passthrough','-c:a','pcm_s16le','-f','framecrc','-'],cancel,120)
    return _decode_manifest(raw,metadata)


def _decode_manifest(raw,metadata):
    video=next(s for s in metadata['streams'] if s['codec_type']=='video')
    try:
        tick=Fraction(video['time_base']);pts=[];durations=[];seen_tick=None
        for line in raw.splitlines():
            if line.startswith('#tb 0:'):seen_tick=Fraction(line.split(':',1)[1].strip())
            if line.startswith('#'):continue
            fields=line.split(',')
            if len(fields)>=6 and fields[0].strip()=='0':
                pts.append(int(fields[2]));durations.append(int(fields[3]))
        if seen_tick!=tick or not pts or len(pts)>15000 or any(b<=a for a,b in zip(pts,pts[1:])):raise ValueError()
        width,height=display_size(video)
        return {'times':[float((p-pts[0])*tick) for p in pts],'width':width,'height':height,
            'start':float(pts[0]*tick),'pts':pts,'time_base':str(tick),'nominal_rate':video.get('r_frame_rate','0/1'),
            'durations':durations,'fully_decoded':True,'audio_decoded':any(s['codec_type']=='audio' for s in metadata['streams'])}
    except (ValueError,KeyError,IndexError,ZeroDivisionError):
        raise Problem('EVIDENCE_TIMING_INVALID','Decoded output has incomplete or inconsistent frame timestamps.',
                      'The output was not accepted. Inspect decoder/timestamp diagnostics.',422)


def decoded_evidence(path,settings,cancel,metadata,selection):
    """Strict full-resolution decode/timing and bounded sample RGB in one pass.

    Only the sparse evidence branch is scaled. All original video frames and
    audio packets still traverse the independent full-resolution integrity path.
    """
    video=next(s for s in metadata['streams'] if s['codec_type']=='video')
    shown_width,shown_height=display_size(video) # FFmpeg auto-rotates before the scaled evidence branch
    indices=sorted(set(selection['selected_indices']));scale=min(256/shown_width,256/shown_height,1)
    width=max(2,int(shown_width*scale)//2*2);height=max(2,int(shown_height*scale)//2*2)
    expected=len(indices)*width*height*3
    if not indices or len(indices)>12 or indices[0]<0 or indices[-1]>=len(selection['manifest']['times']):
        raise Problem('EVIDENCE_TIMING_INVALID','The output evidence selection is invalid.',
                      'Rebuild bounded source evidence before rendering.',422)
    if expected>settings.sample_memory_bytes:
        raise Problem('EVIDENCE_MEMORY_BUDGET','Requested visual evidence exceeds the configured memory budget.',
                      'Reduce sample count or dimensions.',422)
    selected='+'.join(fr'eq(n\,{i})' for i in indices)
    graph=f'[0:v:0]split=2[full][samplein];[samplein]select={selected},scale={width}:{height}[samples]'
    with tempfile.TemporaryDirectory(prefix='takeone-qc-') as directory:
        sample_path=Path(directory)/'selected.rgb'
        raw=command([settings.ffmpeg,'-v','error','-xerror','-nostdin','-copyts','-filter_complex_threads','2',
            '-threads','4','-i',str(path),'-filter_complex',graph,
            '-map','[full]','-map','0:a:0?','-c:v','rawvideo','-threads:v','1','-pix_fmt:v',video['pix_fmt'],
            '-enc_time_base:v',video['time_base'],'-fps_mode','passthrough','-c:a','pcm_s16le','-f','framecrc','-',
            '-map','[samples]','-an','-c:v','rawvideo','-threads:v','1','-pix_fmt','rgb24','-enc_time_base:v',video['time_base'],
            '-fps_mode','passthrough','-f','rawvideo',str(sample_path)],cancel,120)
        manifest=_decode_manifest(raw,metadata)
        if sample_path.stat().st_size!=expected:
            raise Problem('EVIDENCE_DECODE_FAILED','Decoded evidence does not match the selected frame identities.',
                          'Inspect frame timing/decoder diagnostics; no model input was accepted.',422)
        pixels=np.frombuffer(sample_path.read_bytes(),np.uint8).reshape(len(indices),height,width,3)
    return manifest,{index:frame for index,frame in zip(indices,pixels)}


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def frame_manifest(path,settings,cancel):
    from .frames import probe_frames
    raw=probe_frames(path,settings,cancel) # one exact all-core decode, shared with sam.frame_info
    try:
        times=[float(f['best_effort_timestamp_time']) for f in raw['frames']]
        if not times or len(times)>15000 or not all(np.isfinite(times)) or any(b<=a for a,b in zip(times,times[1:])):raise ValueError()
        stream=raw['streams'][0];tick=Fraction(stream['time_base'])
        pts=[int(f['best_effort_timestamp']) for f in raw['frames']]
        if tick<=0 or any(b<=a for a,b in zip(pts,pts[1:])):raise ValueError()
        width,height=display_size(stream) # decoded (auto-rotated) geometry, which extract() scales from
        return {'times':[float((p-pts[0])*tick) for p in pts],'width':width,'height':height,
                'start':float(pts[0]*tick),'pts':pts,'time_base':str(tick),'nominal_rate':stream.get('r_frame_rate','0/1'),
                'durations':[f.get('duration',f.get('pkt_duration')) for f in raw['frames']]}
    except (KeyError,ValueError,IndexError,ZeroDivisionError):
        raise Problem('EVIDENCE_TIMING_INVALID','Visual evidence has missing or unsupported frame timestamps.',
                      'Use a clip within the 15,000-frame analysis limit. No model call was made.',422)


def source_manifest(path,settings,cancel,existing,metadata,identity,cache=None):
    """Upgrade old, hash-bound decimal evidence without decoding the whole source.

    Decimal PTS can be recovered only when the source tick is wider than their
    recorded precision. Verify the final decoded PTS/duration and fall back to
    full inspection on ambiguity. Nothing is resampled to nominal frame rate.
    """
    if existing and 'pts' in existing:return existing
    key=digest({'source':identity,'existing':existing,'version':2})
    if cache and Path(cache).is_file():
        try:
            saved=json.loads(Path(cache).read_text())
            if saved.get('key')==key:return saved['manifest']
        except (ValueError,KeyError):pass
    result=None
    try:
        video=next(s for s in metadata['streams'] if s['codec_type']=='video');tick=Fraction(video['time_base'])
        values=[Fraction(str(existing['start']))+Fraction(str(t)) for t in existing['times']]
        pts=[round(v/tick) for v in values]
        if (tick<=Fraction(1,1000000) or not pts or len(pts)>15000
            or any(b<=a for a,b in zip(pts,pts[1:]))
            or any(abs(p*tick-v)>Fraction(501,1000000000) for p,v in zip(pts,values))
            or video.get('nb_frames') and int(video['nb_frames'])!=len(pts)):raise ValueError()
        # Seek conservatively to the final GOP. This retains the final-frame
        # duration check rather than inventing it from duration * nominal FPS.
        seek=max(0,float(values[-1])-2)
        tail=json.loads(command([settings.ffprobe,'-v','error','-select_streams','v:0','-read_intervals',f'{seek:.6f}%',
            '-show_frames','-show_entries','frame=best_effort_timestamp,duration,pkt_duration','-of','json',str(path)],cancel,90))['frames']
        last=tail[-1]
        if int(last['best_effort_timestamp'])!=pts[-1]:raise ValueError()
        durations=[None]*len(pts);durations[-1]=last.get('duration',last.get('pkt_duration'))
        result={**existing,'pts':pts,'time_base':str(tick),'nominal_rate':video.get('r_frame_rate','0/1'),
                'times':[float((p-pts[0])*tick) for p in pts],'start':float(pts[0]*tick),'durations':durations,
                'legacy_pts_recovered':True}
    except (ValueError,KeyError,TypeError,IndexError,ZeroDivisionError,StopIteration):pass
    if result is None:result=frame_manifest(path,settings,cancel)
    if cache:
        from .sam import atomic
        atomic(cache,{'key':key,'manifest':result})
    return result


def extract(path,manifest,indices,settings,cancel,width=256):
    indices=sorted(set(indices));scale=min(width/manifest['width'],width/manifest['height'],1)
    w=max(2,int(manifest['width']*scale)//2*2);h=max(2,int(manifest['height']*scale)//2*2)
    expected=len(indices)*w*h*3
    if expected>settings.sample_memory_bytes:
        raise Problem('EVIDENCE_MEMORY_BUDGET','Requested visual evidence exceeds the configured memory budget.',
                      'Reduce sample count or dimensions.',422)
    # The same frames of the same file are requested by planning, preflight and review: decode once.
    from .frames import identity
    try:file_identity=identity(path)
    except OSError:file_identity=None # the decode below reports the problem as before
    key=digest({'file':file_identity,'indices':indices,'width':w,'height':h,'version':1})
    cache=Path(path).with_name(Path(path).name+f'.samples-{key[:20]}.npz')
    try:
        if file_identity is None:raise OSError()
        with np.load(cache,allow_pickle=False) as saved:
            if str(saved['key'])==key and saved['pixels'].shape==(len(indices),h,w,3) and saved['pixels'].dtype==np.uint8:
                return {i:frame for i,frame in zip(indices,saved['pixels'].copy())}
    except (OSError,ValueError,KeyError):pass
    selection='+'.join(fr'eq(n\,{i})' for i in indices)
    # -threads 0: all-core decode, byte-identical pixels to the previous fixed 4 threads, ~2.5x faster.
    raw=command([settings.ffmpeg,'-v','error','-nostdin','-threads','0','-i',str(path),'-an','-vf',f'select={selection},scale={w}:{h}',
        '-frames:v',str(len(indices)),'-fps_mode','passthrough','-pix_fmt','rgb24','-f','rawvideo','-'],cancel,60,binary=True)
    if len(raw)!=expected:raise Problem('EVIDENCE_DECODE_FAILED','Decoded evidence does not match the selected frame identities.',
                                       'Inspect frame timing/decoder diagnostics; no model input was accepted.',422)
    pixels=np.frombuffer(raw,np.uint8).reshape(len(indices),h,w,3)
    temporary=cache.with_name('.'+cache.name+'.'+str(time.monotonic_ns())+'.npz')
    try:
        if file_identity is None:raise OSError()
        with temporary.open('wb') as stream:np.savez(stream,key=np.array(key),pixels=pixels)
        temporary.replace(cache)
    except OSError:temporary.unlink(missing_ok=True)
    return {i:frame for i,frame in zip(indices,pixels)}


def measure(pixels):
    rgb=pixels.astype(np.float32)/255;y=rgb@np.array([.2126,.7152,.0722],np.float32)
    hist=np.histogram(y,bins=16,range=(0,1))[0]/y.size
    grey=cv2.cvtColor(pixels,cv2.COLOR_RGB2GRAY)
    high=tonal.highlight_mask(y)
    # Brightest region (shared tonal definition): what a "sky too bright" or "glare" issue is about.
    brightest={'highlight_fraction':float(high.mean()),'highlight_luma':float(y[high].mean()),
               'highlight_rgb':[float(v) for v in rgb[high].mean(axis=0)]} if high is not None else {
               'highlight_fraction':0.,'highlight_luma':None,'highlight_rgb':None}
    return {**brightest,'mean_chroma':float((rgb.max(axis=-1)-rgb.min(axis=-1)).mean()),
            'median_encoded_luma':float(np.median(y)),'p05_encoded_luma':float(np.quantile(y,.05)),
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
    key=digest({'file':file_hash,'source':source.sha256,'budget':budget,'shots':shots,'focus':focus,'version':3})
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


def collect(path,source,settings,cancel,*,selection,frames=None):
    frames=frames if frames is not None else extract(path,selection['manifest'],selection['selected_indices'],settings,cancel,width=256)
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
                'image_budget':settings.sample_image_bytes,'version':2})
    if cache and Path(cache).exists():
        value=json.loads(Path(cache).read_text())
        if value.get('key')==key:return value
    manifest=frame_manifest(rendered,settings,cancel)
    if len(manifest['times'])!=len(selection['manifest']['times']) or any(abs(a-b)>.002 for a,b in zip(manifest['times'],selection['manifest']['times'])):
        raise Problem('REVIEW_TIMING_INVALID','Source/rendered review samples do not align.','Technical validation must pass before creative review.',422)
    from dataclasses import replace
    bounded=replace(settings,sample_image_bytes=settings.sample_image_bytes//2)
    a=extract(original,selection['manifest'],selection['selected_indices'],settings,cancel)
    b=extract(rendered,selection['manifest'],selection['selected_indices'],settings,cancel)
    source_records,source_images=collect(original,source,bounded,cancel,selection=selection,frames=a)
    output_records,output_images=collect(rendered,source,bounded,cancel,selection=selection,frames=b)
    details=[]
    for i in a:
        x=a[i].astype(np.float32)@np.array([.2126,.7152,.0722],np.float32)/255
        y=b[i].astype(np.float32)@np.array([.2126,.7152,.0722],np.float32)/255
        shadow=x<=np.quantile(x,.25);high=x>=np.quantile(x,.95)
        spread=lambda v:float(np.quantile(v,.9)-np.quantile(v,.1))
        detail={'frame_index':i,'source_shadow_spread':spread(x[shadow]),'render_shadow_spread':spread(y[shadow]),
            'source_shadow_clipped_fraction':float(np.mean(x[shadow]<=1/255)),
            'render_shadow_clipped_fraction':float(np.mean(y[shadow]<=1/255)),
            'source_highlight_spread':spread(x[high]),'render_highlight_spread':spread(y[high])}
        # Pixel-paired brightest region: the SAME source pixels before and after, so the critic can see
        # whether a bright sky/sand/window area actually got darker or brighter, and its colour.
        bright=tonal.highlight_mask(x)
        if bright is not None:
            rgb_a=a[i].astype(np.float32)/255;rgb_b=b[i].astype(np.float32)/255
            detail.update(brightest_region_fraction=float(bright.mean()),source_brightest_luma=float(x[bright].mean()),
                render_brightest_luma=float(y[bright].mean()),source_brightest_rgb=[float(v) for v in rgb_a[bright].mean(axis=0)],
                render_brightest_rgb=[float(v) for v in rgb_b[bright].mean(axis=0)])
        details.append(detail)
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
