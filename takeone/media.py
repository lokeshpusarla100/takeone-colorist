import json
import math
import subprocess
import threading
import time
from pathlib import Path

from .config import Settings
from .contracts import Source, Preview
from .errors import Problem


def command(args: list[str], cancel: threading.Event, timeout: float = 180) -> str:
    try:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        raise Problem("MEDIA_TOOL_MISSING", "FFmpeg or FFprobe is not installed.",
                      "Set the tool paths or install the required media tools.", 503)
    start = time.monotonic()
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if cancel.is_set() or time.monotonic() - start > timeout:
                process.kill()
                process.communicate()
                if cancel.is_set():
                    raise Problem("JOB_CANCELLED", "The operation was cancelled.", "You can retry the saved step.", 409)
                raise Problem("MEDIA_TOOL_TIMEOUT", "Media processing timed out.",
                              "Try a shorter clip or retry the failed step.", 422)
    if cancel.is_set():
        raise Problem("JOB_CANCELLED", "The operation was cancelled.", "You can retry the saved step.", 409)
    if process.returncode:
        raise Problem("MEDIA_TOOL_FAILED", "The media tool could not process this video.",
                      "Inspect the source or use another video. The original upload is preserved.", 422,
                      tool=Path(args[0]).name, exit_code=process.returncode, diagnostic=stderr[-5000:])
    return stdout


def probe(path: Path, settings: Settings, cancel: threading.Event) -> dict:
    raw = command([settings.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], cancel, 30)
    try:
        return json.loads(raw)
    except ValueError:
        raise Problem("MEDIA_METADATA_INVALID", "FFprobe returned invalid metadata.", "Try another source.", 422)


def inspect_source(path: Path, name: str, digest: str, settings: Settings, cancel: threading.Event):
    raw = probe(path, settings, cancel)
    video = next((s for s in raw.get("streams", []) if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in raw.get("streams", []) if s.get("codec_type") == "audio"), None)
    if video is None:
        raise Problem("NO_VIDEO_STREAM", "The upload has no readable video stream.", "Choose a video file.", 422)
    try:
        duration = float(video.get("duration") or raw["format"]["duration"])
        width, height = int(video["width"]), int(video["height"])
    except (KeyError, ValueError, TypeError):
        raise Problem("MEDIA_METADATA_INVALID", "The video has no usable dimensions or duration.", "Choose another source.", 422)
    if not math.isfinite(duration) or duration <= 0 or width <= 0 or height <= 0:
        raise Problem("MEDIA_METADATA_INVALID", "Invalid source dimensions or duration.", "Choose another source.", 422)
    aspect = video.get("sample_aspect_ratio")
    if aspect not in {None, "N/A", "0:1", "1:1"}:
        raise Problem("SOURCE_GEOMETRY_UNSUPPORTED", "This source uses non-square pixels.",
                      "Use a square-pixel preview. Automatic aspect correction is not verified yet.", 422,
                      sample_aspect_ratio=aspect)
    if duration > settings.max_duration or width * height > 3840 * 2160:
        raise Problem("SOURCE_LIMIT", "This video exceeds the initial processing limits.",
                      "Use a clip up to " + str(settings.max_duration) + " seconds and up to 4K.", 422,
                      duration=duration, width=width, height=height)
    rotation = next((float(s["rotation"]) for s in video.get("side_data_list", []) if "rotation" in s), 0.0)
    notes = []
    transfer = video.get("color_transfer")
    if transfer in {"smpte2084", "arib-std-b67"}:
        raise Problem("HDR_UNSUPPORTED", "This source is HDR; SDR preview conversion is not verified yet.",
                      "Use an explicitly converted SDR copy. The original upload has been preserved.", 422,
                      color_transfer=transfer)
    if not transfer or not video.get("color_primaries"):
        notes.append("Source color interpretation is uncertain; global grading is unavailable until clarified.")
    if video.get("pix_fmt") not in {"yuv420p", "yuvj420p", "yuv422p", "yuv444p", "rgb24", "bgr24"}:
        notes.append("Preview is 8-bit. The original precision is retained; final grading is not enabled for this pixel format.")
    if abs(rotation) % 180 == 90:
        width, height = height, width
    return Source(
        name=name, bytes=path.stat().st_size, sha256=digest, width=width, height=height, duration=duration,
        video_codec=video["codec_name"], audio_codec=audio["codec_name"] if audio else None,
        pixel_format=video.get("pix_fmt", "unknown"), color_transfer=transfer,
        color_primaries=video.get("color_primaries"), color_space=video.get("color_space"),
        start_time=float(video.get("start_time") or 0), rotation=rotation, technical_notes=notes,
    ), raw


def make_previews(directory: Path, source: Source, project_id: str, settings: Settings, cancel: threading.Event):
    # Display preview avoids the H.264 decoder path that failed in the user's Chrome.
    preview_tmp = directory / "preview.partial.webm"
    analysis_tmp = directory / "analysis.partial.mp4"
    base = [settings.ffmpeg, "-hide_banner", "-v", "error", "-nostdin", "-y", "-hwaccel", "none", "-i", str(directory / "source.bin")]
    geometry = "scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1"
    command(base + ["-map", "0:v:0", "-map", "0:a:0?", "-vf", geometry, "-c:v", "libvpx-vp9",
                    "-deadline", "realtime", "-cpu-used", "6", "-crf", "32", "-b:v", "0", "-pix_fmt", "yuv420p",
                    "-c:a", "libopus", "-b:a", "96k", str(preview_tmp)], cancel)
    command(base + ["-map", "0:v:0", "-an", "-vf", "scale=w='min(640,iw)':h='min(640,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
                    "-fps_mode", "vfr", "-movflags", "+faststart", str(analysis_tmp)], cancel)
    metadata = probe(preview_tmp, settings, cancel)
    v = next(s for s in metadata["streams"] if s["codec_type"] == "video")
    if abs(float(metadata["format"]["duration"]) - source.duration) > 0.5:
        raise Problem("PREVIEW_TIMING_MISMATCH", "The preview duration does not match the source.",
                      "The preview has not been accepted. Inspect the source timing.", 422)
    preview_tmp.replace(directory / "preview.webm")
    analysis_tmp.replace(directory / "analysis.mp4")
    return Preview(width=v["width"], height=v["height"], duration=float(metadata["format"]["duration"]),
                   bytes=(directory / "preview.webm").stat().st_size,
                   url="/api/projects/" + project_id + "/preview",
                   analysis_url="/api/projects/" + project_id + "/analysis-proxy",
                   source_start_time=source.start_time, preview_start_time=float(v.get("start_time") or 0))


def global_grade(directory: Path, settings: Settings, cancel: threading.Event, *, contrast=1.0, saturation=1.0, gamma=1.0):
    """Bounded SDR display-domain base tool. No physical exposure or relighting claims."""
    if not (0.75 <= contrast <= 1.25 and 0 <= saturation <= 1.5 and 0.8 <= gamma <= 1.2):
        raise Problem("INVALID_GRADE", "Global grade parameters are outside supported bounds.", "Use supported settings.")
    output = directory / "global-preview.mp4"
    command([settings.ffmpeg, "-hide_banner", "-v", "error", "-nostdin", "-y", "-i", str(directory / "source.bin"),
             "-vf", f"eq=contrast={contrast}:saturation={saturation}:gamma={gamma}",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(output)], cancel)
    return output


def capabilities(source: Source):
    supported = source.pixel_format == "yuv420p" and source.color_transfer == "bt709" and source.color_primaries == "bt709"
    return {
        "operations": ["global_grade"] if supported else [],
        "limitations": [
            "Only global SDR display-domain contrast, saturation and gamma are implemented in the base tool.",
            "Automatic colorist execution is not connected. Local masks, relighting, reconstruction and shot matching are unavailable.",
            *source.technical_notes,
        ],
        "supports_look_previews": False,
    }
