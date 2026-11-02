"""Revision convergence, metric-based final choice and the semantic mask check."""
import json
import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient

from takeone import convergence, creative_review
from takeone.app import create_app
from takeone.contracts import CreativeIssue, CreativeReview, GradeParameters
from takeone.provider import MuseProvider
from tests.test_backend import settings, start, upload  # noqa: F401
from tests.test_local_grading import Planner, Masks, generate, parameters, scene  # noqa: F401


def issue(target, id='x', correctable=True):
    return CreativeIssue(id=id, description='Fixture issue.', evidence_ids=['candidate:0'], correctable=correctable, target=target)


def records(**values):
    base = dict(p05_encoded_luma=.1, median_encoded_luma=.45, p95_encoded_luma=.85, median_rgb=[.5, .45, .4], mean_chroma=.1)
    return [{'frame_index': i, **base, **values} for i in range(3)]


# ---- unit ------------------------------------------------------------------------------------

def test_a_revision_that_moves_the_target_the_wrong_way_is_ineffective():
    result = convergence.revision_progress(records(p95_encoded_luma=.9), records(p95_encoded_luma=.85), [issue('darker_highlights')])
    assert result['ineffective'] and result['rows'][0]['progress'] < 0


def test_a_change_below_visibility_is_ineffective_and_a_visible_one_is_not():
    small = convergence.revision_progress(records(p95_encoded_luma=.84), records(p95_encoded_luma=.85), [issue('darker_highlights')])
    big = convergence.revision_progress(records(p95_encoded_luma=.8), records(p95_encoded_luma=.85), [issue('darker_highlights')])
    assert small['ineffective'] and not big['ineffective']


def test_any_visible_progress_on_one_targeted_issue_keeps_the_revision():
    result = convergence.revision_progress(records(p95_encoded_luma=.9, median_rgb=[.4, .45, .5]), records(),
                                           [issue('darker_highlights', 'sky'), issue('cooler', 'cast')])
    assert not result['ineffective']


def test_untargeted_or_uncorrectable_issues_never_trigger_the_rule():
    assert not convergence.revision_progress(records(), records(), [issue(None), issue('cooler', correctable=False)])['ineffective']


def test_records_without_a_metric_are_skipped_not_guessed():
    old = [{k: v for k, v in r.items() if k != 'mean_chroma'} for r in records()]
    assert convergence.revision_progress(old, records(), [issue('more_saturation')])['rows'] == []


def test_ranking_prefers_least_harm_over_recency():
    """Monsoon shape: every revision cooled more but pushed the sky further past the source."""
    source = records(p95_encoded_luma=.84, median_rgb=[.56, .47, .25])
    pool = [('V3', records(p95_encoded_luma=.94, median_rgb=[.41, .41, .25]), source),
            ('V2', records(p95_encoded_luma=.93, median_rgb=[.44, .43, .26]), source),
            ('V1', records(p95_encoded_luma=.91, median_rgb=[.46, .43, .26]), source)]
    ranked = convergence.rank_candidates(pool, [issue('darker_highlights', 'sky'), issue('cooler', 'cast')])
    assert ranked[0][0] == 'V1' and ranked[0][1]['regressions'] == 1


def test_ranking_ties_keep_the_supplied_order_latest_first():
    same = records()
    assert convergence.rank_candidates([('V2', same, same), ('V1', same, same)], [issue('cooler')])[0][0] == 'V2'


# ---- service ---------------------------------------------------------------------------------

class TargetCritic(Planner):
    """Each review asks for darker midtones; `gains` are the successive revision brightness gains."""

    def __init__(self, gains, decisions=None, extra=None, masks=None, local=False):
        super().__init__(local=local)
        self.gains, self.decisions, self.extra, self.masks = gains, decisions or [], extra or [], masks
        self.review_calls = 0
        self.seen = []

    def grade(self, *args, **kwargs):
        plan, usage = super().grade(*args, **kwargs)
        plan.review_required = True
        plan.parameters = GradeParameters(**parameters(brightness_gain=.85))
        return plan, usage

    def review(self, brief, candidate, data, previous=None, feedback=()):
        self.review_calls += 1
        self.seen.append(data)
        frame = 'candidate:' + str(data['candidate_images'][0]['frame_index'])
        decision = self.decisions[self.review_calls - 1] if self.review_calls <= len(self.decisions) else 'revise'
        revision = candidate.recipe.proposal.model_copy(deep=True)
        revision.parameters = GradeParameters(**parameters(brightness_gain=self.gains[min(self.review_calls - 1, len(self.gains) - 1)]))
        issues = [{'id': 'mid', 'description': 'Not dark enough.', 'evidence_ids': [frame], 'correctable': True, 'target': 'darker_midtones'},
                  *({'id': t, 'description': 'Fixture.', 'evidence_ids': [frame], 'correctable': True, 'target': t} for t in self.extra)]
        masks = self.masks(data) if self.masks else []
        return CreativeReview(decision=decision, summary='Fixture opinion.', issues=issues if decision == 'revise' else [],
                              revision_plan=revision if decision == 'revise' else None, mask_assessments=masks), {}


def event(result, title):
    return next((e for e in result['events'] if e['title'] == title), None)


def test_ineffective_revision_stops_before_another_critic_call_and_keeps_the_better_candidate(settings, scene):
    planner = TargetCritic(gains=[.9])  # asked for darker, revision brightens
    with TestClient(create_app(settings, planner, Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark atmosphere'))
        assert result['status'] == 'render_ready', result.get('error')
        first, second = result['candidates']
        assert planner.review_calls == 1  # the second render was measured, not sent to the critic
        assert second['revision_stop_reason'] == 'revision_ineffective'
        assert second['revision_progress']['ineffective'] and second['revision_progress']['rows'][0]['progress'] < 0
        assert result['selected_candidate_id'] == first['id']
        assert 'darker_midtones' in event(result, 'Revision did not move the requested issues')['details']['measured']


def test_effective_revision_continues_to_the_critic(settings, scene):
    planner = TargetCritic(gains=[.7], decisions=['revise', 'sufficient'])
    with TestClient(create_app(settings, planner, Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark atmosphere'))
        assert result['status'] == 'render_ready', result.get('error')
        assert planner.review_calls == 2 and len(result['candidates']) == 2
        assert result['candidates'][1]['revision_progress']['rows'][0]['visible_progress']
        assert result['selected_candidate_id'] == result['candidates'][1]['id']


def test_at_the_iteration_limit_the_final_candidate_is_chosen_by_measured_fit(settings, scene):
    # Every revision darkens midtones visibly (effective) but also crushes the subject's shadows further.
    planner = TargetCritic(gains=[.75, .65, .65], extra=['lifted_shadows'])
    with TestClient(create_app(settings, planner, Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark atmosphere, keep shadow detail'))
        assert result['status'] == 'render_ready', result.get('error')
        first, second, third = result['candidates']
        assert third['revision_stop_reason'] == 'render_iteration_limit' and planner.review_calls == 3
        assert result['selected_candidate_id'] == first['id']  # least harm, not the last render
        assert event(result, 'Candidate selected by measured fit')['details']['candidate'] == first['name']


def test_critic_receives_levels_and_per_concept_mask_overlays(settings, scene):
    planner = TargetCritic(gains=[.7], decisions=['sufficient'], local=True)
    with TestClient(create_app(settings, planner, Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark background, keep red subject'))
        assert result['status'] == 'render_ready', result.get('error')
    data = planner.seen[0]
    overlays = data['mask_overlay_images']
    assert overlays and {o['concept'] for o in overlays} == {'red subject'} and len(overlays) <= creative_review.OVERLAY_FRAMES_PER_CONCEPT
    assert all(o['url'].startswith('data:image/jpeg;base64,') and 0 < o['coverage'] < 1 for o in overlays)
    levels = data['tonal_levels']
    assert len(levels['source']) == len(levels['candidate']) and levels['candidate'][0]['p95_encoded_luma'] is not None
    assert levels['source'][0]['highlight_rgb'] is None or len(levels['source'][0]['highlight_rgb']) == 3
    assert all('render_brightest_luma' in d or 'brightest_region_fraction' not in d for d in data['detail_retention'])


def test_a_mask_the_critic_judges_wrong_stops_revisions_for_a_person(settings, scene):
    def wrong(data):
        overlay = data['mask_overlay_images'][0]
        return [{'concept': overlay['concept'], 'verdict': 'wrong_object', 'description': 'The tint covers a different boat.',
                 'evidence_ids': [f"mask_overlay_{overlay['overlay']}:{overlay['frame_index']}"]}]
    planner = TargetCritic(gains=[.7], masks=wrong, local=True)
    with TestClient(create_app(settings, planner, Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark background, keep red subject'))
        assert result['status'] == 'render_ready', result.get('error')
        assert len(result['candidates']) == 1 and planner.review_calls == 1
        assert result['candidates'][0]['revision_stop_reason'] == 'mask_needs_user_review'
        assert 'different boat' in event(result, 'Mask may not match its concept')['reason']


def test_a_mask_assessment_citing_unseen_evidence_is_rejected(settings, scene):
    def invented(data):
        return [{'concept': 'red subject', 'verdict': 'matches', 'description': 'Fine.', 'evidence_ids': ['mask_overlay_9:999']}]
    planner = TargetCritic(gains=[.7], masks=invented, local=True)
    with TestClient(create_app(settings, planner, Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark background, keep red subject'))
        assert result['candidates'][0]['review_error']['code'] == 'MUSE_INVALID_REVIEW'


def test_provider_sends_overlays_levels_and_target_instructions(settings, scene):
    captured = []

    def parse(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=CreativeReview(decision='needs_user', summary='x')))], usage=None)

    class SDK:
        beta = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
        def __enter__(self): return self
        def __exit__(self, *_): pass

    with TestClient(create_app(settings, Planner(local=True), Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark background, keep red subject'))
        service = client.app.state.service
        state = service.store.get(result['id'])
        candidate = state.candidates[-1]
        data, previous, _ = creative_review.collect(service, state, candidate, threading.Event())
        provider = MuseProvider(settings, service.prompt_gate)
        provider.client = lambda: SDK()
        provider.review(state.brief, candidate, data, previous, [])
    system = captured[0]['messages'][0]['content']
    content = captured[0]['messages'][1]['content']
    payload = json.loads(content[0]['text'])
    assert 'mask_assessments' in system and 'target' in system and 'tonal_levels' in system
    assert payload['mask_concepts'] == ['red subject'] and payload['tonal_levels']['candidate']
    assert any(p['type'] == 'text' and p['text'].startswith('mask_overlay_0:') and 'red subject' in p['text'] for p in content)
