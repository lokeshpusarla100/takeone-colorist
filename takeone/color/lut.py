"""Camera-maker LUTs (.cube): strict reading, and the ffmpeg filter that applies them.

A person drops in a file we did not make, so nothing about it is trusted: it is parsed completely, every number is
checked, and only a file that is a well-formed 1D or 3D LUT of a sane size is ever handed to ffmpeg. Parsing never
raises anything but `LutError`, whose code and message are written for the person (they are shown in the interface).

Applying one: ffmpeg's `lut3d` / `lut1d` read the file named in the filter graph. The path is escaped for the two
levels of ffmpeg's filter-graph syntax, so a project folder with spaces, colons, commas or quotes in its path still works
(tested against such paths with the real ffmpeg).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

MAX_BYTES = 24 * 1024 * 1024
MAX_3D_SIZE = 65            # 65^3 = 274,625 rows; the largest size camera makers publish
MAX_1D_SIZE = 65536
MAX_ABS_VALUE = 64.0        # outputs are display values, a few stops of headroom at most; anything beyond is garbage


class LutError(ValueError):
    """The file is not a usable LUT. `code` is machine-readable, the message is for the person."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class CubeLut:
    kind: str                         # '3d' | '1d'
    size: int
    title: str
    domain_min: tuple[float, float, float]
    domain_max: tuple[float, float, float]

    def to_dict(self) -> dict:
        return {'kind': self.kind, 'size': self.size, 'title': self.title, 'domain_min': list(self.domain_min), 'domain_max': list(self.domain_max)}


def _triplet(text: str, what: str) -> tuple[float, float, float]:
    parts = text.split()
    try:
        values = tuple(float(p) for p in parts)
    except ValueError:
        raise LutError('LUT_BAD_NUMBER', f'{what} must be three numbers.') from None
    if len(values) != 3 or not all(np.isfinite(values)):
        raise LutError('LUT_BAD_NUMBER', f'{what} must be three finite numbers.')
    return values  # type: ignore[return-value]


def parse_cube(data: bytes) -> CubeLut:
    """Validate a .cube LUT completely and describe it. Raises LutError, never anything else."""
    if len(data) > MAX_BYTES:
        raise LutError('LUT_TOO_LARGE', f'This LUT is {len(data) / 1048576:.0f} MB; the limit is {MAX_BYTES // 1048576} MB. Camera LUTs are normally a few MB.')
    if not data.strip():
        raise LutError('LUT_EMPTY', 'The file is empty.')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise LutError('LUT_NOT_TEXT', 'This is not a .cube LUT: it is not a text file.') from None
    if '\x00' in text:
        raise LutError('LUT_NOT_TEXT', 'This is not a .cube LUT: it contains binary data.')
    title, size_1d, size_3d = '', None, None
    domain_min, domain_max = (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)
    rows: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        head = line.split(None, 1)[0]
        rest = line[len(head):].strip()
        if head == 'TITLE':
            title = rest.strip().strip('"')[:120]
        elif head in {'LUT_1D_SIZE', 'LUT_3D_SIZE'}:
            try:
                size = int(rest)
            except ValueError:
                raise LutError('LUT_BAD_SIZE', f'Line {number}: {head} must be a whole number.') from None
            if head == 'LUT_1D_SIZE':
                size_1d = size
            else:
                size_3d = size
        elif head == 'DOMAIN_MIN':
            domain_min = _triplet(rest, 'DOMAIN_MIN')
        elif head == 'DOMAIN_MAX':
            domain_max = _triplet(rest, 'DOMAIN_MAX')
        elif head in {'LUT_1D_INPUT_RANGE', 'LUT_3D_INPUT_RANGE'}:
            parts = rest.split()
            try:
                low, high = float(parts[0]), float(parts[1])
            except (ValueError, IndexError):
                raise LutError('LUT_BAD_NUMBER', f'Line {number}: {head} must be two numbers.') from None
            domain_min, domain_max = (low,) * 3, (high,) * 3
        elif head[0].isalpha():
            raise LutError('LUT_UNKNOWN_KEYWORD', f'Line {number}: “{head}” is not part of the .cube format, so this file may not be a LUT.')
        else:
            rows.append(line)
    if (size_1d is None) == (size_3d is None):
        raise LutError('LUT_NO_SIZE', 'The file must declare exactly one of LUT_3D_SIZE or LUT_1D_SIZE.')
    kind, size = ('3d', size_3d) if size_3d is not None else ('1d', size_1d)
    if kind == '3d' and not 2 <= size <= MAX_3D_SIZE:
        raise LutError('LUT_BAD_SIZE', f'A 3D LUT of size {size} is outside the supported range (2 to {MAX_3D_SIZE}).')
    if kind == '1d' and not 2 <= size <= MAX_1D_SIZE:
        raise LutError('LUT_BAD_SIZE', f'A 1D LUT of size {size} is outside the supported range (2 to {MAX_1D_SIZE}).')
    expected = size ** 3 if kind == '3d' else size
    if len(rows) != expected:
        raise LutError('LUT_ROW_COUNT', f'A {kind.upper()} LUT of size {size} needs {expected:,} rows of values, but this file has {len(rows):,}.')
    try:
        values = np.array([r.split() for r in rows], dtype=np.float64)
    except ValueError:
        raise LutError('LUT_BAD_NUMBER', 'A row of the LUT is not three numbers.') from None
    if values.ndim != 2 or values.shape[1] != 3:
        raise LutError('LUT_BAD_NUMBER', 'Every row of the LUT must hold exactly three numbers.')
    if not np.isfinite(values).all() or np.abs(values).max() > MAX_ABS_VALUE:
        raise LutError('LUT_BAD_NUMBER', 'The LUT contains values that are not finite or are far outside any real picture range.')
    if any(low >= high for low, high in zip(domain_min, domain_max)):
        raise LutError('LUT_BAD_DOMAIN', 'DOMAIN_MIN must be below DOMAIN_MAX for every channel.')
    return CubeLut(kind, size, title, domain_min, domain_max)


def escape_value(value: str) -> str:
    """A filter-option value (a path) made safe for both levels of ffmpeg filter-graph parsing.

    Level 2 (the filter's own `key=value:key=value` parser) needs backslash, colon and quote escaped; level 1 (the graph
    parser) then needs backslash, quote, comma, semicolon and the square brackets escaped, and the level-2 backslashes
    escape again."""
    level2 = ''.join('\\' + c if c in "\\:'" else c for c in value)
    return ''.join('\\' + c if c in "\\',;[]" else c for c in level2)


def filter_for(lut: CubeLut, path: Path) -> str:
    """The ffmpeg filter that applies this LUT to RGB frames (16-bit planar RGB in, same out)."""
    name = escape_value(str(path))
    if lut.kind == '3d':
        return f'lut3d=file={name}:interp=tetrahedral'
    return f'lut1d=file={name}:interp=linear'
