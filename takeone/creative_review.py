"""Bounded paired evidence and executable-plan identity for Muse review."""
from dataclasses import replace
import json
import base64
import cv2
import numpy as np

from . import evidence,local_grading,sam
from .errors import Problem

TECHNICAL_ERRORS={'REVIEW_OUTPUT_CHANGED','REVIEW_TIMING_INVALID','EVIDENCE_TIMING_INVALID','EVIDENCE_DECODE_FAILED','MEDIA_TOOL_FAILED'}
# Semantic mask check: overlay frames per SAM concept, so the critic can say whether a mask covers
# the intended object at all (technical QA checks geometry and timing, never meaning).
OVERLAY_FRAMES_PER_CONCEPT=3
OVERLAY_CONCEPTS=5
OVERLAY_COLOURS=[(255,64,64),(64,200,255),(255,220,0),(120,255,120),(255,120,255)]
LEVEL_KEYS=['p05_encoded_luma','median_encoded_luma','p95_encoded_luma','median_rgb','mean_chroma',
            'highlight_fraction','highlight_luma','highlight_rgb']


def levels(records):
    return [{'frame_index':r['frame_index'],**{k:r.get(k) for k in LEVEL_KEYS}} for r in records]


def _overlay(image,binary,colour):
    pixels=cv2.imdecode(np.frombuffer(base64.b64decode(image['url'].split(',')[1]),np.uint8),cv2.IMREAD_COLOR)
    h,w=pixels.shape[:2];mask=cv2.resize(binary,(w,h),interpolation=cv2.INTER_NEAREST)>0
    tint=np.array(colour[::-1],np.float32)
    out=pixels.astype(np.float32);out[mask]=out[mask]*.55+tint*.45
    contours,_=cv2.findContours(mask.astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    out=out.clip(0,255).astype(np.uint8);cv2.drawContours(out,contours,-1,tuple(int(c) for c in colour[::-1]),1)
    ok,jpeg=cv2.imencode('.jpg',out,[cv2.IMWRITE_JPEG_QUALITY,75])
    return 'data:image/jpeg;base64,'+base64.b64encode(jpeg).decode() if ok else None


def mask_overlays(state,candidate,directory,images,budget):
    """Up to OVERLAY_FRAMES_PER_CONCEPT overlay frames for each SAM concept used by the candidate."""
    runs={r.id:r for r in state.segmentations};by_concept={}
    for run_id in candidate.mask_run_ids:
        path=directory/'segmentations'/run_id/'manifest.json'
        if not path.is_file() or run_id not in runs:continue
        manifest=json.loads(path.read_text());mapping=manifest['frame_map'];concept=runs[run_id].concept
        for record in manifest['records']:
            index=mapping['frames'][record['frame_index']]['index']
            by_concept.setdefault(concept,{}).setdefault(index,[]).append(dict(record,directory=str(path.parent),proxy=mapping['proxy']))
    by_frame={i['frame_index']:i for i in images};result=[];used=0
    for number,(concept,frames) in enumerate(list(by_concept.items())[:OVERLAY_CONCEPTS]):
        available=sorted(set(frames)&set(by_frame))
        if not available:continue
        picks=sorted({available[round(k*(len(available)-1)/max(1,OVERLAY_FRAMES_PER_CONCEPT-1))] for k in range(OVERLAY_FRAMES_PER_CONCEPT)})
        for index in picks:
            records=frames[index];proxy=records[0]['proxy']
            try:binary,_=local_grading.raster(records,proxy['width'],proxy['height'])
            except Problem:continue
            url=_overlay(by_frame[index],binary,OVERLAY_COLOURS[number%len(OVERLAY_COLOURS)])
            if not url:continue
            size=len(url)
            if used+size>budget:continue
            used+=size
            result.append({'frame_index':index,'time':by_frame[index]['time'],'url':url,'concept':concept,'overlay':number,
                           'coverage':float(binary.mean())})
    return result


def execution_identity(plan,operator='luminance_ocio_v3',duration=None):
    def normalize(v):
        if isinstance(v,float):return round(v,8)
        if isinstance(v,dict):return {k:normalize(x) for k,x in v.items()}
        if isinstance(v,list):return [normalize(x) for x in v]
        return v
    value=plan.model_dump(mode='json')
    executable={k:value[k] for k in ['mode','parameters','local']}
    if plan.local and duration is not None:
        intervals=sorted(plan.local.intervals,key=lambda i:i.start);end=0.
        for interval in intervals:
            if interval.start>end+1e-8:break
            end=max(end,interval.end)
        if end>=duration:executable['parameters']=None # Base is never used under full local coverage.
    return evidence.digest({'operator':operator,'plan':normalize(executable)})


def collect(service,state,candidate,cancel):
    directory=service.store.directory(state.id);output=directory/'candidates'/candidate.id
    if not (directory/'source.bin').is_file() or sam.checksum(directory/'source.bin')!=state.source.sha256:
        raise Problem('SOURCE_CHANGED','The source no longer matches the candidate analysis.','Restore the original source before review.',409)
    rendered=output/'grade.mp4'
    initial_hash=sam.checksum(rendered) if rendered.exists() else None
    if not rendered.exists() or candidate.output_sha256 and sam.checksum(rendered)!=candidate.output_sha256:
        raise Problem('REVIEW_OUTPUT_CHANGED','The rendered candidate is missing or changed.','Restore/re-render it; no critic call was made.',409)
    # Legacy accepted files are decoded again before first review; new files have a bound QC hash.
    if not candidate.output_sha256:
        sam.command([service.settings.ffmpeg,'-v','error','-xerror','-i',str(rendered),'-f','null','-'],cancel,120)
    focus=[(i.start,i.end) for i in candidate.recipe.proposal.local.intervals] if candidate.recipe.proposal.local else []
    shots=candidate.recipe.timeline['shots'] if candidate.recipe.timeline else None
    source_plan=evidence.plan(directory/'source.bin',state.source,service.settings,cancel,proxy=directory/'analysis.mp4',
                              shots=shots,focus=focus,cache=directory/'source-review-evidence-v3.json')
    result_plan=evidence.plan(rendered,state.source,service.settings,cancel,shots=shots,focus=focus,cache=output/'review-render-selection.json')
    # Add measured output changes to source coverage; frame identities remain shared.
    source_by={f['frame_index']:f for f in source_plan['features']}
    changes=sorted(result_plan['features'],key=lambda f:abs(f['median_encoded_luma']-source_by.get(f['frame_index'],f)['median_encoded_luma'])
                   +abs(f['black_fraction']-source_by.get(f['frame_index'],f)['black_fraction']),reverse=True)
    indices=[]
    for i in [changes[0]['frame_index'],*source_plan['selected_indices'],*result_plan['selected_indices']]:
        if i not in indices:indices.append(i)
        if len(indices)>=service.settings.sample_count:break
    selection=dict(source_plan,selected_indices=sorted(indices))
    previous=next((c for c in reversed(state.candidates) if c.id!=candidate.id and c.status=='ready'
        and c.source_sha256==candidate.source_sha256 and (c.id==state.selected_candidate_id or c.id==candidate.parent_id)),None)
    budget=service.settings.sample_image_bytes
    roles=2+(1 if previous else 0)+(2 if focus else 0)
    pair_settings=replace(service.settings,sample_image_bytes=budget*2//roles)
    data=evidence.paired(directory/'source.bin',rendered,state.source,pair_settings,cancel,selection,cache=output/'review-evidence.json')
    comparison=None
    if previous:
        path=directory/'candidates'/previous.id/'grade.mp4'
        if path.exists() and (not previous.output_sha256 or sam.checksum(path)==previous.output_sha256):
            try:
                if not previous.output_sha256:
                    sam.command([service.settings.ffmpeg,'-v','error','-xerror','-i',str(path),'-f','null','-'],cancel,120)
                previous_manifest=evidence.frame_manifest(path,service.settings,cancel)
                if len(previous_manifest['times'])!=len(selection['manifest']['times']) or any(abs(a-b)>.002 for a,b in zip(previous_manifest['times'],selection['manifest']['times'])):raise ValueError('timing')
                previous_records,images=evidence.collect(path,state.source,replace(service.settings,sample_image_bytes=budget//roles),cancel,selection=selection)
                common={i['frame_index'] for i in images}&{i['frame_index'] for i in data['source_images']}&{i['frame_index'] for i in data['candidate_images']}
                if common:
                    data=dict(data,source_images=[i for i in data['source_images'] if i['frame_index'] in common],
                              candidate_images=[i for i in data['candidate_images'] if i['frame_index'] in common])
                    comparison={'id':previous.id,'images':[i for i in images if i['frame_index'] in common],'output_sha256':sam.checksum(path),
                                'records':previous_records}
            except (Problem,ValueError):
                data['uncertainty']=data['uncertainty']+['Previous candidate could not be aligned for comparison; it was not shown.']
    regions={}
    # Subject crops come from the subject concept's masks only; additional region masks (sky,
    # water...) would otherwise widen the crop to most of the frame.
    used=((candidate.validation or {}).get('mask_validation') or {}).get('subject_concept_used')
    subject=(used or candidate.recipe.proposal.local.concept).strip().casefold() if candidate.recipe.proposal.local else None
    concepts={r.id:r.concept.strip().casefold() for r in state.segmentations}
    for run_id in candidate.mask_run_ids:
        path=directory/'segmentations'/run_id/'manifest.json'
        if not path.is_file() or concepts.get(run_id,subject)!=subject:continue
        manifest=json.loads(path.read_text());mapping=manifest['frame_map']
        for record in manifest['records']:
            index=mapping['frames'][record['frame_index']]['index']
            if index in selection['selected_indices']:
                regions.setdefault(index,[]).append((record['bounds'],mapping['proxy']))
    def crops(images):
        result=[];used=0
        for image in images:
            boxes=regions.get(image['frame_index'])
            if not boxes:continue
            pixels=cv2.imdecode(np.frombuffer(base64.b64decode(image['url'].split(',')[1]),np.uint8),cv2.IMREAD_COLOR)
            h,w=pixels.shape[:2]
            x=min(b[0]/p['width'] for b,p in boxes);y=min(b[1]/p['height'] for b,p in boxes)
            x2=max(b[2]/p['width'] for b,p in boxes);y2=max(b[3]/p['height'] for b,p in boxes)
            if (x2-x)*(y2-y)>.9:continue
            left=max(0,int(x*w)-3);top=max(0,int(y*h)-3);right=min(w,int(x2*w)+3);bottom=min(h,int(y2*h)+3)
            crop=pixels[top:bottom,left:right]
            if not crop.size:continue
            scale=min(256/crop.shape[1],256/crop.shape[0]);crop=cv2.resize(crop,(max(1,int(crop.shape[1]*scale)),max(1,int(crop.shape[0]*scale))))
            ok,jpeg=cv2.imencode('.jpg',crop,[cv2.IMWRITE_JPEG_QUALITY,78])
            if not ok:continue
            size=4*((len(jpeg)+2)//3)+len('data:image/jpeg;base64,')
            if used+size>budget//roles:continue
            used+=size
            result.append({**image,'url':'data:image/jpeg;base64,'+base64.b64encode(jpeg).decode(),
                           'view':'subject_region','bounds_fraction':[x,y,x2,y2]})
        return result
    source_crops=crops(data['source_images']);candidate_crops=crops(data['candidate_images'])
    crop_indices={i['frame_index'] for i in source_crops}&{i['frame_index'] for i in candidate_crops}
    data=dict(data,source_region_images=[i for i in source_crops if i['frame_index'] in crop_indices],
              candidate_region_images=[i for i in candidate_crops if i['frame_index'] in crop_indices])
    data['mask_overlay_images']=mask_overlays(state,candidate,directory,data['source_images'],budget//4)
    data['tonal_levels']={'source':levels(data['source_records']),'candidate':levels(data['candidate_records']),
                          'previous':levels(comparison['records']) if comparison else None}
    if sam.checksum(rendered)!=initial_hash:
        raise Problem('REVIEW_OUTPUT_CHANGED','The candidate changed while evidence was collected.','Re-render or restore the frozen candidate.',409)
    data['rendered_sha256']=initial_hash
    summary=[{'frame_index':f['frame_index'],'time':f['time'],'roles':['source','candidate']+(['previous'] if comparison else [])
              +(['source_region','candidate_region'] if any(i['frame_index']==f['frame_index'] for i in data['candidate_region_images']) else [])
              +[f"mask_overlay_{o['overlay']}" for o in data['mask_overlay_images'] if o['frame_index']==f['frame_index']]}
             for f in data['source_images']]
    return data,comparison,summary
