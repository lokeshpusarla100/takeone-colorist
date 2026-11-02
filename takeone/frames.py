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

from .media import command

ENTRIES = ('frame=best_effort_timestamp,best_effort_timestamp_time,duration,pkt_duration'
           ':stream=width,height,avg_frame_rate,r_frame_rate,time_base'
           ':stream_side_data=rotation:stream_tags=rotate:format=start_time')
VERSION = 1
_memory = {}
_lock = threading.Lock()


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
        raw = json.loads(command([settings.ffprobe, '-v', 'error', '-threads', '0', '-select_streams', 'v:0', '-show_frames',
                                  '-show_entries', ENTRIES, '-of', 'json', str(path)], cancel, timeout))
        temporary = cache.with_name('.' + cache.name + '.' + uuid4().hex)
        try:
            temporary.write_text(json.dumps({'identity': current, 'raw': raw}))
            os.replace(temporary, cache)
        except OSError:
            temporary.unlink(missing_ok=True)  # a read-only location just means no persistent cache
    with _lock:
        _memory[resolved] = (current, raw)
    return raw
