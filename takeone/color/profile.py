"""Input profiles: how a person tells us to read footage whose own tags cannot be trusted.

Camera log (Apple Log, S-Log3, V-Log, O-Log, ...) is ordinary-looking video as far as the file is concerned: the container
says Rec.709 or says nothing, and the picture is simply flat. No reader can know that from metadata, which is why DaVinci
Resolve and Premiere also ask the person ("input color space", "input LUT"). There are two ways to answer here:

  * `normalize`  a fallback for any flat clip: stretch the clip's own measured black and white points to the full range.
                 Approximate, and labelled so. It is not the camera maker's transform.
  * `lut`        the camera maker's own .cube LUT, applied to the camera's log RGB. This is the exact route.

Both are applied at the same place and by the same chain as the automatic conversion in `domain.py`, so the render, the
previews, the evidence frames, the tracking proxy and the matte QC cannot disagree about them. Footage without a profile
gets exactly the chain it always got.

The profile is saved next to the media it describes (`source.bin.color.json`), because every decoder is handed that path.
A reader therefore cannot forget to apply it, and one that is never told about it is not possible to write by accident.
This module is a leaf (it imports only `domain`, `lut` and `errors`) so `media` can use it without a cycle.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from . import domain as color_domain
from . import lut as color_lut
from ..errors import Problem

NORMALIZE, LUT = 'normalize', 'lut'
PROFILE_VERSION = 1
SAVED = object()          # "use whatever profile is saved with this file" (the default everywhere)
SIDECAR = '.color.json'
LUT_PREFIX, LUT_SUFFIX = 'input-lut-', '.cube'

# What a normalization may do. A clip whose measured levels fall outside these is not flat footage, and stretching it
# would amplify noise rather than recover contrast.
MAX_BLACK, MIN_WHITE, MIN_SPAN = 0.45, 0.5, 0.2
# ... and a clip that already reaches near-black and near-white has nothing to stretch.
USES_FULL_RANGE = (0.03, 0.95)


@dataclass(frozen=True)
class InputProfile:
    kind: str
    black: float | None = None        # normalize: the clip's measured black and white, as display-referred levels (0-1)
    white: float | None = None
    lut_file: str | None = None       # lut: content-addressed file in the project folder
    lut_name: str | None = None       # the name it was uploaded under
    lut_sha256: str | None = None
    lut_kind: str | None = None       # '3d' | '1d'
    lut_size: int | None = None
    lut_title: str | None = None
    version: int = PROFILE_VERSION

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @property
    def signature(self) -> str:
        """Changes whenever the pictures built from this profile would change (the LUT is identified by its content)."""
        body = {k: v for k, v in self.to_dict().items() if k not in {'lut_name', 'lut_title'}}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:16]

    @property
    def label(self) -> str:
        if self.kind == NORMALIZE:
            return 'Contrast normalized (approximate)'
        return 'Camera LUT: ' + (self.lut_title or self.lut_name or 'custom')

    @property
    def steps(self) -> tuple[str, ...]:
        if self.kind == NORMALIZE:
            return (f'This clip looks flat, so its own black level ({self.black:.2f}) and white level ({self.white:.2f}) are stretched to the full range before grading.',
                    'This is an approximation of the camera’s log curve, not the maker’s transform. A camera LUT is exact.')
        shape = f'{self.lut_size}x{self.lut_size}x{self.lut_size}' if self.lut_kind == '3d' else f'{self.lut_size} points'
        return (f'Your LUT “{self.lut_title or self.lut_name}” ({(self.lut_kind or "").upper()}, {shape}) is applied to the footage before grading.',
                'Use the camera maker’s “log to Rec.709” LUT for this camera; any other LUT will look wrong.')

    def summary(self) -> dict:
        """What the interface shows."""
        return {'kind': self.kind, 'label': self.label, 'steps': list(self.steps), 'signature': self.signature,
                'lut_name': self.lut_name, 'black': self.black, 'white': self.white}

    @classmethod
    def from_dict(cls, data) -> 'InputProfile':
        if not isinstance(data, dict):
            raise ValueError('not an object')
        fields = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        profile = cls(**fields)
        profile.validate()
        return profile

    def validate(self) -> None:
        if self.version != PROFILE_VERSION:
            raise ValueError('unknown version')
        if self.kind == NORMALIZE:
            ok = (isinstance(self.black, (int, float)) and isinstance(self.white, (int, float)) and 0 <= self.black <= MAX_BLACK
                  and MIN_WHITE <= self.white <= 1 and self.white - self.black >= MIN_SPAN)
            if not ok:
                raise ValueError('black and white levels are outside what a normalization can use')
        elif self.kind == LUT:
            name = self.lut_file or ''
            if not (name.startswith(LUT_PREFIX) and name.endswith(LUT_SUFFIX) and '/' not in name and '\\' not in name and '..' not in name):
                raise ValueError('LUT file name')
            if self.lut_kind not in {'3d', '1d'} or not isinstance(self.lut_size, int) or not self.lut_sha256:
                raise ValueError('LUT description')
        else:
            raise ValueError('unknown kind')


def normalize(black: float, white: float) -> InputProfile:
    profile = InputProfile(NORMALIZE, black=round(float(black), 4), white=round(float(white), 4))
    try:
        profile.validate()
        if profile.black < USES_FULL_RANGE[0] and profile.white > USES_FULL_RANGE[1]:
            raise ValueError('already uses the full range')
    except ValueError:
        raise Problem('INPUT_PROFILE_NOT_FLAT', 'This clip’s levels are not those of flat footage (it is either not low-contrast, or already uses the full range), so there is nothing to normalize.',
                      'Use a camera LUT if the footage is camera log, or keep the footage as it is.', 422,
                      black=round(float(black), 3), white=round(float(white), 3)) from None
    return profile


def lut_profile(data: bytes, name: str) -> tuple[InputProfile, color_lut.CubeLut]:
    """Validate an uploaded .cube and describe it. Raises Problem (422) with the specific reason."""
    try:
        cube = color_lut.parse_cube(data)
    except color_lut.LutError as error:
        raise Problem(error.code, error.message, 'Export the “log to Rec.709” LUT as a .cube file from the camera maker’s site or app.', 422) from None
    digest = hashlib.sha256(data).hexdigest()
    profile = InputProfile(LUT, lut_file=f'{LUT_PREFIX}{digest[:16]}{LUT_SUFFIX}', lut_name=Path(name.replace('\\', '/')).name[:120] or 'lut.cube',
                           lut_sha256=digest, lut_kind=cube.kind, lut_size=cube.size, lut_title=cube.title or None)
    return profile, cube


# --- saved next to the media -------------------------------------------------------------------------------------

def sidecar(source_path) -> Path:
    path = Path(source_path)
    return path.with_name(path.name + SIDECAR)


def read(source_path) -> InputProfile | None:
    """The profile saved with this file, or None. A saved profile that cannot be read is an error, never a silent
    "no profile": the pictures would quietly differ from what the person chose."""
    path = sidecar(source_path)
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    try:
        return InputProfile.from_dict(json.loads(text))
    except (ValueError, TypeError):
        raise Problem('INPUT_PROFILE_INVALID', 'The saved input profile for this clip cannot be read.',
                      'Clear the input profile and choose it again.', 500) from None


def write(source_path, profile: InputProfile | None) -> None:
    """Save (or with None remove) the profile, atomically."""
    path = sidecar(source_path)
    if profile is None:
        path.unlink(missing_ok=True)
        return
    profile.validate()
    handle, temporary = tempfile.mkstemp(prefix='.color-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(handle, 'w') as stream:
            json.dump(profile.to_dict(), stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def signature(profile: InputProfile | None) -> str:
    return profile.signature if profile else ''


def applicable(domain: color_domain.SourceDomain | None) -> tuple[bool, str]:
    """A profile re-interprets SDR video. HDR is already mapped by its own tags, so a profile would be applied twice."""
    if domain is None or not domain.gradable:
        return False, 'This footage cannot be graded.'
    if domain.kind not in {color_domain.NATIVE, color_domain.CONVERT}:
        return False, 'HDR footage is already converted from its own tags, so an input profile does not apply to it.'
    return True, ''


# --- the chain ---------------------------------------------------------------------------------------------------

LIMITED_709 = 'primariesin=bt709:transferin=bt709:primaries=bt709:transfer=bt709'
BACK = f'zscale=rangein=full:{LIMITED_709}:range=limited:matrix=bt709:dither=error_diffusion,format=yuv444p16le'


def to_rgb(range_in: str, matrix_in: str) -> str:
    """The file's YCbCr to full-range RGB with no change of primaries or curve: the numbers a camera LUT expects."""
    return f'zscale=rangein={range_in}:matrixin={matrix_in}:{LIMITED_709}:range=full:matrix=gbr:dither=error_diffusion,format=gbrp16le'


def conform_chain(domain: color_domain.SourceDomain | None, profile: InputProfile | None, source_path) -> str:
    """The ffmpeg filters that bring the source into 16-bit Rec.709 limited-range YUV 4:4:4 ('' when none are needed).

    Without a profile this is exactly `domain.conform_filters`. With one, a LUT replaces the automatic conversion (it is the
    camera's own transform, so the generic one must not run first), and a normalization runs on top of it."""
    if profile is None:
        return color_domain.conform_filters(domain)
    ok, why = applicable(domain)
    if not ok:
        raise Problem('INPUT_PROFILE_NOT_APPLICABLE', why, 'Remove the input profile.', 422)
    if profile.kind == NORMALIZE:
        base = color_domain.conform_filters(domain)
        black, white = repr(float(profile.black)), repr(float(profile.white))
        levels = f'colorlevels=rimin={black}:gimin={black}:bimin={black}:rimax={white}:gimax={white}:bimax={white}'
        return ','.join(part for part in (base, to_rgb('limited', 'bt709'), levels, BACK) if part)
    path = Path(source_path).with_name(profile.lut_file)
    if not path.is_file():
        raise Problem('INPUT_LUT_MISSING', 'The LUT saved for this clip is missing.', 'Upload the LUT again, or clear the input profile.', 409)
    cube = color_lut.CubeLut(profile.lut_kind, profile.lut_size, profile.lut_title or '', (0, 0, 0), (1, 1, 1))
    return ','.join((to_rgb(domain.range or 'limited', domain.matrix or 'bt709'), color_lut.filter_for(cube, path), BACK))


def store_lut(directory, data: bytes, profile: InputProfile) -> Path:
    """Keep an uploaded LUT in the project folder under its content-addressed name (the same LUT is stored once)."""
    path = Path(directory) / profile.lut_file
    if not path.is_file():
        handle, temporary = tempfile.mkstemp(prefix='.lut-', suffix='.tmp', dir=directory)
        try:
            with os.fdopen(handle, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
    return path


def prune_luts(directory, keep: InputProfile | None) -> None:
    """Remove stored LUTs nothing refers to any more."""
    for path in Path(directory).glob(f'{LUT_PREFIX}*{LUT_SUFFIX}'):
        if keep is None or keep.kind != LUT or path.name != keep.lut_file:
            path.unlink(missing_ok=True)
