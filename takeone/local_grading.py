"""Frame-bound SAM mattes and technical QA for the existing FFmpeg renderer."""
import collections
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
import os
import threading
from pathlib import Path
import json
import re
import subprocess

import cv2
import numpy as np

from . import sam
from .errors import Problem
from .media import command, probe


def timeline(directory,source,settings,cancel):
    """Source/proxy identity and native scdet cuts, independent of creative wording.

    A nonzero container start is not rejected here and is never "fixed" by
    resetting timestamps. sam.mapping verifies, from the decoded frames
    themselves, that source and proxy share one relative timeline; the
    absolute first-frame PTS of each file is retained in the frame map.
    The container's declared start_time is a hint only and is not trusted
    as frame identity.
    """
    key={'source_sha256':source.sha256,'proxy_sha256':sam.checksum(directory/'analysis.mp4'),'version':3}
    path=directory/'grading-timeline.json'
    if path.is_file():
        cached=json.loads(path.read_text())
        proxy=directory/cached.get('frame_map',{}).get('proxy_filename','analysis.mp4')
        if cached.get('key')==key and proxy.is_file() and sam.checksum(proxy)==cached['frame_map']['proxy_sha256'] and sam.checksum(directory/'source.bin')==source.sha256:return cached
    mapping=sam.mapping(directory,source,settings,cancel)
    analysis=sam.frame_info(directory/'analysis.mp4',settings,cancel,require_cfr=False)
    if len(analysis['pts'])!=mapping['frame_count']:
        raise Problem('SAM_FRAME_MAPPING_FAILED','The old analysis proxy has a different frame count.',
                      'Re-import the original to prepare a frame-preserving analysis proxy.',422)
    text=command([settings.ffmpeg,'-v','error','-i',str(directory/'analysis.mp4'),'-an',
                  '-vf','scdet=t=10,metadata=mode=print:file=-','-f','null','-'],cancel,120)
    cuts={0}
    for value in re.findall(r'lavfi\.scd\.time=([0-9.]+)',text):
        t=float(value);index=min(range(mapping['frame_count']),key=lambda i:abs(analysis['normalized'][i]-t))
        if abs(analysis['normalized'][index]-t)<=.002:cuts.add(index)
    result={'key':key,'frame_map':mapping,'cut_frames':sorted(cuts)}
    ends=sorted(cuts)+[mapping['frame_count']]
    step=float(1/Fraction(mapping['proxy']['rate']))
    result['shots']=[{'start':mapping['frames'][a]['time'],
                      'end':mapping['frames'][b]['time'] if b<mapping['frame_count'] else source.duration,'first_frame':a,'last_frame':b-1}
                     for a,b in zip(ends,ends[1:]) if b>a]
    sam.atomic(path,result)
    return result


def plan_ranges(plan,evidence,source):
    mapping=evidence['frame_map'];times=[f['time'] for f in mapping['frames']]
    result=[]
    for interval in sorted(plan.intervals,key=lambda v:v.start):
        if interval.end>source.duration+.002:
            raise Problem('LOCAL_INTERVAL_INVALID','The localized plan extends past the source.',
                          'Revise the plan; no mask request was made.',422)
        frames=[i for i,t in enumerate(times) if interval.start<=t<interval.end]
        if not frames:raise Problem('LOCAL_INTERVAL_EMPTY','A local interval contains no source frames.','Use an actual source interval.',422)
        boundaries=[frames[0],*(c for c in evidence['cut_frames'] if frames[0]<c<=frames[-1]),frames[-1]+1]
        result.extend((a,b-1) for a,b in zip(boundaries,boundaries[1:]))
    return result


def records_for_range(manifest,directory,source,first,last,selection,*,require_complete=True):
    mapping=manifest['frame_map']
    if mapping['source_sha256']!=source.sha256:
        raise Problem('LOCAL_MASK_SOURCE','Masks belong to a different original.','Request masks for this source.',409)
    by_frame={}
    object_ids=set()
    for record in manifest['records']:
        local=record['frame_index']
        if not 0<=local<len(mapping['frames']):raise Problem('LOCAL_MASK_FRAME','Invalid mask frame reference.','Rebuild masks.',422)
        frame=mapping['frames'][local]['index']
        if first<=frame<=last:
            item=dict(record,directory=str(directory),proxy=mapping['proxy'])
            by_frame.setdefault(frame,[]).append(item);object_ids.add(record['object_id'])
    missing=[i for i in range(first,last+1) if i not in by_frame]
    if missing and require_complete:
        raise Problem('LOCAL_MASK_GAP','Required local frames have no usable subject mask.',
                      'Inspect tracking/visibility or narrow the requested intervals. No global fallback was rendered.',422,
                      missing_frames=len(missing),first_missing_frame=missing[0])
    if selection=='single_instance' and object_ids and len(object_ids)!=1:
        raise Problem('LOCAL_SUBJECT_AMBIGUOUS','SAM found multiple object IDs in a required shot interval.',
                      'Use a more specific subject phrase or inspect the manual mask tool. No object was guessed.',422,object_count=len(object_ids))
    return by_frame


def raster(records,width,height):
    result=np.zeros((height,width),np.uint8)
    ids=set()
    for r in records:
        proxy=r['proxy']
        if (proxy['width'],proxy['height'])!=(width,height):
            raise Problem('LOCAL_MASK_GEOMETRY','Mask coordinate space differs from the current proxy.','Rebuild aligned masks.',422)
        x,y,x2,y2=r['bounds']
        if any(type(v) is not int for v in [x,y,x2,y2]) or not(0<=x<x2<=width and 0<=y<y2<=height):
            raise Problem('LOCAL_MASK_GEOMETRY','Mask bounds exceed the verified frame.','Inspect/rebuild masks.',422)
        path=(Path(r['directory'])/r['mask']).resolve()
        if not path.is_relative_to(Path(r['directory']).resolve()):
            raise Problem('LOCAL_MASK_PATH','Invalid mask artifact reference.','Rebuild the mask manifest.',422)
        mask=cv2.imread(str(path),cv2.IMREAD_GRAYSCALE)
        if mask is None or mask.size==0:
            raise Problem('LOCAL_MASK_MISSING','A saved mask file is missing or unreadable.','Restore/rebuild masks before retrying.',422)
        result[y:y2,x:x2]|=(cv2.resize(mask,(x2-x,y2-y),interpolation=cv2.INTER_NEAREST)>127).astype(np.uint8)
        ids.add(r['object_id'])
    if not result.any():raise Problem('LOCAL_MASK_EMPTY','A required mask has no foreground pixels.','Inspect segmentation before grading.',422)
    return result,ids


MATTE_VERSION=1


def matte_key(proxy,source,evidence,records,plan,adaptive):
    """Everything a matte depends on: masks (and their files), feather, timeline, geometry and code version."""
    from .evidence import digest
    frames=[]
    for index in sorted(records):
        rows=[]
        for r in records[index]:
            path=Path(r['directory'])/r['mask'];stat=path.stat()
            rows.append([r['mask'],r['directory'],list(r['bounds']),r['object_id'],r['proxy'],stat.st_size,stat.st_mtime_ns])
        frames.append([index,rows])
    return digest({'frames':frames,'feather':plan.feather_px,'adaptive':adaptive,'timeline':evidence['key'],
                   'cuts':evidence['cut_frames'],'proxy':str(Path(proxy).name),'size':[source.width,source.height],'version':MATTE_VERSION})


def prepare_matte(proxy,output,source,evidence,records,plan,settings,cancel,*,adaptive=False,name='matte'):
    """Encode and validate one lossless matte; name separates the subject matte from region mattes.

    A matte depends only on its masks, feather, timeline and geometry, so revisions that keep them (the
    usual critic revision changes colour, not masks) reuse the validated matte instead of rebuilding it.
    """
    output.mkdir(parents=True,exist_ok=True)
    try:key=matte_key(proxy,source,evidence,records,plan,adaptive)
    except OSError:key=None # a mask file is unreadable: build normally, which reports it
    cache=Path(proxy).parent/'matte-cache'
    if key and (cache/f'{key[:32]}.mkv').is_file() and (cache/f'{key[:32]}.json').is_file():
        try:
            qa=json.loads((cache/f'{key[:32]}.json').read_text())
            target=output/f'{name}.mkv';target.unlink(missing_ok=True)
            try:os.link(cache/f'{key[:32]}.mkv',target)
            except OSError:import shutil;shutil.copyfile(cache/f'{key[:32]}.mkv',target)
            sam.atomic(output/('mask-validation.json' if name=='matte' else f'{name}-validation.json'),qa)
            return dict(qa,matte_cache_hit=True)
        except (OSError,ValueError):pass # unusable cache entry: rebuild
    qa=_build_matte(proxy,output,source,evidence,records,plan,settings,cancel,adaptive=adaptive,name=name)
    if key:
        try:
            cache.mkdir(exist_ok=True);temporary=cache/f'.{key[:32]}.{os.getpid()}.mkv'
            try:os.link(output/f'{name}.mkv',temporary)
            except OSError:import shutil;shutil.copyfile(output/f'{name}.mkv',temporary)
            os.replace(temporary,cache/f'{key[:32]}.mkv');sam.atomic(cache/f'{key[:32]}.json',qa)
        except OSError:pass # caching is best effort
    return qa


def _build_matte(proxy,output,source,evidence,records,plan,settings,cancel,*,adaptive=False,name='matte'):
    mapping=evidence['frame_map'];w,h=mapping['proxy']['width'],mapping['proxy']['height']
    capture=cv2.VideoCapture(str(proxy));last_gray=last_mask=last_ids=None
    cuts=set(evidence['cut_frames']);unstable=[];warnings=[];active=0;uncertain_flow=[]
    flow_scale=min(160/w,160/h,1);fw,fh=max(8,int(w*flow_scale)),max(8,int(h*flow_scale))
    args=[settings.ffmpeg,'-v','error','-y','-f','rawvideo','-pixel_format','gray16le','-video_size',f'{source.width}x{source.height}',
          '-framerate',mapping['proxy']['rate'],'-i','-','-an','-c:v','ffv1','-level','3','-slices','24','-threads','0', # lossless; slices let FFV1 use all cores
          '-pix_fmt','gray16le',str(output/f'{name}.partial.mkv')]
    output.mkdir(parents=True,exist_ok=True)
    with (output/f'{name}-encode.log').open('wb') as log:
        process=subprocess.Popen(args,stdin=subprocess.PIPE,stderr=log)
        try:
            for index in range(mapping['frame_count']):
                sam.check(cancel);ok,pixels=capture.read()
                if not ok:raise Problem('LOCAL_PROXY_DECODE','Proxy frame count changed during mask validation.','Prepare the source again.',422)
                if index not in records:
                    alpha=np.zeros((source.height,source.width),np.uint16);last_gray=last_mask=last_ids=None
                else:
                    binary,ids=raster(records[index],w,h);active+=1
                    gray=cv2.resize(cv2.cvtColor(pixels,cv2.COLOR_BGR2GRAY),(fw,fh))
                    small=cv2.resize(binary,(fw,fh),interpolation=cv2.INTER_NEAREST).astype(np.float32)
                    current=binary.astype(np.float32)
                    soft=cv2.GaussianBlur(current,(0,0),plan.feather_px)
                    if last_mask is not None and index not in cuts and ids==last_ids:
                        flow=cv2.calcOpticalFlowFarneback(gray,last_gray,None,.5,2,11,2,5,1.1,0)
                        xx,yy=np.meshgrid(np.arange(fw,dtype=np.float32),np.arange(fh,dtype=np.float32))
                        warped=cv2.remap(last_mask,xx+flow[:,:,0],yy+flow[:,:,1],cv2.INTER_LINEAR)
                        intersection=np.logical_and(warped>.5,small>.5).sum();union=np.logical_or(warped>.5,small>.5).sum()
                        iou=float(intersection/max(1,union))
                        bad=iou<.35;registration_valid=True;detail={'frame':index,'motion_compensated_iou':round(iou,4)}
                        if adaptive:
                            registered=cv2.remap(last_gray,xx+flow[:,:,0],yy+flow[:,:,1],cv2.INTER_LINEAR)
                            error=float(np.median(np.abs(registered.astype(float)-gray))/255)
                            tolerance=max(2/255,float(np.quantile(gray,.9)-np.quantile(gray,.1))/255*.1)
                            edge=cv2.dilate((small>.5).astype(np.uint8),np.ones((3,3),np.uint8))-cv2.erode((small>.5).astype(np.uint8),np.ones((3,3),np.uint8))
                            allowance=min(1,float(edge.sum())*2/max(1,union))
                            registration_valid=error<=tolerance;bad=registration_valid and 1-iou>allowance
                            detail.update(photometric_error=round(error,5),registration_tolerance=round(tolerance,5),boundary_disagreement_allowance=round(allowance,5))
                            if not registration_valid:uncertain_flow.append(detail)
                        if bad:unstable.append(detail)
                        elif registration_valid:
                            band=(cv2.dilate(binary,np.ones((3,3),np.uint8))!=cv2.erode(binary,np.ones((3,3),np.uint8)))
                            prior=cv2.resize(warped,(w,h))
                            soft[band]=.8*soft[band]+.2*prior[band]
                    # Current-frame support is authoritative; no hold across gaps or cuts.
                    soft[cv2.erode(binary,np.ones((3,3),np.uint8))>0]=1
                    soft[cv2.dilate(binary,np.ones((5,5),np.uint8))==0]=0
                    alpha=(np.clip(cv2.resize(soft,(source.width,source.height),interpolation=cv2.INTER_LINEAR),0,1)*65535).astype('<u2')
                    last_gray,last_mask,last_ids=gray,small,ids
                process.stdin.write(alpha.tobytes())
            if capture.read()[0]:raise Problem('LOCAL_PROXY_DECODE','Proxy has extra unmapped frames.','Prepare the source again.',422)
            process.stdin.close()
            if process.wait(timeout=90):raise Problem('LOCAL_MATTE_ENCODE','FFmpeg could not encode the lossless matte.',f'Inspect {name}-encode.log and retry local processing.',422)
        except Exception as error:
            process.kill();process.wait()
            if isinstance(error,Problem):raise
            raise Problem('LOCAL_MATTE_ENCODE','FFmpeg failed while preparing the localized matte.',
                          f'Inspect {name}-encode.log and codec support, then retry the saved local step. Source and masks are retained.',422,
                          exception_type=type(error).__name__)
        finally:
            capture.release()
            if not process.stdin.closed:process.stdin.close()
    if unstable:
        warnings.append(f'{len(unstable)} frames have low motion-compensated mask overlap; this diagnostic is not model confidence.')
    if uncertain_flow:
        warnings.append(f'Motion registration is uncertain on {len(uncertain_flow)} frames; temporal smoothing was not applied there. Inspect moving edges.')
    consecutive=any(b['frame']==a['frame']+1 and c['frame']==b['frame']+1 for a,b,c in zip(unstable,unstable[1:],unstable[2:]))
    qa={'active_frames':active,'frame_count':mapping['frame_count'],'cut_frames':evidence['cut_frames'],
        'uncertain_registration_frames':uncertain_flow,'stability_policy':'content_scaled_boundary_and_registration' if adaptive else 'legacy_iou',
        'unstable_frames':unstable,'warnings':warnings,'refinement':'current-supported spatial feather + 20% motion-compensated boundary smoothing; reset at cuts/gaps'}
    sam.atomic(output/('mask-validation.json' if name=='matte' else f'{name}-validation.json'),qa)
    if consecutive or len(unstable)>max(2,active*.1):
        raise Problem('LOCAL_MASK_UNSTABLE','Required masks are temporally unstable within a shot.',
                      'Inspect mask-validation.json and the diagnostic overlay; narrow/rebuild the affected interval.',422,
                      flagged_frames=len(unstable))
    (output/f'{name}.partial.mkv').replace(output/f'{name}.mkv')
    matte=sam.frame_info(output/f'{name}.mkv',settings,cancel,interval_tolerance=.002)
    expected=[i/float(Fraction(mapping['proxy']['rate'])) for i in range(mapping['frame_count'])] if mapping.get('schema_version')==3 else [f['time'] for f in mapping['frames']]
    if len(matte['pts'])!=mapping['frame_count'] or any(abs(t-e)>.002 for t,e in zip(matte['normalized'],expected)):
        raise Problem('LOCAL_MATTE_TIMING','The matte does not align frame-for-frame with the source.','Rebuild the matte.',422)
    return qa


QC_WORKERS=max(1,min(12,(os.cpu_count() or 2)-1))
_QC_POOL=None


def _qc_pool():
    """Shared worker pool for per-frame output statistics (NumPy releases the GIL in this work)."""
    global _QC_POOL
    if _QC_POOL is None:
        _QC_POOL=ThreadPoolExecutor(max_workers=QC_WORKERS,thread_name_prefix='takeone-qc')
    return _QC_POOL


def frame_statistics(x,y,mask):
    """Per-frame subject/background measurements, unchanged maths from the former sequential loop.

    x, y: source/output frames already resized to the proxy (uint8 BGR); mask: proxy uint8 0/1.
    Returns {layer: (shadow_increase, luma_ratio, chroma, highlight_chroma, mean_change, near_black_increase) | None}.
    """
    cores={'subject':cv2.erode(mask,np.ones((5,5),np.uint8))>0,
           'background':cv2.erode(1-mask,np.ones((5,5),np.uint8))>0}
    x=x.astype(np.float32)/255;y=y.astype(np.float32)/255
    old=x@np.array([.0722,.7152,.2126]);new=y@np.array([.0722,.7152,.2126])
    result={}
    for name,core in cores.items():
        if core.sum()<4:result[name]=None;continue
        increase=float(np.mean(new[core]<=.01)-np.mean(old[core]<=.01))
        # A 0.005 encoded-luma allowance avoids treating one-code quantization as lost legibility.
        ratio=float((np.median(new[core])+.005)/max(.005,np.median(old[core])))
        valid=core&(x.sum(axis=2)>.15)&(y.sum(axis=2)>.15)
        difference=np.zeros(core.shape,np.float32)
        if valid.any():
            difference[valid]=np.abs(x[valid]/x[valid].sum(axis=1,keepdims=True)-y[valid]/y[valid].sum(axis=1,keepdims=True)).mean(axis=1)
        chroma=float(np.median(difference[valid])) if valid.any() else 0
        highlights=valid&(x.max(axis=2)>.75)
        light_chroma=float(np.quantile(difference[highlights],.95)) if highlights.any() else 0
        change=float(np.abs(y[core]-x[core]).mean())
        black=float(np.mean(new[core]<=.02)-np.mean(old[core]<=.02)) if name=='background' else 0.
        result[name]=(increase,ratio,chroma,light_chroma,change,black)
    return result


def _output_timing(graded,mapping,settings,cancel):
    timing=sam.frame_info(graded,settings,cancel,require_cfr=mapping.get('schema_version')!=3)
    if len(timing['pts'])!=mapping['frame_count'] or any(abs(t-f['time'])>.002 for t,f in zip(timing['normalized'],mapping['frames'])):
        raise Problem('LOCAL_OUTPUT_TIMING','Rendered frames no longer match source timing.','The candidate was not accepted.',422)


def _measure_output(a,b,mapping,records,cancel,stop=None):
    """Per-frame protected-area statistics of two open captures, folded in frame order; releases both."""
    w,h=mapping['proxy']['width'],mapping['proxy']['height']
    def empty():return {'shadow_increase':0.,'subject_luma_ratio':1.,'chroma_change':0.,'highlight_chroma_change':0.}
    worst={'subject':empty(),'background':empty()};counts={'subject':0,'background':0};background_black=0.;pixel_change={'subject':0.,'background':0.};worst_frames={}
    # Frames are read in order here; each frame's statistics are independent, so they run on all cores
    # and are folded back strictly in frame order, giving exactly the sequential results (including which
    # frame is reported as worst and which frame raises first).
    pending=collections.deque();window=max(4,4*QC_WORKERS)
    def fold(i,future):
        nonlocal background_black
        stats=future.result()
        for name in ['subject','background']:
            value=stats[name]
            if value is None:
                if name=='subject':raise Problem('LOCAL_MASK_CORE','The selected subject has no measurable interior.','Inspect the mask geometry.',422)
                continue
            increase,ratio,chroma,light_chroma,change,black=value
            metrics=worst[name]
            pixel_change[name]=max(pixel_change[name],change)
            metrics['shadow_increase']=max(metrics['shadow_increase'],increase)
            if ratio<metrics['subject_luma_ratio']:worst_frames[name]=i
            metrics['subject_luma_ratio']=min(metrics['subject_luma_ratio'],ratio)
            metrics['chroma_change']=max(metrics['chroma_change'],chroma)
            metrics['highlight_chroma_change']=max(metrics['highlight_chroma_change'],light_chroma);counts[name]+=1
            if name=='background':background_black=max(background_black,black)
    pool=_qc_pool()
    try:
        for i in range(mapping['frame_count']):
            sam.check(cancel)
            if stop is not None and stop.is_set():raise Problem('JOB_CANCELLED','The output check was abandoned.','The render did not complete.',409)
            oka,x=a.read();okb,y=b.read()
            if not oka or not okb:raise Problem('LOCAL_OUTPUT_DECODE','Source/output decode ended before the mapped frames.','Candidate was not accepted.',422)
            if i not in records:continue
            mask,_=raster(records[i],w,h)
            pending.append((i,pool.submit(frame_statistics,cv2.resize(x,(w,h)),cv2.resize(y,(w,h)),mask)))
            while len(pending)>window:fold(*pending.popleft())
        while pending:fold(*pending.popleft())
    finally:
        for _,future in pending:future.cancel()
        a.release();b.release()
    return worst,counts,background_black,pixel_change,worst_frames


def _judge_output(measured,plan,limits,adaptive):
    worst,counts,background_black,pixel_change,worst_frames=measured
    neutral=all(abs(getattr(plan.background,k)-v)<1e-8 for k,v in
                [('contrast',1),('gamma',1),('saturation',1),('brightness_gain',1),('cooling',0),('highlight_ceiling',1)])
    result={'checked_subject_frames':counts['subject'],'worst_measured':worst['subject'],
            'background_checked_frames':counts['background'],'background_worst_measured':worst['background'],
            'background_near_black_increase_max':background_black,'review_status':'needs_user_review',
            'max_layer_mean_rgb_change':pixel_change,
            'warnings':[f'Background near-black fraction increased by up to {background_black:.3f}; inspect meaningful dark detail.'] if background_black>.08 and not adaptive else [],
            'limitations':['Mask-core measurements are technical proxies, not semantic paint/light fidelity or aesthetic approval.']}
    for name,protected in [('subject',plan.preserve_subject_color),('background',neutral)]:
        metrics=worst[name]
        failed=(metrics['shadow_increase']>.05 or metrics['subject_luma_ratio']<.85 or metrics['chroma_change']>.035 or metrics['highlight_chroma_change']>.06) if not adaptive else (
            limits is not None and name=='subject' and (
                limits.subject_min_luma_ratio is not None and metrics['subject_luma_ratio']<limits.subject_min_luma_ratio
                or limits.subject_max_chroma_change is not None and metrics['chroma_change']>limits.subject_max_chroma_change))
        enforce=protected if not adaptive else name=='subject' and limits is not None
        if enforce and failed:
            raise Problem('LOCAL_PROTECTION_FAILED','A protected rendered layer did not meet measured preservation bounds.',
                          'Review source/output at the reported frame; the candidate was not accepted.',422,layer=name,
                          minimum_luma_frame=worst_frames.get(name),required_min_luma_ratio=limits.subject_min_luma_ratio if limits else None,**metrics)
    result['change_below_encoding_resolution']=max(pixel_change.values())<1/255
    if max(pixel_change.values())<.01 and not adaptive:
        raise Problem('LOCAL_NO_VISIBLE_EFFECT','The localized recipe produced no measurable change beyond encoding noise.',
                      'Revise the supported treatment; the candidate was not labeled a successful selective edit.',422)
    return result


def check_output(original,graded,source,evidence,records,plan,settings,cancel,*,limits=None,adaptive=False):
    mapping=evidence['frame_map'];_output_timing(graded,mapping,settings,cancel)
    a,b=cv2.VideoCapture(str(original)),cv2.VideoCapture(str(graded))
    return _judge_output(_measure_output(a,b,mapping,records,cancel),plan,limits,adaptive)


_CHECK_RUNNER=None


class PendingOutputCheck:
    """The output check's measurement, started right after encoding so it overlaps the render's own
    integrity decode. finish() then runs the same timing check, measurement and judgement, in the same
    order of precedence as check_output, so results and errors are identical; abort() stops it early."""

    def __init__(self,original,graded,evidence,records,plan,settings,cancel,limits,adaptive):
        global _CHECK_RUNNER
        self.mapping=evidence['frame_map'];self.plan,self.settings,self.cancel=plan,settings,cancel
        self.limits,self.adaptive=limits,adaptive;self.stop=threading.Event()
        # Opened now: the open files survive the render's later rename of the partial output.
        a,b=cv2.VideoCapture(str(original)),cv2.VideoCapture(str(graded))
        if not a.isOpened() or not b.isOpened():
            a.release();b.release();self.future=None;return
        if _CHECK_RUNNER is None:_CHECK_RUNNER=ThreadPoolExecutor(max_workers=1,thread_name_prefix='takeone-output-check')
        self.future=_CHECK_RUNNER.submit(_measure_output,a,b,self.mapping,records,cancel,self.stop)

    def finish(self,graded):
        """Judge the finished output (the final, renamed file) exactly as check_output would."""
        try:
            _output_timing(graded,self.mapping,self.settings,self.cancel)
        except BaseException:
            self.abort();raise
        if self.future is None:raise Problem('LOCAL_OUTPUT_DECODE','Source/output decode ended before the mapped frames.','Candidate was not accepted.',422)
        return _judge_output(self.future.result(),self.plan,self.limits,self.adaptive)

    def abort(self):
        self.stop.set()
        if self.future is not None:
            try:self.future.result()
            except BaseException:pass
