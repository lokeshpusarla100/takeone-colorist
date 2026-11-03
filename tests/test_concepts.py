"""SAM concept phrasing: a descriptive planner phrase that matches nothing falls back to simpler ones."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from takeone.app import create_app
from takeone.concepts import MAX_ATTEMPTS, candidates
from takeone.contracts import GradeParameters, LocalizedPlan, RegionTreatment
from tests.test_backend import settings, start, upload  # noqa: F401
from tests.test_local_grading import Planner, done, generate, parameters, scene  # noqa: F401
from tests.test_regions import BOXES, ConceptMasks, DARK_COOL


class LiteralMasks(ConceptMasks):
    """Like SAM: an unknown phrase completes normally with no masks at all."""

    def segment(self, path, concept, *args):
        if concept in self.boxes:
            return super().segment(path, concept, *args)
        self.calls.append(concept)
        remote, remember, cancel, progress, raw_path, on_usage, on_request = args
        on_request()
        remember('fixture-upload')
        from tests.test_sam import parsed
        result = parsed([])
        raw_path.write_text(result.raw_output)
        progress(result)
        on_usage({'video_frames_processed': 12})
        return result


class PhrasePlanner(Planner):
    def __init__(self, concept, alternatives=(), regions=()):
        super().__init__(local=True)
        self.concept, self.alternatives, self.regions = concept, list(alternatives), list(regions)

    def grade(self, *args, **kwargs):
        plan, usage = super().grade(*args, **kwargs)
        plan.local = LocalizedPlan.model_validate({**plan.local.model_dump(), 'concept': self.concept,
            'concept_alternatives': self.alternatives,
            'regions': [RegionTreatment(concept=c, treatment=GradeParameters(**DARK_COOL)).model_dump() for c in self.regions]})
        return plan, usage


@pytest.mark.parametrize('concept,expected', [
    ('dark coastal rocks forming natural arch', ['dark coastal rocks forming natural arch', 'dark coastal rocks', 'rocks']),
    ('pale hazy sky above horizon', ['pale hazy sky above horizon', 'pale hazy sky', 'sky']),
    ('ocean sea water with waves and foam', ['ocean sea water with waves and foam', 'ocean sea water', 'water']),
    ('person in red jacket', ['person in red jacket', 'person']),
    ('sky', ['sky']),
])
def test_fallback_phrases_follow_noun_phrase_structure(concept, expected):
    assert candidates(concept) == expected


def test_planner_alternatives_come_first_and_attempts_are_bounded():
    assert candidates('rock arch', ['rocks', 'rock', 'stone']) == ['rock arch', 'rocks', 'rock']
    assert len(candidates('a b c with d', ['e', 'f', 'g'])) == MAX_ATTEMPTS


def test_alternatives_must_be_single_phrases():
    with pytest.raises(ValueError):
        RegionTreatment(concept='sky', treatment=GradeParameters(contrast=1., saturation=1., gamma=1., highlight_ceiling=.7),
                        concept_alternatives=['sky, clouds'])


def test_a_descriptive_subject_phrase_falls_back_to_one_sam_can_match(settings, scene):
    masks = LiteralMasks()
    planner = PhrasePlanner('red subject forming a block in the centre')
    with TestClient(create_app(settings, planner, masks), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark background, keep the red block'))
        assert result['status'] == 'render_ready', result.get('error')
        assert masks.calls == ['red subject forming a block in the centre', 'red subject']
        validation = result['candidates'][-1]['validation']
        assert validation['mask_validation']['subject_concept_used'] == 'red subject'
        assert any(e['title'] == 'Using a simpler SAM phrase' for e in result['events'])
        assert validation['subject_protection']['checked_subject_frames'] == 12


def test_planner_alternatives_are_tried_before_the_heuristic(settings, scene):
    masks = LiteralMasks()
    with TestClient(create_app(settings, PhrasePlanner('crimson thing', ['red subject']), masks), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark background'))
        assert result['status'] == 'render_ready', result.get('error')
        assert masks.calls == ['crimson thing', 'red subject']


def test_region_phrases_fall_back_too(settings, scene):
    masks = LiteralMasks()
    planner = PhrasePlanner('red subject', regions=['pale hazy sky above horizon'])
    with TestClient(create_app(settings, planner, masks), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark sky, keep subject'))
        assert result['status'] == 'render_ready', result.get('error')
        region = result['candidates'][-1]['validation']['mask_validation']['regions'][0]
        assert region['concept'] == 'pale hazy sky above horizon' and region['concept_used'] == 'sky'
        # Subject and region requests run concurrently (sam_parallel): only each area's own fallback order is fixed.
        assert sorted(masks.calls) == sorted(['red subject', 'pale hazy sky above horizon', 'pale hazy sky', 'sky'])
        assert [c for c in masks.calls if c != 'red subject'] == ['pale hazy sky above horizon', 'pale hazy sky', 'sky']


def test_nothing_found_is_a_clear_error_and_a_retry_does_not_rebill(settings, scene):
    masks = LiteralMasks()
    with TestClient(create_app(settings, PhrasePlanner('unicorn standing on a cloud'), masks), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit unicorn'))
        assert result['status'] == 'failed' and result['error']['code'] == 'LOCAL_CONCEPT_NOT_FOUND'
        assert len(masks.calls) == MAX_ATTEMPTS and '"unicorn standing on a cloud"' in result['error']['message']
        assert 'short common noun' in result['error']['action']
        retry = client.post('/api/projects/' + result['id'] + '/retry', json={'revision': result['revision'], 'command_id': str(uuid4())})
        assert retry.status_code == 202
        again = done(client, result['id'])
        assert again['error']['code'] == 'LOCAL_CONCEPT_NOT_FOUND' and len(masks.calls) == MAX_ATTEMPTS  # cached empty runs reused


def test_planner_prompt_states_sam_phrasing_rules():
    import inspect
    from takeone.provider import MuseProvider
    source = inspect.getsource(MuseProvider.grade)
    assert 'SHORT common noun phrases' in source and 'concept_alternatives' in source and 'all_matches for materials' in source


# ---- plural / material phrases and the explicit "grade all matches" choice -------------------

from takeone.concepts import names_a_class  # noqa: E402


@pytest.mark.parametrize('concept,expected', [
    ('dark coastal rocks', True), ('rocks', True), ('water', True), ('pale hazy sky', True), ('boats', True), ('people', True),
    ('boat', False), ('person', False), ('moving yellow-canopy passenger boat', False), ('yellow canopy', False), ('red subject', False),
    ('glass', False), ('bus', False),
])
def test_class_phrases_are_plural_or_material_heads(concept, expected):
    assert names_a_class(concept) is expected


class TwoObjectMasks(LiteralMasks):
    """SAM returning two separate objects for some phrases (like four rocks for 'dark coastal rocks')."""

    def __init__(self, two=('rocks', 'block')):
        super().__init__({**BOXES, **{name: (60, 24, 131, 83) for name in two}})
        self.two = set(two)

    def segment(self, path, concept, *args):
        if concept not in self.two:
            return super().segment(path, concept, *args)
        self.calls.append(concept)
        remote, remember, cancel, progress, raw_path, on_usage, on_request = args
        on_request()
        remember('fixture-upload')
        from meta_sam_parser._mask_codec import _encode_raster
        from tests.test_sam import parsed

        def record(x1, y1, x2, y2):
            w, h = x2 - x1 + 1, y2 - y1 + 1
            return (f'<|box;x1={x1};y1={y1};x2={x2};y2={y2};w=192;h=108|>'
                    f'<|mask;x=0;y=0;data={h},{w},{_encode_raster(bytes([1]) * w * h, w, h)}|>')
        lines = [f'<{i}f>0' + record(60, 24, 131, 83) + ',1' + record(150, 70, 185, 100) for i in range(12)]
        result = parsed(lines)
        raw_path.write_text(result.raw_output)
        progress(result)
        on_usage({'video_frames_processed': 12})
        return result


def test_a_plural_phrase_grades_every_match_instead_of_failing_as_ambiguous(settings, scene):
    masks = TwoObjectMasks()
    planner = PhrasePlanner('rocks')  # Planner's plan says single_instance
    with TestClient(create_app(settings, planner, masks), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit make the rocks warm'))
        assert result['status'] == 'render_ready', result.get('error')
        validation = result['candidates'][-1]['validation']['mask_validation']
        assert validation['selection_policy'] == 'all_matches' and validation['selected_objects_by_interval'][0]['object_ids'] == ['0', '1']
        event = next(e for e in result['events'] if e['title'] == 'Using every match of a class phrase')
        assert event['details']['object_count'] == 2


def test_an_ambiguous_singular_phrase_offers_grade_all_matches_and_resumes_without_rebilling(settings, scene):
    masks = TwoObjectMasks()
    with TestClient(create_app(settings, PhrasePlanner('block'), masks), base_url='http://127.0.0.1') as client:
        failed = generate(client, start(client, upload(client, scene), 'explicit warm the block'))
        assert failed['status'] == 'failed' and failed['error']['code'] == 'LOCAL_SUBJECT_AMBIGUOUS'
        details = failed['error']['details']
        assert details['all_matches_available'] and details['object_count'] == 2 and details['concept'] == 'block'
        assert 'Grade all matches' in failed['error']['action']
        calls = len(masks.calls)
        response = client.post(f"/api/projects/{failed['id']}/use-all-matches",
                               json={'revision': failed['revision'], 'command_id': str(uuid4()), 'candidate_id': details['candidate_id']})
        assert response.status_code == 202, response.text
        result = done(client, failed['id'])
        assert result['status'] == 'render_ready', result.get('error')
        candidate = result['candidates'][-1]
        assert candidate['id'] == details['candidate_id'] and len(result['candidates']) == 1  # same version, no extra budget
        assert candidate['recipe']['proposal']['local']['selection'] == 'all_matches'
        assert candidate['recipe']['execution_adjustment']['kind'] == 'user_selected_all_matches'
        assert len(masks.calls) == calls  # cached masks reused, SAM not billed again
        assert any(e['title'] == 'Grading all matches' for e in result['events'])


def test_grade_all_matches_is_refused_without_an_ambiguous_failure(settings, scene):
    with TestClient(create_app(settings, PhrasePlanner('red subject'), LiteralMasks()), base_url='http://127.0.0.1') as client:
        ready = generate(client, start(client, upload(client, scene), 'explicit dark background'))
        assert ready['status'] == 'render_ready'
        response = client.post(f"/api/projects/{ready['id']}/use-all-matches",
                               json={'revision': ready['revision'], 'command_id': str(uuid4()), 'candidate_id': ready['candidates'][0]['id']})
        assert response.status_code == 409 and response.json()['error']['code'] == 'ALL_MATCHES_UNAVAILABLE'
