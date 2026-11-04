"""Input profiles: flat and camera-log footage, a camera maker's LUT, and a clip's own measured levels.

Log footage cannot be recognised from its tags, so the person chooses how to read it. These tests hold the two ways to
account for it to an independent reference: real camera-to-Rec.709 transforms from OpenColorIO's built-in library (V-Log,
S-Log3, Canon Log 3, RED Log3G10, ARRI LogC3) exported as .cube files and applied by our chain, and a straight numpy stretch.
"""
import json
import subprocess
import threading
from uuid import uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient

from takeone import evidence
from takeone.app import create_app
from takeone.color import domain as cd, files as source_color, levels as color_levels, lut as color_lut, profile as color_profile
from takeone.config import Settings
from takeone.errors import Problem
from tests.color.test_domain import PATCHES, W, H, T, centres, encode, ffmpeg, needs_tonemap, needs_zscale
from tests.color.test_phone_footage import make_scene, rendered_frame
from tests.test_backend import client, settings, start, upload, wait  # noqa: F401
from tests.test_local_grading import Planner, generate

pytestmark = [needs_zscale, pytest.mark.filterwarnings('ignore')]
NATIVE_TAGS = T(colorspace='bt709', color_primaries='bt709', color_trc='bt709', range='tv')
FULL_TAGS = T(colorspace='bt709', color_primaries='bt709', color_trc='bt709', range='pc')


# ---------- helpers ----------

def cube_text(function, size=17, title='test'):
    """A 3D .cube (red index fastest) sampling `function`, which maps an (n, 3) array of 0-1 RGB to 0-1 RGB."""
    grid = np.linspace(0, 1, size)
    b, g, r = np.meshgrid(grid, grid, grid, indexing='ij')
    rgb = np.stack([r, g, b], -1).reshape(-1, 3)
    out = np.clip(function(rgb), 0, 1)
    return (f'TITLE "{title}"\nLUT_3D_SIZE {size}\n' + '\n'.join(f'{x:.6f} {y:.6f} {z:.6f}' for x, y, z in out) + '\n').encode()


def install(directory, data, name='cam.cube'):
    profile, _ = color_profile.lut_profile(data, name)
    color_profile.store_lut(directory, data, profile)
    return profile


def graded_frame(path, vf):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-vf', vf, '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8)


def patch_colors(path, chain, width=W, height=H):
    """The 24 patch colors of the first frame after `chain`, read as Rec.709 (the tags the chain gives its output)."""
    image = graded_frame(path, chain + ',' + cd.tagged_709()).reshape(height, width, 3).astype(float)
    return np.array([image[y - 3:y + 4, x - 3:x + 4].reshape(-1, 3).mean(0) for y, x in centres(width, height)])


def ramp_scene(width=192, height=108):
    """A horizontal grey ramp over the whole picture with a saturated block in the middle."""
    image = np.repeat(np.tile(np.linspace(0, 255, width)[None, :], (height, 1))[..., None], 3, 2)
    image[round(height * .22):round(height * .78), round(width * .31):round(width * .69)] = (190, 35, 25)
    return image


def flatten(image, black=.15, white=.78):
    return black * 255 + image / 255 * (white - black) * 255


def make_clip(path, image, pix='yuv420p', rng='tv', tags=NATIVE_TAGS, frames=12):
    height, width = image.shape[:2]
    data = np.repeat(np.round(image).astype(np.uint8)[None], frames, 0).tobytes()
    ffmpeg(['-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{width}x{height}', '-r', '12', '-i', '-', '-vf', f'scale=out_color_matrix=bt709:out_range={rng},setparams={tags}',
            '-c:v', 'libx264', '-pix_fmt', pix, '-crf', '0', str(path)], data)
    return path


# ---------- .cube files ----------

IDENTITY_2 = 'LUT_3D_SIZE 2\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n'


def test_cube_files_in_their_real_world_variants_are_read():
    plain = color_lut.parse_cube(IDENTITY_2.encode())
    assert (plain.kind, plain.size, plain.title) == ('3d', 2, '') and plain.domain_min == (0, 0, 0) and plain.domain_max == (1, 1, 1)
    # Windows line endings, a byte-order mark, comments, a quoted title, domain lines, ragged spacing, blank lines.
    messy = ('﻿# made by a camera app\r\nTITLE "Cam to 709"\r\nDOMAIN_MIN 0 0 0\r\nDOMAIN_MAX 1.0 1.0 1.0\r\n\r\nLUT_3D_SIZE 2\r\n'
             + ''.join(f'  {r}   {g}  {b} \r\n' for b in (0, 1) for g in (0, 1) for r in (0, 1)))
    assert color_lut.parse_cube(messy.encode()).title == 'Cam to 709'
    one_d = color_lut.parse_cube(('LUT_1D_SIZE 4\n' + ''.join(f'{v} {v} {v}\n' for v in (0, .2, .6, 1))).encode())
    assert (one_d.kind, one_d.size) == ('1d', 4)
    assert color_lut.parse_cube((IDENTITY_2.replace('LUT_3D_SIZE 2', 'LUT_3D_SIZE 2\nDOMAIN_MIN -0.1 -0.1 -0.1\nDOMAIN_MAX 1.2 1.2 1.2')).encode()).domain_max == (1.2, 1.2, 1.2)


@pytest.mark.parametrize('data,code', [
    (b'', 'LUT_EMPTY'), (b'   \n', 'LUT_EMPTY'), (b'\xff\xfe\x00\x01 not text', 'LUT_NOT_TEXT'), (b'abc\x00def', 'LUT_NOT_TEXT'),
    (b'PNG\n1 2 3\n', 'LUT_UNKNOWN_KEYWORD'), (b'0 0 0\n1 1 1\n', 'LUT_NO_SIZE'),
    (('LUT_3D_SIZE 2\nLUT_1D_SIZE 2\n' + '0 0 0\n' * 8).encode(), 'LUT_NO_SIZE'),
    (b'LUT_3D_SIZE two\n', 'LUT_BAD_SIZE'), (b'LUT_3D_SIZE 1\n0 0 0\n', 'LUT_BAD_SIZE'), (b'LUT_3D_SIZE 200\n', 'LUT_BAD_SIZE'), (b'LUT_1D_SIZE 1\n0 0 0\n', 'LUT_BAD_SIZE'),
    (IDENTITY_2.rsplit('\n', 2)[0].encode(), 'LUT_ROW_COUNT'), ((IDENTITY_2 + '0 0 0\n').encode(), 'LUT_ROW_COUNT'),
    (IDENTITY_2.replace('1 1 1', '1 1 nan').encode(), 'LUT_BAD_NUMBER'), (IDENTITY_2.replace('1 1 1', '1 1 inf').encode(), 'LUT_BAD_NUMBER'),
    (IDENTITY_2.replace('1 1 1', '1 1 9999').encode(), 'LUT_BAD_NUMBER'), (IDENTITY_2.replace('1 1 1', '1 1').encode(), 'LUT_BAD_NUMBER'),
    (IDENTITY_2.replace('1 1 1', '1 1 x').encode(), 'LUT_BAD_NUMBER'),
    (IDENTITY_2.replace('LUT_3D_SIZE 2', 'LUT_3D_SIZE 2\nDOMAIN_MIN 1 1 1\nDOMAIN_MAX 0 0 0').encode(), 'LUT_BAD_DOMAIN'),
    (IDENTITY_2.replace('LUT_3D_SIZE 2', 'LUT_3D_SIZE 2\nDOMAIN_MIN 0 0').encode(), 'LUT_BAD_NUMBER'),
    (b'x' * (color_lut.MAX_BYTES + 1), 'LUT_TOO_LARGE'),
])
def test_a_file_that_is_not_a_usable_lut_is_refused_with_its_specific_reason(data, code):
    with pytest.raises(color_lut.LutError) as refused:
        color_lut.parse_cube(data)
    assert refused.value.code == code and refused.value.message


@pytest.mark.parametrize('folder', ['plain', 'with space', 'a,b', 'a:b', "it's", 'sq[1]', 'semi;colon', 'back\\slash', 'ünï côde', "all of: ,'[];\\ together"])
def test_a_project_folder_with_any_awkward_character_still_applies_its_lut(tmp_path, folder):
    directory = tmp_path / folder
    directory.mkdir()
    (directory / 'lut.cube').write_text(IDENTITY_2)
    cube = color_lut.parse_cube(IDENTITY_2.encode())
    out = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=64x64:rate=1:duration=1', '-vf', 'format=gbrp16le,' + color_lut.filter_for(cube, directory / 'lut.cube'),
                          '-frames:v', '1', '-f', 'null', '-'], capture_output=True)
    assert out.returncode == 0, out.stderr.decode()[-300:]


# ---------- the profile ----------

def test_a_profile_knows_what_it_is_and_what_changes_the_pictures_built_from_it():
    a, b = color_profile.normalize(.14, .77), color_profile.normalize(.14, .77)
    assert a == b and a.signature == b.signature and color_profile.InputProfile.from_dict(json.loads(json.dumps(a.to_dict()))) == a
    assert color_profile.normalize(.15, .77).signature != a.signature
    one, _ = color_profile.lut_profile(IDENTITY_2.encode(), 'one.cube')
    renamed, _ = color_profile.lut_profile(IDENTITY_2.encode(), 'two.cube')
    other, _ = color_profile.lut_profile(IDENTITY_2.replace('1 1 1', '1 1 0.9').encode(), 'one.cube')
    assert one.signature == renamed.signature != other.signature           # the LUT is its content, not its file name
    assert color_profile.signature(None) == '' and 'approximat' in ' '.join(a.steps) and 'Contrast' in a.label and 'LUT' in one.label


@pytest.mark.parametrize('black,white', [(.5, .9), (.1, .3), (.3, .45), (-.1, .8), (.1, 1.2), (0, 1), (.01, .99), (.029, .951)])
def test_levels_that_are_not_those_of_flat_footage_cannot_be_normalized(black, white):
    with pytest.raises(Problem) as refused:
        color_profile.normalize(black, white)
    assert refused.value.code == 'INPUT_PROFILE_NOT_FLAT' and refused.value.status == 422


def test_a_saved_profile_is_atomic_removable_and_never_silently_ignored_when_damaged(tmp_path):
    source = tmp_path / 'source.bin'
    assert color_profile.read(source) is None
    profile = color_profile.normalize(.14, .77)
    color_profile.write(source, profile)
    assert color_profile.read(source) == profile and color_profile.sidecar(source).name == 'source.bin.color.json'
    assert [p.name for p in tmp_path.iterdir()] == ['source.bin.color.json']              # no temporary files left behind
    color_profile.write(source, None)
    assert color_profile.read(source) is None and not list(tmp_path.iterdir())
    for damage in ('{', '[]', '{"kind": "lut"}', '{"kind": "normalize", "black": 0.9, "white": 0.95}', '{"kind": "bogus"}',
                   '{"kind": "lut", "lut_file": "../x.cube", "lut_kind": "3d", "lut_size": 2, "lut_sha256": "a"}'):
        color_profile.sidecar(source).write_text(damage)
        with pytest.raises(Problem) as broken:
            color_profile.read(source)
        assert broken.value.code == 'INPUT_PROFILE_INVALID'


def test_without_a_profile_the_chain_is_exactly_the_automatic_conversion(tmp_path):
    native = cd.SourceDomain(kind=cd.NATIVE, matrix='bt709', primaries='bt709', transfer='bt709', range='limited')
    android = cd.resolve(dict(codec_type='video', pix_fmt='yuv420p10le', color_space='smpte170m', color_primaries='bt470bg', color_transfer='smpte170m', color_range='pc'), width=3840, height=1644)
    for domain in (native, android):
        assert color_profile.conform_chain(domain, None, tmp_path / 'source.bin') == cd.conform_filters(domain)
    assert color_profile.conform_chain(native, None, tmp_path / 'source.bin') == ''


def test_a_profile_is_not_applied_to_hdr_which_is_already_converted_from_its_own_tags(tmp_path):
    hlg = cd.resolve(dict(codec_type='video', pix_fmt='yuv420p10le', color_space='bt2020nc', color_primaries='bt2020', color_transfer='arib-std-b67', color_range='tv'), width=3840, height=2160)
    assert color_profile.applicable(hlg)[0] is False
    with pytest.raises(Problem) as refused:
        color_profile.conform_chain(hlg, color_profile.normalize(.14, .77), tmp_path / 'source.bin')
    assert refused.value.code == 'INPUT_PROFILE_NOT_APPLICABLE'
    assert color_profile.applicable(cd.SourceDomain(kind=cd.NATIVE))[0] and not color_profile.applicable(None)[0]


def test_a_missing_lut_file_is_a_clear_error_not_an_unprofiled_picture(tmp_path):
    profile, _ = color_profile.lut_profile(IDENTITY_2.encode(), 'x.cube')
    with pytest.raises(Problem) as refused:
        color_profile.conform_chain(cd.SourceDomain(kind=cd.NATIVE, matrix='bt709', range='limited'), profile, tmp_path / 'source.bin')
    assert refused.value.code == 'INPUT_LUT_MISSING'


def test_stored_luts_are_content_addressed_and_unused_ones_are_removed(tmp_path):
    first, second = install(tmp_path, IDENTITY_2.encode()), install(tmp_path, IDENTITY_2.replace('1 1 1', '1 1 .9').encode())
    assert install(tmp_path, IDENTITY_2.encode()) == first and len(list(tmp_path.glob('input-lut-*.cube'))) == 2
    color_profile.prune_luts(tmp_path, second)
    assert [p.name for p in tmp_path.glob('input-lut-*.cube')] == [second.lut_file]
    color_profile.prune_luts(tmp_path, None)
    assert not list(tmp_path.glob('input-lut-*.cube'))


# ---------- camera LUTs, held to real camera transforms ----------

try:
    import PyOpenColorIO as ocio
    OCIO_CONFIG = ocio.Config.CreateFromFile('ocio://studio-config-v2.1.0_aces-v1.3_ocio-v2.3')
    CAMERAS = ['V-Log V-Gamut', 'S-Log3 S-Gamut3', 'CanonLog3 CinemaGamut D55', 'Log3G10 REDWideGamutRGB', 'ARRI LogC3 (EI800)']
    for name in CAMERAS:
        OCIO_CONFIG.getColorSpace(name).getName()
except Exception:  # noqa: BLE001 - optional reference library
    ocio, CAMERAS = None, []
needs_ocio = pytest.mark.skipif(ocio is None, reason='OpenColorIO is not available')


def camera_transform(camera):
    """OpenColorIO's own camera-to-Rec.709 display transform, as a function on 0-1 RGB: the reference and the LUT's source."""
    transform = ocio.DisplayViewTransform(src=camera, display='Rec.1886 Rec.709 - Display', view='ACES 1.0 - SDR Video')
    processor = OCIO_CONFIG.getProcessor(transform).getDefaultCPUProcessor()

    def apply(rgb):
        out = np.array(rgb, np.float32).reshape(-1, 3).copy()
        processor.applyRGB(out)
        return np.clip(out, 0, 1)
    return apply


@needs_ocio
@pytest.mark.parametrize('rng,pix,tags', [('tv', 'yuv420p10le', NATIVE_TAGS), ('pc', 'yuv420p10le', FULL_TAGS)], ids=['limited range', 'full range'])
@pytest.mark.parametrize('camera', CAMERAS)
def test_a_camera_makers_lut_gives_the_cameras_own_rec709_picture(tmp_path, settings, camera, rng, pix, tags):
    transform = camera_transform(camera)
    reference = transform(PATCHES / 255) * 255
    clip = tmp_path / 'source.mp4'
    encode(clip, W, H, 'bt709', rng, pix, 'libx265', tags)
    # The camera's log picture is what the patches hold; the maker's LUT turns it into the picture the camera intends.
    profile = install(tmp_path, cube_text(transform, 65, camera))
    color_profile.write(clip, profile)
    chain = source_color.chain_for_file(clip, settings)
    got = patch_colors(clip, chain)
    error = np.abs(got - reference)
    assert error.mean() <= 1.6 and np.median(error.max(1)) <= 2.5, f'{camera}: mean {error.mean():.2f}, median patch worst {np.median(error.max(1)):.1f}'
    # The same tolerance must fail a real mistake: the LUT fed range-misread footage.
    wrong = patch_colors(clip, color_profile.to_rgb('limited' if rng == 'pc' else 'full', 'bt709') + ',' + chain.split(',', 2)[2])
    assert np.abs(wrong - reference).mean() > 4


@needs_ocio
def test_a_camera_lut_replaces_the_automatic_conversion_instead_of_following_it(tmp_path, settings):
    """Phone clips that are tagged BT.601/BT.470BG still hold the camera's log numbers; the LUT must see exactly those."""
    transform = camera_transform('V-Log V-Gamut')
    clip = tmp_path / 'source.mp4'
    tags = T(colorspace='smpte170m', color_primaries='bt470bg', color_trc='smpte170m', range='pc')
    encode(clip, W, H, 'bt601', 'pc', 'yuv420p10le', 'libx265', tags)
    color_profile.write(clip, install(tmp_path, cube_text(transform, 65)))
    assert source_color.domain_for_file(clip, settings).kind == cd.CONVERT
    error = np.abs(patch_colors(clip, source_color.chain_for_file(clip, settings)) - transform(PATCHES / 255) * 255)
    assert error.mean() <= 1.6, error.mean()


def test_a_one_dimensional_lut_is_applied_per_channel(tmp_path, settings):
    curve = lambda x: x ** (1 / 1.8)                                           # noqa: E731
    points = np.linspace(0, 1, 1024)
    data = ('LUT_1D_SIZE 1024\n' + ''.join(f'{v:.6f} {v:.6f} {v:.6f}\n' for v in curve(points))).encode()
    clip = tmp_path / 'source.mp4'
    encode(clip, W, H, 'bt709', 'tv', 'yuv420p10le', 'libx265', NATIVE_TAGS)
    color_profile.write(clip, install(tmp_path, data))
    got = patch_colors(clip, source_color.chain_for_file(clip, settings))
    assert np.abs(got - curve(PATCHES / 255) * 255).mean() <= 1.5


# ---------- normalizing flat footage ----------

def test_normalizing_stretches_the_clips_own_black_and_white_to_the_full_range(tmp_path, settings):
    black, white = .15, .78
    levels = np.linspace(black, white, 24)
    image = np.zeros((H, W, 3), np.uint8)
    pw = W // 6
    for i, v in enumerate(levels):
        r, c = divmod(i, 6)
        image[r * (H // 4):(r + 1) * (H // 4), c * pw:(c + 1) * pw] = round(v * 255)
    clip = tmp_path / 'source.mp4'
    encode(clip, W, H, 'bt709', 'tv', 'yuv420p', 'libx264', NATIVE_TAGS, image=image)
    color_profile.write(clip, color_profile.normalize(black, white))
    got = patch_colors(clip, source_color.chain_for_file(clip, settings))[:, 1]
    expected = np.clip((levels - black) / (white - black), 0, 1) * 255
    assert np.abs(got - expected).max() <= 2.5 and np.all(np.diff(got) > 0)           # exact stretch, order preserved, ends at 0 and 255
    assert got[0] <= 2.5 and got[-1] >= 252.5


def test_normalizing_keeps_hue_and_changes_nothing_but_contrast(tmp_path, settings):
    image = np.zeros((H, W, 3), np.uint8)
    image[:] = (190, 120, 80)                                                  # one warm color
    flat = np.round(.15 * 255 + image / 255 * .63 * 255).astype(np.uint8)
    clip = tmp_path / 'source.mp4'
    encode(clip, W, H, 'bt709', 'tv', 'yuv420p10le', 'libx265', NATIVE_TAGS, image=flat)
    color_profile.write(clip, color_profile.normalize(.15, .78))
    got = patch_colors(clip, source_color.chain_for_file(clip, settings))[0]
    assert np.abs(got - image[0, 0]).max() <= 2.5, got


# ---------- measuring a clip ----------

def test_levels_say_a_clip_is_flat_only_when_its_blacks_are_lifted_and_its_whites_are_capped():
    def levels(low, high):
        return color_levels.describe(np.random.default_rng(1).uniform(low, high, (200, 200)), 1)
    assert levels(.13, .77).flat and levels(.13, .77).normalizable
    assert not levels(0, 1).flat                       # an ordinary clip
    assert not levels(0, .35).flat                     # a dark, low-key clip has true blacks
    assert not levels(.2, .98).flat                    # bright clip that reaches white
    assert not levels(.5, .6).normalizable             # lifted blacks but nothing to stretch
    assert color_levels.describe(np.zeros(100), 1) is None and color_levels.describe(np.full(50000, np.nan), 1) is None


def test_a_clips_levels_are_measured_from_its_pictures(tmp_path, settings):
    flat, normal = tmp_path / 'flat.mp4', tmp_path / 'normal.mp4'
    make_clip(flat, flatten(ramp_scene()))
    make_clip(normal, ramp_scene())
    a, b = (color_levels.measure(p, settings, threading.Event()) for p in (flat, normal))
    assert a.flat and abs(a.black - .15) < .03 and abs(a.white - .78) < .03 and a.samples >= 10
    assert not b.flat and b.black < .03 and b.white > .97
    assert color_levels.measure(tmp_path / 'missing.mp4', settings, threading.Event()) is None
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(Problem) as stopped:
        color_levels.measure(flat, settings, cancelled)
    assert stopped.value.code == 'JOB_CANCELLED'


# ---------- through the service ----------

def apply_profile(c, state, **choice):
    response = c.put('/api/projects/' + state['id'] + '/input-profile', json={'command_id': str(uuid4()), 'revision': state['revision'], **choice})
    assert response.status_code == 202, response.text
    return wait(c, state['id'], 'ready')


def upload_lut(c, state, data, name='cam.cube'):
    return c.post('/api/projects/' + state['id'] + '/input-profile/lut', files={'file': (name, data, 'application/octet-stream')})


@pytest.fixture
def flat_video(tmp_path):
    return make_clip(tmp_path / 'flat.mp4', flatten(ramp_scene()))


@pytest.fixture
def normal_video(tmp_path):
    return make_clip(tmp_path / 'normal.mp4', ramp_scene())


def test_flat_footage_is_recognised_at_import_and_offered_a_fix_but_never_given_one(settings, flat_video, normal_video):
    with client(settings, Planner()) as c:
        flat = upload(c, flat_video)
        assert color_levels.Levels.from_dict(flat['source']['levels']).flat and flat['source']['profile'] is None
        info = c.get('/api/projects/' + flat['id'] + '/input-profile').json()
        assert info['suggest'] == 'normalize' and info['applicable'] and info['locked'] is None and info['profile'] is None and info['levels']['flat']
        ordinary = c.get('/api/projects/' + upload(c, normal_video)['id'] + '/input-profile').json()
        assert ordinary['suggest'] is None and ordinary['applicable'] and not ordinary['levels']['flat']
        assert not color_profile.sidecar(settings.workspace / flat['id'] / 'source.bin').exists()


def test_choosing_normalize_rebuilds_the_proxies_and_says_so_everywhere(settings, flat_video):
    directory = None
    with client(settings, Planner()) as c:
        state = upload(c, flat_video)
        directory = settings.workspace / state['id']
        before = color_levels.measure(directory / 'analysis.mp4', settings, threading.Event())
        assert before.flat and state['preview']['conform_sig'] == ''
        applying = c.put('/api/projects/' + state['id'] + '/input-profile', json={'command_id': str(uuid4()), 'revision': state['revision'], 'kind': 'normalize'})
        assert applying.status_code == 202 and applying.json()['status'] == 'ingesting'
        done = wait(c, state['id'], 'ready')
        profile = color_profile.read(directory / 'source.bin')
        assert profile.kind == 'normalize' and done['preview']['conform_sig'] == profile.signature and done['source']['profile']['signature'] == profile.signature
        assert any('approximation' in note for note in done['source']['technical_notes']) and done['source']['profile_error'] is None
        after = color_levels.measure(directory / 'analysis.mp4', settings, threading.Event())
        assert not after.flat and after.black < .05 and after.white > .93        # what Muse will see, and what the grade works from
        info = c.get('/api/projects/' + state['id'] + '/input-profile').json()
        assert info['profile']['kind'] == 'normalize' and info['suggest'] is None and info['levels']['flat']      # levels are of the clip as imported
        assert [e['title'] for e in done['events'] if 'nput profile' in e['title']][-3:] == ['Input profile queued', 'Applying the input profile', 'Input profile applied']
        # Choosing nothing puts everything back, and the same request again is a no-op.
        again = apply_profile(c, done, kind='normalize')
        assert again['status'] == 'ready' and again['source']['profile']['kind'] == 'normalize'
        removed = apply_profile(c, again, kind='none')
        assert color_profile.read(directory / 'source.bin') is None and removed['preview']['conform_sig'] == '' and removed['source']['profile'] is None
        assert color_levels.measure(directory / 'analysis.mp4', settings, threading.Event()).flat


def test_a_request_that_changes_nothing_starts_no_work(settings, flat_video):
    with client(settings, Planner()) as c:
        state = upload(c, flat_video)
        done = apply_profile(c, state, kind='normalize')
        same = c.put('/api/projects/' + done['id'] + '/input-profile', json={'command_id': str(uuid4()), 'revision': done['revision'], 'kind': 'normalize'})
        assert same.status_code == 202 and same.json()['status'] == 'ready'
        replay_id = str(uuid4())
        body = {'command_id': replay_id, 'revision': same.json()['revision'], 'kind': 'none'}
        first = c.put('/api/projects/' + done['id'] + '/input-profile', json=body)
        wait(c, done['id'], 'ready')
        replay = c.put('/api/projects/' + done['id'] + '/input-profile', json=body)
        assert first.status_code == 202 and replay.status_code == 202 and replay.json()['status'] == 'ready'
        conflict = c.put('/api/projects/' + done['id'] + '/input-profile', json={**body, 'kind': 'normalize'})
        assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'COMMAND_CONFLICT'


def test_normalizing_a_clip_that_is_not_flat_is_refused(settings, normal_video):
    with client(settings, Planner()) as c:
        state = upload(c, normal_video)
        refused = c.put('/api/projects/' + state['id'] + '/input-profile', json={'command_id': str(uuid4()), 'revision': state['revision'], 'kind': 'normalize'})
        assert refused.status_code == 422 and refused.json()['error']['code'] == 'INPUT_PROFILE_NOT_FLAT'
        assert c.get('/api/projects/' + state['id']).json()['status'] == 'ready'


def test_a_camera_lut_is_uploaded_checked_previewed_and_then_applied(tmp_path, settings, flat_video):
    stretch = lambda rgb: (rgb - .15) / .63                                    # noqa: E731
    data = cube_text(stretch, 17, 'Stretch')
    with client(settings, Planner()) as c:
        state = upload(c, flat_video)
        directory = settings.workspace / state['id']
        bad = upload_lut(c, state, b'not a lut')
        assert bad.status_code == 422 and bad.json()['error']['code'] == 'LUT_UNKNOWN_KEYWORD'
        assert not list(directory.glob('input-lut-*'))                           # a refused file is never stored
        good = upload_lut(c, state, data, 'Stretch-709.cube')
        assert good.status_code == 200, good.text
        lut = good.json()
        assert lut['kind'] == '3d' and lut['size'] == 17 and lut['title'] == 'Stretch' and lut['name'] == 'Stretch-709.cube' and len(lut['lut_id']) == 16
        choice = {'kind': 'lut', 'lut_id': lut['lut_id'], 'lut_name': lut['name']}
        shown = c.post('/api/projects/' + state['id'] + '/input-profile/preview', json=choice).json()
        assert shown['before'].startswith('data:image/jpeg;base64,') and shown['after'].startswith('data:image/jpeg;base64,') and shown['before'] != shown['after']
        assert shown['warnings'] == [] and shown['after_levels']['black'] < .06 and shown['after_levels']['white'] > .9
        assert not color_profile.sidecar(directory / 'source.bin').exists()      # a preview changes nothing
        done = apply_profile(c, state, **choice)
        profile = color_profile.read(directory / 'source.bin')
        assert profile.kind == 'lut' and profile.lut_name == 'Stretch-709.cube' and done['source']['profile']['lut_name'] == 'Stretch-709.cube'
        assert not color_levels.measure(directory / 'analysis.mp4', settings, threading.Event()).flat
        assert (directory / profile.lut_file).is_file()
        # A different LUT replaces it, and the old one is removed with it.
        other = upload_lut(c, done, cube_text(lambda rgb: rgb, 5, 'Identity'), 'id.cube').json()
        apply_profile(c, done, kind='lut', lut_id=other['lut_id'], lut_name='id.cube')
        assert [p.name for p in directory.glob('input-lut-*.cube')] == [f"input-lut-{other['lut_id']}.cube"]
        assert color_levels.measure(directory / 'analysis.mp4', settings, threading.Event()).flat      # the identity LUT leaves it flat


def test_a_lut_that_does_not_suit_the_footage_is_flagged_in_the_preview(settings, flat_video):
    with client(settings, Planner()) as c:
        state = upload(c, flat_video)
        for function, words in ((lambda rgb: rgb * .02, 'almost black'), (lambda rgb: .97 + rgb * .03, 'almost white'), (lambda rgb: .4 + rgb * .1, 'still looks flat')):
            lut = upload_lut(c, state, cube_text(function, 5)).json()
            warnings = c.post('/api/projects/' + state['id'] + '/input-profile/preview', json={'kind': 'lut', 'lut_id': lut['lut_id']}).json()['warnings']
            assert len(warnings) == 1 and words in warnings[0], warnings


def test_the_profile_requests_are_validated(settings, flat_video):
    with client(settings, Planner()) as c:
        state = upload(c, flat_video)
        url = '/api/projects/' + state['id'] + '/input-profile'
        base = {'command_id': str(uuid4()), 'revision': state['revision']}
        assert c.put(url, json={**base, 'kind': 'lut'}).status_code == 422                                # a LUT needs a file
        assert c.put(url, json={**base, 'kind': 'normalize', 'lut_id': '0' * 16}).status_code == 422         # and only a LUT has one
        assert c.put(url, json={**base, 'kind': 'lut', 'lut_id': '../../x'}).status_code == 422
        assert c.put(url, json={**base, 'kind': 'bogus'}).status_code == 422
        missing = c.put(url, json={**base, 'kind': 'lut', 'lut_id': '0' * 16})
        assert missing.status_code == 409 and missing.json()['error']['code'] == 'INPUT_LUT_MISSING'
        stale = c.put(url, json={**base, 'revision': state['revision'] + 9, 'kind': 'none'})
        assert stale.status_code == 409 and stale.json()['error']['code'] == 'STALE_PROJECT'
        assert c.get('/api/projects/' + str(uuid4()) + '/input-profile').status_code == 404


def test_the_profile_is_locked_once_the_footage_has_been_analysed(settings, flat_video):
    with client(settings, Planner()) as c:
        state = start(c, upload(c, flat_video), 'Make it cinematic')
        assert state['status'] == 'awaiting_answer' or state['status'] == 'brief_ready'
        info = c.get('/api/projects/' + state['id'] + '/input-profile').json()
        assert 'already been analysed' in info['locked']
        response = c.put('/api/projects/' + state['id'] + '/input-profile', json={'command_id': str(uuid4()), 'revision': state['revision'], 'kind': 'normalize'})
        assert response.status_code == 409 and response.json()['error']['code'] == 'INPUT_PROFILE_LOCKED'
        assert upload_lut(c, state, cube_text(lambda rgb: rgb, 5)).json()['error']['code'] == 'INPUT_PROFILE_LOCKED'
        assert not color_profile.sidecar(settings.workspace / state['id'] / 'source.bin').exists()


@needs_tonemap
def test_hdr_footage_is_told_a_profile_does_not_apply(tmp_path, settings):
    tags = T(colorspace='bt2020nc', color_primaries='bt2020', color_trc='arib-std-b67', range='tv')
    clip = make_scene(tmp_path / 'hlg.mp4', (192, 108), 'bt2020', 'tv', 'yuv420p10le', 'libx265', tags)
    with client(settings, Planner()) as c:
        state = upload(c, clip)
        info = c.get('/api/projects/' + state['id'] + '/input-profile').json()
        assert info['applicable'] is False and 'HDR' in info['applicable_reason'] and info['suggest'] is None
        response = c.put('/api/projects/' + state['id'] + '/input-profile', json={'command_id': str(uuid4()), 'revision': state['revision'], 'kind': 'none'})
        assert response.status_code == 422 and response.json()['error']['code'] == 'INPUT_PROFILE_NOT_APPLICABLE'
        assert upload_lut(c, state, cube_text(lambda rgb: rgb, 5)).json()['error']['code'] == 'INPUT_PROFILE_NOT_APPLICABLE'


def test_a_failed_change_leaves_the_project_exactly_as_it_was(settings, flat_video, monkeypatch):
    from takeone.color import service as color_service
    with client(settings, Planner()) as c:
        state = apply_profile(c, upload(c, flat_video), kind='normalize')
        directory = settings.workspace / state['id']
        before = color_profile.read(directory / 'source.bin')
        proxy = (directory / 'analysis.mp4').read_bytes()

        def broken(*args, **kwargs):
            raise Problem('MEDIA_TOOL_FAILED', 'The media tool could not process this video.', 'Try again.', 422)
        monkeypatch.setattr(color_service, 'make_previews', broken)
        response = c.put('/api/projects/' + state['id'] + '/input-profile', json={'command_id': str(uuid4()), 'revision': state['revision'], 'kind': 'none'})
        assert response.status_code == 202
        failed = wait(c, state['id'], 'ready')
        assert color_profile.read(directory / 'source.bin') == before and (directory / 'analysis.mp4').read_bytes() == proxy       # nothing half-changed
        assert failed['source']['profile_error']['code'] == 'MEDIA_TOOL_FAILED' and failed['source']['profile']['kind'] == 'normalize' and failed['error'] is None
        assert any(e['kind'] == 'error' and e['title'] == 'Input profile not applied' for e in failed['events'])
        monkeypatch.undo()
        fixed = apply_profile(c, failed, kind='none')                                      # the next attempt works, and clears the error
        assert fixed['source']['profile_error'] is None and fixed['source']['profile'] is None


def test_proxies_that_do_not_match_the_saved_profile_are_rebuilt_before_anything_reads_them(settings, flat_video):
    """An interrupted change leaves the saved profile ahead of the proxies; the next job repairs it instead of reading wrong pictures."""
    with client(settings, Planner()) as c:
        state = upload(c, flat_video)
        directory = settings.workspace / state['id']
        service = c.app.state.service
        assert color_levels.measure(directory / 'analysis.mp4', settings, threading.Event()).flat
        color_profile.write(directory / 'source.bin', color_profile.normalize(.15, .78))          # as if the process stopped right after saving it
        service.ensure_current_source(state['id'], threading.Event())
        repaired = c.get('/api/projects/' + state['id']).json()
        assert repaired['preview']['conform_sig'] == color_profile.read(directory / 'source.bin').signature and repaired['source']['profile']['kind'] == 'normalize'
        assert not color_levels.measure(directory / 'analysis.mp4', settings, threading.Event()).flat
        revision = repaired['revision']
        service.ensure_current_source(state['id'], threading.Event())                               # and then it is a no-op
        assert c.get('/api/projects/' + state['id']).json()['revision'] == revision


def test_a_session_from_before_levels_were_recorded_still_gets_its_suggestion(settings, flat_video):
    with client(settings, Planner()) as c:
        state = upload(c, flat_video)
        service = c.app.state.service
        service.mutate(state['id'], lambda s: setattr(s.source, 'levels', None))
        revision = c.get('/api/projects/' + state['id']).json()['revision']
        info = c.get('/api/projects/' + state['id'] + '/input-profile').json()
        assert info['suggest'] == 'normalize' and info['levels']['flat']
        assert c.get('/api/projects/' + state['id']).json()['revision'] == revision               # looking never changes the project
        done = apply_profile(c, c.get('/api/projects/' + state['id']).json(), kind='normalize')
        assert done['source']['profile']['kind'] == 'normalize'


# ---------- every reader of the original agrees ----------

def test_every_reader_of_the_original_applies_the_profile(settings, flat_video):
    """Preview, evidence frames, the matte reader and the tracking proxy all use the one chain, so none can show a different picture."""
    with client(settings, Planner()) as c:
        state = apply_profile(c, upload(c, flat_video), kind='normalize')
        directory = settings.workspace / state['id']
        original = directory / 'source.bin'
        profile = color_profile.read(original)
        reference = np.clip((ramp_scene()[..., 1] / 255 * (.78 - .15) + .15 - profile.black) / (profile.white - profile.black), 0, 1) * 255
        rows, columns = slice(4, 20), slice(150, 180)                                             # the grey ramp above the block, where flat and normalized differ most
        expected = reference[rows, columns].mean()

        def grey(frame):
            import cv2
            picture = cv2.resize(np.asarray(frame, np.float32), (192, 108), interpolation=cv2.INTER_AREA)
            return picture[rows, columns].mean()

        manifest = evidence.frame_manifest(original, settings, threading.Event())
        extracted = evidence.extract(original, manifest, [3], settings, threading.Event(), width=192)[3]
        assert abs(grey(extracted) - expected) <= 4, 'evidence frames'
        reader, _ = source_color.open_pair(original, directory / 'preview.webm', settings, size=(192, 108))
        ok, frame = reader.read()
        reader.release()
        assert ok and abs(grey(frame) - expected) <= 4, 'matte reader'
        proxy = graded_frame(directory / 'analysis.mp4', 'scale=192:108').reshape(108, 192, 3)
        assert abs(grey(proxy) - expected) <= 4, 'analysis proxy'
        flat = graded_frame(original, 'scale=192:108').reshape(108, 192, 3)
        assert abs(grey(flat) - expected) > 20, 'the unprofiled original really is different, so this test can tell'


@pytest.mark.parametrize('route', ['normalize', 'lut'])
def test_a_graded_flat_clip_equals_the_same_picture_recorded_with_normal_contrast(tmp_path, settings, route):
    """The end-to-end truth: render a flat clip under a profile and the same footage already corrected at the source; same picture."""
    flat_image = flatten(ramp_scene())
    stretch = lambda rgb: (rgb - .15) / .63                                    # noqa: E731
    flat = make_clip(tmp_path / 'flat.mp4', flat_image)
    outputs = {}
    for name in ('profiled', 'native'):
        run = Settings(workspace=settings.workspace / name, max_duration=120)
        with TestClient(create_app(run, Planner()), base_url='http://127.0.0.1') as c:
            if name == 'profiled':
                state = upload(c, flat)
                if route == 'normalize':
                    state = apply_profile(c, state, kind='normalize')
                    profile = color_profile.read(run.workspace / state['id'] / 'source.bin')
                    corrected = np.clip((flat_image / 255 - profile.black) / (profile.white - profile.black), 0, 1) * 255
                else:
                    lut = upload_lut(c, state, cube_text(stretch, 33)).json()
                    state = apply_profile(c, state, kind='lut', lut_id=lut['lut_id'], lut_name='s.cube')
                    corrected = np.clip(stretch(flat_image / 255), 0, 1) * 255
                native_clip = make_clip(tmp_path / f'native-{route}.mp4', corrected)
            else:
                state = upload(c, native_clip)
            result = generate(c, start(c, state, 'explicit increase overall contrast'))
            assert result['status'] == 'render_ready', (name, result.get('error'))
            outputs[name] = rendered_frame(run, result)
    height, width = outputs['native'].shape[:2]
    inside = np.ones((height, width), bool)
    inside[round(height * .22) - 5:round(height * .78) + 5, round(width * .31) - 5:round(width * .69) + 5] = False
    inside[round(height * .22) + 5:round(height * .78) - 5, round(width * .31) + 5:round(width * .69) - 5] = True
    difference = np.abs(outputs['profiled'] - outputs['native'])[inside]
    assert difference.mean() <= 1.6 and np.percentile(difference, 99.9) <= 7, f'{route}: mean {difference.mean():.2f}, p99.9 {np.percentile(difference, 99.9):.0f}'
