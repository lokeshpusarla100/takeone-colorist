"""SAM requests for the subject and regions run concurrently, with results identical to one-by-one."""
from dataclasses import replace
import hashlib
import subprocess
import threading
import time

from fastapi.testclient import TestClient

from takeone.app import create_app
from tests.test_backend import settings as base_settings, start, upload  # noqa: F401
from tests.test_concepts import LiteralMasks, PhrasePlanner
from tests.test_local_grading import generate, scene  # noqa: F401


class SlowMasks(LiteralMasks):
    """Fake SAM with network-like latency; records when each request ran and which upload it reused."""

    def __init__(self, delay=.5):
        super().__init__()
        self.delay, self.spans, self.remotes = delay, [], []
        self.lock = threading.Lock()

    def segment(self, path, concept, remote, *args):
        began = time.monotonic()
        with self.lock:
            self.remotes.append(remote)
        time.sleep(self.delay)
        result = super().segment(path, concept, remote, *args)
        with self.lock:
            self.spans.append((concept, began, time.monotonic()))
        return result


def decoded_md5(path):
    return subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:v:0', '-f', 'md5', '-'],
                          capture_output=True, text=True, check=True).stdout.strip()


def render(settings, scene, regions, parallel, subject='red subject'):
    masks = SlowMasks()
    planner = PhrasePlanner(subject, regions=regions)
    with TestClient(create_app(replace(settings, sam_parallel=parallel), planner, masks), base_url='http://127.0.0.1') as client:
        result = generate(client, start(client, upload(client, scene), 'explicit dark regions, keep subject'))
    return result, masks, settings


def test_concurrent_sam_requests_give_a_byte_identical_render_and_overlap(base_settings, scene, tmp_path):
    one = replace(base_settings, workspace=tmp_path / 'one')
    many = replace(base_settings, workspace=tmp_path / 'many')
    sequential, seq_masks, _ = render(one, scene, ['sky', 'lower band'], 1)
    parallel, par_masks, _ = render(many, scene, ['sky', 'lower band'], 3)
    assert sequential['status'] == parallel['status'] == 'render_ready', (sequential.get('error'), parallel.get('error'))
    out = lambda s, r: s.workspace / r['id'] / 'candidates' / r['candidates'][-1]['id'] / 'grade.mp4'
    assert decoded_md5(out(one, sequential)) == decoded_md5(out(many, parallel))
    regions = lambda r: [(x['concept'], x['concept_used'], x['active_frames']) for x in r['candidates'][-1]['validation']['mask_validation']['regions']]
    assert regions(sequential) == regions(parallel)
    starts, ends = [b for _, b, _ in par_masks.spans], [e for _, _, e in par_masks.spans]
    assert max(starts) < min(ends)  # all three requests were in flight together
    seq_spans = sorted(seq_masks.spans, key=lambda s: s[1])
    assert all(a[2] <= b[1] for a, b in zip(seq_spans, seq_spans[1:]))  # sam_parallel=1: strictly one at a time


def test_the_reported_error_is_the_one_sequential_order_reports(base_settings, scene, tmp_path):
    for parallel in [1, 3]:
        settings = replace(base_settings, workspace=tmp_path / f'w{parallel}')
        result, _, _ = render(settings, scene, ['unicorn horn'], parallel, subject='dragon wing')
        assert result['status'] == 'failed' and result['error']['code'] == 'LOCAL_CONCEPT_NOT_FOUND'
        assert '"dragon wing"' in result['error']['message']  # the subject's failure, as one-by-one reports it


def test_areas_falling_back_to_one_phrase_share_one_sam_request(base_settings, scene, tmp_path):
    settings = replace(base_settings, workspace=tmp_path / 'w')
    result, masks, _ = render(settings, scene, ['pale sky', 'sky above horizon'], 3)
    assert result['status'] == 'render_ready', result.get('error')
    assert masks.calls.count('sky') == 1  # both regions' fallback reached "sky"; requested once, reused


def test_one_request_uploads_a_shared_input_and_the_others_reuse_it(base_settings, tmp_path):
    from takeone.service import Service
    service = Service(replace(base_settings, workspace=tmp_path / 'w'))
    try:
        from uuid import uuid4
        from takeone.contracts import State
        pid = str(uuid4())
        (service.store.root / pid).mkdir(parents=True, exist_ok=True)
        service.store.save(State(id=pid, status='ready', stage='ready', filename='x'))
        owners, remotes = [], []
        def request():
            remote, gate = service.claim_upload(pid, 'sha', threading.Event())
            if gate:
                owners.append(1)
                time.sleep(.3)
                service.mutate(pid, lambda s: setattr(s, 'sam_upload', {'file_id': 'file-1', 'sha256': 'sha'}))
                service.release_upload(pid, 'sha', gate)
            else:
                remotes.append(remote)
        threads = [threading.Thread(target=request) for _ in range(3)]
        [t.start() for t in threads]
        [t.join(5) for t in threads]
        assert len(owners) == 1 and remotes == ['file-1', 'file-1']
    finally:
        service.close()
