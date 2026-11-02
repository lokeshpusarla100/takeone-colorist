"""Versioned SDR global/local rendering from the original with bounded evidence/QC."""
import base64
import hashlib
import json
import math
from pathlib import Path
import threading
import time
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction

import cv2
import numpy as np

from .config import Settings
from .contracts import GradeParameters, GradeRecipe, Source
from .errors import Problem
from .media import command, probe
from . import evidence
from . import color_processing
from . import timing


_BUILD_IDENTITIES={}


def _ffmpeg_build(settings,cancel):
    path=Path(shutil.which(settings.ffmpeg) or settings.ffmpeg)
    try:
        stat=path.stat();key=(str(path.resolve()),stat.st_mtime_ns,stat.st_size)
    except OSError:key=(str(path),None,None)
    if key not in _BUILD_IDENTITIES:
        _BUILD_IDENTITIES[key]=command([settings.ffmpeg,'-version'],cancel,10)
    return _BUILD_IDENTITIES[key]


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify_source(path: Path, source: Source, settings: Settings, cancel: threading.Event):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            if cancel.is_set():
                raise Problem("JOB_CANCELLED", "Rendering cancelled.", "Saved versions remain available.", 409)
            digest.update(chunk)
    if digest.hexdigest() != source.sha256:
        raise Problem("SOURCE_CHANGED", "The source file no longer matches the analyzed upload.",
                      "Re-import the original. No recipe was applied to the changed file.", 409)
    metadata = probe(path, settings, cancel)
    video = next(s for s in metadata["streams"] if s["codec_type"] == "video")
    if (video.get("pix_fmt") != "yuv420p" or video.get("color_transfer") != "bt709"
            or video.get("color_primaries") != "bt709" or video.get("color_space") != "bt709"
            or video.get("color_range") != "tv" or source.width % 2 or source.height % 2):
        raise Problem("GRADE_DOMAIN_UNSUPPORTED", "Global grading requires known 8-bit limited-range Rec.709 input.",
                      "Use supported SDR footage. No HDR/log interpretation or precision reduction is assumed.", 422)
    return metadata


def _legacy_samples(path: Path, source: Source, settings: Settings, cancel: threading.Event):
    """Small uncompressed analysis frames; final rendering never uses these pixels."""
    import subprocess
    scale = min(256 / source.width, 256 / source.height, 1)
    width = max(2, int(source.width * scale) // 2 * 2)
    height = max(2, int(source.height * scale) // 2 * 2)
    last_seek = max(0, source.duration - 0.25)
    times = sorted({round(min(last_seek, max(0, source.duration * part)), 3) for part in (0.0, 0.33, 0.67, 0.95)})
    records, images = [], []
    for second in times:
        if cancel.is_set():
            raise Problem("JOB_CANCELLED", "Rendering cancelled.", "The source and brief are kept.", 409)
        args = [settings.ffmpeg, "-hide_banner", "-v", "error", "-nostdin", "-ss", str(second), "-i", str(path),
                "-frames:v", "1", "-vf", f"scale={width}:{height}", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
        try:
            result = subprocess.run(args, capture_output=True, timeout=20, check=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            raise Problem("GRADE_SAMPLE_FAILED", "Could not inspect the selected source frames.",
                          "Retry the render step. No model request was made for incomplete evidence.", 422)
        if len(result.stdout) != width * height * 3:
            raise Problem("GRADE_SAMPLE_FAILED", "A source sample has incomplete image data.", "Inspect the source timing.", 422)
        pixels = np.frombuffer(result.stdout, np.uint8).reshape(height, width, 3)
        luma = pixels.astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32) / 255
        records.append({
            "requested_time": second,
            "median_encoded_luma": round(float(np.median(luma)), 4),
            "near_black_fraction": round(float(np.mean(luma <= 0.02)), 4),
            "near_white_fraction": round(float(np.mean(luma >= 0.98)), 4),
            "black_fraction":round(float(np.mean(luma<=1/255)),4),
            "p05_encoded_luma":round(float(np.quantile(luma,.05)),4),
            "p95_encoded_luma":round(float(np.quantile(luma,.95)),4),
            "median_rgb":[round(float(v)/255,4) for v in np.median(pixels,axis=(0,1))],
        })
        ok, jpeg = cv2.imencode(".jpg", pixels[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            raise Problem("GRADE_SAMPLE_FAILED", "Could not encode source evidence.", "Retry source inspection.", 500)
        images.append({"time": second, "url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()})
    return records, images


def samples(path,source,settings,cancel,*,selection=None,proxy=None,shots=None,focus=(),cache=None):
    selection=selection or evidence.plan(path,source,settings,cancel,proxy=proxy,shots=shots,focus=focus,cache=cache)
    return evidence.collect(path,source,settings,cancel,selection=selection)


def treatment_filter(parameters: GradeParameters, *, operator='bounded_sigmoid_v2', protected=False):
    p = parameters
    if protected:
        if p.brightness_gain==1:return 'null'
        peak='max(1,max(r(X,Y),max(g(X,Y),b(X,Y))))'
        gain=f'min({p.brightness_gain:.8f},65535/{peak})'
        return 'geq='+':'.join(f"{c}='{c}(X,Y)*{gain}'" for c in ['r','g','b'])
    if operator=='legacy_fixed_pivot':
        tone=f"clip((pow(val/maxval,1/{p.gamma:.8f})-0.5)*{p.contrast:.8f}+0.5,0,1)*maxval"
    else:
        x=f'pow(clip(val/maxval,0,1),1/{p.gamma:.8f})'
        y=f'(pow({x},{p.contrast:.8f})/(pow({x},{p.contrast:.8f})+pow(1-{x},{p.contrast:.8f})))'
        tone=f'({p.brightness_gain:.8f}*{y}/(1+({p.brightness_gain:.8f}-1)*{y}))*maxval'
    saturation = p.saturation
    weights = [0.2126, 0.7152, 0.0722]
    names = ["r", "g", "b"]
    matrix = ":".join(
        names[row] + names[col] + "=" + f"{((1-saturation)*weights[col] + (saturation if row==col else 0)):.8f}"
        for row in range(3) for col in range(3)
    )
    c=p.cooling
    norm=1/(0.2126*(1-c)+0.7152+0.0722*(1+c))
    balance=f'colorchannelmixer=rr={(1-c)*norm:.8f}:gg={norm:.8f}:bb={(1+c)*norm:.8f}'
    return "lutrgb=r='"+tone+"':g='"+tone+"':b='"+tone+"',colorchannelmixer="+matrix+','+balance+',format=gbrp16le'


def filter_graph(parameters: GradeParameters, *, recipe=None,directory=None,first_frame_offset=0.,fast_local=True,native_time_base=None) -> str:
    operator=recipe.tone_operator if recipe else 'bounded_sigmoid_v2'
    def treatment(p,protected=False):
        return color_processing.lut(directory,p,protected) if operator=='luminance_ocio_v3' else treatment_filter(p,operator=operator,protected=protected)
    start='[0:v]format=gbrp16le,setparams=range=full'
    if recipe and recipe.proposal.mode=='localized':
        local=recipe.proposal.local
        ranges=recipe.timeline['active_frame_ranges']
        condition='+'.join(f'(gte(n,{a})*lte(n,{b}))' for a,b in ranges)
        mapping=recipe.timeline['frame_map'];rate=Fraction(mapping['proxy']['rate']);dt=float(1/rate)
        weights=[];cuts=set(recipe.timeline['cut_frames'])
        for a,b in ranges:
            # Frame-index weight, not presentation time. The render preserves
            # source PTS (-copyts -start_at_zero), so a video stream whose
            # first PTS sits after the audio/container origin would shift
            # every T-based gate and silently ungrade the final frame(s).
            # The localized timeline is verified CFR, so frame index is the
            # exact identity; the 0.15 s ramp is expressed in frames.
            ramp_frames=max(1.,min(.15/dt,(b-a+1)/2))
            # blend's all_expr N is 1-based (verified against the FFmpeg in
            # use: the last of 12 frames evaluates N=12), unlike the 0-based
            # n in the enable expression. Convert once: index = N-1.
            incoming='1' if a in cuts else f'clip((N-1-{a})/{ramp_frames:.9f},0,1)'
            outgoing='1' if b+1==mapping['frame_count'] or b+1 in cuts else f'clip(({b}-N+2)/{ramp_frames:.9f},0,1)'
            weights.append(f'(gte(N,{a+1})*lte(N,{b+1})*min({incoming},{outgoing}))')
        weight='+'.join(weights)
        steady=[];fading=[];used=[]
        for index in range(mapping['frame_count']):
            value=0.
            for a,b in ranges:
                if a<=index<=b:
                    ramp=float(f'{max(1.,min(.15/dt,(b-a+1)/2)):.9f}')
                    incoming=1 if a in cuts else min(1,max(0,(index-a)/ramp))
                    outgoing=1 if b+1==mapping['frame_count'] or b+1 in cuts else min(1,max(0,(b-index+1)/ramp))
                    value+=min(incoming,outgoing)
            steady.append(value==1);fading.append(0<value<1);used.append(value>0)
        def enabled(flags):
            spans=[];i=0
            while i<len(flags):
                if not flags[i]:i+=1;continue
                a=i
                while i+1<len(flags) and flags[i+1]:i+=1
                spans.append(f'between(n,{a},{i})');i+=1
            return '+'.join(spans) or '0'
        def gated(p,flags,protected=False):
            op=treatment(p,protected)
            return op+f":enable='{enabled(flags)}'" if fast_local and op.startswith('lut3d=') else op
        # SIMD copies cover steady sections. The original floating-point blend
        # expression remains solely on transition frames, preserving its math.
        start+=(',split=4[basein][fgin][bgin][nativeclock];' if operator=='luminance_ocio_v3' else ',split=3[basein][fgin][bgin];')
        start+='[basein]'+gated(parameters,[not x for x in steady])+'[base];'
        foreground_used=[any(a<=i<=b for a,b in ranges) for i in range(mapping['frame_count'])] if operator=='luminance_ocio_v3' else used
        start+='[fgin]'+gated(local.foreground,foreground_used,local.preserve_subject_color)+'[fg];'
        start+='[bgin]'+gated(local.background,used)+'[bg];'
        # Matroska stores milliseconds; rebase the verified CFR mask by frame
        # index and place each mask half a frame before its video frame.
        # The video keeps its preserved PTS (which may start after the audio/
        # container origin); framesync pairs a video frame with the latest
        # mask at or before it, so an exactly coincident or late mask lags
        # one frame, while a zero-based mask misaligns when the video origin
        # is offset by a frame or more.
        if mapping.get('schema_version')==3:
            ticks=[f['source_tick'] for f in mapping['frames']]
            tick=Fraction(mapping['source']['time_base'])
            origin=round(Fraction(mapping['source']['format_start'])/tick)
            lead=max(1,min((b-a for a,b in zip(ticks,ticks[1:])),default=2)//2)
            def lookup(lo,hi):
                if hi-lo==1:return str(ticks[lo]-origin-lead)
                middle=(lo+hi)//2
                return f'if(lt(N,{middle}),{lookup(lo,middle)},{lookup(middle,hi)})'
            start+=f"[1:v]settb=expr={tick},setpts='{lookup(0,len(ticks))}',format=gbrp16le,setparams=range=full[mask];"
        else:
            offset_ticks=round((float(first_frame_offset)-float(1/rate)/2)*90000)
            start+=f'[1:v]settb=expr=1/90000,setpts=(N*{90000*rate.denominator})/{rate.numerator}+{offset_ticks},format=gbrp16le,setparams=range=full[mask];'
        if operator=='luminance_ocio_v3':
            start+='[fg]null[subject];[bg]null[local];'
        else:start+='[bg][fg][mask]maskedmerge=planes=7[local];'
        # The base is selected outside requested intervals, not a repeated stale mask.
        if fast_local:
            start+='[local]split=2[solidlocal][fadelocal];'
            start+=f"[base][solidlocal]blend=all_mode=normal:all_opacity=0:enable='{enabled(steady)}':eof_action=endall:repeatlast=0[solid];"
            start+=f"[solid][fadelocal]blend=all_expr='A+(B-A)*({weight})':enable='{enabled(fading)}':eof_action=endall:repeatlast=0"
        else:start+=f"[base][local]blend=all_expr='A+(B-A)*({weight})':enable='{condition}':eof_action=endall:repeatlast=0"
        if operator=='luminance_ocio_v3':
            source_tb=native_time_base or mapping.get('source',{}).get('time_base','1/90000')
            start+=f"[fadedbg];[fadedbg]settb=expr={source_tb}[nativebg];[nativebg][subject][mask]maskedmerge=planes=7[protected];"
            start+='[nativeclock][protected]blend=all_mode=normal:all_opacity=0:eof_action=endall:repeatlast=0,'
        else:start+=','
    else:
        start+=','+treatment(parameters)+','
    return (start+
            "scale=in_range=pc:out_color_matrix=bt709:out_range=tv,format=yuv420p,"
            "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709,"
            "split=2[master][view];"
            "[view]scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1[preview]")


def render_args(source: Path, directory: Path, recipe: GradeRecipe, settings: Settings, audio_codec: str | None, *, timing_policy,filter_threads=None,fast_local=True):
    source=source.resolve();directory=directory.resolve()
    audio = "copy" if audio_codec in {"aac", "mp3", "alac"} else "aac"
    inputs=['-i',str(source)]
    offset=0.
    if recipe.proposal.mode=='localized':
        inputs+=['-i',str(directory/'matte.mkv')]
        # In-filter first video PTS = absolute first-frame PTS minus the
        # container origin that -start_at_zero shifts by.
        try:
            offset=float(recipe.timeline['frame_map']['frames'][0]['source_pts'])-float(timing_policy['source_origin_seconds'])
        except (KeyError,TypeError,ValueError):
            offset=0.
        if not (offset==offset and abs(offset)!=float('inf')):offset=0.
    args=[settings.ffmpeg, "-hide_banner", "-v", "error", "-nostdin", "-y",
            "-filter_complex_threads", str(filter_threads or min(4,max(1,(os.cpu_count() or 2)//3))), "-copyts", "-start_at_zero", *inputs,
            "-filter_complex", filter_graph(recipe.proposal.parameters,recipe=recipe,directory=directory,first_frame_offset=offset,fast_local=fast_local,native_time_base=timing_policy['encoder_time_base']),
            "-map", "[master]", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "fast", "-crf", "17",
            "-threads", "4", "-pix_fmt", "yuv420p", "-fps_mode", "passthrough",
            "-enc_time_base:v",timing_policy['encoder_time_base'],
            "-video_track_timescale",str(timing_policy['video_track_timescale']),
            "-movie_timescale",str(timing_policy['movie_timescale']),"-c:a", audio,
            "-movflags", "+faststart", str(directory / "grade.partial.mp4"),
            "-map", "[preview]", "-map", "0:a:0?", "-c:v", "libvpx-vp9", "-deadline", "realtime",
            "-cpu-used", "6", "-crf", "32", "-b:v", "0", "-threads", "4", "-pix_fmt", "yuv420p",
            "-g", "48", "-keyint_min", "48",
            "-fps_mode", "passthrough", "-enc_time_base:v",timing_policy['encoder_time_base'],
            "-c:a", "libopus", "-b:a", "96k", str(directory / "preview.partial.webm")]
    index=args.index('-filter_complex')
    if len(args[index+1])>60000:
        # A balanced per-frame PTS lookup may exceed Linux's per-argument
        # limit. The graph is generated solely from validated integer PTS.
        graph_path=directory/'native-mask-timing.ffscript'
        temporary=graph_path.with_suffix('.tmp');temporary.write_text(args[index+1]);temporary.replace(graph_path)
        help_text=command([settings.ffmpeg,'-h','full'],threading.Event(),10)
        args[index]='-filter_complex_script' if '-filter_complex_script ' in help_text else '-/filter_complex'
        args[index+1]=str(graph_path)
    return args


def probe_grade_samples(source,directory,recipe,original,settings,cancel,selection):
    """Encode bounded full-resolution global samples with the real master path.

    Spatial transforms, format and encoder quality settings match the master.
    Sparse temporal references differ from a full encode, so this is an early
    check, never a replacement for final dense decode/timing/clipping QC.
    """
    if recipe.proposal.mode!='global':
        raise Problem('GRADE_PREFLIGHT_UNAVAILABLE','Sparse encoder preflight currently supports global grades only.',
                      'Localized candidates retain their dense mask and output validation.',422)
    source=Path(source).resolve();directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    metadata=verify_source(source,original,settings,cancel);policy=timing.encoding_policy(metadata)
    indices=sorted(set(selection['selected_indices']));manifest=selection['manifest']
    if not indices or len(indices)>12 or indices[0]<0 or indices[-1]>=len(manifest['times']):
        raise Problem('GRADE_PREFLIGHT_SELECTION','The preflight selection is invalid or exceeds its sample budget.',
                      'Rebuild bounded source evidence before rendering.',422)
    args=render_args(source,directory,recipe,settings,original.audio_codec,timing_policy=policy)
    spatial_graph=args[args.index('-filter_complex')+1].split(',split=2[master][view];',1)[0]+'[master]'
    key=fingerprint({'source':original.sha256,'indices':indices,'spatial_graph':spatial_graph,
        'tone_operator':recipe.tone_operator,'color_domain':recipe.color_domain,'working_pixel_format':recipe.working_pixel_format,
        'output_pixel_format':recipe.output_pixel_format,'ffmpeg_build':_ffmpeg_build(settings,cancel),
        'ocio':color_processing.ocio.__version__,'numpy':np.__version__,'probe_operator_version':2,
        'encoder':{'codec':'libx264','preset':'fast','crf':17,'threads':4,'filter_threads':min(4,max(1,(os.cpu_count() or 2)//3)),
                   'fps_mode':'passthrough',**policy}})
    path=directory/('preflight-'+key[:24]+'.mp4');receipt=path.with_suffix('.json')
    if path.is_file() and receipt.is_file():
        saved=json.loads(receipt.read_text())
        with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if saved.get('key')==key and saved.get('sha256')==digest:
            return [{**r,'requested_time':manifest['times'][r['frame_index']]} for r in saved['records']]
    output=args.index(str(directory/'grade.partial.mp4'));args=args[:output+1];args[-1]=str(path)
    pos=args.index('-filter_complex')+1
    selected='+'.join(fr'eq(n\,{i})' for i in indices)
    args[pos]=spatial_graph.replace('[0:v]',f'[0:v]select={selected},',1)
    # Audio has no influence on colour; final rendering still preserves/checks it.
    audio_map=args.index('0:a:0?');del args[audio_map-1:audio_map+1]
    args[-1:-1]=['-an','-frames:v',str(len(indices))]
    command(args,cancel,120,cwd=directory)
    observed=evidence.frame_manifest(path,settings,cancel)
    if len(observed['times'])!=len(indices):
        raise Problem('GRADE_PREFLIGHT_SELECTION','Sparse preflight changed the selected frame count.',
                      'No full candidate was rendered. Inspect source/frame selection.',422)
    frames=evidence.extract(path,observed,range(len(indices)),settings,cancel)
    records=[]
    for ordinal,index in enumerate(indices):
        value=evidence.measure(frames[ordinal]);value.update(frame_index=index,requested_time=manifest['times'][index],
            preflight_sparse_temporal_context=True);records.append(value)
    from .sam import atomic
    with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    atomic(receipt,{'key':key,'sha256':digest,'records':records})
    return records


def _elapsed(profile,name,operation):
    start=time.monotonic()
    try:return operation()
    finally:profile[name]=time.monotonic()-start


def render(source: Path, directory: Path, recipe: GradeRecipe, original: Source,
           settings: Settings, cancel: threading.Event, *, on_stage=None,qc_mode='optimized'):
    if qc_mode not in {'optimized','reference'}:raise ValueError('Unknown render QC mode')
    started=time.monotonic();profile={'qc_mode':qc_mode}
    try:
        result=_render(source,directory,recipe,original,settings,cancel,on_stage,qc_mode,profile)
        profile['total_seconds']=time.monotonic()-started;result['performance']=dict(profile)
        return result
    except Problem as error:
        profile['total_seconds']=time.monotonic()-started;error.details['performance']=dict(profile)
        raise


def _render(source,directory,recipe,original,settings,cancel,on_stage,qc_mode,profile):
    source_meta = _elapsed(profile,'source_verify_seconds',lambda:verify_source(source,original,settings,cancel))
    directory.mkdir(exist_ok=True)
    policy=timing.encoding_policy(source_meta)
    existing=(recipe.evidence_selection or {}).get('manifest')
    source_manifest=_elapsed(profile,'source_timeline_seconds',lambda:evidence.source_manifest(source,settings,cancel,
        existing,source_meta,original.sha256,cache=directory.parent/'source-timeline-v1.json')) if qc_mode=='optimized' else (
        existing if existing and 'pts' in existing else _elapsed(profile,'source_timeline_seconds',lambda:evidence.frame_manifest(source,settings,cancel)))
    profile['filter_threads']=min(4,max(1,(os.cpu_count() or 2)//3)) if qc_mode=='optimized' else 2
    if on_stage:on_stage('render_local_grade' if recipe.proposal.mode=='localized' else 'render_grade')
    args=render_args(source,directory,recipe,settings,original.audio_codec,
        timing_policy=policy,filter_threads=profile['filter_threads'],fast_local=qc_mode=='optimized')
    progress_path=directory.resolve()/'encode-progress.log'
    from uuid import uuid4
    for name in ['grade.partial.mp4','preview.partial.webm','grade.mp4','preview.webm','encode-progress.log']:
        path=directory/name
        if path.exists():path.rename(path.with_name(path.name+'.previous-'+uuid4().hex[:8]))
    args[1:1]=['-progress',str(progress_path),'-stats_period','1']
    _elapsed(profile,'encode_seconds',lambda:command(args,cancel,None,cwd=directory.resolve(),
        progress_path=progress_path,stall_timeout=120))
    if on_stage:on_stage('check_output_quality')
    master = directory / "grade.partial.mp4"
    view = directory / "preview.partial.webm"
    def metadata_pair():
        with ThreadPoolExecutor(max_workers=2,thread_name_prefix='takeone-probe') as pool:
            a=pool.submit(probe,master,settings,cancel);b=pool.submit(probe,view,settings,cancel)
            return a.result(),b.result()
    metadata,preview_meta=_elapsed(profile,'metadata_seconds',metadata_pair)
    # Decode every frame and audio packet. The optimized path combines strict
    # decode validation and integer timing capture; it does not use packet PTS
    # as a substitute for decoding or remove any output integrity check.
    def decode_pair():
        if qc_mode=='reference':
            command([settings.ffmpeg,'-v','error','-xerror','-i',str(master),'-f','null','-'],cancel,120)
            return evidence.frame_manifest(master,settings,cancel),evidence.frame_manifest(view,settings,cancel),None
        with ThreadPoolExecutor(max_workers=2,thread_name_prefix='takeone-decode') as pool:
            a=(pool.submit(evidence.decoded_evidence,master,settings,cancel,metadata,recipe.evidence_selection)
               if recipe.evidence_selection else pool.submit(evidence.decoded_manifest,master,settings,cancel,metadata))
            b=pool.submit(evidence.decoded_manifest,view,settings,cancel,preview_meta)
            decoded=a.result()
            manifest,frames=decoded if recipe.evidence_selection else (decoded,None)
            return manifest,b.result(),frames
    output_manifest,preview_manifest,output_pixels=_elapsed(profile,'decode_and_timing_seconds',decode_pair)
    video = next(s for s in metadata["streams"] if s["codec_type"] == "video")
    preview_video = next(s for s in preview_meta["streams"] if s["codec_type"] == "video")
    audio = next((s for s in metadata["streams"] if s["codec_type"] == "audio"), None)
    duration = float(video.get("duration") or metadata["format"]["duration"])
    if ((video["width"], video["height"]) != (original.width, original.height)
            or not math.isfinite(duration) or abs(duration-original.duration) > 0.15
            or video.get("pix_fmt") != "yuv420p" or video.get("color_transfer") != "bt709"
            or video.get("color_primaries") != "bt709" or video.get("color_space") != "bt709"
            or bool(audio) != bool(original.audio_codec)
            or abs(float(preview_meta["format"]["duration"])-float(source_meta['format']['duration'])) > 0.5):
        raise Problem("RENDER_VALIDATION_FAILED", "The rendered media did not meet the output contract.",
                      "The candidate was not accepted. The original and earlier versions are kept.", 422)
    source_video = next(s for s in source_meta["streams"] if s["codec_type"] == "video")
    master_timing=timing.validate(source_manifest,output_manifest,
        origin_shift=-Fraction(str(policy['source_origin_seconds'])),movie_timescale=policy['movie_timescale'])
    preview_timing=timing.validate(source_manifest,preview_manifest,role='preview')
    audio_timing=timing.validate_audio(source_meta,metadata,policy,original.audio_codec in {'aac','mp3','alac'})
    timing_result={'encoding':policy,'master':master_timing,'preview':preview_timing,'audio':audio_timing}
    if recipe.evidence_selection:
        after,_=_elapsed(profile,'output_samples_seconds',lambda:evidence.collect(master,original,settings,cancel,
            selection=recipe.evidence_selection,frames=output_pixels))
    else:after,_=_elapsed(profile,'output_samples_seconds',lambda:_legacy_samples(master,original,settings,cancel))
    warnings = []
    diagnostics=evidence.compare(recipe.source_samples,after,recipe.proposal.intent.model_dump()) if recipe.evidence_selection else None
    if recipe.tone_operator=='luminance_ocio_v3' and diagnostics:
        limits=recipe.proposal.execution_limits
        for pair in diagnostics['paired_measurements']:
            if ((limits.max_new_black_fraction is not None and pair['new_black_fraction']>limits.max_new_black_fraction)
                or (limits.max_new_white_fraction is not None and pair['new_white_fraction']>limits.max_new_white_fraction)):
                raise Problem('GRADE_EXECUTION_LIMIT','Rendered clipping exceeded a declared execution bound.',
                              'Revise the plan or clarify intentional clipping; no critic call was made.',422,
                              frame_index=pair['frame_index'],observed_clipping=pair,
                              execution_limits=limits.model_dump(),timing=timing_result)
    for before, result in ([] if recipe.tone_operator=='luminance_ocio_v3' else zip(recipe.source_samples, after)):
        if result["near_black_fraction"] > before["near_black_fraction"] + 0.08:
            warnings.append(f'Check lost shadow detail near {before["requested_time"]:.2f}s.')
        if result["near_white_fraction"] > before["near_white_fraction"] + 0.03:
            warnings.append(f'Check bright-detail clipping near {before["requested_time"]:.2f}s.')
        if result.get('black_fraction',0)>before.get('black_fraction',0)+.03:
            warnings.append(f'New encoded-black pixels near {before["requested_time"]:.2f}s; inspect lost detail.')
    master.replace(directory / "grade.mp4")
    view.replace(directory / "preview.webm")
    version = command([settings.ffmpeg, "-version"], cancel, 10).splitlines()[0]
    return {
        "width": video["width"], "height": video["height"], "duration": duration,
        "preview_width": preview_video["width"], "preview_height": preview_video["height"],
        "audio_mode": "copied" if original.audio_codec in {"aac", "mp3", "alac"} else "transcoded" if audio else "none",
        "fully_decoded": True, "ffmpeg_version": version,
        "source_frames": source_video.get("nb_frames"), "output_frames": video.get("nb_frames"),
        "bytes": (directory / "grade.mp4").stat().st_size,
        "source_samples": recipe.source_samples, "output_samples": after, "warnings": warnings,
        "review_status": "needs_user_review",
        "mode":recipe.proposal.mode,"tone_operator":recipe.tone_operator,
        "local_composition_policy":'background_fade_then_foreground_merge_v1' if recipe.proposal.local and recipe.tone_operator=='luminance_ocio_v3' else 'legacy',
        "timing":timing_result,
        "diagnostics":diagnostics,
        "limitations": [
            "Technical checks and bounded samples do not establish aesthetic improvement or full constraint compliance.",
            "Localized foreground/background treatment on validated intervals; no physical relighting or shot matching."
            if recipe.proposal.mode=='localized' else "One global treatment is applied across source shots. No shot matching.",
        ],
    }
