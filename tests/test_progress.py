from uuid import uuid4

from takeone.contracts import State
from takeone.store import Store


def markers(state):return [e for e in state.events if e.details.get('progress_timing')]


def test_stage_timestamps_persist_without_polling_duplicates(tmp_path,monkeypatch):
    clock=['2026-11-02T10:00:00+00:00']
    monkeypatch.setattr('takeone.store.now',lambda:clock[0])
    store=Store(tmp_path);state=State(id=str(uuid4()),filename='test.mp4',status='ingesting',stage='inspect_source',active_job='job1')
    store.save(state)
    clock[0]='2026-11-02T10:00:03+00:00';store.save(state)
    assert len(markers(state))==1
    state.stage='prepare_preview';store.save(state)
    clock[0]='2026-11-02T10:00:08+00:00';state.status='ready';state.stage='ready';state.active_job=None;store.save(state)
    persisted=markers(store.get(state.id))
    assert [e.details['progress_stage'] for e in persisted]==['inspect_source','prepare_preview','ready']
    assert [e.timestamp for e in persisted]==['2026-11-02T10:00:00+00:00','2026-11-02T10:00:03+00:00','2026-11-02T10:00:08+00:00']
    clock[0]='2026-11-02T11:00:00+00:00';store.save(state)
    assert len(markers(store.get(state.id)))==3
    state.active_job='job2';state.status='analyzing';state.stage='select_question';store.save(state)
    assert markers(store.get(state.id))[-1].details['progress_job']=='job2'


def test_restart_closes_timing_at_last_durable_update_not_downtime(tmp_path,monkeypatch):
    monkeypatch.setattr('takeone.store.now',lambda:'2026-11-02T10:00:00+00:00')
    store=Store(tmp_path);state=State(id=str(uuid4()),filename='test.mp4',status='rendering',stage='render_grade',active_job='job1')
    store.save(state)
    monkeypatch.setattr('takeone.store.now',lambda:'2026-11-02T10:00:05+00:00');store.save(state)
    monkeypatch.setattr('takeone.store.now',lambda:'2026-11-02T12:00:00+00:00')
    state.active_job=None;state.status='failed';state.error={'code':'JOB_INTERRUPTED'};store.save(state)
    end=markers(store.get(state.id))[-1]
    assert end.timestamp=='2026-11-02T10:00:05+00:00'
    assert end.details['progress_interrupted'] is True and end.details['progress_active'] is False


def test_public_question_history_preserves_user_choices_and_response_types():
    state=State(id=str(uuid4()),filename='test.mp4',status='brief_ready',stage='brief_ready',rubric={
        'questions':[{'id':'q1','question':'How much contrast?','options':[{'id':'low','label':'Soft contrast'}]}],
        'answers':[{'question_id':'q1','response_type':'option','response':'low'},
                   {'question_id':'q2','response_type':'free_text','response':'Keep the red paint'},
                   {'question_id':'q3','response_type':'delegate','response':''},
                   {'question_id':'q4','response_type':'skip','response':''}]})
    public=state.public()
    assert 'rubric' not in public
    assert [a['answer'] for a in public['rubric_history']]==['Soft contrast','Keep the red paint','Delegated to Muse','Skipped']
    assert public['rubric_history'][0]['question']=='How much contrast?'


def test_retry_marker_retains_job_and_retry_identity(tmp_path):
    from takeone.contracts import Event
    store=Store(tmp_path)
    state=State(id=str(uuid4()),filename='test.mp4',status='rendering',stage='plan_grade',active_job='retry-job')
    state.events.append(Event(title='Grade queued',details={'progress_job':'retry-job','progress_retry':True}))
    store.save(state)
    marker=markers(store.get(state.id))[-1]
    assert marker.details['progress_retry'] is True
    assert marker.details['progress_job']=='retry-job'
