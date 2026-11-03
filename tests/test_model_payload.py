"""What Muse is shown: every decision-relevant fact once, no duplicated statistics, bounded size."""
import json
import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient

from takeone import creative_review, model_payload
from takeone.app import create_app
from takeone.contracts import CreativeReview, GradeParameters, GradeProposal
from takeone.provider import MuseProvider
from tests.test_backend import settings, start, upload  # noqa: F401
from tests.test_local_grading import Masks, Planner, generate, scene  # noqa: F401
from tests.test_convergence import TargetCritic


def tokens(value):
    return len(json.dumps(value, separators=(',', ':'))) // 4


def validation():
    frame = lambda i, med, p95: {'frame_index': i, 'p05_encoded_luma': .1, 'median_encoded_luma': med, 'p95_encoded_luma': p95,
                                 'median_rgb': [.5, .4, .3], 'mean_chroma': .1, 'luma_histogram': [1 / 16] * 16}
    return {'width': 192, 'height': 108, 'mode': 'localized', 'tier': 'full', 'warnings': ['Inspect edges near 1.2s.'],
            'subject_protection': {'worst_measured': {'subject_luma_ratio': 0.912345678}}, 'mask_validation': {'active_frames': 12},
            'source_samples': [frame(i, .45, .80) for i in range(8)], 'output_samples': [frame(i, .40, .90) for i in range(8)],
            'diagnostics': {'paired_measurements': [{'new_black_fraction': .012345, 'new_white_fraction': 0.}]},
            'timing': {'master': {'frames': list(range(500))}}, 'performance': {'encode_seconds': 180.0}, 'ffmpeg_version': 'ffmpeg 9',
            'execution_capabilities': {'operations': ['global_grade']}}


def test_technical_result_keeps_decisions_and_drops_duplicated_statistics():
    result = model_payload.technical_result(validation())
    assert not set(result) & model_payload.NOT_FOR_MODEL
    assert result['warnings'] == ['Inspect edges near 1.2s.'] and result['mask_validation'] == {'active_frames': 12}
    assert result['subject_protection']['worst_measured']['subject_luma_ratio'] == 0.9123
    change = result['measured_change_vs_source']
    assert change['midtones'] == -.05 and change['highlights'] == .1 and change['max_new_black_fraction'] == .0123
    assert tokens(result) < tokens(validation()) / 4


def test_rounding_touches_only_floats():
    assert model_payload.rounded({'a': 0.123456789, 'b': [1, 'x', 2.000049], 'c': None}) == {'a': .1235, 'b': [1, 'x', 2.0], 'c': None}


def test_a_rendered_revision_plan_is_sent_once():
    review = CreativeReview(decision='revise', summary='x', revision_plan=GradeProposal(
        parameters=GradeParameters(contrast=1., saturation=1., gamma=.9), summary='x', limitations=[],
        decisions=[{'parameter': 'gamma', 'reason': 'x', 'source_ids': ['user_request']}]))
    parent = SimpleNamespace(id='p', name='V1', brief_sha256='b', tier='full', recipe=None, validation=validation(), creative_review=review,
                             revision_stop_reason=None, error=None, status='ready', parent_id=None)
    child = SimpleNamespace(**{**vars(parent), 'id': 'c', 'name': 'V2', 'creative_review': None, 'parent_id': 'p'})
    rendered, unrendered = model_payload.previous_versions([parent, child]), model_payload.previous_versions([parent])
    assert rendered[0]['creative_opinion']['revision_plan'] is None and rendered[0]['creative_opinion']['revision_plan_rendered_as'] == 'V2'
    assert unrendered[0]['creative_opinion']['revision_plan'] is not None  # never rendered: still the only copy


def capture_sdk(captured, parsed):
    def parse(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed(kwargs)))], usage=None)

    class SDK:
        beta = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
        def __enter__(self): return self
        def __exit__(self, *_): pass
    return SDK


def test_real_critic_and_planner_payloads_carry_no_duplicates_and_stay_bounded(settings, scene):
    captured = []
    def parsed(kwargs):
        if kwargs['response_format'] is CreativeReview:
            return CreativeReview(decision='needs_user', summary='x')
        return GradeProposal(parameters=GradeParameters(contrast=1., saturation=1., gamma=1.), summary='x', limitations=[],
                             decisions=[{'parameter': 'contrast', 'reason': 'x', 'source_ids': ['user_request']}])
    planner = TargetCritic(gains=[.7], decisions=['revise', 'sufficient'], local=True)
    with TestClient(create_app(settings, planner, Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark background, keep red subject'))
        assert result['status'] == 'render_ready', result.get('error')
        service = client.app.state.service
        state = service.store.get(result['id'])
        candidate = next(c for c in state.candidates if c.status == 'ready' and c.validation)
        data, previous, _ = creative_review.collect(service, state, candidate, threading.Event())
        provider = MuseProvider(settings, service.prompt_gate)
        provider.client = lambda: capture_sdk(captured, parsed)()
        provider.review(state.brief, candidate, data, previous, [])
        technical = {'operations': ['global_grade'], 'previous_candidates': model_payload.previous_versions(state.candidates)}
        provider.grade(state.brief, state.analysis, state.source, candidate.recipe.source_samples, [], technical_context=technical)
    critic = json.loads(captured[0]['messages'][1]['content'][0]['text'])
    planner_ctx = json.loads(captured[1]['messages'][1]['content'][0]['text'])
    for dropped in ['source_samples', 'output_samples', 'timing', 'performance']:
        assert dropped not in critic['technical_result']
        assert f'"{dropped}"' not in json.dumps(planner_ctx['execution_capabilities']['previous_candidates'])
    assert critic['tonal_levels']['candidate'] and critic['paired_diagnostics'] and critic['technical_result']['measured_change_vs_source']
    assert critic['plan'] == candidate.recipe.proposal.model_dump(mode='json')  # plans are never rounded or altered
    versions = planner_ctx['execution_capabilities']['previous_candidates']
    assert versions and all(tokens(v) < 2500 for v in versions)  # compact; was ~5-7k each verbatim
    assert all(v['technical_result']['measured_change_vs_source'] for v in versions if v['technical_result'])


def test_requests_are_compact_json_with_identical_content(settings, scene):
    captured = []
    with TestClient(create_app(settings, Planner(local=True), Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark background, keep red subject'))
        service = client.app.state.service
        state = service.store.get(result['id'])
        candidate = state.candidates[-1]
        data, previous, _ = creative_review.collect(service, state, candidate, threading.Event())
        provider = MuseProvider(settings, service.prompt_gate)
        provider.client = lambda: capture_sdk(captured, lambda kw: CreativeReview(decision='needs_user', summary='x'))()
        provider.review(state.brief, candidate, data, previous, [])
    text = captured[0]['messages'][1]['content'][0]['text']
    assert text == json.dumps(json.loads(text), separators=(',', ':'))  # no whitespace beyond the content itself
    assert 'table_layout' not in json.loads(text)  # default row layout: the verified format, unchanged


def test_column_layout_carries_exactly_the_same_facts():
    records = [{'frame_index': 0, 'p95': .8, 'rgb': [.1, .2, .3]}, {'frame_index': 5, 'p95': .7, 'rgb': [.3, .2, .1], 'extra': 1}]
    table = model_payload.columns(records)
    assert table['columns'] == ['frame_index', 'p95', 'rgb', 'extra']
    assert model_payload.as_rows(table) == records
    context = {'tonal_levels': {'source': records, 'previous': None}, 'detail_retention': records,
               'paired_diagnostics': {'paired_measurements': records, 'sampled_temporal_changes': [], 'intent': {}}, 'brief': {'x': 1}}
    converted = model_payload.as_columns(context)
    assert model_payload.as_rows(converted['tonal_levels']['source']) == records and converted['tonal_levels']['previous'] is None
    assert model_payload.as_rows(converted['paired_diagnostics']['paired_measurements']) == records
    assert converted['brief'] == context['brief'] and converted['paired_diagnostics']['intent'] == {}


def test_column_layout_is_opt_in_and_self_describing(settings, scene):
    from dataclasses import replace
    captured = []
    with TestClient(create_app(settings, Planner(local=True), Masks()), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark background, keep red subject'))
        service = client.app.state.service
        state = service.store.get(result['id'])
        candidate = state.candidates[-1]
        data, previous, _ = creative_review.collect(service, state, candidate, threading.Event())
        provider = MuseProvider(replace(settings, critic_table_layout='columns'), service.prompt_gate)
        provider.client = lambda: capture_sdk(captured, lambda kw: CreativeReview(decision='needs_user', summary='x'))()
        provider.review(state.brief, candidate, data, previous, [])
    sent = json.loads(captured[0]['messages'][1]['content'][0]['text'])
    assert sent['table_layout'].startswith('columns:')
    assert model_payload.as_rows(sent['detail_retention']) == model_payload.rounded(data['detail_retention'])
