"""Unfulfillable instructions are never a dead end: the user can render the supported part."""
from uuid import uuid4

from fastapi.testclient import TestClient

from takeone.app import create_app
from tests.test_backend import settings, start, upload  # noqa: F401
from tests.test_local_grading import Planner, done, generate, scene  # noqa: F401

OMITTED = ['Physical sun relighting with new cast shadows is outside the supported envelope.']


class Partial(Planner):
    def grade(self, *args, **kwargs):
        proposal, usage = super().grade(*args, **kwargs)
        proposal.unfulfilled_requests = list(OMITTED)
        return proposal, usage


def render_supported(client, state, candidate_id):
    return client.post(f"/api/projects/{state['id']}/render-supported",
                       json={'revision': state['revision'], 'command_id': str(uuid4()), 'candidate_id': candidate_id})


def test_unfulfilled_plan_offers_the_supported_part_and_renders_it_on_request(settings, scene):
    planner = Partial()
    with TestClient(create_app(settings, planner, None), base_url='http://127.0.0.1') as client:
        failed = generate(client, start(client, upload(client, scene), 'explicit dramatic grade with sun relighting'))
        assert failed['status'] == 'failed' and failed['error']['code'] == 'GRADE_REQUEST_UNFULFILLED'
        details = failed['error']['details']
        assert details['supported_plan_available'] and details['candidate_id'] == failed['candidates'][0]['id']
        assert 'Render the supported part' in failed['error']['action']
        response = render_supported(client, failed, details['candidate_id'])
        assert response.status_code == 202, response.text
        result = done(client, failed['id'])
        assert result['status'] == 'render_ready', result.get('error')
        first, second = result['candidates']
        assert second['status'] == 'ready' and second['parent_id'] == first['id']
        assert second['approved_omissions'] == OMITTED
        proposal = second['recipe']['proposal']
        assert proposal['unfulfilled_requests'] == [] and any(OMITTED[0] in item for item in proposal['limitations'])
        assert planner.grade_calls == 1  # the approved plan was rendered as-is, no second planner call
        titles = [e['title'] for e in result['events']]
        assert 'Rendering the supported part' in titles and 'Using the approved supported plan' in titles
        assert result['selected_candidate_id'] == second['id']


def test_supported_render_is_only_offered_for_an_unfulfilled_failure(settings, scene):
    with TestClient(create_app(settings, Planner(), None), base_url='http://127.0.0.1') as client:
        ready = generate(client, start(client, upload(client, scene), 'explicit gentle contrast'))
        assert ready['status'] == 'render_ready'
        response = render_supported(client, ready, ready['candidates'][0]['id'])
        assert response.status_code == 409 and response.json()['error']['code'] == 'SUPPORTED_PLAN_UNAVAILABLE'


def test_supported_render_cannot_be_approved_twice_for_the_same_failure_command(settings, scene):
    with TestClient(create_app(settings, Partial(), None), base_url='http://127.0.0.1') as client:
        failed = generate(client, start(client, upload(client, scene), 'explicit dramatic grade with sun relighting'))
        candidate = failed['error']['details']['candidate_id']
        command = str(uuid4())
        body = {'revision': failed['revision'], 'command_id': command, 'candidate_id': candidate}
        first = client.post(f"/api/projects/{failed['id']}/render-supported", json=body)
        assert first.status_code == 202
        again = client.post(f"/api/projects/{failed['id']}/render-supported", json=body)  # same command id: idempotent
        assert again.status_code == 202
        result = done(client, failed['id'])
        assert len(result['candidates']) == 2


def test_planner_prompt_reads_lighting_requests_by_their_executable_part(settings, scene):
    import inspect
    from takeone.provider import MuseProvider
    source = inspect.getsource(MuseProvider.grade)
    assert 'sun lighting' in source and 'physical part' in source and 'typos' in source
