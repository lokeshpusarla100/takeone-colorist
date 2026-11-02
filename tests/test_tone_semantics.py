"""What the v3 tone operator can and cannot do to highlights.

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


def test_pure_white_stays_white_under_every_tone_control():
    for controls in combos():
        if controls['cooling'] or controls['black_point']:
            continue  # cooling rescales channels; black_point is covered by the white_point test
        assert luma([1.], **controls)[0] > .999, controls


def test_no_supported_control_substantially_darkens_a_near_white_highlight():
    """HIGHLIGHT_HANDLING promises this; a dark steel-blue sky from a pale one is outside the operator."""
    worst = min(float(luma([.9], **controls)[0]) for controls in combos())
    assert worst > .65, worst
    # With the punchy contrast the monsoon brief asked for, the highlight barely moves at all.
    punchy = min(float(luma([.9], **controls)[0]) for controls in combos() if controls['contrast'] >= 1.25)
    assert punchy > .80, punchy


def test_documented_best_case_numbers_match_the_operator():
    deepest = dict(gamma=.8, brightness_gain=.65, cooling=.15, black_point=0., white_point=1.)
    flat = float(luma([.9], contrast=.75, **deepest)[0])
    punchy = float(luma([.9], contrast=1.25, **deepest)[0])
    assert abs(flat - .74) < .02 and abs(punchy - .88) < .02, (flat, punchy)
    assert '0.74' in HIGHLIGHT_HANDLING and '0.88' in HIGHLIGHT_HANDLING and 'unfulfilled_requests' in HIGHLIGHT_HANDLING


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
    assert payload['controls'] == CONTROL_SEMANTICS and payload['highlight_handling'] == HIGHLIGHT_HANDLING
    assert 'BRIGHTENS' in payload['controls']['white_point']
    critic_system, critic_payload = captured[1]['messages'][0]['content'], json.loads(captured[1]['messages'][1]['content'][0]['text'])
    assert 'it BRIGHTENS and flattens highlights' in critic_system and 'correctable=false' in critic_system
    assert critic_payload['highlight_handling'] == HIGHLIGHT_HANDLING and critic_payload['control_semantics'] == CONTROL_SEMANTICS
