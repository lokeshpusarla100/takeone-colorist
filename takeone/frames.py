"""One exact, all-core, cached frame-timestamp probe per media file.

sam.frame_info and evidence.frame_manifest both need decoded-frame timestamps of the same files
(the 4K original above all). Each used to run its own single-threaded `ffprobe -show_frames`, a
full decode taking ~52 s for a 25 s 4K clip. This runs one probe with every field either needs,
decoding on all cores (`-threads 0`: byte-identical frame timestamps, ~6x faster), and caches the
raw result next to the file, keyed by size/mtime/inode, so later stages and restarts reuse it.

The probe still decodes: packet timestamps alone can differ from decoded frames (edit lists,
frames a decoder drops), so they are not used as a substitute.
"""
import json
import os
from pathlib import Path
import threading
from uuid import uuid4

from .errors import Problem
from .media import command

ENTRIES = ('frame=best_effort_timestamp,best_effort_timestamp_time,duration,pkt_duration'
           ':stream=width,height,avg_frame_rate,r_frame_rate,time_base'
           ':stream_side_data=rotation:stream_tags=rotate:format=start_time')
VERSION = 1
_memory = {}
_lock = threading.Lock()
_inflight = {}  # (resolved path, identity) -> _Flight: the one probe currently running for that file


class _Flight:
    def __init__(self):
        self.done = threading.Event()
        self.raw = None


def identity(path):
    stat = Path(path).stat()
    return {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'inode': stat.st_ino, 'version': VERSION, 'entries': ENTRIES}


def probe_frames(path, settings, cancel, timeout=90):
    """Raw ffprobe JSON (frames, streams, format) for the first video stream; callers must not mutate it."""
    path = Path(path)
    try:
        current = identity(path)
    except OSError:
        # Unreadable path: no cache; ffprobe reports it exactly as before (MEDIA_TOOL_FAILED).
        return json.loads(command([settings.ffprobe, '-v', 'error', '-threads', '0', '-select_streams', 'v:0', '-show_frames',
                                   '-show_entries', ENTRIES, '-of', 'json', str(path)], cancel, timeout))
    resolved = str(path.resolve())
    with _lock:
        hit = _memory.get(resolved)
    if hit and hit[0] == current:
        return hit[1]
    cache = path.with_name(path.name + f'.frames-v{VERSION}.json')
    raw = None
    try:
        saved = json.loads(cache.read_text())
        if saved.get('identity') == current:
            raw = saved['raw']
    except (OSError, ValueError, KeyError):
        pass
    if raw is None:
        key = (resolved, json.dumps(current, sort_keys=True))
        with _lock:
            flight = _inflight.get(key)
            leader = flight is None
            if leader:
                flight = _inflight[key] = _Flight()
        if not leader:
            # The same probe is already running (the background one started when the source became ready, or
            # another stage's): wait for it instead of decoding the whole file a second time.
            while not flight.done.wait(0.2):
                if cancel.is_set():
                    raise Problem("JOB_CANCELLED", "The operation was cancelled.", "You can retry the saved step.", 409)
            if flight.raw is not None:
                return flight.raw
            return probe_frames(path, settings, cancel, timeout)  # that probe failed or was cancelled: probe for ourselves
        try:
            raw = json.loads(command([settings.ffprobe, '-v', 'error', '-threads', '0', '-select_streams', 'v:0', '-show_frames',
                                      '-show_entries', ENTRIES, '-of', 'json', str(path)], cancel, timeout))
            temporary = cache.with_name('.' + cache.name + '.' + uuid4().hex)
            try:
                temporary.write_text(json.dumps({'identity': current, 'raw': raw}))
                os.replace(temporary, cache)
            except OSError:
                temporary.unlink(missing_ok=True)  # a read-only location just means no persistent cache
            flight.raw = raw
        finally:
            with _lock:
                _inflight.pop(key, None)
            flight.done.set()
    with _lock:
        _memory[resolved] = (current, raw)
    return raw


def remember_decoded(path, metadata, manifest):
    """Record a decode the caller already performed (e.g. the render's strict full-output decode) as this
    file's probe, so no second full decode is needed for its timestamps.

    `manifest` is evidence._decode_manifest output (integer decoded PTS in the stream time base, frame
    durations); `metadata` is the file's ffprobe -show_streams/-show_format JSON. Times are formatted as
    ffprobe does (ts * time_base as a double, six decimals). tests/test_render_timing.py checks the result
    is identical to a real probe of a rendered file.
    """
    from fractions import Fraction
    video = next(s for s in metadata['streams'] if s['codec_type'] == 'video')
    tick = Fraction(manifest['time_base'])
    if tick != Fraction(video['time_base']) or len(manifest['pts']) != len(manifest['durations']):
        return False
    seconds = tick.numerator / tick.denominator
    stream = {k: video[k] for k in ['width', 'height', 'r_frame_rate', 'avg_frame_rate', 'time_base'] if k in video}
    # Same filtering as the real probe's ENTRIES: only the rotate tag and rotation side data.
    if 'tags' in video:
        stream['tags'] = {k: v for k, v in video['tags'].items() if k == 'rotate'}
    rotation = [{'rotation': item['rotation']} for item in video.get('side_data_list') or [] if 'rotation' in item]
    if rotation:
        stream['side_data_list'] = rotation
    raw = {'frames': [{'best_effort_timestamp': pts, 'best_effort_timestamp_time': f'{pts * seconds:.6f}', 'duration': duration}
                      for pts, duration in zip(manifest['pts'], manifest['durations'])],
           'streams': [stream], 'format': {'start_time': metadata.get('format', {}).get('start_time', '0.000000')}}
    path = Path(path)
    try:
        current = identity(path)
    except OSError:
        return False
    cache = path.with_name(path.name + f'.frames-v{VERSION}.json')
    temporary = cache.with_name('.' + cache.name + '.' + uuid4().hex)
    try:
        temporary.write_text(json.dumps({'identity': current, 'raw': raw, 'source': 'render_decode'}))
        os.replace(temporary, cache)
    except OSError:
        temporary.unlink(missing_ok=True)
        return False
    with _lock:
        _memory[str(path.resolve())] = (current, raw)
    return True
