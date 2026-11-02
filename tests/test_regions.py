"""Multi-region localized grading: one SAM matte per region, precedence subject > regions > background."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from takeone import sam
from takeone.app import create_app
from takeone.contracts import CreativeReview, GradeParameters, GradeProposal, GradeRecipe, LocalizedPlan, RegionTreatment
from takeone.render_preflight import prepare
from tests.test_backend import settings, start, upload  # noqa: F401
from tests.test_local_grading import Planner, generate, parameters, pixels, scene  # noqa: F401
from tests.test_sam import parsed

# Proxy-space boxes (192x108 scene): the subject is the red block at x 60..131, y 24..83.
SUBJECT = (60, 24, 131, 83)
BOXES = {'red subject': SUBJECT, 'sky': (0, 0, 191, 19), 'upper band': (0, 0, 191, 39), 'lower band': (0, 70, 191, 99)}


class ConceptMasks:
    """Fake SAM returning a fixed box per concept, full mask inside the box."""

    def __init__(self, boxes=BOXES):
        self.boxes = boxes
        self.calls = []

    def available(self):
        return True

    def segment(self, path, concept, remote, remember, cancel, progress, raw_path, on_usage, on_request):
        self.calls.append(concept)
        on_request()
        remember('fixture-upload')
        from takeone.config import Settings
        from meta_sam_parser._mask_codec import _encode_raster
        info = sam.frame_info(path, Settings(path.parent), cancel)
        x1, y1, x2, y2 = self.boxes[concept]
        w, h = x2 - x1 + 1, y2 - y1 + 1
        payload = _encode_raster(bytes([1]) * w * h, w, h)
        record = f'<|box;x1={x1};y1={y1};x2={x2};y2={y2};w=192;h=108|><|mask;x=0;y=0;data={h},{w},{payload}|>'
        result = parsed([f'<{i}f>0' + record for i in range(len(info['pts']))])
        raw_path.write_text(result.raw_output)
        progress(result)
        on_usage({'video_frames_processed': len(info['pts'])})
        return result


DARK_COOL = dict(contrast=1.05, saturation=1., gamma=.9, brightness_gain=.75, cooling=.15, highlight_ceiling=.7)
WARM = dict(contrast=1., saturation=1., gamma=1., brightness_gain=1.2, cooling=-.15)


class RegionPlanner(Planner):
    def __init__(self, regions, background=None):
        super().__init__(local=True)
        self.regions, self.background = regions, background or parameters(brightness_gain=.9)

    def grade(self, *args, **kwargs):
        plan, usage = super().grade(*args, **kwargs)
        plan.local.background = GradeParameters(**self.background)
        plan.local.regions = [RegionTreatment(concept=c, treatment=GradeParameters(**t)) for c, t in self.regions]
        plan = GradeProposal.model_validate(plan.model_dump())
        return plan, usage


def rendered(settings, result):
    candidate = result['candidates'][-1]
    return pixels(settings.workspace / result['id'] / 'candidates' / candidate['id'] / 'grade.mp4')


def test_sky_region_gets_its_own_grade_while_subject_and_background_keep_theirs(settings, scene):
    masks = ConceptMasks()
    with TestClient(create_app(settings, RegionPlanner([('sky', DARK_COOL)]), masks), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark cool sky, keep red subject'))
        assert result['status'] == 'render_ready', result.get('error')
        before, after = pixels(scene), rendered(settings, result)
        sky, ground, subject = (slice(2, 16), slice(10, 180)), (slice(90, 104), slice(10, 180)), (slice(35, 72), slice(75, 118))
        assert after[sky].mean() < before[sky].mean() - 25  # dark sky
        assert after[sky][..., 0].mean() > after[sky][..., 2].mean() + 8  # BGR: blue above red, cool
        assert before[ground].mean() - 25 < after[ground].mean() < before[ground].mean() - 3  # background's gentler darkening
        assert np.abs(after[subject] - before[subject]).mean() < 4  # protected subject untouched
        assert masks.calls == ['red subject', 'sky']
        candidate = result['candidates'][-1]
        regions = candidate['validation']['mask_validation']['regions']
        assert [r['concept'] for r in regions] == ['sky'] and regions[0]['active_frames'] == 12
        concepts = {s['concept'] for s in result['segmentations'] if s['id'] in candidate['mask_run_ids']}
        assert concepts == {'red subject', 'sky'}


def test_subject_wins_over_an_overlapping_region(settings, scene):
    planner = RegionPlanner([('upper band', DARK_COOL)])  # covers y 0..39, overlapping the subject's top rows
    with TestClient(create_app(settings, planner, ConceptMasks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark upper band, keep red subject'))
        assert result['status'] == 'render_ready', result.get('error')
        before, after = pixels(scene), rendered(settings, result)
        overlap = (slice(27, 37), slice(70, 120))  # inside both the subject and the upper band
        assert np.abs(after[overlap] - before[overlap]).mean() < 4
        band_only = (slice(27, 37), slice(5, 50))
        assert after[band_only].mean() < before[band_only].mean() - 25


def test_earlier_listed_region_wins_where_regions_overlap(settings, scene):
    boxes = {**BOXES, 'upper band': (0, 0, 191, 39), 'lower band': (0, 0, 191, 99)}  # lower band contains the upper band
    planner = RegionPlanner([('upper band', DARK_COOL), ('lower band', WARM)])
    with TestClient(create_app(settings, planner, ConceptMasks(boxes)), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit two regions'))
        assert result['status'] == 'render_ready', result.get('error')
        before, after = pixels(scene), rendered(settings, result)
        first = (slice(4, 16), slice(5, 50))      # in both bands: the first listed (dark, cool) applies
        second = (slice(88, 96), slice(5, 50))    # only in the lower band: warm and brighter
        assert after[first].mean() < before[first].mean() - 25 and after[first][..., 0].mean() > after[first][..., 2].mean()
        assert after[second].mean() > before[second].mean() + 5 and after[second][..., 2].mean() > after[second][..., 0].mean()


def test_critic_gets_one_overlay_set_per_concept(settings, scene):
    seen = []

    class Critic(RegionPlanner):
        def grade(self, *args, **kwargs):
            plan, usage = super().grade(*args, **kwargs)
            plan.review_required = True
            return plan, usage

        def review(self, brief, candidate, data, previous=None, feedback=()):
            seen.append(data)
            return CreativeReview(decision='sufficient', summary='Fixture.'), {}

    with TestClient(create_app(settings, Critic([('sky', DARK_COOL)]), ConceptMasks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark sky, keep subject'))
        assert result['status'] == 'render_ready', result.get('error')
    overlays = seen[0]['mask_overlay_images']
    assert {o['concept'] for o in overlays} == {'red subject', 'sky'}
    assert len({o['overlay'] for o in overlays}) == 2
    # Subject crops still come from the subject mask only, not widened by the sky.
    crop = seen[0]['candidate_region_images'][0]['bounds_fraction']
    assert crop[1] > .15


def localized(**overrides):
    base = dict(concept='boat', intervals=[{'start': 0, 'end': 1}], foreground=GradeParameters(contrast=1., saturation=1., gamma=1.),
                background=GradeParameters(contrast=1., saturation=1., gamma=1., brightness_gain=.8), preserve_subject_color=True)
    return LocalizedPlan(**{**base, **overrides})


def test_region_contract_rejects_duplicates_no_ops_and_lists():
    sky = {'concept': 'sky', 'treatment': GradeParameters(contrast=1., saturation=1., gamma=1., highlight_ceiling=.7)}
    assert localized(regions=[sky]).regions[0].selection == 'all_matches'
    with pytest.raises(ValueError):
        localized(regions=[sky, sky])
    with pytest.raises(ValueError):
        localized(regions=[{**sky, 'concept': 'Boat'}])
    with pytest.raises(ValueError):
        localized(regions=[{'concept': 'sky', 'treatment': GradeParameters(contrast=1., saturation=1., gamma=1., brightness_gain=.8)}])
    with pytest.raises(ValueError):
        localized(regions=[{**sky, 'concept': 'sky, water'}])
    with pytest.raises(ValueError):
        localized(regions=[{**sky, 'concept': f'r{i}'} for i in range(5)])


def test_regions_require_the_v3_operator():
    proposal = GradeProposal(mode='localized', parameters=GradeParameters(contrast=1., saturation=1., gamma=1.),
        local=localized(regions=[{'concept': 'sky', 'treatment': GradeParameters(contrast=1., saturation=1., gamma=1., highlight_ceiling=.7)}]),
        summary='Fixture.', limitations=[], decisions=[{'parameter': 'local_treatment', 'reason': 'x', 'source_ids': ['user_request']}])
    with pytest.raises(ValueError):
        GradeRecipe(source_sha256='s', brief_sha256='b', model='m', proposal=proposal, source_samples=[], tone_operator='bounded_sigmoid_v2')


def test_region_layers_go_through_the_highlight_gate():
    bug = GradeParameters(contrast=1.2, saturation=1., gamma=.85, brightness_gain=.85, white_point=.9)
    proposal = GradeProposal(mode='localized', parameters=GradeParameters(contrast=1., saturation=1., gamma=1.),
        local=localized(regions=[{'concept': 'sky', 'treatment': bug}]), summary='Fixture.', limitations=[],
        decisions=[{'parameter': 'local_treatment', 'reason': 'x', 'source_ids': ['user_request']}])
    recipe = GradeRecipe(source_sha256='s', brief_sha256='b', model='m', schema_version='takeone-grade-v3', prompt_version='colorist-v3',
                         tone_operator='luminance_ocio_v3', proposal=proposal, source_samples=[])
    frame = np.full((144, 256, 3), 120, np.uint8)
    frame[:58] = np.linspace(205, 235, 256, dtype=np.uint8)[None, :, None]
    result = prepare(recipe, {0: frame})
    assert result['proposal'] is None and result['report']['highlights']['violations'] == ['region:sky']


def test_sky_only_experiment_renders_the_sky_and_leaves_everything_else(settings, scene):
    """Experiment 6 through the unchanged single-concept path: sky as the localized layer, the rest neutral."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('sky_only', __file__.replace('tests/test_regions.py', 'scripts/sky_only_experiment.py'))
    experiment = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(experiment)

    class SkyOnly(Planner):
        def grade(self, brief, analysis, source, *args, **kwargs):
            super().grade(brief, analysis, source, *args, **kwargs)
            return experiment.sky_only_proposal(source.duration, 'sky', experiment.sky_treatment(ceiling=.62)), {}

    masks = ConceptMasks()
    with TestClient(create_app(settings, SkyOnly(), masks), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark sky only'))
        assert result['status'] == 'render_ready', result.get('error')
        before, after = pixels(scene), rendered(settings, result)
        sky, rest, subject = (slice(2, 16), slice(10, 180)), (slice(90, 104), slice(10, 180)), (slice(35, 72), slice(75, 118))
        assert after[sky].mean() < before[sky].mean() - 25 and after[sky][..., 0].mean() > after[sky][..., 2].mean()
        assert np.abs(after[rest] - before[rest]).mean() < 3 and np.abs(after[subject] - before[subject]).mean() < 3
        assert masks.calls == ['sky']
