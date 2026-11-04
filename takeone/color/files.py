"""The color conversion, applied at every place that reads the original's pixels.

color/domain.py decides what a source is and what conversion it needs (pure). This module is the small amount of glue
each decoder shares, so the render, evidence frames, tracking proxy and matte QC cannot disagree about it:

  * `domain_for_file` / `domain_for_stream`  resolve a file's domain (a recorded one wins, so every stage of a project
    agrees with the proxies that were made under it).
  * `chain_for_file`                         the ffmpeg filters, '' for native footage.
  * `open_pair`                              readers of original and graded frames for the matte QC.

Native footage (tagged Rec.709, limited range) always gets '' and OpenCV readers: its graphs and decoders are exactly
what they were before color domains existed.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import threading

import numpy as np

from . import domain as color_domain
from . import profile as color_profile
from ..errors import Problem
from ..media import display_size, probe


def _gradable(domain: color_domain.SourceDomain) -> color_domain.SourceDomain:
    if not domain.gradable:
        raise Problem('GRADE_DOMAIN_UNSUPPORTED', domain.message, domain.action, 422, reason=domain.reason)
    return domain


def domain_for_stream(stream: dict, settings) -> color_domain.SourceDomain:
    width, height = display_size(stream)
    return color_domain.resolve(stream, width=width, height=height, hdr=settings.hdr_to_sdr, tools=color_domain.converter_tools(settings.ffmpeg))


_domains: dict[tuple, color_domain.SourceDomain] = {}


def domain_for_file(path, settings, cancel: threading.Event | None = None, *, recorded: dict | None = None) -> color_domain.SourceDomain:
    """The domain of the file at `path`: the one recorded with the project's source if there is one, else probed.

    Probing is a few milliseconds and cached per file, so frame-by-frame callers can ask every time."""
    known = color_domain.SourceDomain.from_dict(recorded)
    if known is not None:
        return _gradable(known)
    path = Path(path)
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns, settings.hdr_to_sdr, settings.ffmpeg)
    if key not in _domains:
        stream = next(s for s in probe(path, settings, cancel or threading.Event())['streams'] if s['codec_type'] == 'video')
        if len(_domains) > 64:
            _domains.clear()
        _domains[key] = domain_for_stream(stream, settings)
    return _gradable(_domains[key])


def chain_for_file(path, settings, cancel=None, *, recorded: dict | None = None) -> str:
    """The ffmpeg filters that bring the file into Rec.709: the automatic conversion of its domain, and the input profile
    saved next to it, if the person chose one. '' for native footage with no profile."""
    return color_profile.conform_chain(domain_for_file(path, settings, cancel, recorded=recorded), color_profile.read(path), path)


class FfmpegCapture:
    """The part of cv2.VideoCapture the matte QC uses (isOpened, read, release), fed by ffmpeg.

    OpenCV decodes YUV with its own default matrix and ignores tags, which is consistent only while both files
    are read the same way. When the original needs converting, both sides are read here, through the same
    explicit conversion, so a protected area is compared like with like."""

    def __init__(self, path, chain: str, settings, cancel: threading.Event | None = None, size: tuple[int, int] | None = None):
        stream = next(s for s in probe(Path(path), settings, cancel or threading.Event())['streams'] if s['codec_type'] == 'video')
        self.width, self.height = size or display_size(stream)
        self._bytes = self.width * self.height * 3
        # Shrunk before it is converted when the caller works at a smaller size (the matte check does): the same picture, a fraction of the work.
        graph = (f'scale={size[0]}:{size[1]},' if size else '') + (chain + ',' if chain else '') + 'format=bgr24'
        # Opened here, in this process, and handed to ffmpeg as an open descriptor: like cv2.VideoCapture, the reader then
        # survives the render renaming its partial output while the check is still running.
        self._file = open(path, 'rb')
        try:
            self._process = subprocess.Popen([settings.ffmpeg, '-v', 'error', '-nostdin', '-i', f'/dev/fd/{self._file.fileno()}', '-map', '0:v:0', '-an', '-vf', graph,
                                              '-fps_mode', 'passthrough', '-f', 'rawvideo', '-'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                             pass_fds=(self._file.fileno(),))
        except BaseException:
            self._file.close()
            raise

    def isOpened(self) -> bool:  # noqa: N802 - mirrors cv2.VideoCapture
        return self._process is not None and self._process.poll() in (None, 0)

    def read(self):
        if self._process is None:
            return False, None
        data = self._process.stdout.read(self._bytes)
        if len(data) != self._bytes:
            return False, None
        return True, np.frombuffer(data, np.uint8).reshape(self.height, self.width, 3).copy()

    def release(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            try:
                process.stdout.close()
            except OSError:
                pass
            process.kill()
            process.wait()
        self._file.close()


def open_pair(original, graded, settings, cancel=None, *, recorded: dict | None = None, size: tuple[int, int] | None = None):
    """(original, graded) readers for the matte QC: OpenCV for native footage as always, ffmpeg for converted footage.
    `size` is the (width, height) the caller will work at; converted footage is read at that size."""
    import cv2
    chain = chain_for_file(original, settings, cancel, recorded=recorded)
    if not chain:
        return cv2.VideoCapture(str(original)), cv2.VideoCapture(str(graded))
    return (FfmpegCapture(original, chain, settings, cancel, size),
            FfmpegCapture(graded, '', settings, cancel, size))
