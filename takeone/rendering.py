"""One explicit global SDR path, always rendered from the original source."""
import base64
import hashlib
import json
import math
from pathlib import Path
import threading
from fractions import Fraction

import cv2
import numpy as np

from .config import Settings
from .contracts import GradeParameters, GradeRecipe, Source
from .errors import Problem
from .media import command, probe


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


def samples(path: Path, source: Source, settings: Settings, cancel: threading.Event):
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


def filter_graph(parameters: GradeParameters, *, recipe=None) -> str:
    operator=recipe.tone_operator if recipe else 'bounded_sigmoid_v2'
    start='[0:v]format=gbrp16le,setparams=range=full'
    if recipe and recipe.proposal.mode=='localized':
        local=recipe.proposal.local
        ranges=recipe.timeline['active_frame_ranges']
        condition='+'.join(f'(gte(n,{a})*lte(n,{b}))' for a,b in ranges)
        mapping=recipe.timeline['frame_map'];rate=Fraction(mapping['proxy']['rate']);dt=float(1/rate)
        weights=[];cuts=set(recipe.timeline['cut_frames'])
        for a,b in ranges:
            begin=mapping['frames'][a]['time'];end=mapping['frames'][b]['time']+dt
            ramp=min(.15,(b-a+1)*dt/2)
            incoming='1' if a in cuts else f'clip((T-{begin:.9f})/{ramp:.9f},0,1)'
            outgoing='1' if b+1==mapping['frame_count'] or b+1 in cuts else f'clip(({end:.9f}-T)/{ramp:.9f},0,1)'
            weights.append(f'(gte(T,{begin-dt*.25:.9f})*lt(T,{end-dt*.75:.9f})*min({incoming},{outgoing}))')
        weight='+'.join(weights)
        # On disabled frames maskedmerge passes its first input (the base treatment).
        start+=',split=3[basein][fgin][bgin];'
        start+='[basein]'+treatment_filter(parameters,operator=operator)+'[base];'
        start+='[fgin]'+treatment_filter(local.foreground,operator=operator,protected=local.preserve_subject_color)+'[fg];'
        start+='[bgin]'+treatment_filter(local.background,operator=operator)+'[bg];'
        # Matroska stores milliseconds; rebase the verified CFR mask by frame index.
        start+=f'[1:v]settb=expr={rate.denominator}/{rate.numerator},setpts=N,format=gbrp16le,setparams=range=full[mask];'
        start+='[bg][fg][mask]maskedmerge=planes=7[local];'
        # The base is selected outside requested intervals, not a repeated stale mask.
        start+=f"[base][local]blend=all_expr='A+(B-A)*({weight})':enable='{condition}':eof_action=endall:repeatlast=0,"
    else:
        start+=','+treatment_filter(parameters,operator=operator)+','
    return (start+
            "scale=in_range=pc:out_color_matrix=bt709:out_range=tv,format=yuv420p,"
            "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709,"
            "split=2[master][view];"
            "[view]scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1[preview]")


def render_args(source: Path, directory: Path, recipe: GradeRecipe, settings: Settings, audio_codec: str | None):
    audio = "copy" if audio_codec in {"aac", "mp3", "alac"} else "aac"
    inputs=['-i',str(source)]
    if recipe.proposal.mode=='localized':inputs+=['-i',str(directory/'matte.mkv')]
    return [settings.ffmpeg, "-hide_banner", "-v", "error", "-nostdin", "-y",
            "-filter_complex_threads", "2", "-copyts", "-start_at_zero", *inputs,
            "-filter_complex", filter_graph(recipe.proposal.parameters,recipe=recipe),
            "-map", "[master]", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "fast", "-crf", "17",
            "-threads", "4", "-pix_fmt", "yuv420p", "-fps_mode", "passthrough", "-c:a", audio,
            "-movflags", "+faststart", str(directory / "grade.partial.mp4"),
            "-map", "[preview]", "-map", "0:a:0?", "-c:v", "libvpx-vp9", "-deadline", "realtime",
            "-cpu-used", "6", "-crf", "32", "-b:v", "0", "-threads", "4", "-pix_fmt", "yuv420p",
            "-fps_mode", "passthrough", "-c:a", "libopus", "-b:a", "96k", str(directory / "preview.partial.webm")]


def render(source: Path, directory: Path, recipe: GradeRecipe, original: Source,
           settings: Settings, cancel: threading.Event):
    source_meta = verify_source(source, original, settings, cancel)
    directory.mkdir(exist_ok=True)
    command(render_args(source, directory, recipe, settings, original.audio_codec), cancel, 300)
    master = directory / "grade.partial.mp4"
    view = directory / "preview.partial.webm"
    # Fully decode the output; a successful encoder exit alone is insufficient.
    command([settings.ffmpeg, "-v", "error", "-xerror", "-i", str(master), "-f", "null", "-"], cancel, 120)
    metadata = probe(master, settings, cancel)
    preview_meta = probe(view, settings, cancel)
    video = next(s for s in metadata["streams"] if s["codec_type"] == "video")
    preview_video = next(s for s in preview_meta["streams"] if s["codec_type"] == "video")
    audio = next((s for s in metadata["streams"] if s["codec_type"] == "audio"), None)
    duration = float(video.get("duration") or metadata["format"]["duration"])
    if ((video["width"], video["height"]) != (original.width, original.height)
            or not math.isfinite(duration) or abs(duration-original.duration) > 0.15
            or video.get("pix_fmt") != "yuv420p" or video.get("color_transfer") != "bt709"
            or video.get("color_primaries") != "bt709" or video.get("color_space") != "bt709"
            or bool(audio) != bool(original.audio_codec)
            or abs(float(preview_meta["format"]["duration"])-original.duration) > 0.5):
        raise Problem("RENDER_VALIDATION_FAILED", "The rendered media did not meet the output contract.",
                      "The candidate was not accepted. The original and earlier versions are kept.", 422)
    source_video = next(s for s in source_meta["streams"] if s["codec_type"] == "video")
    if source_video.get("nb_frames") and video.get("nb_frames") and source_video["nb_frames"] != video["nb_frames"]:
        raise Problem("RENDER_TIMING_CHANGED", "Rendering changed the frame count.", "The candidate was not accepted.", 422)
    after, _ = samples(master, original, settings, cancel)
    warnings = []
    for before, result in zip(recipe.source_samples, after):
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
        "limitations": [
            "Technical checks and four sampled frames do not establish aesthetic improvement or full constraint compliance.",
            "Localized foreground/background treatment on validated intervals; no physical relighting or shot matching."
            if recipe.proposal.mode=='localized' else "One global treatment is applied across source shots. No shot matching.",
        ],
    }
