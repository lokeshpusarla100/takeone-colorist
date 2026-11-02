import time
from uuid import uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient

from takeone.app import create_app
from tests.test_backend import settings, upload, start
from tests.test_local_grading import Planner, Masks, generate, scene


def wait_done(c, project_id):
    for _ in range(1500):
        state = c.get('/api/projects/' + project_id).json()
        if state['status'] not in {'analyzing', 'rendering', 'segmenting', 'reviewing', 'ingesting'}:
            return state
        time.sleep(.02)
    raise AssertionError('looks job did not finish')


def make_looks(c, state):
    result = c.post('/api/projects/' + state['id'] + '/looks',
                    json={'command_id': str(uuid4()), 'revision': state['revision']})
    assert result.status_code == 202, result.text
    return wait_done(c, state['id'])


def select(c, state, look_id):
    result = c.post('/api/projects/' + state['id'] + '/looks/select',
                    json={'command_id': str(uuid4()), 'revision': state['revision'], 'look_id': look_id})
    assert result.status_code == 200, result.text
    return result.json()


def test_eight_brief_derived_previews_on_a_representative_sampled_frame(settings, scene):
    planner = Planner()
    with TestClient(create_app(settings, planner, Masks()), base_url='http://127.0.0.1') as c:
        state = start(c, upload(c, scene), 'explicit increase overall contrast')
        brief_before = state['brief']
        done = make_looks(c, state)
        assert done['status'] == 'brief_ready', done.get('error')
        assert done['brief'] == brief_before
        looks = done['looks']
        assert len(looks) == 8
        assert looks[0]['name'] == 'Brief look'
        assert len({tuple(sorted(l['proposal']['parameters'].items())) for l in looks}) == 8
        assert all(l['brief_sha256'] == done['brief_sha256'] for l in looks)
        assert all(l['frame_index'] == done['looks_frame_index'] for l in looks)
        # Representative frame comes from the adaptive evidence selection.
        import json as _json
        selection = _json.loads((settings.workspace / done['id'] / 'source-evidence-v3.json').read_text())
        assert done['looks_frame_index'] in selection['selected_indices']
        assert abs(looks[0]['frame_time'] - selection['manifest']['times'][done['looks_frame_index']]) < 1e-6
        for look in looks:
            image = c.get(look['image_url'])
            assert image.status_code == 200 and image.headers['content-type'] == 'image/jpeg'
            assert len(image.content) > 500
        assert planner.grade_calls == 1


def test_selection_persists_look_records_are_immutable_and_reselection_moves_pointer(settings, scene):
    with TestClient(create_app(settings, Planner(), Masks()), base_url='http://127.0.0.1') as c:
        state = make_looks(c, start(c, upload(c, scene), 'explicit neutral'))
        looks = state['looks']
        chosen = select(c, state, looks[3]['id'])
        assert chosen['selected_look_id'] == looks[3]['id']
        again = c.get('/api/projects/' + state['id']).json()
        assert again['selected_look_id'] == looks[3]['id'] and again['looks'] == looks
        other = select(c, again, looks[5]['id'])
        assert other['selected_look_id'] == looks[5]['id'] and other['looks'] == looks


def test_selected_look_is_the_exact_render_recipe_without_a_second_planner_call(settings, scene):
    planner = Planner()
    with TestClient(create_app(settings, planner, Masks()), base_url='http://127.0.0.1') as c:
        state = make_looks(c, start(c, upload(c, scene), 'explicit increase overall contrast'))
        look = state['looks'][2]
        chosen = select(c, state, look['id'])
        result = generate(c, chosen)
        assert result['status'] == 'render_ready', result.get('error')
        candidate = result['candidates'][-1]
        assert candidate['recipe']['proposal'] == look['proposal']
        assert planner.grade_calls == 1
        assert any(e['title'] == 'Using selected look' for e in result['events'])


def test_render_without_selection_still_uses_the_planner(settings, scene):
    planner = Planner()
    with TestClient(create_app(settings, planner, Masks()), base_url='http://127.0.0.1') as c:
        state = start(c, upload(c, scene), 'explicit increase overall contrast')
        result = generate(c, state)
        assert result['status'] == 'render_ready', result.get('error')
        assert planner.grade_calls == 1


def test_localized_plan_without_cached_masks_shows_only_feasible_global_looks(settings, scene):
    planner = Planner(local=True)
    with TestClient(create_app(settings, planner, Masks()), base_url='http://127.0.0.1') as c:
        state = make_looks(c, start(c, upload(c, scene), 'explicit dark cool background, preserve red subject'))
        assert state['status'] == 'brief_ready', state.get('error')
        assert len(state['looks']) == 8
        assert all(l['proposal']['mode'] == 'global' and not l['uses_local'] for l in state['looks'])
        assert state['looks_limitation'] and 'SAM masks' in state['looks_limitation']


def test_localized_looks_use_cached_sam_mask_when_one_covers_the_frame(settings, scene):
    planner = Planner(local=True); masks = Masks()
    with TestClient(create_app(settings, planner, masks), base_url='http://127.0.0.1') as c:
        state = start(c, upload(c, scene), 'explicit dark cool background, preserve red subject')
        rendered = generate(c, state)
        assert rendered['status'] == 'render_ready', rendered.get('error')
        assert masks.calls == 1
        done = make_looks(c, rendered)
        assert done['status'] == 'render_ready', done.get('error')
        assert len(done['looks']) == 8
        assert all(l['uses_local'] and l['proposal']['mode'] == 'localized' for l in done['looks'])
        assert done['looks_limitation'] is None
        look = done['looks'][1]
        chosen = select(c, done, look['id'])
        result = generate(c, chosen)
        assert result['status'] == 'render_ready', result.get('error')
        assert result['candidates'][-1]['recipe']['proposal'] == look['proposal']
        assert masks.calls == 1  # cached masks reused; no new SAM request for previews or render
