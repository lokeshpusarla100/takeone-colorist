"""How a source's colors are interpreted, and how they are converted into the one domain the grade works in.

The grade, the matte QC and the output contract are all defined for **display-referred Rec.709 SDR**: BT.709
matrix and primaries, the 709 transfer curve, limited range. Phones record many other things (10-bit HEVC tagged
BT.601 full range, untagged files, BT.2020 SDR, HLG or PQ HDR). Reading those as if they were Rec.709 would
silently shift every color, so they must be *converted* first, with the same conversion at every place that reads
the original's pixels (render, evidence frames, previews, tracking proxy, matte QC).

Principles:

* Resolve once, from the probed stream, and record it. Every conversion parameter is then passed to the converter
  explicitly: nothing depends on the file having been tagged correctly, or on a decoder's default for missing tags.
* Fields a file does not state are *assumed* by documented convention and reported as assumed, never hidden.
* A source that already is tagged Rec.709 limited range gets an EMPTY conversion, so its filter graphs are
  byte-identical to what they were before this module existed. Bit depth and chroma subsampling alone are not a
  color difference: the existing 16-bit working path already reads them correctly.
* HDR is tone-mapped to SDR. That is an approximation and is always disclosed. The output is SDR.
* What cannot be converted honestly (log or linear curves, Dolby Vision profile 5, RGB/gray/alpha video, odd frame
  sizes, a missing converter) is refused with a specific reason and action, not a generic format error.

Pure functions over a probed stream dict: no I/O except the cached check of the ffmpeg build.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
import subprocess

DOMAIN_VERSION = 1

NATIVE, CONVERT, TONEMAP, UNSUPPORTED = 'native', 'convert', 'tonemap', 'unsupported'

# Pixel formats (as ffprobe reports the decoded stream) with their bit depth and chroma subsampling.
PIXEL_FORMATS: dict[str, tuple[int, str]] = {
    'yuv420p': (8, '420'), 'yuvj420p': (8, '420'), 'nv12': (8, '420'), 'nv21': (8, '420'),
    'yuv422p': (8, '422'), 'yuvj422p': (8, '422'), 'yuv444p': (8, '444'), 'yuvj444p': (8, '444'),
    'yuv420p10le': (10, '420'), 'yuv422p10le': (10, '422'), 'yuv444p10le': (10, '444'), 'p010le': (10, '420'),
    'yuv420p12le': (12, '420'), 'yuv422p12le': (12, '422'), 'yuv444p12le': (12, '444'),
}
MISSING = {None, '', 'unknown', 'unspecified', 'reserved', 'N/A'}
MATRICES = {'bt709', 'bt470bg', 'smpte170m', 'smpte240m', 'bt2020nc'}
PRIMARIES = {'bt709', 'bt470bg', 'smpte170m', 'smpte240m', 'bt2020', 'smpte431', 'smpte432', 'film', 'bt470m'}
SDR_TRANSFERS = {'bt709', 'smpte170m', 'bt2020-10', 'bt2020-12', 'iec61966-2-1', 'smpte240m', 'bt470m', 'bt470bg'}
HDR_TRANSFERS = {'arib-std-b67': 'HLG', 'smpte2084': 'PQ'}
UNSUPPORTED_TRANSFERS = {'linear', 'log100', 'log316', 'smpte428', 'iec61966-2-4', 'bt1361e'}
PRIMARIES_FOR_MATRIX = {'bt709': 'bt709', 'bt470bg': 'bt470bg', 'smpte170m': 'smpte170m', 'smpte240m': 'smpte240m', 'bt2020nc': 'bt2020'}

# Tone-mapping policy per HDR transfer (measured on HLG/PQ ramps, see docs/SOURCE_COLOR.md):
#  HLG is designed to look right on SDR, and Hable follows that backward-compatible look with a smooth shoulder
#  (mid-grey 0.50 -> 124/255 against 128 for a plain reinterpretation); Mobius lifts HLG midtones to 185.
#  PQ carries absolute light, and Mobius keeps its reference white (PQ 0.58, 203 nits) near SDR white (234/255)
#  where Hable renders it dark (192/255).
TONEMAP_FOR = {'arib-std-b67': 'hable', 'smpte2084': 'mobius'}
REFERENCE_WHITE_NITS = 100  # linear 1.0 is SDR reference white for the tone mapper

NAMES = {'bt709': 'Rec.709', 'bt470bg': 'BT.470BG', 'smpte170m': 'BT.601', 'smpte240m': 'SMPTE 240M', 'bt2020nc': 'BT.2020', 'bt2020': 'BT.2020',
         'smpte431': 'DCI-P3', 'smpte432': 'Display P3', 'film': 'Generic film', 'bt470m': 'BT.470M'}


@dataclass(frozen=True)
class SourceDomain:
    """What a source is and what is done to it before it enters the grade. JSON-friendly; stored with the source."""
    kind: str
    matrix: str | None = None
    primaries: str | None = None
    transfer: str | None = None
    range: str | None = None          # 'limited' | 'full'
    bit_depth: int | None = None
    chroma: str | None = None         # '420' | '422' | '444'
    hdr: str | None = None            # 'HLG' | 'PQ'
    assumed: tuple[str, ...] = ()     # fields the file did not state
    steps: tuple[str, ...] = ()       # what is done, in words, for the user
    reason: str | None = None         # unsupported: machine-readable code
    message: str | None = None        # unsupported: what is wrong
    action: str | None = None         # unsupported: what to do
    version: int = DOMAIN_VERSION

    @property
    def gradable(self) -> bool:
        return self.kind != UNSUPPORTED

    def to_dict(self) -> dict:
        data = asdict(self)
        data['assumed'], data['steps'] = list(self.assumed), list(self.steps)
        return data

    @classmethod
    def from_dict(cls, data: dict | None) -> 'SourceDomain | None':
        if not isinstance(data, dict) or 'kind' not in data:
            return None
        fields = {k: data.get(k) for k in cls.__dataclass_fields__ if k in data}
        fields['assumed'], fields['steps'] = tuple(data.get('assumed') or ()), tuple(data.get('steps') or ())
        return cls(**fields)


@lru_cache(maxsize=8)
def converter_tools(ffmpeg: str = 'ffmpeg') -> dict:
    """Whether this ffmpeg build can convert colors (zscale needs libzimg) and tone-map (the tonemap filter)."""
    try:
        text = subprocess.run([ffmpeg, '-hide_banner', '-filters'], capture_output=True, timeout=20, text=True).stdout
    except (OSError, subprocess.SubprocessError):
        return {'zscale': False, 'tonemap': False}
    names = {line.split()[1] for line in text.splitlines() if len(line.split()) > 1}
    return {'zscale': 'zscale' in names, 'tonemap': 'tonemap' in names}


def _unsupported(reason, message, action, **known):
    return SourceDomain(kind=UNSUPPORTED, reason=reason, message=message, action=action, **known)


def _tag(value):
    return None if value in MISSING else value


def _dolby_vision_profile(stream):
    for item in stream.get('side_data_list') or []:
        if 'dv_profile' in item:
            return item.get('dv_profile'), item.get('dv_bl_signal_compatibility_id')
    return None, None


def resolve(stream: dict, *, width: int, height: int, hdr: bool = True, tools: dict | None = None) -> SourceDomain:
    """Interpret a probed video stream. `width`/`height` are the displayed size (after rotation)."""
    pix = stream.get('pix_fmt')
    if pix not in PIXEL_FORMATS:
        return _unsupported('PIXEL_FORMAT', f'The video uses the pixel format “{pix}”, which cannot be graded.',
                            'Export or convert it to standard 4:2:0 video (H.264 or HEVC, 8-bit or 10-bit) and import that.')
    depth, chroma = PIXEL_FORMATS[pix]
    if width % 2 or height % 2:
        return _unsupported('ODD_FRAME_SIZE', f'The frame size {width}x{height} has an odd dimension, which the delivery format cannot hold.',
                            'Export the video with even dimensions (for example crop one pixel) and import that.', bit_depth=depth, chroma=chroma)
    profile, compatibility = _dolby_vision_profile(stream)
    if profile == 5 or compatibility == 0:
        return _unsupported('DOLBY_VISION_PROFILE_5', 'This is Dolby Vision profile 5, which has no standard HDR or SDR base layer to read.',
                            'Record or export it as HLG/HDR10 or standard SDR video and import that.', bit_depth=depth, chroma=chroma)
    matrix, primaries, transfer = _tag(stream.get('color_space')), _tag(stream.get('color_primaries')), _tag(stream.get('color_transfer'))
    rng = {'tv': 'limited', 'pc': 'full', 'jpeg': 'full', 'mpeg': 'limited'}.get(_tag(stream.get('color_range')))
    base = dict(bit_depth=depth, chroma=chroma)
    if transfer in UNSUPPORTED_TRANSFERS:
        return _unsupported('LOG_OR_LINEAR_TRANSFER', f'The video uses the “{transfer}” transfer curve (log or linear footage), which needs a camera-specific conversion.',
                            'Convert it to Rec.709 in the camera app or an editor and import that.', transfer=transfer, **base)
    if matrix is not None and matrix not in MATRICES:
        return _unsupported('MATRIX', f'The video uses the “{matrix}” color matrix, which is not supported.',
                            'Convert it to standard Rec.709 video and import that.', matrix=matrix, **base)
    if primaries is not None and primaries not in PRIMARIES:
        return _unsupported('PRIMARIES', f'The video uses the “{primaries}” color primaries, which are not supported.',
                            'Convert it to standard Rec.709 video and import that.', primaries=primaries, **base)
    if transfer is not None and transfer not in SDR_TRANSFERS and transfer not in HDR_TRANSFERS:
        return _unsupported('TRANSFER', f'The video uses the “{transfer}” transfer curve, which is not supported.',
                            'Convert it to standard Rec.709 video and import that.', transfer=transfer, **base)
    assumed = []
    if matrix is None:
        # ITU convention: 625/525-line formats are BT.601, everything HD or larger is BT.709.
        matrix = 'smpte170m' if max(width, height) <= 1024 and min(width, height) <= 576 else 'bt709'
        assumed.append('matrix')
    if rng is None:
        rng = 'full' if pix.startswith('yuvj') else 'limited'
        assumed.append('range')
    wide = matrix == 'bt2020nc' or primaries in {'bt2020'}
    if transfer is None:
        if wide:
            return _unsupported('AMBIGUOUS_WIDE_GAMUT', 'The video is wide-gamut (BT.2020) but does not say whether it is SDR or HDR, and the two look completely different.',
                                'Re-export it with its transfer curve set (HLG, PQ or SDR) and import that.', matrix=matrix, primaries=primaries, **base)
        transfer = 'bt709'
        assumed.append('transfer')
    if primaries is None:
        primaries = PRIMARIES_FOR_MATRIX[matrix]
        assumed.append('primaries')
    common = dict(matrix=matrix, primaries=primaries, transfer=transfer, range=rng, assumed=tuple(assumed), **base)
    if transfer in HDR_TRANSFERS:
        name = HDR_TRANSFERS[transfer]
        if not hdr:
            return _unsupported('HDR_DISABLED', f'This is {name} HDR video, and HDR-to-SDR conversion is turned off.',
                                'Turn it on (TAKEONE_HDR_TO_SDR=1) or import an SDR version.', hdr=name, **common)
        steps = (f'{name} HDR is tone-mapped to standard SDR ({TONEMAP_FOR[transfer].capitalize()} curve) and converted to Rec.709. This is an approximation of how an HDR display would show it.',
                 f'Graded in 16-bit and delivered as 8-bit Rec.709 SDR; the {depth}-bit original is kept untouched.')
        domain = SourceDomain(kind=TONEMAP, hdr=name, steps=steps, **common)
    else:
        needs = (matrix != 'bt709' or primaries != 'bt709' or transfer not in {'bt709'} or rng != 'limited' or bool(assumed))
        if not needs:
            return SourceDomain(kind=NATIVE, steps=_native_steps(depth, chroma), **common)
        domain = SourceDomain(kind=CONVERT, steps=_convert_steps(matrix, primaries, transfer, rng, depth, assumed), **common)
    tools = tools if tools is not None else {'zscale': True, 'tonemap': True}
    missing = [name for name, needed in (('zscale', True), ('tonemap', domain.kind == TONEMAP)) if needed and not tools.get(name)]
    if missing:
        return _unsupported('CONVERTER_MISSING', 'This video needs a color conversion, but this ffmpeg build lacks the ' + ' and '.join(f'“{m}”' for m in missing) + ' filter.',
                            'Install an ffmpeg build with libzimg (most full builds have it), then restart the backend.', hdr=domain.hdr, **common)
    return domain


def _native_steps(depth, chroma):
    return ((f'{depth}-bit {chroma[0]}:{chroma[1]}:{chroma[2]} is read at full precision.',) if depth > 8 or chroma != '420' else ())


def _convert_steps(matrix, primaries, transfer, rng, depth, assumed):
    steps = []
    label = {'matrix': 'color matrix', 'range': 'range', 'transfer': 'transfer curve', 'primaries': 'color primaries'}
    if assumed:
        steps.append('The file does not state its ' + ', '.join(label[a] for a in assumed) + '; the usual convention for this kind of video is assumed.')
    if matrix != 'bt709':
        steps.append(f'Color matrix {NAMES.get(matrix, matrix)} is converted to Rec.709.')
    if primaries != 'bt709':
        steps.append(f'Colors in the {NAMES.get(primaries, primaries)} gamut are converted to Rec.709.')
    if transfer not in {'bt709', 'smpte170m'}:
        steps.append(f'The {transfer} transfer curve is converted to Rec.709.')
    if rng == 'full':
        steps.append('Full range is converted to limited range.')
    if depth > 8:
        steps.append(f'Graded in 16-bit and delivered as 8-bit Rec.709 SDR; the {depth}-bit original is kept untouched.')
    return tuple(steps)


def conform_filters(domain: SourceDomain | None) -> str:
    """The ffmpeg filters that turn the source into 16-bit Rec.709 limited-range YUV 4:4:4, or '' when none are needed.

    Every conversion parameter is explicit. The output is tagged Rec.709 by the converter itself, so whatever follows
    (the existing format=gbrp16le, scale or encode steps) reads it exactly as it reads native footage."""
    if domain is None or domain.kind in {NATIVE, UNSUPPORTED}:
        return ''
    src = f'rangein={domain.range}:matrixin={domain.matrix}:primariesin={domain.primaries}:transferin={domain.transfer}'
    if domain.kind == CONVERT:
        return (f'zscale={src}:range=limited:matrix=bt709:primaries=bt709:transfer=bt709:dither=error_diffusion,'
                'format=yuv444p16le')
    tonemap = TONEMAP_FOR[domain.transfer]
    return (f'zscale={src}:transfer=linear:matrix=gbr:primaries={domain.primaries}:range=full:npl={REFERENCE_WHITE_NITS},format=gbrpf32le,'
            f'zscale=primaries=bt709,tonemap=tonemap={tonemap}:desat=0,'
            'zscale=transfer=bt709:matrix=bt709:range=limited:dither=error_diffusion,format=yuv444p16le')


def tagged_709() -> str:
    """Explicit Rec.709 limited-range tags for a stream built from converted frames."""
    return 'setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=limited'


def describe(domain: SourceDomain | None) -> dict | None:
    """A compact, user-facing summary for the interface."""
    if domain is None:
        return None
    label = {'native': 'Rec.709', 'convert': 'Converted to Rec.709', 'tonemap': f'{domain.hdr} HDR tone-mapped to SDR', 'unsupported': 'Not supported'}[domain.kind]
    return {'kind': domain.kind, 'label': label, 'steps': list(domain.steps), 'assumed': list(domain.assumed),
            'matrix': domain.matrix, 'primaries': domain.primaries, 'transfer': domain.transfer, 'range': domain.range,
            'bit_depth': domain.bit_depth, 'hdr': domain.hdr, 'message': domain.message, 'action': domain.action}
