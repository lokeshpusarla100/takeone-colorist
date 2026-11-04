"""Measured levels of a clip: how flat it is, and the black and white points a normalization would use.

Log footage cannot be recognised from its tags, but it can be recognised from its pixels: a camera log curve lifts the
blacks and caps the whites, so ordinary video touches near-black and near-white while log video does not. This measures the
clip's luma on a sample of frames of the analysis proxy (already in display Rec.709) and says whether it looks flat. It only
ever *suggests*: nothing is applied until the person chooses, because a dark, foggy or low-key clip can be flat on purpose.

Measured on real clips: ordinary phone video has its 0.5th percentile near 0.00-0.03 and its 99.5th near 0.93-1.0; the log-like
OnePlus 12R clip had 0.13 and 0.77.
"""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass

import numpy as np

from ..config import Settings
from ..errors import Problem
from ..media import command, probe
from . import profile as color_profile

SAMPLES = 24
WIDTH = 160
LOW, HIGH = 0.5, 99.5          # percentiles that stand for black and white (robust to dead pixels, flares, noise)
FLAT_BLACK = 0.07              # blacks lifted at least this far ...
FLAT_SPAN = 0.75               # ... and a total span no wider than this
FLAT_WHITE = 0.90              # ... and whites that do not reach the top: log curves cap well below it
MIN_PIXELS = 20000             # fewer than this and a measurement is not worth trusting


@dataclass(frozen=True)
class Levels:
    black: float                # 0.5th percentile of luma, 0-1 display-referred
    white: float                # 99.5th percentile
    p1: float
    median: float
    p99: float
    flat: bool                  # looks like camera log or other low-contrast footage
    samples: int                # frames measured

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data) -> 'Levels | None':
        try:
            return cls(**{k: data[k] for k in cls.__dataclass_fields__}) if isinstance(data, dict) else None
        except (KeyError, TypeError):
            return None

    @property
    def span(self) -> float:
        return self.white - self.black

    @property
    def normalizable(self) -> bool:
        try:
            color_profile.InputProfile(color_profile.NORMALIZE, black=self.black, white=self.white).validate()
        except ValueError:
            return False
        return True


def describe(luma: np.ndarray, frames: int) -> Levels | None:
    """Levels from luma values already scaled to 0-1; None when there are too few pixels to say anything."""
    values = np.asarray(luma, dtype=np.float64).ravel()
    if values.size < MIN_PIXELS or not np.isfinite(values).all():
        return None
    black, p1, median, p99, white = (float(x) for x in np.percentile(values, [LOW, 1, 50, 99, HIGH]))
    flat = black >= FLAT_BLACK and (white - black) <= FLAT_SPAN and white <= FLAT_WHITE
    return Levels(round(black, 4), round(white, 4), round(p1, 4), round(median, 4), round(p99, 4), bool(flat), frames)


def measure(path, settings: Settings, cancel: threading.Event, *, samples: int = SAMPLES) -> Levels | None:
    """Levels of the video at `path` (a Rec.709 proxy), or None if it cannot be measured. Never raises for a bad file:
    the measurement only informs a suggestion, so failing to make it must not fail anything else."""
    try:
        info = probe(path, settings, cancel)
        stream = next(s for s in info['streams'] if s['codec_type'] == 'video')
        width, height = int(stream['width']), int(stream['height'])
        duration = float(stream.get('duration') or 0) or float(info['format']['duration'])
        small_h = max(2, int(round(WIDTH * height / width / 2)) * 2)
        rate = max(samples / max(duration, 0.1), 0.05)
        raw = command([settings.ffmpeg, '-v', 'error', '-nostdin', '-i', str(path), '-an', '-vf',
                       f'fps={rate:.6f},scale={WIDTH}:{small_h}:flags=area,format=yuv420p,extractplanes=y', '-frames:v', str(samples),
                       '-f', 'rawvideo', '-'], cancel, 60, binary=True)
        size = WIDTH * small_h
        count = len(raw) // size
        if count == 0:
            return None
        plane = np.frombuffer(raw[:count * size], np.uint8).astype(np.float64)
        # Y' is stored in 16-235 (limited) unless the proxy says it is full range.
        full = stream.get('color_range') == 'pc'
        luma = plane / 255 if full else (plane - 16) / 219
        return describe(np.clip(luma, 0.0, 1.0), count)
    except Problem as problem:
        if problem.code == 'JOB_CANCELLED':
            raise
        return None
    except (OSError, ValueError, KeyError, IndexError, StopIteration, ZeroDivisionError):
        return None
