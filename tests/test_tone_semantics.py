"""What the v3 tone operator can and cannot do to highlights.

highlight_ceiling (the output shoulder) is the only control that darkens near-white
pixels; every other control keeps white at white.

The planner/critic prompts (provider.CONTROL_SEMANTICS, HIGHLIGHT_HANDLING) and the
highlight gate (render_preflight) rest on these facts. A June monsoon run lowered
white_point from 0.92 to 0.85 across three revisions "to subdue the sky" and made the
sky brighter every time; these tests pin the arithmetic so that cannot be forgotten.
"""
import itertools
import json
import threading
from types import SimpleNamespace

import numpy as np

from takeone.color_processing import apply_rgb
from takeone import creative_review
from takeone.contracts import CreativeReview, GradeParameters, GradeProposal
from takeone.provider import CONTROL_SEMANTICS, HIGHLIGHT_HANDLING, MuseProvider
from tests.test_backend import settings, start, upload
from tests.test_local_grading import Planner, Masks, generate
from tests.test_phase2 import dark_clip  # noqa: F401  (fixture)
from fastapi.testclient import TestClient
from takeone.app import create_app

WEIGHTS = np.array([.2126, .7152, .0722], np.float32)
GREYS = np.linspace(.5, 1, 26, dtype=np.float32)
GRID = dict(contrast=[.75, 1., 1.25], gamma=[.8, 1., 1.2], brightness_gain=[.65, 1., 1.35],
            cooling=[-.15, 0., .15], black_point=[0., .1])


def luma(greys=GREYS, **controls):
    parameters = GradeParameters(**{**dict(contrast=1., saturation=1., gamma=1.), **controls})
    rgb = np.repeat(np.asarray(greys, np.float32)[:, None], 3, axis=1)
    return apply_rgb(rgb, parameters) @ WEIGHTS


def combos():
    keys = list(GRID)
    for values in itertools.product(*GRID.values()):
        yield dict(zip(keys, values))


def test_lowering_white_point_never_darkens_any_upper_tone():
    """white_point is an input cutoff: everything at or above it becomes white, so lower means brighter."""
    for controls in combos():
        previous = None
        for white_point in [1., .95, .92, .88, .85, .8, .75]:
            current = luma(white_point=white_point, **controls)
            if previous is not None:
                assert (current >= previous - 1e-6).all(), (controls, white_point)
            previous = current


def test_white_point_below_the_input_saturates_highlights_to_one_flat_value():
    """The measured failure: a pale sky of 0.85-0.95 luma collapses to a single bright plateau."""
    plateau = luma([.88, .9, .95, 1.], white_point=.88)
    assert np.ptp(plateau) < 1e-6
    neutral = luma([.88, .9, .95, 1.])
    assert np.ptp(neutral) > .1  # with the endpoint untouched the same tones stay distinct


def test_pure_white_stays_white_under_every_tone_control_except_the_ceiling():
    for controls in combos():
        if controls['cooling'] or controls['black_point']:
            continue  # cooling rescales channels; black_point is covered by the white_point test
        assert luma([1.], **controls)[0] > .999, controls
        assert abs(luma([1.], highlight_ceiling=.7, **controls)[0] - .7) < 1e-5, controls


def test_without_the_ceiling_no_control_substantially_darkens_a_near_white_highlight():
    """Why highlight_ceiling exists: every other control keeps a pale sky pale."""
    worst = min(float(luma([.9], **controls)[0]) for controls in combos())
    assert worst > .65, worst
    # With the punchy contrast the monsoon brief asked for, the highlight barely moves at all.
    punchy = min(float(luma([.9], **controls)[0]) for controls in combos() if controls['contrast'] >= 1.25)
    assert punchy > .80, punchy


def test_documented_numbers_match_the_operator():
    deepest = dict(gamma=.8, brightness_gain=.65, cooling=.15, black_point=0., white_point=1.)
    punchy = float(luma([.9], contrast=1.25, **deepest)[0])
    shouldered = float(luma([.9], contrast=1.25, highlight_ceiling=.6, **deepest)[0])
    assert abs(punchy - .88) < .02 and abs(shouldered - .57) < .02, (punchy, shouldered)
    for text in ['0.88', '0.57', 'highlight_ceiling 0.6', 'unfulfilled_requests']:
        assert text in HIGHLIGHT_HANDLING, text
    # The per-tone drops quoted in CONTROL_SEMANTICS (0.64*(1-c) at 0.80, 0.25*(1-c) at 0.50).
    for tone, factor in [(.8, .64), (.5, .25)]:
        assert abs((tone - luma([tone], highlight_ceiling=.6)[0]) - factor * .4) < 1e-4


CEILINGS = [1., .9, .8, .7, .6]


def test_highlight_ceiling_darkens_highlights_monotonically_and_keeps_texture_ordered():
    tones = np.linspace(0, 1, 201, dtype=np.float32)
    previous = luma(tones)
    for ceiling in CEILINGS[1:]:
        current = luma(tones, highlight_ceiling=ceiling)
        assert (np.diff(current) > 0).all(), ceiling  # strictly increasing: bright texture is compressed, never inverted
        assert (current <= previous + 1e-6).all(), ceiling  # a lower ceiling never brightens any tone
        slope = (current[-1] - current[-2]) / (tones[-1] - tones[-2])
        assert slope >= 2 * ceiling - 1 - 1e-3 and slope >= .2 - 1e-3, (ceiling, slope)
        previous = current


def test_highlight_ceiling_barely_moves_shadows():
    shadows = np.linspace(0, .2, 21, dtype=np.float32)
    assert np.abs(luma(shadows, highlight_ceiling=.6) - luma(shadows)).max() <= .4 * .04 + 1e-5


def test_highlight_ceiling_stays_monotone_combined_with_every_other_control():
    tones = np.linspace(0, 1, 101, dtype=np.float32)
    for controls in combos():
        current = luma(tones, highlight_ceiling=.6, **controls)
        assert (np.diff(current) >= -1e-6).all(), controls


def test_ceiling_lets_a_cooled_pale_sky_become_steel_blue_instead_of_cyan_white():
    rgb = np.full((4, 4, 3), .9, np.float32)
    result = apply_rgb(rgb, GradeParameters(contrast=1., saturation=1., gamma=1., cooling=.15, highlight_ceiling=.6))
    assert result[..., 2].max() < .85 and result[..., 2].min() > result[..., 0].max()  # no clipped channel, still blue-leaning


def test_ceiling_on_a_protected_subject_changes_luminance_only():
    rgb = np.array([[[.9, .6, .2], [.7, .5, .1]]], np.float32)
    result = apply_rgb(rgb, GradeParameters(contrast=1., saturation=1., gamma=1., highlight_ceiling=.7), protected=True)
    assert np.allclose(result / result[..., :1], rgb / rgb[..., :1], atol=1e-5) and (result < rgb).all()


def reference_v3(values, p):
    """Verbatim tone math from before the shoulder existed; neutral ceiling must reproduce it exactly."""
    import PyOpenColorIO as ocio
    rgb = np.asarray(values, np.float32).copy(); luma_ = rgb @ WEIGHTS
    level = np.clip((luma_ - p.black_point) / (p.white_point - p.black_point), 0, 1)
    x = np.power(level, 1 / p.gamma); a = np.power(x, p.contrast); b = np.power(1 - x, p.contrast)
    tone = a / np.maximum(a + b, np.finfo(np.float32).tiny); gain = p.brightness_gain
    tone = gain * tone / (1 + (gain - 1) * tone)
    ratio = np.divide(tone, luma_, out=np.ones_like(tone), where=luma_ > 0)
    ratio = np.minimum(ratio, 1 / np.maximum(rgb.max(axis=-1), np.finfo(np.float32).tiny)); rgb *= ratio[..., None]
    c = p.cooling; n = 1 / (.2126 * (1 - c) + .7152 + .0722 * (1 + c))
    t = ocio.CDLTransform(); t.setSlope([(1 - c) * n, n, (1 + c) * n]); t.setSat(p.saturation)
    ocio.Config.CreateRaw().getProcessor(t).getDefaultCPUProcessor().applyRGB(rgb)
    return np.clip(rgb, 0, 1)


def test_neutral_ceiling_reproduces_every_existing_v3_recipe_bit_for_bit():
    rng = np.random.default_rng(7)
    values = rng.random((4096, 3), dtype=np.float32)
    for controls in list(combos())[::7]:
        p = GradeParameters(contrast=controls['contrast'], saturation=1.1, gamma=controls['gamma'], brightness_gain=controls['brightness_gain'],
                            cooling=controls['cooling'], black_point=controls['black_point'], white_point=.92)
        assert np.array_equal(apply_rgb(values, p), reference_v3(values, p)), controls


def test_lut_identity_and_neutrality_include_the_ceiling(tmp_path):
    from takeone.color_processing import lut
    neutral = GradeParameters(contrast=1., saturation=1., gamma=1.)
    assert lut(tmp_path, neutral) == 'null'
    shouldered = lut(tmp_path, neutral.model_copy(update={'highlight_ceiling': .8}))
    assert shouldered.startswith('lut3d=') and 'v4' in (tmp_path / shouldered.split('=')[2].split(':')[0]).read_text().splitlines()[0]
    other = lut(tmp_path, neutral.model_copy(update={'highlight_ceiling': .7}))
    assert other != shouldered


def test_legacy_operators_cannot_carry_a_highlight_ceiling():
    import pytest
    from takeone.contracts import GradeRecipe
    proposal = GradeProposal(parameters=GradeParameters(contrast=1., saturation=1., gamma=1., highlight_ceiling=.8), summary='Fixture.',
                             limitations=[], decisions=[{'parameter': 'highlight_ceiling', 'reason': 'Fixture', 'source_ids': ['user_request']}])
    with pytest.raises(ValueError):
        GradeRecipe(source_sha256='s', brief_sha256='b', model='m', proposal=proposal, source_samples=[], tone_operator='bounded_sigmoid_v2')
    GradeRecipe(source_sha256='s', brief_sha256='b', model='m', proposal=proposal, source_samples=[], tone_operator='luminance_ocio_v3',
                schema_version='takeone-grade-v3', prompt_version='colorist-v3')


def test_look_variations_keep_the_planned_ceiling():
    from takeone import looks
    base = GradeProposal(parameters=GradeParameters(contrast=1.1, saturation=1., gamma=.9, highlight_ceiling=.7), summary='Fixture.',
                         limitations=[], decisions=[{'parameter': 'highlight_ceiling', 'reason': 'Fixture', 'source_ids': ['user_request']}])
    varied = {name: looks._apply_tweak(base, fn).parameters.highlight_ceiling for name, fn in looks.variations(base)}
    assert varied['Brief look'] == .7 and varied['Cooler'] == .7
    assert varied['Gentler'] > .7 and varied['Stronger'] < .7  # toward / away from neutral, still within bounds
    assert all(.6 <= value <= 1 for value in varied.values())


def test_contrast_above_one_lifts_upper_highlights():
    assert (luma([.7, .8, .9, .95], contrast=1.25) > luma([.7, .8, .9, .95])).all()


def test_strong_cooling_of_a_pale_sky_saturates_a_channel_instead_of_darkening_it():
    rgb = np.full((4, 4, 3), .9, np.float32)
    result = apply_rgb(rgb, GradeParameters(contrast=1., saturation=1., gamma=1., cooling=.15))
    assert result[..., 2].min() >= .999 and result[..., 0].max() < .9 + 1e-6  # blue clips, red drops


def test_control_semantics_state_the_direction_of_every_highlight_control():
    white = CONTROL_SEMANTICS['white_point']
    assert 'BRIGHTENS' in white and 'LOWERING' in white and 'never darken' in white
    assert 'LIFTS' in CONTROL_SEMANTICS['contrast']
    assert 'cannot darken pure highlights' in CONTROL_SEMANTICS['brightness_gain']
    assert 'cyan-white' in CONTROL_SEMANTICS['cooling']
    assert 'ONLY control that darkens highlights' in CONTROL_SEMANTICS['highlight_ceiling']


def test_planner_and_critic_prompts_carry_the_highlight_rules(settings, dark_clip):
    captured = []

    def parse(**kwargs):
        captured.append(kwargs)
        if kwargs['response_format'] is CreativeReview:
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                parsed=CreativeReview(decision='needs_user', summary='Fixture opinion.')))], usage=None)
        proposal = GradeProposal(parameters=GradeParameters(contrast=1., saturation=1., gamma=1.), summary='Fixture.', limitations=[],
                                 decisions=[{'parameter': 'contrast', 'reason': 'Fixture', 'source_ids': ['user_request']}])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=proposal))], usage=None)

    class SDK:
        beta = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    with TestClient(create_app(settings, Planner(), Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, dark_clip), 'explicit maintain dark atmosphere'))
        service = client.app.state.service
        state = service.store.get(result['id'])
        candidate = state.candidates[-1]
        data, previous, _ = creative_review.collect(service, state, candidate, threading.Event())
        provider = MuseProvider(settings, service.prompt_gate)
        provider.client = lambda: SDK()
        provider.grade(state.brief, state.analysis, state.source, [], [])
        provider.review(state.brief, candidate, data, previous, [])
    messages = captured[0]['messages']
    system, payload = messages[0]['content'], json.loads(messages[1]['content'][0]['text'])
    assert 'Never use white_point or higher contrast to subdue' in system and 'intentional_lift' in system
    assert 'highlight_ceiling is the control that darkens highlights' in system
    assert payload['controls'] == CONTROL_SEMANTICS and payload['highlight_handling'] == HIGHLIGHT_HANDLING
    assert 'BRIGHTENS' in payload['controls']['white_point']
    critic_system, critic_payload = captured[1]['messages'][0]['content'], json.loads(captured[1]['messages'][1]['content'][0]['text'])
    assert 'it BRIGHTENS and flattens highlights' in critic_system and 'correctable=false' in critic_system
    assert 'lower highlight_ceiling instead' in critic_system
    assert critic_payload['highlight_handling'] == HIGHLIGHT_HANDLING and critic_payload['control_semantics'] == CONTROL_SEMANTICS
