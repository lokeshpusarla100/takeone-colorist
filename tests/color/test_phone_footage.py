"""Phone footage through the whole pipeline: upload, previews, analysis gate, grading, matte QC, and old sessions."""
import json
import subprocess
from uuid import uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient

from takeone import color_domain as cd, rendering, source_color
from takeone.app import create_app
from takeone.config import Settings
from takeone.contracts import GradeParameters
from tests.test_backend import FakeMuse, client, settings, start, upload, wait  # noqa: F401
from tests.test_color_domain import (T, XYZ, encode, ffmpeg, needs_tonemap, needs_zscale, probe_stream)
from tests.test_local_grading import Masks, Planner, generate, pixels

pytestmark = [needs_zscale, pytest.mark.filterwarnings('ignore')]
GREY, RED = (120, 120, 120), (190, 35, 25)


def scene_image(width=192, height=108, red=RED):
    image = np.full((height, width, 3), GREY, np.uint8)
    y0, y1, x0, x1 = round(height * 24 / 108), round(height * 84 / 108), round(width * 60 / 192), round(width * 132 / 192)
    image[y0:y1, x0:x1] = red
    return image


def make_scene(path, size, matrix, rng, pix, codec, tags, strip=False, red=RED):
    """A 12-frame, 12 fps clip of the standard test scene (grey with a red block) recorded in a phone-style domain."""
    width, height = size
    vf = f'scale=out_color_matrix={matrix}:out_range={rng}' + (f',setparams={tags}' if tags else '')
    lossless = ['-crf', '0'] if codec == 'libx264' else ['-x265-params', 'lossless=1:log-level=error']
    frames = np.repeat(scene_image(width, height, red)[None], 12, 0).tobytes()
    ffmpeg(['-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{width}x{height}', '-r', '12', '-i', '-', '-vf', vf, '-c:v', codec, '-pix_fmt', pix, *lossless, str(path)], frames)
    if strip:
        from tests.test_color_domain import strip_color_tags
        strip_color_tags(path, codec)
    return path


def to_709(rgb, primaries):
    """The expected Rec.709 value of a color recorded in `primaries` (display-referred, as the conversion does it)."""
    if primaries == 'bt709':
        return np.array(rgb, float)
    linear = (np.array(rgb, float) / 255) ** 2.4 @ (np.linalg.inv(XYZ['bt709']) @ np.array(XYZ[primaries])).T
    return 255 * np.clip(linear, 0, 1) ** (1 / 2.4)


ANDROID = ('android 10-bit BT.601 full range', (192, 108), 'bt601', 'pc', 'yuv420p10le', 'libx265',
           T(colorspace='smpte170m', color_primaries='bt470bg', color_trc='smpte170m', range='pc'), False, 'bt470bg')
HD_UNTAGGED = ('untagged 8-bit HD', (1280, 720), 'bt709', 'tv', 'yuv420p', 'libx264', '', True, 'bt709')
FULL_RANGE = ('8-bit full-range Rec.709', (192, 108), 'bt709', 'pc', 'yuvj420p', 'libx264',
              T(colorspace='bt709', color_primaries='bt709', color_trc='bt709', range='pc'), False, 'bt709')
BT2020_SDR = ('BT.2020 SDR 10-bit', (192, 108), 'bt2020', 'tv', 'yuv420p10le', 'libx265',
              T(colorspace='bt2020nc', color_primaries='bt2020', color_trc='bt2020-10', range='tv'), False, 'bt2020')
NATIVE = ('native Rec.709', (192, 108), 'bt709', 'tv', 'yuv420p', 'libx264', T(colorspace='bt709', color_primaries='bt709', color_trc='bt709', range='tv'), False, 'bt709')
CONVERTED = [ANDROID, HD_UNTAGGED, FULL_RANGE, BT2020_SDR]


def rendered_frame(settings, result):
    candidate = result['candidates'][-1]
    return pixels(settings.workspace / result['id'] / 'candidates' / candidate['id'] / 'grade.mp4')  # BGR


def region(image, size, y0, y1, x0, x1):
    height, width = size[1], size[0]
    return image[round(height * y0 / 108):round(height * y1 / 108), round(width * x0 / 192):round(width * x1 / 192)].reshape(-1, 3).mean(axis=0)[::-1]


@pytest.mark.parametrize('case', CONVERTED, ids=[c[0] for c in CONVERTED])
def test_phone_footage_is_prepared_in_rec709_and_its_conversion_is_recorded(tmp_path, settings, case):
    label, size, matrix, rng, pix, codec, tags, strip, primaries = case
    clip = make_scene(tmp_path / 'phone.mp4', size, matrix, rng, pix, codec, tags, strip)
    with client(settings, FakeMuse()) as c:
        state = upload(c, clip)
        domain = state['source']['domain']
        assert domain['kind'] == 'convert' and state['preview']['conformed'] is True, label
        assert domain['steps'] and state['source']['technical_notes'] == domain['steps']
        proxy = settings.workspace / state['id'] / 'analysis.mp4'
        stream = probe_stream(proxy)
        assert (stream['pix_fmt'], stream['color_space'], stream['color_transfer'], stream['color_primaries'], stream['color_range']) == ('yuv420p', 'bt709', 'bt709', 'bt709', 'tv')
        shown = pixels(proxy, 3)[:, :, ::-1]
        h, w = shown.shape[:2]
        grey = shown[h // 10:h // 5, w // 20:w // 6].reshape(-1, 3).mean(axis=0)
        red = shown[h // 2 - 4:h // 2 + 4, w // 2 - 4:w // 2 + 4].reshape(-1, 3).mean(axis=0)
        assert np.abs(grey - GREY).max() <= 4, (label, grey)                       # neutrals are untouched by any gamut conversion
        assert np.abs(red - to_709(RED, primaries)).max() <= 6, (label, red)      # colors are the recorded colors in Rec.709


@pytest.mark.parametrize('case', [ANDROID, HD_UNTAGGED, FULL_RANGE], ids=['android', 'untagged HD', 'full range'])
def test_a_global_grade_of_phone_footage_matches_the_same_footage_recorded_natively(tmp_path, settings, case):
    label, size, matrix, rng, pix, codec, tags, strip, primaries = case
    phone = make_scene(tmp_path / 'phone.mp4', size, matrix, rng, pix, codec, tags, strip)
    # The reference is the same picture recorded natively: the colors the phone clip must come out as, in Rec.709 primaries.
    native = make_scene(tmp_path / 'native.mp4', size, 'bt709', 'tv', 'yuv420p', 'libx264', NATIVE[6], red=tuple(int(v) for v in np.round(to_709(RED, primaries))))
    outputs = {}
    for name, clip in (('phone', phone), ('native', native)):
        run = Settings(workspace=settings.workspace / name, max_duration=120)
        with TestClient(create_app(run, Planner()), base_url='http://127.0.0.1') as c:
            result = generate(c, start(c, upload(c, clip), 'explicit increase overall contrast'))
            assert result['status'] == 'render_ready', (label, result.get('error'))
            candidate = result['candidates'][-1]
            outputs[name] = rendered_frame(run, result)
            video = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=pix_fmt,color_space,color_primaries,color_transfer,color_range,width,height',
                                               '-of', 'json', str(run.workspace / result['id'] / 'candidates' / candidate['id'] / 'grade.mp4')], capture_output=True, check=True).stdout)['streams'][0]
            assert (video['pix_fmt'], video['color_space'], video['color_transfer'], video['color_primaries'], video['color_range']) == ('yuv420p', 'bt709', 'bt709', 'bt709', 'tv')
    # Judged on the interior of each region: along the hard edge between the grey and the red block the two pipelines
    # resample chroma differently (it is 1% of the pixels and not a color error); in the regions themselves the pictures must agree.
    height, width = outputs['phone'].shape[:2]
    interior = np.ones((height, width), bool)
    y0, y1, x0, x1 = round(height * 24 / 108), round(height * 84 / 108), round(width * 60 / 192), round(width * 132 / 192)
    interior[max(0, y0 - 5):y1 + 5, max(0, x0 - 5):x1 + 5] = False
    interior[y0 + 5:y1 - 5, x0 + 5:x1 - 5] = True
    difference = np.abs(outputs['phone'] - outputs['native'])[interior]
    # The two clips are independently quantized 8-bit encodes (chroma alone is only exact to about +-2 levels, and each is then
    # re-encoded), so single pixels differ by a few levels. A real conversion error (wrong matrix, range or primaries) moves
    # whole regions by 10 to 30 levels, so the mean and the 99.9th percentile still catch it.
    assert difference.mean() <= 1.5 and np.percentile(difference, 99.9) <= 6, f'{label}: mean {difference.mean():.2f} p99.9 {np.percentile(difference, 99.9):.0f}'   # the same picture, whatever the phone recorded


def test_a_localized_grade_of_converted_footage_passes_the_matte_checks(tmp_path, settings):
    """The protected-subject check compares the original with the output pixel by pixel. If the two were decoded
    differently (OpenCV ignores tags) a converted source would fail it; both sides must go through the same conversion."""
    label, size, matrix, rng, pix, codec, tags, strip, primaries = ANDROID
    phone = make_scene(tmp_path / 'phone.mp4', size, matrix, rng, pix, codec, tags, strip)
    with TestClient(create_app(settings, Planner(local=True), Masks()), base_url='http://127.0.0.1') as c:
        result = generate(c, start(c, upload(c, phone), 'explicit dark cool background, preserve red subject'))
        assert result['status'] == 'render_ready', result.get('error')
        before, after = pixels(phone), rendered_frame(settings, result)
        subject = (slice(35, 72), slice(75, 118))
        assert np.abs(after[subject] - before[subject]).mean() < 25                # not the unconverted original: judged on the picture
        assert after[10:20, 10:40].mean() < 110                                    # the background was graded darker
        assert result['candidates'][-1]['validation']['subject_protection']['checked_subject_frames'] == 12


def test_native_footage_is_exactly_what_it_was(tmp_path, settings):
    clip = make_scene(tmp_path / 'native.mp4', NATIVE[1], 'bt709', 'tv', 'yuv420p', 'libx264', NATIVE[6])
    with client(settings, FakeMuse()) as c:
        state = upload(c, clip)
        assert state['source']['domain']['kind'] == 'native' and state['source']['technical_notes'] == [] and state['preview']['conformed'] is True
        directory = settings.workspace / state['id']
        assert cd.conform_filters(cd.SourceDomain.from_dict(state['source']['domain'])) == ''
        assert source_color.chain_for_file(directory / 'source.bin', settings) == ''
    parameters = GradeParameters(contrast=1.1, saturation=1., gamma=1.)
    legacy = rendering.filter_graph(parameters)
    assert legacy == rendering.filter_graph(parameters, conform='') and 'zscale' not in legacy and legacy.startswith('[0:v]format=gbrp16le')
    converted = rendering.filter_graph(parameters, conform='zscale=x,format=yuv444p16le')
    assert converted.startswith('[0:v]zscale=x,format=yuv444p16le,format=gbrp16le') and converted.endswith(legacy.split('format=gbrp16le', 1)[1])


@needs_tonemap
def test_hdr_phone_footage_is_graded_as_sdr_and_says_so(tmp_path, settings):
    tags = T(colorspace='bt2020nc', color_primaries='bt2020', color_trc='arib-std-b67', range='tv')
    clip = make_scene(tmp_path / 'iphone-hlg.mp4', (192, 108), 'bt2020', 'tv', 'yuv420p10le', 'libx265', tags)
    with TestClient(create_app(settings, Planner()), base_url='http://127.0.0.1') as c:
        state = upload(c, clip)
        domain = state['source']['domain']
        assert domain['kind'] == 'tonemap' and domain['hdr'] == 'HLG' and any('approximation' in step for step in domain['steps'])
        assert any('delivered as 8-bit Rec.709 SDR' in note for note in state['source']['technical_notes'])
        result = generate(c, start(c, state, 'explicit increase overall contrast'))
        assert result['status'] == 'render_ready', result.get('error')
        assert any('approximation' in line for line in result['rubric_history'] and [] or [n for n in result['source']['technical_notes']])
        frame = rendered_frame(settings, result)
        assert 20 < frame.mean() < 235 and frame[:, :, ::-1][..., 0].mean() > 0     # a real SDR picture, not black, not blown out


@pytest.mark.parametrize('tags,reason', [
    (T(colorspace='bt709', color_primaries='bt709', color_trc='log100', range='tv'), 'LOG_OR_LINEAR_TRANSFER'),
    (T(colorspace='bt2020nc', color_primaries='bt2020', color_trc='unknown', range='tv'), 'AMBIGUOUS_WIDE_GAMUT'),
])
def test_a_source_that_cannot_be_converted_is_refused_with_its_reason_before_any_billed_call(tmp_path, settings, tags, reason):
    pix = 'yuv420p10le' if 'bt2020' in tags else 'yuv420p'
    clip = make_scene(tmp_path / 'odd.mp4', (192, 108), 'bt2020' if 'bt2020' in tags else 'bt709', 'tv', pix, 'libx265' if 'bt2020' in tags else 'libx264', tags)
    muse = FakeMuse()
    with client(settings, muse) as c:
        state = upload(c, clip)                                                   # the file is accepted; the preview shows what it looks like
        assert state['source']['domain']['kind'] == 'unsupported' and state['source']['domain']['reason'] == reason
        assert state['source']['technical_notes'] and state['preview']['conformed'] is True
        refused = c.post(f"/api/projects/{state['id']}/analyze", json={'command_id': str(uuid4()), 'revision': state['revision'], 'request': 'make it moody'})
        body = refused.json()['error']
        assert refused.status_code == 422 and body['code'] == 'GRADE_DOMAIN_UNSUPPORTED' and body['details']['reason'] == reason
        assert body['message'] and body['action'] and muse.analysis_calls == 0     # nothing was sent to Meta


def test_a_session_saved_before_color_domains_is_upgraded_and_then_graded(tmp_path, settings):
    """The failing recording: an older session whose source was never converted. Recording its domain and rebuilding
    its preview/proxy happens once, before the job that needs them, and the grade then works."""
    label, size, matrix, rng, pix, codec, tags, strip, primaries = ANDROID
    clip = make_scene(tmp_path / 'android.mp4', size, matrix, rng, pix, codec, tags, strip)
    with TestClient(create_app(settings, Planner()), base_url='http://127.0.0.1') as c:
        state = start(c, upload(c, clip), 'explicit increase overall contrast')
        project = state['id']
    path = settings.workspace / project / 'state.json'
    saved = json.loads(path.read_text())
    saved['source'].pop('domain'), saved['preview'].pop('conformed')              # exactly what an older session looks like
    saved['source']['technical_notes'] = ['Preview is 8-bit. The original precision is retained; final grading is not enabled for this pixel format.']
    path.write_text(json.dumps(saved))
    (settings.workspace / project / 'source.bin.frames-256x144.npz').write_bytes(b'stale pixels decoded in the wrong domain')
    stale_proxy = (settings.workspace / project / 'analysis.mp4').stat().st_mtime_ns
    with TestClient(create_app(settings, Planner()), base_url='http://127.0.0.1') as c:
        before = c.get(f'/api/projects/{project}').json()
        assert 'domain' not in before['source'] or before['source']['domain'] is None
        result = generate(c, before)
        assert result['status'] == 'render_ready', result.get('error')
        assert result['source']['domain']['kind'] == 'convert' and result['preview']['conformed'] is True
        assert any(e['title'] == 'Converting the source to Rec.709' for e in result['events'])
        assert not (settings.workspace / project / 'source.bin.frames-256x144.npz').exists()                    # the stale pixel cache is gone
        assert (settings.workspace / project / 'analysis.mp4').stat().st_mtime_ns != stale_proxy                 # the proxy was rebuilt
        stream = probe_stream(settings.workspace / project / 'analysis.mp4')
        assert (stream['color_space'], stream['color_range']) == ('bt709', 'tv')
        again = generate(c, result)                                                                              # and it is not redone
        assert again['status'] == 'render_ready' and sum(e['title'] == 'Converting the source to Rec.709' for e in again['events']) == 1


def test_a_native_session_saved_before_domains_is_simply_recorded_not_rebuilt(tmp_path, settings):
    clip = make_scene(tmp_path / 'native.mp4', NATIVE[1], 'bt709', 'tv', 'yuv420p', 'libx264', NATIVE[6])
    with TestClient(create_app(settings, Planner()), base_url='http://127.0.0.1') as c:
        project = start(c, upload(c, clip), 'explicit increase overall contrast')['id']
    path = settings.workspace / project / 'state.json'
    saved = json.loads(path.read_text())
    saved['source'].pop('domain'), saved['preview'].pop('conformed')
    path.write_text(json.dumps(saved))
    proxy = (settings.workspace / project / 'analysis.mp4').stat().st_mtime_ns
    with TestClient(create_app(settings, Planner()), base_url='http://127.0.0.1') as c:
        result = generate(c, c.get(f'/api/projects/{project}').json())
        assert result['status'] == 'render_ready' and result['source']['domain']['kind'] == 'native'
        assert not any(e['title'] == 'Converting the source to Rec.709' for e in result['events'])
        assert (settings.workspace / project / 'analysis.mp4').stat().st_mtime_ns == proxy                       # nothing was rebuilt


def test_the_matte_readers_agree_on_converted_footage_and_stay_opencv_for_native(tmp_path, settings):
    import cv2
    label, size, matrix, rng, pix, codec, tags, strip, primaries = ANDROID
    phone = make_scene(tmp_path / 'phone.mp4', size, matrix, rng, pix, codec, tags, strip)
    native = make_scene(tmp_path / 'native.mp4', size, 'bt709', 'tv', 'yuv420p', 'libx264', NATIVE[6])
    a, b = source_color.open_pair(native, native, settings)
    assert isinstance(a, cv2.VideoCapture) and isinstance(b, cv2.VideoCapture)
    a.release(), b.release()
    a, b = source_color.open_pair(phone, native, settings)
    assert isinstance(a, source_color.FfmpegCapture) and isinstance(b, source_color.FfmpegCapture)
    ok_a, frame_a = a.read()
    ok_b, frame_b = b.read()
    assert ok_a and ok_b and frame_a.shape == frame_b.shape == (108, 192, 3)
    assert np.abs(frame_a.astype(float) - frame_b.astype(float)).mean() < 2.5      # the converted original and the native original are the same picture
    count = 1
    while a.read()[0]:
        count += 1
    assert count == 12 and not a.read()[0]
    a.release(), b.release()
    assert not a.isOpened()


def test_health_says_whether_this_machine_can_convert_phone_footage(settings):
    with client(settings, FakeMuse()) as c:
        info = c.get('/api/health').json()['source_conversion']
    assert info['zscale'] is True and set(info) == {'zscale', 'tonemap', 'hdr_to_sdr'} and info['hdr_to_sdr'] is True


def captured_preview_graph(monkeypatch, directory, source):
    """The filter graph make_previews hands to ffmpeg, without running the preview encode."""
    from takeone import media
    seen = {}
    def fake(args, cancel, *a, **k):
        if '-filter_complex' in args:
            seen['graph'] = args[args.index('-filter_complex') + 1]
            raise RuntimeError('stop after the graph is built')
        return media.command.__wrapped__(args, cancel, *a, **k) if hasattr(media.command, '__wrapped__') else b''
    monkeypatch.setattr(media, 'command', fake)
    monkeypatch.setattr(media, 'probe', lambda *a, **k: {'streams': [{'codec_type': 'video', 'time_base': '1/12288'}], 'format': {}})
    with pytest.raises(RuntimeError):
        media.make_previews(directory, source, 'p', Settings(directory), threading_event())
    return seen['graph']


def threading_event():
    import threading
    return threading.Event()


def test_previews_shrink_first_and_convert_second_and_native_graphs_are_untouched(tmp_path, monkeypatch):
    from takeone.contracts import Source
    base = dict(name='x', bytes=1, sha256='0', width=3840, height=2160, duration=1., video_codec='hevc', audio_codec=None, pixel_format='yuv420p',
                color_transfer=None, color_primaries=None, color_space=None, start_time=0., rotation=0., technical_notes=[])
    legacy = ("[0:v:0]split=2[displayin][analysisin];[displayin]scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1[display];"
              "[analysisin]scale=w='min(640,iw)':h='min(640,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1,setpts=PTS-STARTPTS[analysis]")
    native = cd.resolve({'pix_fmt': 'yuv420p', 'color_space': 'bt709', 'color_primaries': 'bt709', 'color_transfer': 'bt709', 'color_range': 'tv'}, width=3840, height=2160)
    assert captured_preview_graph(monkeypatch, tmp_path, Source(**base, domain=native.to_dict())) == legacy              # byte-identical
    assert captured_preview_graph(monkeypatch, tmp_path, Source(**base, domain=None)) == legacy                          # older records too
    android = cd.resolve({'pix_fmt': 'yuv420p10le', 'color_space': 'smpte170m', 'color_primaries': 'bt470bg', 'color_transfer': 'smpte170m', 'color_range': 'pc'}, width=3840, height=2160)
    graph = captured_preview_graph(monkeypatch, tmp_path, Source(**base, domain=android.to_dict()))
    display, analysis = graph.split(';')[1], graph.split(';')[2]
    for branch in (display, analysis):
        assert branch.index('scale=w=') < branch.index('zscale=') < branch.index('setparams=colorspace=bt709')           # small first, then converted, then tagged
    assert graph.endswith('[analysis]') and 'format=yuv444p16le' in analysis


def test_the_matte_reader_can_work_at_the_size_the_check_uses(tmp_path, settings):
    label, size, matrix, rng, pix, codec, tags, strip, primaries = ANDROID
    phone = make_scene(tmp_path / 'phone.mp4', size, matrix, rng, pix, codec, tags, strip)
    native = make_scene(tmp_path / 'native.mp4', size, 'bt709', 'tv', 'yuv420p', 'libx264', NATIVE[6])
    a, b = source_color.open_pair(phone, native, settings, size=(96, 54))
    ok_a, frame_a = a.read()
    ok_b, frame_b = b.read()
    assert ok_a and ok_b and frame_a.shape == frame_b.shape == (54, 96, 3)
    a.release(), b.release()
