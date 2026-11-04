"""Source color domains: how phone footage is interpreted and converted into the Rec.709 SDR domain the grade works in."""
import json
import subprocess

import numpy as np
import pytest

from takeone import color_domain as cd
from takeone.color_domain import CONVERT, NATIVE, TONEMAP, UNSUPPORTED, resolve

pytestmark = pytest.mark.filterwarnings('ignore')
TOOLS = cd.converter_tools('ffmpeg')
needs_zscale = pytest.mark.skipif(not TOOLS['zscale'], reason='this ffmpeg build has no zscale')
needs_tonemap = pytest.mark.skipif(not (TOOLS['zscale'] and TOOLS['tonemap']), reason='this ffmpeg build cannot tone-map')


def stream(pix='yuv420p', space='bt709', primaries='bt709', transfer='bt709', rng='tv', **extra):
    return {'pix_fmt': pix, 'color_space': space, 'color_primaries': primaries, 'color_transfer': transfer, 'color_range': rng, **extra}


# ---------- decisions ----------

def test_exactly_what_worked_before_is_native_and_needs_no_conversion():
    domain = resolve(stream(), width=3840, height=2160)
    assert domain.kind == NATIVE and not domain.assumed and not domain.steps and cd.conform_filters(domain) == ''
    # Bit depth and chroma subsampling alone are not a color difference: the 16-bit working path already reads them.
    for pix, depth, chroma in [('yuv420p10le', 10, '420'), ('yuv422p10le', 10, '422'), ('yuv444p', 8, '444')]:
        domain = resolve(stream(pix), width=1920, height=1080)
        assert domain.kind == NATIVE and (domain.bit_depth, domain.chroma) == (depth, chroma) and cd.conform_filters(domain) == ''


def test_the_android_phone_file_that_failed_is_ordinary_sdr_and_is_converted():
    # The real ffprobe values of the failing recording: Android 16, HEVC Main 10, full range, BT.601/PAL tags.
    domain = resolve(stream('yuv420p10le', 'smpte170m', 'bt470bg', 'smpte170m', 'pc'), width=3840, height=1644)
    assert domain.kind == CONVERT and domain.gradable and domain.bit_depth == 10 and not domain.assumed
    assert (domain.matrix, domain.primaries, domain.transfer, domain.range) == ('smpte170m', 'bt470bg', 'smpte170m', 'full')
    text = ' '.join(domain.steps)
    assert 'BT.601' in text and 'BT.470BG' in text and 'Full range' in text and '10-bit original is kept untouched' in text
    chain = cd.conform_filters(domain)
    assert chain == ('zscale=rangein=full:matrixin=smpte170m:primariesin=bt470bg:transferin=smpte170m:'  # explicit input; nothing left to a default
                     'range=limited:matrix=bt709:primaries=bt709:transfer=bt709:dither=error_diffusion,format=yuv444p16le')
    assert 'rangein=full:matrixin=smpte170m:primariesin=bt470bg:transferin=smpte170m' in chain


def test_missing_tags_are_assumed_by_convention_and_reported_as_assumed():
    hd = resolve(stream(space=None, primaries=None, transfer=None, rng=None), width=1920, height=1080)
    assert hd.kind == CONVERT and (hd.matrix, hd.primaries, hd.transfer, hd.range) == ('bt709', 'bt709', 'bt709', 'limited')
    assert set(hd.assumed) == {'matrix', 'primaries', 'transfer', 'range'} and 'does not state' in hd.steps[0]
    portrait_hd = resolve(stream(space='unknown', primaries='unknown', transfer='unknown', rng='unknown'), width=720, height=1280)
    assert portrait_hd.matrix == 'bt709'                                                   # a portrait 720x1280 phone clip is HD, not SD
    sd = resolve(stream(space=None, primaries=None, transfer=None, rng=None), width=720, height=576)
    assert (sd.matrix, sd.primaries) == ('smpte170m', 'smpte170m')                          # 625-line video is BT.601
    jpeg_like = resolve(stream('yuvj420p', space=None, primaries=None, transfer=None, rng=None), width=1920, height=1080)
    assert jpeg_like.range == 'full' and 'range' in jpeg_like.assumed                       # yuvj means full range


def test_hdr_is_tone_mapped_with_a_disclosed_policy_per_curve():
    hlg = resolve(stream('yuv420p10le', 'bt2020nc', 'bt2020', 'arib-std-b67'), width=3840, height=2160)
    pq = resolve(stream('yuv420p10le', 'bt2020nc', 'bt2020', 'smpte2084'), width=3840, height=2160)
    assert (hlg.kind, hlg.hdr) == (TONEMAP, 'HLG') and (pq.kind, pq.hdr) == (TONEMAP, 'PQ')
    assert 'tonemap=hable' in cd.conform_filters(hlg) and 'tonemap=mobius' in cd.conform_filters(pq)
    assert 'approximation' in hlg.steps[0] and 'SDR' in hlg.steps[1]                        # never silent
    off = resolve(stream('yuv420p10le', 'bt2020nc', 'bt2020', 'arib-std-b67'), width=3840, height=2160, hdr=False)
    assert off.kind == UNSUPPORTED and off.reason == 'HDR_DISABLED'


@pytest.mark.parametrize('case,expected', [
    (dict(stream=stream('rgb24'), size=(1920, 1080)), 'PIXEL_FORMAT'),
    (dict(stream=stream('yuva420p'), size=(1920, 1080)), 'PIXEL_FORMAT'),
    (dict(stream=stream('gray'), size=(1920, 1080)), 'PIXEL_FORMAT'),
    (dict(stream=stream(), size=(1919, 1080)), 'ODD_FRAME_SIZE'),
    (dict(stream=stream(transfer='log100'), size=(1920, 1080)), 'LOG_OR_LINEAR_TRANSFER'),
    (dict(stream=stream(transfer='smpte428'), size=(1920, 1080)), 'LOG_OR_LINEAR_TRANSFER'),
    (dict(stream=stream(transfer='linear'), size=(1920, 1080)), 'LOG_OR_LINEAR_TRANSFER'),
    (dict(stream=stream(space='bt2020c'), size=(1920, 1080)), 'MATRIX'),
    (dict(stream=stream('yuv420p10le', 'bt2020nc', 'bt2020', None), size=(3840, 2160)), 'AMBIGUOUS_WIDE_GAMUT'),
    (dict(stream=stream('yuv420p10le', side_data_list=[{'side_data_type': 'DOVI configuration record', 'dv_profile': 5, 'dv_bl_signal_compatibility_id': 0}]), size=(3840, 2160)), 'DOLBY_VISION_PROFILE_5'),
])
def test_what_cannot_be_converted_honestly_is_refused_with_a_specific_reason(case, expected):
    domain = resolve(case['stream'], width=case['size'][0], height=case['size'][1])
    assert domain.kind == UNSUPPORTED and domain.reason == expected and not domain.gradable
    assert domain.message and domain.action and cd.conform_filters(domain) == ''
    assert len(domain.message) < 200 and 'error' not in domain.message.lower()              # plain language


def test_dolby_vision_profile_8_with_an_hdr_base_layer_is_read_through_its_tags():
    dv = {'side_data_type': 'DOVI configuration record', 'dv_profile': 8, 'dv_bl_signal_compatibility_id': 4}
    domain = resolve(stream('yuv420p10le', 'bt2020nc', 'bt2020', 'arib-std-b67', side_data_list=[dv]), width=3840, height=2160)
    assert domain.kind == TONEMAP and domain.hdr == 'HLG'                                   # the iPhone default recording


def test_a_missing_converter_is_a_clear_refusal_not_a_failed_render():
    no_zscale = {'zscale': False, 'tonemap': False}
    assert resolve(stream(rng='pc'), width=1920, height=1080, tools=no_zscale).reason == 'CONVERTER_MISSING'
    assert resolve(stream('yuv420p10le', 'bt2020nc', 'bt2020', 'smpte2084'), width=3840, height=2160, tools={'zscale': True, 'tonemap': False}).reason == 'CONVERTER_MISSING'
    assert resolve(stream(), width=1920, height=1080, tools=no_zscale).kind == NATIVE        # native never needs the converter


def test_a_domain_survives_storage_and_old_records_without_one_read_as_none():
    domain = resolve(stream('yuv420p10le', 'smpte170m', 'bt470bg', 'smpte170m', 'pc'), width=3840, height=1644)
    again = cd.SourceDomain.from_dict(json.loads(json.dumps(domain.to_dict())))
    assert again == domain and cd.conform_filters(again) == cd.conform_filters(domain)
    assert cd.SourceDomain.from_dict(None) is None and cd.SourceDomain.from_dict({}) is None and cd.SourceDomain.from_dict('x') is None
    summary = cd.describe(domain)
    assert summary['kind'] == CONVERT and summary['label'] == 'Converted to Rec.709' and summary['steps']


# ---------- pixels: known colors in, the right colors out ----------

W, H = 640, 360
PATCHES = np.array([(16, 16, 16), (64, 64, 64), (128, 128, 128), (192, 192, 192), (235, 235, 235), (255, 255, 255), (220, 40, 40), (40, 200, 60),
                    (40, 60, 220), (240, 220, 40), (40, 210, 210), (220, 50, 200), (224, 172, 150), (160, 110, 80), (90, 60, 45), (120, 150, 70),
                    (70, 110, 60), (135, 170, 215), (200, 200, 190), (30, 30, 45), (250, 180, 60), (60, 90, 130), (180, 60, 90), (100, 100, 110)], float)
XYZ = {'bt709': [[.4124564, .3575761, .1804375], [.2126729, .7151522, .0721750], [.0193339, .1191920, .9503041]],
       'bt470bg': [[.4306190, .3415419, .1783091], [.2220379, .7066384, .0713236], [.0201853, .1295504, .9390944]],
       'smpte170m': [[.3935209, .3652581, .1916769], [.2123764, .7010599, .0865638], [.0187391, .1119335, .9583847]],
       'bt2020': [[.6369580, .1446169, .1688810], [.2627002, .6779981, .0593017], [0, .0280727, 1.0609851]]}


def expected_in_709(primaries, gamma=2.4):
    """The same colors expressed in Rec.709 primaries, converted the display-referred way (BT.1886-like power law)."""
    if primaries == 'bt709':
        return PATCHES
    linear = (PATCHES / 255) ** gamma @ (np.linalg.inv(XYZ['bt709']) @ np.array(XYZ[primaries])).T
    return 255 * np.clip(linear, 0, 1) ** (1 / gamma)


def ffmpeg(args, data=None):
    done = subprocess.run(['ffmpeg', '-v', 'error', '-y', *args], input=data, capture_output=True)
    assert done.returncode == 0, done.stderr.decode()[-500:]
    return done.stdout


def patch_image(width, height):
    image = np.zeros((height, width, 3), np.uint8)
    pw, ph = width // 6, height // 4
    for i, color in enumerate(PATCHES):
        r, c = divmod(i, 6)
        image[r * ph:(r + 1) * ph, c * pw:(c + 1) * pw] = color
    return image


def centres(width, height):
    pw, ph = width // 6, height // 4
    return [(r * ph + ph // 2, c * pw + pw // 2) for r in range(4) for c in range(6)]


def strip_color_tags(path, codec):
    """Encoders write a matrix tag of their own; make the file genuinely untagged (all three set to 'unspecified')."""
    bsf = ('h264_metadata' if codec == 'libx264' else 'hevc_metadata') + '=colour_primaries=2:transfer_characteristics=2:matrix_coefficients=2'
    stripped = path.with_name('stripped-' + path.name)
    ffmpeg(['-i', str(path), '-c', 'copy', '-bsf:v', bsf, str(stripped)])
    stripped.replace(path)


def encode(path, width, height, matrix, rng, pix, codec, tags, image=None, strip=False):
    vf = f'scale=out_color_matrix={matrix}:out_range={rng}' + (f',setparams={tags}' if tags else '')
    lossless = ['-crf', '0'] if codec == 'libx264' else ['-x265-params', 'lossless=1:log-level=error']
    frames = np.repeat((image if image is not None else patch_image(width, height))[None], 3, 0).tobytes()
    ffmpeg(['-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{width}x{height}', '-r', '25', '-i', '-', '-vf', vf, '-c:v', codec, '-pix_fmt', pix, *lossless, str(path)], frames)
    if strip:
        strip_color_tags(path, codec)


def probe_stream(path):
    raw = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_streams', '-of', 'json', str(path)], capture_output=True, check=True).stdout
    return json.loads(raw)['streams'][0]


def through_the_conversion(path, width, height, hdr=True):
    """Probe like ingest does, resolve, apply the conversion, then read RGB the way the 16-bit working path does."""
    probed = probe_stream(path)
    domain = resolve(probed, width=width, height=height, hdr=hdr)
    assert domain.gradable, domain.message
    chain = cd.conform_filters(domain)
    raw = ffmpeg(['-i', str(path), '-vf', (chain + ',' if chain else '') + 'format=rgb48le', '-frames:v', '1', '-f', 'rawvideo', '-'])
    image = np.frombuffer(raw, np.uint16).reshape(height, width, 3) / 65535 * 255
    return domain, np.array([image[y, x] for y, x in centres(width, height)])


T = lambda **k: ':'.join(f'{a}={b}' for a, b in k.items())  # noqa: E731
PHONES = [  # label, size, forward matrix, forward range, pix_fmt, codec, tags written to the file, primaries of the content, expected kind
    ('android 10-bit BT.601 full range', (W, H), 'bt601', 'pc', 'yuv420p10le', 'libx265', T(colorspace='smpte170m', color_primaries='bt470bg', color_trc='smpte170m', range='pc'), 'bt470bg', CONVERT),
    ('untagged 8-bit HD', (1280, 720), 'bt709', 'tv', 'yuv420p', 'libx264', 'STRIP', 'bt709', CONVERT),
    ('untagged 8-bit SD (BT.601 convention)', (640, 480), 'bt601', 'tv', 'yuv420p', 'libx264', 'STRIP', 'smpte170m', CONVERT),
    ('SD with only the matrix tagged', (640, 480), 'bt601', 'tv', 'yuv420p', 'libx264', '', 'bt470bg', CONVERT),
    ('8-bit full range Rec.709 (yuvj)', (W, H), 'bt709', 'pc', 'yuvj420p', 'libx264', T(colorspace='bt709', color_primaries='bt709', color_trc='bt709', range='pc'), 'bt709', CONVERT),
    ('NTSC/PAL BT.601 limited', (W, H), 'bt601', 'tv', 'yuv420p', 'libx264', T(colorspace='smpte170m', color_primaries='smpte170m', color_trc='smpte170m', range='tv'), 'smpte170m', CONVERT),
    ('BT.2020 SDR 10-bit', (W, H), 'bt2020', 'tv', 'yuv420p10le', 'libx265', T(colorspace='bt2020nc', color_primaries='bt2020', color_trc='bt2020-10', range='tv'), 'bt2020', CONVERT),
    ('10-bit Rec.709 limited', (W, H), 'bt709', 'tv', 'yuv420p10le', 'libx265', T(colorspace='bt709', color_primaries='bt709', color_trc='bt709', range='tv'), 'bt709', NATIVE),
    ('10-bit 4:2:2 Rec.709 limited', (W, H), 'bt709', 'tv', 'yuv422p10le', 'libx265', T(colorspace='bt709', color_primaries='bt709', color_trc='bt709', range='tv'), 'bt709', NATIVE),
]


@needs_zscale
@pytest.mark.parametrize('case', PHONES, ids=[c[0] for c in PHONES])
def test_phone_footage_comes_out_as_the_colors_that_were_recorded(tmp_path, case):
    label, (w, h), matrix, rng, pix, codec, tags, content_primaries, kind = case
    path = tmp_path / 'clip.mp4'
    encode(path, w, h, matrix, rng, pix, codec, '' if tags == 'STRIP' else tags, strip=tags == 'STRIP')
    domain, got = through_the_conversion(path, w, h)
    assert domain.kind == kind, (label, domain)
    if tags == 'STRIP':
        assert {'matrix', 'primaries', 'transfer'} <= set(domain.assumed), domain.assumed          # nothing was stated, and the result says so
    error = np.abs(got - expected_in_709(content_primaries))
    assert error.max() <= 3.0 and error.mean() <= 1.5, f'{label}: max {error.max():.2f} mean {error.mean():.2f} (8-bit levels)'


@needs_zscale
def test_the_untagged_conventions_are_the_ones_a_real_player_would_use_and_are_not_the_decoder_default(tmp_path):
    """An untagged HD file read with ffmpeg's own defaults is decoded with the BT.601 matrix and is visibly wrong;
    the conversion states the matrix, so it is not."""
    path = tmp_path / 'untagged.mp4'
    encode(path, 1280, 720, 'bt709', 'tv', 'yuv420p', 'libx264', '', strip=True)
    raw = ffmpeg(['-i', str(path), '-vf', 'scale=in_color_matrix=bt601:in_range=tv,format=rgb48le', '-frames:v', '1', '-f', 'rawvideo', '-'])
    wrong = np.frombuffer(raw, np.uint16).reshape(720, 1280, 3) / 65535 * 255
    wrong = np.array([wrong[y, x] for y, x in centres(1280, 720)])
    _, right = through_the_conversion(path, 1280, 720)
    assert np.abs(wrong - PATCHES).max() > 8 and np.abs(right - PATCHES).max() <= 3


# ---------- HDR: a documented approximation, checked for the properties that must hold ----------

def hdr_ramp(path, transfer):
    levels = np.linspace(0.05, 1.0, 16)
    image = np.zeros((H, W, 3), np.uint8)
    for i, level in enumerate(levels):
        image[:, i * (W // 16):(i + 1) * (W // 16)] = round(level * 255)
    tags = T(colorspace='bt2020nc', color_primaries='bt2020', color_trc=transfer, range='tv')
    encode(path, W, H, 'bt2020', 'tv', 'yuv420p10le', 'libx265', tags, image=image)
    return levels, [(H // 2, i * (W // 16) + W // 32) for i in range(16)]


@needs_tonemap
@pytest.mark.parametrize('transfer,reference_signal,low,high', [('arib-std-b67', .75, 170, 215), ('smpte2084', .58, 215, 250)])
def test_hdr_ramps_map_monotonically_into_sdr_with_white_near_white(tmp_path, transfer, reference_signal, low, high):
    path = tmp_path / 'hdr.mp4'
    levels, points = hdr_ramp(path, transfer)
    domain = resolve(probe_stream(path), width=W, height=H)
    assert domain.kind == TONEMAP
    raw = ffmpeg(['-i', str(path), '-vf', cd.conform_filters(domain) + ',format=rgb48le', '-frames:v', '1', '-f', 'rawvideo', '-'])
    image = np.frombuffer(raw, np.uint16).reshape(H, W, 3) / 65535 * 255
    grey = np.array([image[y, x].mean() for y, x in points])
    assert np.all(np.diff(grey) >= -0.6), grey.round()                                       # no inversions anywhere in the range
    assert grey[0] < 40 and grey[-1] >= 245                                                  # shadows stay shadows, the peak reaches white
    assert np.abs(image[points[7][0], points[7][1]] - image[points[7][0], points[7][1]].mean()).max() < 2   # neutrals stay neutral
    reference = grey[int(np.argmin(np.abs(levels - reference_signal)))]
    assert low <= reference <= high, f'{transfer} reference white maps to {reference:.0f}/255'


@needs_tonemap
def test_hdr_saturated_colors_stay_in_gamut_and_keep_their_hue(tmp_path):
    path = tmp_path / 'hdr-colors.mp4'
    colors = np.zeros((H, W, 3), np.uint8)
    for i, color in enumerate([(200, 60, 60), (60, 200, 60), (60, 60, 200), (200, 200, 60)]):
        colors[:, i * (W // 4):(i + 1) * (W // 4)] = color
    encode(path, W, H, 'bt2020', 'tv', 'yuv420p10le', 'libx265', T(colorspace='bt2020nc', color_primaries='bt2020', color_trc='arib-std-b67', range='tv'), image=colors)
    domain = resolve(probe_stream(path), width=W, height=H)
    raw = ffmpeg(['-i', str(path), '-vf', cd.conform_filters(domain) + ',format=rgb48le', '-frames:v', '1', '-f', 'rawvideo', '-'])
    image = np.frombuffer(raw, np.uint16).reshape(H, W, 3) / 65535 * 255
    red, green, blue, yellow = (image[H // 2, i * (W // 4) + W // 8] for i in range(4))
    assert red[0] > red[1] + 40 and red[0] > red[2] + 40 and green[1] > green[0] + 40 and green[1] > green[2] + 40
    assert blue[2] > blue[0] + 40 and blue[2] > blue[1] + 40 and yellow[0] > yellow[2] + 40 and yellow[1] > yellow[2] + 40
    assert image.min() >= 0 and image.max() <= 255.01


@needs_zscale
def test_these_tolerances_would_catch_a_wrong_conversion(tmp_path):
    """The accuracy bounds above are only worth something if a real mistake exceeds them. Misreading the Android file's
    matrix and range (what happens when tags are ignored) must be far outside them."""
    import dataclasses
    path = tmp_path / 'clip.mp4'
    encode(path, W, H, 'bt601', 'pc', 'yuv420p10le', 'libx265', T(colorspace='smpte170m', color_primaries='bt470bg', color_trc='smpte170m', range='pc'))
    domain = resolve(probe_stream(path), width=W, height=H)
    for wrong in (dataclasses.replace(domain, matrix='bt709'), dataclasses.replace(domain, range='limited'), dataclasses.replace(domain, primaries='bt709')):
        raw = ffmpeg(['-i', str(path), '-vf', cd.conform_filters(wrong) + ',format=rgb48le', '-frames:v', '1', '-f', 'rawvideo', '-'])
        image = np.frombuffer(raw, np.uint16).reshape(H, W, 3) / 65535 * 255
        error = np.abs(np.array([image[y, x] for y, x in centres(W, H)]) - expected_in_709('bt470bg'))
        assert error.max() > 5 or error.mean() > 1.5, (wrong, error.max(), error.mean())
