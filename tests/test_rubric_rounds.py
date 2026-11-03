"""Batch consultation: a round's independent questions are shown and answered together; Muse reconsiders once
per round. One-at-a-time sessions, saved sessions and the closing-call prediction keep working."""
from dataclasses import replace
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from takeone_rubric_v1 import muse_adapter
from takeone_rubric_v1.rubric import Answer, Candidate, Option, Proposal, RubricSession
from takeone_rubric_v1.test_rubric import question, session
from tests.test_backend import FakeMuse, client, settings, upload, video, wait  # noqa: F401


def q(id, family, **kw):
    return question(id=id, target_key=family, preference_family=family, decision_id=id,
                    question=f'{family} choice?', **kw)


def batch_session():
    s = session()
    s.batch = True
    return s


def test_a_batch_round_is_shown_together_and_answered_in_order():
    s = batch_session()
    first = s.next_question(Proposal(candidates=[q('a', 'tonal_readability'), q('b', 'color_intensity'), q('c', 'subject_priority')]))
    assert first.id == 'a' and [x.id for x in s.pending_questions()] == ['a', 'b', 'c'] and s.asked_count == 3
    with pytest.raises(RuntimeError):
        s.next_question(Proposal())  # no model call while the round is open
    s.record_answer(Answer(question_id='a', response_type='option', response='readable'))
    assert s.pending.id == 'b'
    restored = RubricSession.restore(s.snapshot())  # mid-round save/restore keeps the rest of the round
    assert restored.batch and [x.id for x in restored.pending_questions()] == ['b', 'c']
    restored.record_answer(Answer(question_id='b', response_type='skip'))
    restored.record_answer(Answer(question_id='c', response_type='free_text', response='Keep the bike bright'))
    assert restored.pending is None and not restored.queued
    brief = restored.brief()
    assert [a.question_id for a in brief.answers] == ['a', 'b', 'c'] and brief.delegated_or_skipped == ['b']


def test_a_round_is_cut_to_the_budget_in_muse_order():
    s = batch_session()
    s.max_decisions = 2
    s.next_question(Proposal(candidates=[q('a', 'tonal_readability'), q('b', 'color_intensity'), q('c', 'subject_priority')]))
    assert [x.id for x in s.pending_questions()] == ['a', 'b']


@pytest.mark.parametrize('second, message', [
    (dict(id='a', family='color_intensity'), 'unique'),
    (dict(id='b', family='color_intensity', decision_id='a'), 'Decision ID asked twice'),
])
def test_round_mates_follow_the_same_data_rules(second, message):
    s = batch_session()
    family = second.pop('family')
    decision = second.pop('decision_id', None)
    mate = q(second['id'], family)
    if decision:
        mate = mate.model_copy(update={'decision_id': decision})
    with pytest.raises(ValueError, match=message):
        s.next_question(Proposal(candidates=[q('a', 'tonal_readability'), mate]))
    assert s.pending is None and not s.queued and s.asked_count == 0  # nothing half-applied


def test_identical_questions_in_one_round_are_rejected():
    s = batch_session()
    twin = q('a', 'tonal_readability').model_copy(update={'id': 'b', 'decision_id': 'b'})
    with pytest.raises(ValueError, match='repeat another question in this round'):
        s.next_question(Proposal(candidates=[q('a', 'tonal_readability'), twin]))


def test_one_at_a_time_still_takes_only_the_first_candidate():
    s = session()
    s.next_question(Proposal(candidates=[q('a', 'tonal_readability'), q('b', 'color_intensity')]))
    assert [x.id for x in s.pending_questions()] == ['a'] and s.asked_count == 1


def test_sessions_saved_before_batching_restore_as_one_at_a_time():
    s = session()
    s.next_question(Proposal(candidates=[question()]))
    old = s.snapshot()
    old.pop('batch'), old.pop('queued')
    restored = RubricSession.restore(old)
    assert restored.batch is False and restored.pending.id == 'q1' and restored.queued == []


def captured_request(s):
    captured = {}
    def parse(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=Proposal()))], usage=None)
    muse_adapter.propose(s, client=SimpleNamespace(beta=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))))
    return captured['messages'][0]['content'], json.loads(captured['messages'][1]['content'])


def test_the_request_carries_every_answer_once_and_the_mode_instructions():
    s = batch_session()
    s.next_question(Proposal(candidates=[q('a', 'tonal_readability')]))
    s.record_answer(Answer(question_id='a', response_type='option', response='readable'))
    system, context = captured_request(s)
    assert 'answers' not in context and 'previously_addressed_keys' not in context
    decision = context['previous_decisions'][0]
    assert (decision['question_id'], decision['response_type'], decision['response'], decision['target_key']) == \
        ('a', 'option', 'readable', 'tonal_readability')  # everything the removed duplicates carried
    assert 'every consequential question that is independent' in system and 'zero or ONE' not in system
    one_system, _ = captured_request(session())
    assert 'zero or ONE' in one_system and 'legacy-compatible' not in one_system
    assert '\nand memory_used_ids' not in one_system and 'Use memory_used_ids only' in one_system


class RoundMuse(FakeMuse):
    """First round: two independent questions. Second round: Muse reconsiders both answers once."""
    def __init__(self, predict=False):
        super().__init__()
        self.predict, self.seen = predict, []

    def propose(self, session):
        self.proposal_calls += 1
        self.seen.append([d['question_id'] for d in session.answered_decisions()])
        if session.answers:
            return Proposal(completion_reason='Both choices settle the edit.')
        option = lambda i, o: Option(id=i, label=i.title(), intended_outcome=o, required_operations=[self.operation])
        ask = lambda id, family, text: Candidate(id=id, dimension='appearance', target_key=family, preference_family=family,
            scope='whole clip', question=text, evidence_ids=['obs1'], uncertainty_type='preference', decision_id=id,
            decision_fork='Different global result.', impact='high', preservation_risk=False, answerability='high',
            options=[option('low', 'Restrained ' + family), option('high', 'Strong ' + family)])
        return Proposal(candidates=[ask('q1', 'tonal_readability', 'How dark?'), ask('q2', 'color_intensity', 'How colorful?')],
                        final_if_options_chosen=self.predict,
                        completion_reason_if_final='Darkness and color intensity were the only open choices.' if self.predict else '')


def begin(c, video, request='Make it cinematic'):
    state = upload(c, video)
    response = c.post(f"/api/projects/{state['id']}/analyze", json={'command_id': str(uuid4()), 'revision': state['revision'], 'request': request})
    assert response.status_code == 202, response.text
    return wait(c, state['id'])


def answer_round(c, state, answers):
    return c.post(f"/api/projects/{state['id']}/answers/round", json={'command_id': str(uuid4()), 'revision': state['revision'], 'answers': answers})


def test_api_round_one_call_per_round_with_every_answer(settings, video):
    muse = RoundMuse()
    with client(replace(settings, max_questions=5), muse) as c:
        first = begin(c, video)
        assert first['status'] == 'awaiting_answer' and [x['id'] for x in first['pending_questions']] == ['q1', 'q2']
        assert first['question']['id'] == 'q1' and muse.proposal_calls == 1
        partial = answer_round(c, first, [{'question_id': 'q1', 'response_type': 'option', 'response': 'low'}])
        assert partial.status_code == 422 and muse.proposal_calls == 1  # the whole round, or nothing
        assert wait(c, first['id'])['answered_count'] == 0
        response = answer_round(c, first, [{'question_id': 'q2', 'response_type': 'option', 'response': 'high'},
                                           {'question_id': 'q1', 'response_type': 'delegate', 'response': ''}])
        assert response.status_code == 202, response.text
        done = wait(c, first['id'])
        assert done['status'] == 'brief_ready' and done['pending_questions'] == [] and done['answered_count'] == 2
        assert muse.proposal_calls == 2 and muse.seen[1] == ['q1', 'q2']  # one reconsideration, with both answers, in round order
        assert [a['question_id'] for a in done['brief']['answers']] == ['q1', 'q2']
        assert [e['details']['question_id'] for e in done['events'] if e.get('kind') == 'decision'] == ['q1', 'q2']


def test_single_answers_also_work_in_a_batch_round(settings, video):
    muse = RoundMuse()
    with client(replace(settings, max_questions=5), muse) as c:
        state = begin(c, video)
        for question_id in ['q1', 'q2']:
            response = c.post(f"/api/projects/{state['id']}/answers", json={'command_id': str(uuid4()), 'revision': state['revision'],
                'answer': {'question_id': question_id, 'response_type': 'option', 'response': 'low'}})
            assert response.status_code == 202, response.text
            state = wait(c, state['id'])
            if question_id == 'q1':
                assert state['status'] == 'awaiting_answer' and state['question']['id'] == 'q2' and muse.proposal_calls == 1
        assert state['status'] == 'brief_ready' and muse.proposal_calls == 2


def test_one_at_a_time_mode_is_unchanged(settings, video):
    muse = RoundMuse()
    with client(replace(settings, max_questions=5, rubric_mode='one_at_a_time'), muse) as c:
        state = begin(c, video)
        assert [x['id'] for x in state['pending_questions']] == ['q1']


@pytest.mark.parametrize('skip', [False, True])
def test_closing_prediction_is_recorded_and_the_skip_is_opt_in(settings, video, skip):
    muse = RoundMuse(predict=True)
    with client(replace(settings, max_questions=5, rubric_skip_closing=skip), muse) as c:
        state = begin(c, video)
        answers = [{'question_id': i, 'response_type': 'option', 'response': 'low'} for i in ['q1', 'q2']]
        assert answer_round(c, state, answers).status_code == 202
        done = wait(c, state['id'])
        assert done['status'] == 'brief_ready'
        service = c.app.state.service
        proposals = service.store.get(state['id']).question_proposals
        if skip:
            assert muse.proposal_calls == 1 and len(proposals) == 1
            assert done['consultation']['model_explanation'] == 'Darkness and color intensity were the only open choices.'
        else:
            assert muse.proposal_calls == 2
            assert proposals[-1]['closing_prediction'] == {'predicted_final': True, 'actual_final': True}


def test_a_free_text_answer_always_gets_the_closing_call(settings, video):
    muse = RoundMuse(predict=True)
    with client(replace(settings, max_questions=5, rubric_skip_closing=True), muse) as c:
        state = begin(c, video)
        answers = [{'question_id': 'q1', 'response_type': 'option', 'response': 'low'},
                   {'question_id': 'q2', 'response_type': 'free_text', 'response': 'Colorful but keep skin natural'}]
        assert answer_round(c, state, answers).status_code == 202
        assert wait(c, state['id'])['status'] == 'brief_ready' and muse.proposal_calls == 2
