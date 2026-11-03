"""The frame-timestamp probe runs in the background once the source is ready; stages that need it share that one run."""
import json
import threading
import time

import pytest

from takeone import frames
from takeone.config import Settings
from takeone.errors import Problem
from tests.test_backend import FakeMuse, client, settings, upload, video  # noqa: F401


@pytest.fixture
def media_file(tmp_path):
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'not really media; the probe command is faked')
    frames._memory.clear()
    yield path
    frames._memory.clear()


def fake_probe(monkeypatch, delay=0.5, fail_first=False):
    calls = []
    def command(args, cancel, timeout=None, **kwargs):
        calls.append(threading.current_thread().name)
        time.sleep(delay)
        if fail_first and len(calls) == 1:
            raise Problem('MEDIA_TOOL_FAILED', 'fixture failure', 'x', 422)
        return json.dumps({'frames': [{'best_effort_timestamp': 0}], 'streams': [], 'format': {}})
    monkeypatch.setattr(frames, 'command', command)
    return calls


def run_threads(media_file, count, cancels=None):
    settings = Settings(media_file.parent)
    results, errors = [None] * count, [None] * count
    def work(i):
        try:
            results[i] = frames.probe_frames(media_file, settings, (cancels or {}).get(i, threading.Event()))
        except Exception as error:  # noqa: BLE001
            errors[i] = error
    threads = [threading.Thread(target=work, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
        time.sleep(0.05)  # the first becomes the leader
    return threads, results, errors


def test_concurrent_requests_for_one_file_run_one_probe(media_file, monkeypatch):
    calls = fake_probe(monkeypatch)
    threads, results, errors = run_threads(media_file, 4)
    [t.join(10) for t in threads]
    assert errors == [None] * 4 and len(calls) == 1  # one decode, however many stages asked
    assert all(r == results[0] for r in results)
    assert frames.probe_frames(media_file, Settings(media_file.parent), threading.Event()) == results[0] and len(calls) == 1  # now cached
    assert not frames._inflight


def test_a_waiting_stage_honours_its_own_cancel_and_leaves_the_probe_running(media_file, monkeypatch):
    calls = fake_probe(monkeypatch, delay=1.0)
    stop = threading.Event()
    threads, results, errors = run_threads(media_file, 2, cancels={1: stop})
    time.sleep(0.2)
    stop.set()
    [t.join(10) for t in threads]
    assert isinstance(errors[1], Problem) and errors[1].code == 'JOB_CANCELLED'
    assert errors[0] is None and results[0] is not None and len(calls) == 1  # the probe finished for everyone else


def test_when_the_running_probe_fails_the_waiting_stage_probes_for_itself(media_file, monkeypatch):
    calls = fake_probe(monkeypatch, delay=0.4, fail_first=True)
    threads, results, errors = run_threads(media_file, 2)
    [t.join(10) for t in threads]
    assert isinstance(errors[0], Problem) and errors[0].code == 'MEDIA_TOOL_FAILED'  # the first caller sees its own failure
    assert errors[1] is None and results[1] is not None and len(calls) == 2
    assert not frames._inflight


def test_ready_does_not_wait_for_the_probe_and_the_stub_is_started_in_the_background(settings, video, monkeypatch):
    started, release, seen = threading.Event(), threading.Event(), {}
    def slow(path, settings_, cancel, timeout=90):
        seen['path'], seen['cancel'] = path, cancel
        started.set()
        release.wait(20)
        return {}
    monkeypatch.setattr(frames, 'probe_frames', slow)
    with client(settings, FakeMuse()) as c:
        state = upload(c, video)  # returns when the backend says "ready"
        assert state['status'] == 'ready' and state['preview'] is not None
        assert started.wait(10) and not release.is_set()  # still probing while the source is already usable
        assert str(seen['path']).endswith('source.bin')
        assert seen['cancel'] is c.app.state.service._background  # background work is stopped by close(), not by a job
        release.set()


def test_closing_the_service_stops_a_running_background_probe(settings, video, monkeypatch):
    started, finished = threading.Event(), threading.Event()
    def blocking(path, settings_, cancel, timeout=90):
        started.set()
        cancel.wait(30)  # like ffprobe: ends when told to stop
        finished.set()
        raise Problem('JOB_CANCELLED', 'cancelled', 'x', 409)
    monkeypatch.setattr(frames, 'probe_frames', blocking)
    begun = time.monotonic()
    with client(settings, FakeMuse()) as c:
        upload(c, video)
        assert started.wait(10)
    assert finished.is_set() and time.monotonic() - begun < 25  # shutdown did not wait the 30 s


def test_the_real_probe_cache_appears_after_ready_and_matches_a_direct_probe(settings, video):
    with client(settings, FakeMuse()) as c:
        state = upload(c, video)
        directory = c.app.state.service.store.directory(state['id'])
        cache = directory / 'source.bin.frames-v1.json'
        for _ in range(200):
            if cache.is_file():
                break
            time.sleep(0.05)
        assert cache.is_file()
        saved = json.loads(cache.read_text())['raw']
        direct = frames.probe_frames(directory / 'source.bin', settings, threading.Event())
        assert direct == saved and len(saved['frames']) == 12  # 1 s at 12 fps
