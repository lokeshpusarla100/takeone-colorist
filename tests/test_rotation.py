"""Rotated (portrait phone) sources: stored sideways with display-matrix rotation.

FFmpeg and OpenCV both auto-rotate, so analysis, SAM, mattes, rendering and output QC all
work in the upright display geometry. These tests pin that every geometry the pipeline reads
is that display geometry, and that localized grading lands on the right pixels.
"""
import base64
import subprocess
import threading

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from takeone import evidence, local_grading, sam
from takeone.app import create_app
from takeone.config import Settings
from takeone.media import capabilities, display_size
from tests.test_backend import settings, start, upload  # noqa: F401
from tests.test_local_grading import Planner, generate, pixels
from tests.test_sam import parsed

W, H = 108, 192                      # upright (display) portrait geometry
SUBJECT = (24, 60, 83, 131)          # x1, y1, x2, y2 in display space


def upright_frame():
    frame = np.full((H, W, 3), 120, np.uint8)
    x1, y1, x2, y2 = SUBJECT
    frame[y1:y2 + 1, x1:x2 + 1] = [190, 35, 25]  # RGB red subject
    return frame


def decode_first(path):
    info = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-select_streams', 'v', '-of', 'json', str(path)],
                          capture_output=True, text=True, check=True)
    import json
    w, h = display_size(json.loads(info.stdout)['streams'][0])
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3)


def rotated_clip(directory, rotation):
    """Store the upright frame sideways (or upside down) and tag it so decoders show it upright."""
    upright = upright_frame()
    for coded, tag in ([(np.rot90(upright, 1), -rotation), (np.rot90(upright, -1), -rotation),
                        (np.rot90(upright, 1), rotation), (np.rot90(upright, -1), rotation)] if abs(rotation) == 90
                       else [(np.rot90(upright, 2), rotation)]):
        coded = np.ascontiguousarray(coded)
        plain, tagged = directory / f'coded-{tag}.mp4', directory / f'rotated-{rotation}.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', f'{coded.shape[1]}x{coded.shape[0]}',
                        '-framerate', '12', '-i', '-', '-vf', 'scale=in_range=pc:out_range=tv,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709',
                        '-c:v', 'libx264', '-crf', '14', '-pix_fmt', 'yuv420p', '-color_range', 'tv', '-colorspace', 'bt709',
                        '-color_primaries', 'bt709', '-color_trc', 'bt709', str(plain)], input=coded.tobytes() * 12, check=True)
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-display_rotation:v:0', str(tag), '-i', str(plain), '-c', 'copy', str(tagged)], check=True)
        decoded = decode_first(tagged)
        if decoded.shape == upright.shape and np.abs(decoded.astype(int) - upright).mean() < 6:
            return tagged
    raise AssertionError('Could not build a rotated fixture that decodes upright')


@pytest.fixture(scope='module', params=[-90, 90, 180])
def rotated(request, tmp_path_factory):
    return request.param, rotated_clip(tmp_path_factory.mktemp(f'rot{request.param}'), request.param)


class UprightMasks:
    """Fake SAM: a mask on the subject in the proxy's (upright) coordinate space."""

    def __init__(self):
        self.calls = 0
        self.geometry = []

    def available(self):
        return True

    def segment(self, path, concept, remote, remember, cancel, progress, raw_path, on_usage, on_request):
        self.calls += 1
        on_request()
        remember('fixture-upload')
        from meta_sam_parser._mask_codec import _encode_raster
        info = sam.frame_info(path, Settings(path.parent), cancel)
        self.geometry.append((info['width'], info['height']))
        sx, sy = info['width'] / W, info['height'] / H
        x1, y1, x2, y2 = (round(SUBJECT[0] * sx), round(SUBJECT[1] * sy), round(SUBJECT[2] * sx), round(SUBJECT[3] * sy))
        w, h = x2 - x1 + 1, y2 - y1 + 1
        payload = _encode_raster(bytes([1]) * w * h, w, h)
        record = f"<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={info['width']};h={info['height']}|><|mask;x=0;y=0;data={h},{w},{payload}|>"
        result = parsed([f'<{i}f>0' + record for i in range(len(info['pts']))])
        raw_path.write_text(result.raw_output)
        progress(result)
        on_usage({'video_frames_processed': len(info['pts'])})
        return result


def test_source_is_described_in_display_geometry_and_localized_grading_is_offered(settings, rotated):
    rotation, path = rotated
    with TestClient(create_app(settings, Planner(), UprightMasks()), base_url='http://127.0.0.1') as client:
        state = upload(client, path)
        source = client.app.state.service.store.get(state['id']).source
        assert (source.width, source.height) == (W, H) and abs(source.rotation) == abs(rotation)
        assert 'regional_grade' in capabilities(source)['operations']
        directory = settings.workspace / state['id']
        for name in ['preview.webm', 'analysis.mp4']:  # browser preview and SAM/analysis proxy are upright
            frame = decode_first(directory / name)
            assert frame.shape[0] > frame.shape[1], name


def test_every_probe_reports_decoded_geometry(settings, rotated):
    rotation, path = rotated
    cancel = threading.Event()
    assert (sam.frame_info(path, settings, cancel)['width'], sam.frame_info(path, settings, cancel)['height']) == (W, H)
    manifest = evidence.frame_manifest(path, settings, cancel)
    assert (manifest['width'], manifest['height']) == (W, H)
    frames = evidence.extract(path, manifest, [0], settings, cancel, width=256)
    assert np.abs(frames[0].astype(int) - upright_frame()).mean() < 6  # not squashed or sideways
    capture = cv2.VideoCapture(str(path))  # output QC reads the original through OpenCV: same orientation
    ok, frame = capture.read()
    capture.release()
    assert ok and np.abs(frame[..., ::-1].astype(int) - upright_frame()).mean() < 6


def test_frame_mapping_accepts_quarter_turns(settings, rotated):
    rotation, path = rotated
    with TestClient(create_app(settings, Planner(), UprightMasks()), base_url='http://127.0.0.1') as client:
        state = upload(client, path)
        service = client.app.state.service
        timeline = local_grading.timeline(settings.workspace / state['id'], service.store.get(state['id']).source, settings, threading.Event())
        assert timeline['frame_map']['frame_count'] == 12
        assert (timeline['frame_map']['source']['width'], timeline['frame_map']['source']['height']) == (W, H)


def test_localized_grade_lands_on_the_right_pixels_of_a_rotated_source(settings, rotated):
    rotation, path = rotated
    masks = UprightMasks()
    planner = Planner(local=True)
    with TestClient(create_app(settings, planner, masks), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, path), 'explicit dark cool background, keep red subject'))
        assert result['status'] == 'render_ready', result.get('error')
        candidate = result['candidates'][-1]
        assert candidate['validation']['mode'] == 'localized' and masks.calls == 1
        assert masks.geometry[0][1] > masks.geometry[0][0]  # SAM saw the upright portrait proxy
        output = settings.workspace / result['id'] / 'candidates' / candidate['id'] / 'grade.mp4'
        assert (candidate['width'], candidate['height']) == (W, H)
        after = pixels(output)[..., ::-1]  # BGR -> RGB, upright
        before = pixels(path)[..., ::-1]   # the decoded (auto-rotated) source, so only grading differs
        assert before.shape == after.shape == (H, W, 3)
        x1, y1, x2, y2 = SUBJECT
        subject = (slice(y1 + 6, y2 - 6), slice(x1 + 6, x2 - 6))
        background = (slice(10, 50), slice(10, 98))
        assert np.abs(after[subject] - before[subject]).mean() < 5   # protected subject is where SAM said
        assert after[background].mean() < before[background].mean() - 10  # background graded
        assert candidate['validation']['subject_protection']['checked_subject_frames'] == 12


def test_planner_sees_upright_unsquashed_samples(settings, rotated):
    rotation, path = rotated
    seen = []

    class Recording(Planner):
        def grade(self, brief, analysis, source, stats, images, technical_context=None):
            seen.extend(images)
            return super().grade(brief, analysis, source, stats, images, technical_context)

    with TestClient(create_app(settings, Recording(), UprightMasks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, path), 'explicit gentle contrast'))
        assert result['status'] == 'render_ready', result.get('error')
    image = cv2.imdecode(np.frombuffer(base64.b64decode(seen[0]['url'].split(',')[1]), np.uint8), cv2.IMREAD_COLOR)
    assert abs(image.shape[1] / image.shape[0] - W / H) < .02  # portrait aspect, not squashed into landscape
