"""Batch B speed-ups must not change results: same frames, same QC numbers, same masks, same mattes."""
import json
import subprocess
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from takeone import evidence, frames, local_grading, mask_decode
from tests.test_backend import settings  # noqa: F401
from tests.test_sam import parsed


def clip(tmp_path, seconds=1, name='clip.mp4'):
    path = tmp_path / name
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=192x108:rate=12', '-t', str(seconds),
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)], check=True)
    return path


# ---- review evidence: per-frame store and render-recorded timing ------------------------------

def test_store_decodes_only_frames_it_has_not_seen(tmp_path, settings, monkeypatch):
    path = clip(tmp_path)
    cancel = threading.Event()
    manifest = evidence.frame_manifest(path, settings, cancel)
    first = evidence.extract(path, manifest, [0, 5], settings, cancel)
    decodes = []
    original = evidence.command
    monkeypatch.setattr(evidence, 'command', lambda args, *a, **k: decodes.append(args) or original(args, *a, **k))
    again = evidence.extract(path, manifest, [5, 0], settings, cancel)
    assert not decodes and all(np.array_equal(first[i], again[i]) for i in [0, 5])
    mixed = evidence.extract(path, manifest, [0, 5, 9], settings, cancel)
    assert len(decodes) == 1 and r'eq(n\,9)' in ' '.join(decodes[0]) and r'eq(n\,0)' not in ' '.join(decodes[0])
    monkeypatch.setattr(evidence, 'command', original)
    for name in list(tmp_path.glob('clip.mp4.frames-*x*.npz')):
        name.unlink()
    fresh = evidence.extract(path, manifest, [0, 5, 9], settings, cancel)
    assert all(np.array_equal(mixed[i], fresh[i]) for i in [0, 5, 9])


def test_render_recorded_timing_equals_a_real_probe(tmp_path, settings):
    from takeone.media import probe
    path = clip(tmp_path, 2)
    cancel = threading.Event()
    real = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-threads', '0', '-select_streams', 'v:0', '-show_frames',
                                      '-show_entries', frames.ENTRIES, '-of', 'json', str(path)],
                                     capture_output=True, text=True, check=True).stdout)
    metadata = probe(path, settings, cancel)
    assert frames.remember_decoded(path, metadata, evidence.decoded_manifest(path, settings, cancel, metadata))
    synthesized = json.loads((tmp_path / 'clip.mp4.frames-v1.json').read_text())['raw']
    keys = ['best_effort_timestamp', 'best_effort_timestamp_time', 'duration']
    assert [{k: f.get(k) for k in keys} for f in synthesized['frames']] == [{k: f.get(k) for k in keys} for f in real['frames']]
    assert synthesized['streams'] == real['streams'] and synthesized['format'] == real['format']


def test_render_pass_decodes_the_review_frames_without_another_decode(tmp_path, settings):
    from takeone.media import probe
    path = clip(tmp_path, 2)
    cancel = threading.Event()
    manifest = evidence.frame_manifest(path, settings, cancel)
    selection = {'selected_indices': [3, 10], 'manifest': manifest}
    scan = evidence.scan_indices(manifest['times'], [{'start': 0., 'end': 2., 'first_frame': 0, 'last_frame': 23}], [], settings,
                                 manifest['width'], manifest['height'])
    _, pixels, extras = evidence.decoded_evidence(path, settings, cancel, probe(path, settings, cancel), selection,
                                                  also=[(scan, settings.sample_width), (scan, 256)])
    assert set(extras) == {settings.sample_width, 256}
    reference = {limit: evidence.extract(path, manifest, scan, settings, cancel, width=limit) for limit in extras}
    for limit, frames_at_size in extras.items():  # identical to the stand-alone extraction
        assert all(np.array_equal(frames_at_size[i], reference[limit][i]) for i in scan)
    assert all(np.array_equal(pixels[i], reference[256][i]) for i in [3, 10])


# ---- output check: parallel statistics fold back in frame order ---------------------------------

def test_parallel_output_statistics_equal_a_sequential_fold(monkeypatch):
    rng = np.random.default_rng(5)
    frames_in = [(rng.integers(0, 255, (36, 64, 3), np.uint8), rng.integers(0, 255, (36, 64, 3), np.uint8),
                  (rng.random((36, 64)) > .5).astype(np.uint8)) for _ in range(40)]
    sequential = [local_grading.frame_statistics(*f) for f in frames_in]
    pool = local_grading._qc_pool()
    parallel = [future.result() for future in [pool.submit(local_grading.frame_statistics, *f) for f in frames_in]]
    assert parallel == sequential


def test_output_check_is_identical_with_one_or_many_workers(tmp_path, settings, monkeypatch):
    from tests.test_local_grading import Planner  # noqa: F401  (fixtures below build a real localized render)
    pytest.importorskip('cv2')
    # A synthetic localized setup: identical source and graded videos, a fixed square mask.
    source = clip(tmp_path, 1, 'source.mp4')
    graded = clip(tmp_path, 1, 'graded.mp4')
    info = local_grading.sam.frame_info(source, settings, threading.Event())
    mapping = {'frame_count': len(info['pts']), 'proxy': {'width': 192, 'height': 108, 'rate': info['rate']},
               'frames': [{'index': i, 'time': t} for i, t in enumerate(info['normalized'])], 'schema_version': 2}
    masks = tmp_path / 'm'
    masks.mkdir()
    import cv2
    mask = np.zeros((40, 40), np.uint8)
    mask[5:35, 5:35] = 255
    cv2.imwrite(str(masks / 'm.png'), mask)
    records = {i: [{'bounds': [40, 30, 80, 70], 'mask': 'm.png', 'directory': str(masks), 'object_id': '0',
                    'proxy': {'width': 192, 'height': 108}}] for i in range(mapping['frame_count'])}
    plan = SimpleNamespace(background=SimpleNamespace(contrast=1.2, gamma=1, saturation=1, brightness_gain=1, cooling=0, highlight_ceiling=1),
                           preserve_subject_color=False)
    src = SimpleNamespace(width=192, height=108)
    run = lambda: local_grading.check_output(source, graded, src, {'frame_map': mapping}, records, plan, settings, threading.Event(),
                                             adaptive=True)
    many = run()
    monkeypatch.setattr(local_grading, '_QC_POOL', None)
    monkeypatch.setattr(local_grading, 'QC_WORKERS', 1)
    one = run()
    assert one == many and many['checked_subject_frames'] == mapping['frame_count']


# ---- SAM decoding: once, in parallel, identical records and diagnostics -------------------------

def sam_lines(count, corrupt=None):
    from meta_sam_parser._mask_codec import _encode_raster
    rows = []
    for i in range(count):
        bits = bytes(x < 20 + i * 7 or x % (i + 2) == 0 for x in range(30 * 20))  # distinct mask per frame
        payload = _encode_raster(bits, 30, 20)
        if i == corrupt:
            payload = payload[:-4] + '!!!!'
        rows.append(f'<{i}f>0<|box;x1=4;y1=5;x2=34;y2=25;w=192;h=108|><|mask;x=0;y=0;data=20,30,{payload}|>')
    return rows


@pytest.mark.parametrize('corrupt', [None, 7])
def test_parallel_predecode_gives_identical_parse_records_and_diagnostics(corrupt, monkeypatch):
    from dataclasses import asdict
    from meta_sam_parser import decode_mask_to_raster as strict
    import meta_sam_parser._segmentation as segmentation
    lines = sam_lines(24, corrupt)
    monkeypatch.setattr(segmentation, 'decode_mask_to_raster', strict)  # the library exactly as shipped
    reference = parsed(lines)
    monkeypatch.setattr(segmentation, 'decode_mask_to_raster', mask_decode.decode)
    assert mask_decode.warm('\n'.join(lines)) == 24  # every payload decoded up front (one of them invalid)
    accelerated = parsed(lines)
    assert [asdict(r) for r in accelerated.records] == [asdict(r) for r in reference.records]
    assert [asdict(d) for d in accelerated.diagnostics] == [asdict(d) for d in reference.diagnostics]
    if corrupt is not None:
        assert any(d.code == 'invalid_mask_payload' for d in reference.diagnostics)
    for record in reference.records:
        if record.kind == 'mask':
            assert mask_decode.take(record.mask) == strict(record.mask)


def test_predecode_failure_falls_back_to_the_inline_decoder(monkeypatch):
    monkeypatch.setattr(mask_decode, '_workers', lambda: (_ for _ in ()).throw(RuntimeError('pool unavailable')))
    lines = sam_lines(20)
    assert mask_decode.warm('\n'.join(lines)) == 0  # nothing cached, nothing raised
    assert len([r for r in parsed(lines).records if r.kind == 'mask']) == 20


def test_parser_hook_is_only_installed_for_the_verified_version(monkeypatch):
    monkeypatch.setattr(mask_decode, 'version', lambda name: '9.9.9')
    assert mask_decode.install() is False


# ---- matte: cached per masks/feather/timeline ---------------------------------------------------

def test_matte_is_reused_only_when_everything_it_depends_on_is_unchanged(tmp_path):
    import cv2
    masks = tmp_path / 'masks'
    masks.mkdir()
    mask = np.zeros((10, 10), np.uint8)
    mask[2:8, 2:8] = 255
    cv2.imwrite(str(masks / 'a.png'), mask)
    records = {0: [{'mask': 'a.png', 'directory': str(masks), 'bounds': [1, 1, 11, 11], 'object_id': '0', 'proxy': {'width': 64, 'height': 36}}]}
    evidence_map = {'key': {'v': 1}, 'cut_frames': [0]}
    source = SimpleNamespace(width=64, height=36)
    key = lambda feather=1.0, ev=evidence_map, adaptive=True: local_grading.matte_key(tmp_path / 'p.mp4', source, ev, records,
                                                                                     SimpleNamespace(feather_px=feather), adaptive)
    assert key() == key()
    assert key(feather=1.5) != key() and key(adaptive=False) != key() and key(ev={'key': {'v': 2}, 'cut_frames': [0]}) != key()
    before = key()
    cv2.imwrite(str(masks / 'a.png'), 255 - mask)  # a rewritten mask file is a different matte
    assert key() != before


# ---- output check overlapping the render's integrity decode ----------------------------------

def _square_check_setup(tmp_path, settings):
    import cv2
    source = clip(tmp_path, 1, 'source.mp4')
    graded = clip(tmp_path, 1, 'graded.mp4')
    info = local_grading.sam.frame_info(source, settings, threading.Event())
    mapping = {'frame_count': len(info['pts']), 'proxy': {'width': 192, 'height': 108, 'rate': info['rate']},
               'frames': [{'index': i, 'time': t} for i, t in enumerate(info['normalized'])], 'schema_version': 2}
    masks = tmp_path / 'm'
    masks.mkdir(exist_ok=True)
    mask = np.zeros((40, 40), np.uint8)
    mask[5:35, 5:35] = 255
    cv2.imwrite(str(masks / 'm.png'), mask)
    records = {i: [{'bounds': [40, 30, 80, 70], 'mask': 'm.png', 'directory': str(masks), 'object_id': '0',
                    'proxy': {'width': 192, 'height': 108}}] for i in range(mapping['frame_count'])}
    plan = SimpleNamespace(background=SimpleNamespace(contrast=1.2, gamma=1, saturation=1, brightness_gain=1, cooling=0, highlight_ceiling=1),
                           preserve_subject_color=False)
    return source, graded, {'frame_map': mapping}, records, plan


def test_early_started_output_check_equals_the_normal_one_even_after_a_rename(tmp_path, settings):
    source, graded, evidence_map, records, plan = _square_check_setup(tmp_path, settings)
    expected = local_grading.check_output(source, graded, None, evidence_map, records, plan, settings, threading.Event(), adaptive=True)
    partial = graded.with_name('graded.partial.mp4')
    graded.rename(partial)
    pending = local_grading.PendingOutputCheck(source, partial, evidence_map, records, plan, settings, threading.Event(), None, True)
    partial.rename(graded)  # the render renames its output while the check is running
    assert pending.finish(graded) == expected


def test_timing_errors_still_take_precedence_and_abort_stops_the_measurement(tmp_path, settings):
    from takeone.errors import Problem
    source, graded, evidence_map, records, plan = _square_check_setup(tmp_path, settings)
    broken = {'frame_map': {**evidence_map['frame_map'], 'frame_count': evidence_map['frame_map']['frame_count'] + 1}}
    pending = local_grading.PendingOutputCheck(source, graded, broken, records, plan, settings, threading.Event(), None, True)
    with pytest.raises(Problem) as caught:
        pending.finish(graded)
    assert caught.value.code == 'LOCAL_OUTPUT_TIMING'  # same error check_output reports first
    aborted = local_grading.PendingOutputCheck(source, graded, evidence_map, records, plan, settings, threading.Event(), None, True)
    aborted.abort()
    assert aborted.future.done()


def test_render_starts_the_output_check_before_its_integrity_decode(settings, monkeypatch):
    from fastapi.testclient import TestClient
    from takeone import evidence as ev
    from takeone.app import create_app
    from tests.test_backend import start, upload
    from tests.test_local_grading import Masks, Planner, generate
    import tests.test_local_grading as fixtures
    order = []
    original_pending, original_decoded = local_grading.PendingOutputCheck.__init__, ev.decoded_evidence
    def pending(self, *a, **k):
        order.append('output check started')
        original_pending(self, *a, **k)
    def decoded(*a, **k):
        order.append('integrity decode')
        return original_decoded(*a, **k)
    monkeypatch.setattr(local_grading.PendingOutputCheck, '__init__', pending)
    monkeypatch.setattr(ev, 'decoded_evidence', decoded)
    import subprocess
    path = settings.workspace.parent / 'scene.mp4'
    frames = []
    for _ in range(12):
        f = np.full((108, 192, 3), 120, np.uint8)
        f[24:84, 60:132] = [25, 35, 190]
        frames.append(f)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pixel_format', 'bgr24', '-video_size', '192x108', '-framerate', '12', '-i', '-',
                    '-an', '-vf', 'setparams=range=full:color_primaries=bt709:color_trc=bt709:colorspace=bt709,scale=in_range=pc:out_range=tv',
                    '-c:v', 'libx264', '-crf', '14', '-pix_fmt', 'yuv420p', '-color_range', 'tv', '-colorspace', 'bt709', '-color_primaries', 'bt709',
                    '-color_trc', 'bt709', str(path)], input=b''.join(f.tobytes() for f in frames), check=True)
    with TestClient(create_app(settings, Planner(local=True), Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, path), 'explicit dark cool background, keep red subject'))
        assert result['status'] == 'render_ready', result.get('error')
        assert result['candidates'][0]['validation']['subject_protection']['checked_subject_frames'] == 12
    assert order[:2] == ['output check started', 'integrity decode']
