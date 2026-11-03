"""Hybrid review renders: V1 full, critic revisions as drafts, the chosen draft re-rendered in full."""
from dataclasses import replace
from uuid import uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient

from takeone import rendering
from takeone.app import create_app
from tests.test_backend import settings as base_settings, start, upload  # noqa: F401
from tests.test_convergence import TargetCritic
from tests.test_local_grading import Masks, done, generate, pixels, scene  # noqa: F401


@pytest.fixture
def settings(base_settings):
    return replace(base_settings, draft_long_side=128)  # the 192x108 fixture gets real 128x72 drafts


def test_draft_size_scales_only_larger_sources():
    assert rendering.draft_size(3840, 2160) == (1920, 1080)
    assert rendering.draft_size(2160, 3840) == (1080, 1920)  # portrait phone video
    assert rendering.draft_size(1920, 1080) is None and rendering.draft_size(3840, 2160, 0) is None


def by_name(result):
    return {c['name']: c for c in result['candidates']}


def test_chosen_draft_is_rendered_in_full_and_delivered_without_another_review(settings, scene):
    planner = TargetCritic(gains=[.7], decisions=['revise', 'sufficient'])
    with TestClient(create_app(settings, planner, Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark atmosphere'))
        assert result['status'] == 'render_ready', result.get('error')
        v = by_name(result)
        assert set(v) == {'V1', 'V2', 'V2 4K'}
        assert v['V1']['tier'] == 'full' and (v['V1']['width'], v['V1']['height']) == (192, 108)
        assert v['V2']['tier'] == 'draft' and (v['V2']['width'], v['V2']['height']) == (128, 72)
        final = v['V2 4K']
        assert final['tier'] == 'full' and final['promoted_from'] == v['V2']['id'] and (final['width'], final['height']) == (192, 108)
        assert final['recipe']['proposal'] == v['V2']['recipe']['proposal']  # exactly the reviewed plan
        assert result['selected_candidate_id'] == final['id']
        assert planner.review_calls == 2 and planner.grade_calls == 1  # the full render is not re-reviewed or re-planned
        assert any(e['title'] == 'Rendering the chosen draft in full resolution' for e in result['events'])


def test_when_the_full_first_version_wins_nothing_extra_is_rendered(settings, scene):
    planner = TargetCritic(gains=[.9])  # revision moves the wrong way -> ineffective -> V1 selected
    with TestClient(create_app(settings, planner, Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark atmosphere'))
        assert result['status'] == 'render_ready', result.get('error')
        v = by_name(result)
        assert set(v) == {'V1', 'V2'} and v['V2']['tier'] == 'draft'
        assert result['selected_candidate_id'] == v['V1']['id']


def test_full_iteration_loop_uses_drafts_and_the_budget_ignores_the_final_render(settings, scene):
    planner = TargetCritic(gains=[.75, .65, .65], extra=['lifted_shadows'])  # measured choice picks V1 (see test_convergence)
    with TestClient(create_app(settings, planner, Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark atmosphere, keep shadow detail'))
        assert result['status'] == 'render_ready', result.get('error')
        v = by_name(result)
        assert [v[n]['tier'] for n in ['V1', 'V2', 'V3']] == ['full', 'draft', 'draft'] and planner.review_calls == 3
        assert result['selected_candidate_id'] == v['V1']['id'] and 'V3 4K' not in v


def test_a_draft_can_be_rendered_at_full_resolution_on_request(settings, scene):
    planner = TargetCritic(gains=[.9])
    with TestClient(create_app(settings, planner, Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark atmosphere'))
        draft = by_name(result)['V2']
        body = {'revision': result['revision'], 'command_id': str(uuid4()), 'candidate_id': draft['id']}
        assert client.post(f"/api/projects/{result['id']}/render-full", json=body).status_code == 202
        after = done(client, result['id'])
        final = by_name(after)['V2 4K']
        assert final['status'] == 'ready' and after['selected_candidate_id'] == final['id'] and planner.review_calls == 1
        again = client.post(f"/api/projects/{result['id']}/render-full",
                            json={'revision': after['revision'], 'command_id': str(uuid4()), 'candidate_id': draft['id']})
        assert again.status_code == 202 and len(again.json()['candidates']) == 3  # selects the existing one, no new render
        refused = client.post(f"/api/projects/{result['id']}/render-full",
                              json={'revision': again.json()['revision'], 'command_id': str(uuid4()), 'candidate_id': by_name(after)['V1']['id']})
        assert refused.status_code == 409 and refused.json()['error']['code'] == 'FULL_RENDER_UNAVAILABLE'


class LocalCritic(TargetCritic):
    """Revises the background layer (the base layer is unused when the interval covers the whole clip)."""

    def review(self, brief, candidate, data, previous=None, feedback=()):
        review, usage = super().review(brief, candidate, data, previous, feedback)
        if review.revision_plan is not None:
            from takeone.contracts import GradeParameters
            from tests.test_local_grading import parameters
            review.revision_plan.local.background = GradeParameters(**parameters(brightness_gain=.66, cooling=.12, saturation=.9))
        return review, usage


def test_localized_draft_lands_on_the_same_pixels_as_the_full_render(settings, scene):
    planner = LocalCritic(gains=[.7], decisions=['revise', 'sufficient'], local=True)
    with TestClient(create_app(settings, planner, Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark background, keep red subject'))
        assert result['status'] == 'render_ready', result.get('error')
        v = by_name(result)
        root = settings.workspace / result['id'] / 'candidates'
        draft, full = pixels(root / v['V2']['id'] / 'grade.mp4'), pixels(root / v['V2 4K']['id'] / 'grade.mp4')
        assert draft.shape[:2] == (72, 128) and full.shape[:2] == (108, 192)
        import cv2
        scaled = cv2.resize(full.astype(np.uint8), (128, 72), interpolation=cv2.INTER_AREA).astype(float)
        interior = (slice(10, 62), slice(10, 118))
        assert np.abs(draft[interior] - scaled[interior]).mean() < 3  # same grade, same mask placement
        assert v['V2']['validation']['subject_protection']['checked_subject_frames'] == 12


def test_drafts_can_be_disabled(settings, scene):
    planner = TargetCritic(gains=[.7], decisions=['revise', 'sufficient'])
    with TestClient(create_app(replace(settings, draft_long_side=0), planner, Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark atmosphere'))
        assert {c['tier'] for c in result['candidates']} == {'full'} and len(result['candidates']) == 2
