"""Single-pass SAM tracking: one request for every area, checked per area, with the ordinary path as the fallback."""
from dataclasses import replace
from pathlib import Path
import re

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from takeone import sam, sam_single_pass as sp
from takeone.app import create_app
from takeone.errors import Problem
from tests.test_backend import settings, start, upload  # noqa: F401
from tests.test_local_grading import generate, pixels, scene  # noqa: F401
from tests.test_regions import DARK_COOL, BOXES, ConceptMasks, RegionPlanner, rendered
from tests.test_sam import parsed

W, H = 192, 108


# ---------- the arithmetic ----------

def saved_mask(folder: Path, frame: int, object_id: str, box, holes=()):
    """A saved mask record (and its PNG) for a rectangle, optionally with rectangular holes."""
    x1, y1, x2, y2 = box
    mask = np.full((y2 - y1, x2 - x1), 255, np.uint8)
    for hx1, hy1, hx2, hy2 in holes:
        mask[hy1 - y1:hy2 - y1, hx1 - x1:hx2 - x1] = 0
    name = f'f{frame:05d}-o{object_id}.png'
    cv2.imwrite(str(folder / name), mask)
    return {'frame_index': frame, 'object_id': object_id, 'bounds': [x1, y1, x2, y2], 'mask': name, 'confidence': None,
            'directory': str(folder), 'proxy': {'width': W, 'height': H, 'rate': '12'}}


def test_a_seed_points_into_the_object_and_away_from_what_is_not_part_of_it(tmp_path):
    record = saved_mask(tmp_path, 0, '0', (20, 10, 140, 70), holes=[(60, 20, 100, 60)])  # a frame with a hole in the middle
    binary, _ = sp.local_grading.raster([record], W, H)
    box, points = sp.seed_for(binary)
    assert (box.left, box.top, box.right, box.bottom) == (20, 10, 140, 70)
    positive = [p for p in points if p.label == 'positive']
    negative = [p for p in points if p.label == 'negative']
    assert 1 <= len(positive) <= 3 and 1 <= len(negative) <= 3
    assert all(binary[p.y, p.x] == 1 for p in positive)      # inside the object
    assert all(binary[p.y, p.x] == 0 for p in negative)      # in the part of its box that is not the object
    assert all(box.left <= p.x < box.right and box.top <= p.y < box.bottom for p in points)
    solid = saved_mask(tmp_path, 1, '0', (20, 10, 60, 40))
    assert not [p for p in sp.seed_for(sp.local_grading.raster([solid], W, H)[0])[1] if p.label == 'negative']  # nothing to exclude


def test_objects_are_numbered_per_area_and_seeded_where_they_first_appear(tmp_path):
    by_frame = {5: [saved_mask(tmp_path, 5, '0', (10, 10, 50, 50))], 6: [saved_mask(tmp_path, 6, '0', (10, 10, 50, 50)), saved_mask(tmp_path, 6, '3', (70, 20, 120, 60))],
                7: [saved_mask(tmp_path, 7, '3', (70, 20, 120, 60))]}
    objects = sp.area_objects(by_frame, W, H, first=5, area=2)
    assert [o.id for o in objects] == [3000, 3003]                     # area 2 -> 3000 + own id
    assert [(o.prompts[0].frame) for o in objects] == [0, 1]           # relative to the request's first frame
    assert sp.area_of('3003') == (2, '3') and sp.prompt_id(0, '12') == 1012
    text = sp.build_prompt(W, H, objects)
    assert text.startswith('<0f>3000<|box;') and '<1f>3003<|box;' in text and '<|point;' in text
    with pytest.raises(sp.NotApplicable):
        sp.prompt_id(0, '1000')                                        # ids are kept apart per area; one that does not fit is refused


def test_a_single_pass_manifest_splits_back_into_each_areas_own_objects():
    records = [{'object_id': '1000', 'frame_index': 0}, {'object_id': '2003', 'frame_index': 0}, {'object_id': '1001', 'frame_index': 1}]
    manifest = {'frame_map': {'x': 1}, 'records': records}
    first, second = sp.split_manifest(manifest, 0), sp.split_manifest(manifest, 1)
    assert [r['object_id'] for r in first['records']] == ['0', '1'] and [r['object_id'] for r in second['records']] == ['3']
    assert first['frame_map'] == {'x': 1} and manifest['records'][0]['object_id'] == '1000'   # the original is untouched


def test_agreement_requires_the_detection_masks_to_be_reproduced(tmp_path):
    ref = {i: [saved_mask(tmp_path, i, '0', (20, 10, 100, 60))] for i in range(4)}
    same = sp.agreement(ref, {i: [dict(r)] for i, [r] in ref.items()}, W, H)
    assert same['mean_iou'] == 1 and same['lost'] == 0 and sp.acceptable(same, .85)
    missing = sp.agreement(ref, {i: ref[i] for i in range(3)}, W, H)                   # one frame the detection saw has no tracked mask
    assert missing['lost'] == 1 and not sp.acceptable(missing, .85)
    shifted = {i: [saved_mask(tmp_path, i + 10, '0', (60, 10, 140, 60))] for i in range(4)}
    far = sp.agreement(ref, dict(zip(range(4), shifted.values())), W, H)
    assert far['mean_iou'] < .5 and not sp.acceptable(far, .85)
    bigger = sp.agreement(ref, {i: [saved_mask(tmp_path, i + 20, '0', (0, 0, 190, 100))] for i in range(4)}, W, H)
    assert bigger['mean_iou'] < .85 and not sp.acceptable(bigger, .85)                 # right place, too much of the frame (the "water" case)
    assert not sp.acceptable(sp.agreement({}, {}, W, H), .85)                          # nothing to compare is never a pass
    assert not sp.acceptable({'frames': 10, 'lost': 0, 'mean_iou': .9, 'poor_fraction': .3}, .85)  # good on average but far off on many frames


# ---------- through the service, with a fake SAM ----------

class PromptingMasks(ConceptMasks):
    """Concept requests as in test_regions, plus object-prompted requests that answer every id with its seed box."""

    def __init__(self, bad_area=None, before_request=None, after_request=None):
        super().__init__()
        self.sizes, self.prompted, self.bad_area, self.before_request, self.after_request = [], [], bad_area, before_request, after_request

    def segment(self, path, concept, remote, remember, cancel, progress, raw_path, on_usage, on_request):
        from takeone.config import Settings
        self.sizes.append((concept, len(sam.frame_info(path, Settings(path.parent), cancel)['pts'])))
        return super().segment(path, concept, remote, remember, cancel, progress, raw_path, on_usage, on_request)

    def segment_prompted(self, path, concept, remote, remember, cancel, progress, raw_path, on_usage, on_request, *, object_prompt, propagation_direction):
        from meta_sam_parser._mask_codec import _encode_raster
        from takeone.config import Settings
        if self.before_request:
            raise self.before_request
        frames = len(sam.frame_info(path, Settings(path.parent), cancel)['pts'])
        seeds = {int(i): tuple(int(v) for v in box) for i, *box in re.findall(r'(?:^|,|>)(\d+)<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);', object_prompt)}
        self.prompted.append((frames, sorted(seeds), propagation_direction))
        on_request()
        remember('fixture-upload')
        if self.after_request:
            raise self.after_request
        records = []
        for oid, (x1, y1, x2, y2) in sorted(seeds.items()):
            if self.bad_area is not None and oid // 1000 - 1 == self.bad_area:
                x1, y1, x2, y2 = 0, 0, W - 1, H - 1                         # claims the whole frame, like a seed box that holds neighbours
            w, h = x2 - x1 + 1, y2 - y1 + 1
            payload = _encode_raster(bytes([1]) * w * h, w, h)
            records.append(f'{oid}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={W};h={H}|><|mask;x=0;y=0;data={h},{w},{payload}|>')
        result = parsed([f'<{i}f>' + ','.join(records) for i in range(frames)])
        raw_path.write_text(result.raw_output)
        progress(result)
        on_usage({'video_frames_processed': frames})
        return result


@pytest.fixture
def on(settings):
    return replace(settings, sam_single_pass=True, sam_single_pass_window=4, sam_single_pass_min_frames=8, sam_single_pass_min_areas=2)


def run(settings, scene, masks, prompt='explicit dark cool sky, keep red subject'):
    with TestClient(create_app(settings, RegionPlanner([('sky', DARK_COOL)]), masks), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), prompt))
        again = generate(client, result) if result['status'] == 'render_ready' else None
        return result, again


def titles(result):
    return [e['title'] for e in result['events']]


def assert_graded_like_the_ordinary_path(settings, scene, result):
    before, after = pixels(scene), rendered(settings, result)
    sky, subject = (slice(2, 16), slice(10, 180)), (slice(35, 72), slice(75, 118))
    assert after[sky].mean() < before[sky].mean() - 25 and after[sky][..., 0].mean() > after[sky][..., 2].mean() + 8
    assert np.abs(after[subject] - before[subject]).mean() < 4


def test_one_request_tracks_every_area_and_the_grade_matches_the_ordinary_path(on, scene):
    masks = PromptingMasks()
    result, again = run(on, scene, masks)
    assert result['status'] == 'render_ready', result.get('error')
    assert sorted(masks.sizes) == [('red subject', 4), ('sky', 4)]            # detection windows only: 4 frames each
    assert masks.prompted == [(12, [1000, 2000], 'both')]                      # one request, both areas, every frame
    assert_graded_like_the_ordinary_path(on, scene, result)
    candidate = result['candidates'][-1]
    regions = candidate['validation']['mask_validation']['regions']
    assert [r['concept'] for r in regions] == ['sky'] and regions[0]['active_frames'] == 12
    assert titles(result).count('Single-pass check passed') == 2 and 'Single-pass check failed' not in titles(result)
    assert 'Single-pass SAM tracking' in titles(result)
    used = [s['concept'] for s in result['segmentations'] if s['id'] in candidate['mask_run_ids']]
    assert 'all areas: red subject, sky' in used
    frames = sum(e['details']['video_frames_processed'] for e in result['events'] if e['title'] == 'SAM request usage')
    assert frames == 4 + 4 + 12 < 2 * 12                                       # fewer frames than one full request per area
    # Grading the same brief again reuses the saved tracking: no new SAM request of any kind.
    assert again['status'] == 'render_ready' and sorted(masks.sizes) == [('red subject', 4), ('sky', 4)] and len(masks.prompted) == 1


def test_an_area_that_does_not_reproduce_its_detection_is_requested_on_its_own(on, scene):
    masks = PromptingMasks(bad_area=1)                                          # the sky comes back as the whole frame
    result, _ = run(on, scene, masks)
    assert result['status'] == 'render_ready', result.get('error')
    assert sorted(masks.sizes) == [('red subject', 4), ('sky', 4), ('sky', 12)]  # only the sky is requested again, over the whole range
    assert titles(result).count('Single-pass check passed') == 1 and titles(result).count('Single-pass check failed') == 1
    assert_graded_like_the_ordinary_path(on, scene, result)                     # and the result is the sky it should be, not the whole frame


def test_plans_that_cannot_benefit_use_the_ordinary_path(settings, scene):
    masks = PromptingMasks()
    few = replace(settings, sam_single_pass=True, sam_single_pass_window=4, sam_single_pass_min_frames=8, sam_single_pass_min_areas=3)
    result, _ = run(few, scene, masks)                                          # two areas, three required
    assert result['status'] == 'render_ready' and masks.prompted == [] and sorted(masks.sizes) == [('red subject', 12), ('sky', 12)]
    assert not [t for t in titles(result) if t.startswith('Single-pass')]
    short = PromptingMasks()
    result, _ = run(replace(few, sam_single_pass_min_areas=2, sam_single_pass_min_frames=50), scene, short)   # 12 frames is too short
    assert result['status'] == 'render_ready' and short.prompted == [] and sorted(short.sizes) == [('red subject', 12), ('sky', 12)]
    assert 'Single-pass SAM not used' in titles(result)


def test_the_default_is_unchanged_when_the_setting_is_off(settings, scene):
    masks = PromptingMasks()
    result, _ = run(settings, scene, masks)
    assert settings.sam_single_pass is False
    assert result['status'] == 'render_ready' and masks.prompted == [] and sorted(masks.sizes) == [('red subject', 12), ('sky', 12)]
    assert not [t for t in titles(result) if t.startswith('Single-pass')]


def test_when_the_request_could_not_be_sent_each_area_is_requested_the_ordinary_way(on, scene):
    masks = PromptingMasks(before_request=Problem('SAM_NOT_CONFIGURED', 'Prompting is unavailable.', 'None.', 503))
    result, _ = run(on, scene, masks)
    assert result['status'] == 'render_ready', result.get('error')
    assert 'Single-pass SAM not used' in titles(result)
    assert sorted(masks.sizes) == [('red subject', 4), ('red subject', 12), ('sky', 4), ('sky', 12)]   # nothing was billed, so no harm


def test_a_request_that_was_sent_is_never_silently_repeated_as_one_request_per_area(on, scene):
    masks = PromptingMasks(after_request=Problem('SAM_INCOMPLETE', 'The stream ended early.', 'Retry explicitly.', 502))
    result, _ = run(on, scene, masks)
    assert result['status'] == 'failed' and result['error']['code'] == 'SAM_INCOMPLETE'
    assert sorted(masks.sizes) == [('red subject', 4), ('sky', 4)] and len(masks.prompted) == 1   # it may already be billed: no paid second try


def test_the_setting_is_read_from_the_environment_and_bounded(monkeypatch, tmp_path):
    from takeone.config import Settings
    monkeypatch.setenv('TAKEONE_WORKSPACE', str(tmp_path))
    monkeypatch.delenv('TAKEONE_SAM_SINGLE_PASS', raising=False)
    monkeypatch.delenv('TAKEONE_SAM_SINGLE_PASS_GATE', raising=False)
    assert Settings.from_env().sam_single_pass is False and Settings.from_env().sam_single_pass_gate == .85
    monkeypatch.setenv('TAKEONE_SAM_SINGLE_PASS', '1')
    monkeypatch.setenv('TAKEONE_SAM_SINGLE_PASS_GATE', '0.2')
    assert Settings.from_env().sam_single_pass is True and Settings.from_env().sam_single_pass_gate == .5   # a gate that lets anything through is not allowed
