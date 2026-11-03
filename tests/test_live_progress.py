"""Live stage progress: ffmpeg's measured encode position reaches the UI, never affects the run, never goes stale."""
import sys
import threading
from uuid import uuid4


from takeone import media
from tests.test_backend import FakeMuse, client, settings, upload, video  # noqa: F401

WRITER = r'''
import sys, time
path = sys.argv[1]
for step in range(1, 4):
    with open(path, "a") as log:
        log.write(f"frame={step * 10}\nout_time_us={step * 500000}\nspeed=1.5x\nprogress=continue\n")
    time.sleep(0.45)
'''


def run(tmp_path, on_progress):
    log = tmp_path / 'progress.log'
    media.command([sys.executable, '-c', WRITER, str(log)], threading.Event(), None, progress_path=log, on_progress=on_progress)


def test_command_reports_each_measured_position_once(tmp_path):
    seen = []
    run(tmp_path, seen.append)
    positions = [v['out_time_us'] for v in seen]
    assert positions and positions == sorted(set(positions), key=int)  # changes only, in order
    assert seen[-1]['speed'] == '1.5x' and int(seen[-1]['out_time_us']) == 1500000


def test_a_failing_observer_never_breaks_the_tool_run(tmp_path):
    calls = []
    def broken(values):
        calls.append(values)
        raise RuntimeError('observer bug')
    run(tmp_path, broken)  # completes normally
    assert len(calls) == 1  # dropped after its first failure


def test_no_observer_behaves_as_before(tmp_path):
    run(tmp_path, None)


def test_live_progress_is_only_returned_while_its_job_runs(settings, video):
    with client(settings, FakeMuse()) as c:
        service = c.app.state.service
        project = upload(c, video)['id']
        assert c.get(f'/api/projects/{project}/progress').json() == {'live': None}  # idle
        job = threading.Event()
        service._live[project] = {'cancel': job, 'stage': 'render_local_grade', 'kind': 'encode', 'fraction': .4, 'speed': 1.2, 'eta_seconds': 30.}
        assert c.get(f'/api/projects/{project}/progress').json() == {'live': None}  # no running job owns it
        service.cancels[project] = job
        live = c.get(f'/api/projects/{project}/progress').json()['live']
        assert live['fraction'] == .4 and live['stage'] == 'render_local_grade' and 'cancel' not in live
        service.cancels[project] = threading.Event()  # a different (later) job: the old measurement is not shown
        assert c.get(f'/api/projects/{project}/progress').json() == {'live': None}
        service.cancels.pop(project)
        assert c.get(f'/api/projects/{project}/progress').json() == {'live': None}
        assert c.get(f'/api/projects/{uuid4()}/progress').status_code == 404
