"""The critic starts while the output check runs; check warnings trigger a re-review with full results."""
from fastapi.testclient import TestClient

from takeone import local_grading
from takeone.app import create_app
from takeone.errors import Problem
from tests.test_backend import settings, start, upload  # noqa: F401
from tests.test_convergence import TargetCritic
from tests.test_drafts import LocalCritic
from tests.test_local_grading import Masks, generate, scene  # noqa: F401


class Recording(TargetCritic):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.validations = []

    def review(self, brief, candidate, data, previous=None, feedback=()):
        self.validations.append(dict(candidate.validation))
        return super().review(brief, candidate, data, previous, feedback)


class RecordingLocal(LocalCritic, Recording):
    pass


def titles(result):
    return [e['title'] for e in result['events']]


def test_critic_starts_before_the_output_check_has_finished(settings, scene):
    planner = Recording(gains=[.7], decisions=['sufficient'], local=True)
    with TestClient(create_app(settings, planner, Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark background, keep red subject'))
        assert result['status'] == 'render_ready', result.get('error')
        assert planner.review_calls == 1
        seen = planner.validations[0]
        assert 'subject_protection' not in seen and 'mask_validation' in seen  # early: masks known, check pending
        candidate = result['candidates'][0]
        assert candidate['creative_review']['decision'] == 'sufficient'
        assert 'subject_protection' in candidate['validation']  # the stored result still has the full check
        assert candidate['review_history'][0]['request_id']


def test_output_check_warnings_cause_a_re_review_with_the_full_results(settings, scene, monkeypatch):
    original = local_grading.check_output

    def warned(*args, **kwargs):
        result = original(*args, **kwargs)
        result['warnings'] = result['warnings'] + ['Fixture: inspect dark background detail.']
        return result
    monkeypatch.setattr(local_grading, 'check_output', warned)
    planner = Recording(gains=[.7], decisions=['sufficient', 'sufficient'], local=True)
    with TestClient(create_app(settings, planner, Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark background, keep red subject'))
        assert result['status'] == 'render_ready', result.get('error')
        assert planner.review_calls == 2  # early opinion discarded, asked again
        assert 'subject_protection' not in planner.validations[0] and 'subject_protection' in planner.validations[1]
        assert 'Fixture: inspect dark background detail.' in planner.validations[1]['subject_protection']['warnings']
        assert 'Re-reviewing with the output check results' in titles(result)


def test_no_early_call_for_a_revision_the_convergence_rule_will_stop(settings, scene):
    class WrongWay(RecordingLocal):
        def review(self, brief, candidate, data, previous=None, feedback=()):
            review, usage = Recording.review(self, brief, candidate, data, previous, feedback)
            if review.revision_plan is not None:  # brighten the background although darker midtones were asked for
                from takeone.contracts import GradeParameters
                from tests.test_local_grading import parameters
                review.revision_plan.local.background = GradeParameters(**parameters(brightness_gain=1.2))
            return review, usage
    planner = WrongWay(gains=[.7], local=True)
    with TestClient(create_app(settings, planner, Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark background, keep red subject'))
        assert result['status'] == 'render_ready', result.get('error')
        assert result['candidates'][1]['revision_stop_reason'] == 'revision_ineffective'
        assert planner.review_calls == 1  # the ineffective revision got no (early or normal) critic call


def test_a_failed_output_check_never_stores_the_early_opinion(settings, scene, monkeypatch):
    def failing(*args, **kwargs):
        raise Problem('LOCAL_PROTECTION_FAILED', 'Fixture protection failure.', 'Fixture.', 422)
    monkeypatch.setattr(local_grading, 'check_output', failing)
    planner = Recording(gains=[.7], decisions=['sufficient'], local=True)
    with TestClient(create_app(settings, planner, Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark background, keep red subject'))
        assert result['status'] == 'failed' and result['error']['code'] == 'LOCAL_PROTECTION_FAILED'
        assert result['candidates'][0]['creative_review'] is None and result['candidates'][0]['status'] == 'failed'
