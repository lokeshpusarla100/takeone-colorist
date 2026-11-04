from dataclasses import replace
import hashlib
from pathlib import Path
import subprocess
import threading
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from takeone.app import create_app
from takeone.config import Settings
from takeone.contracts import SceneAnalysis, VisualObservation
from takeone.errors import Problem
from takeone.media import global_grade, probe
from takeone_rubric_v1.rubric import Candidate, Option, Proposal


class FakeMuse:
    """Test injection only. The production app always constructs MuseProvider."""
    def __init__(self):
        self.analysis_calls = 0
        self.proposal_calls = 0
        self.fail_proposals = 0
        self.operation = "global_grade"

    def available(self):
        return True

    def analyze(self, path, duration, remote_id, remember_upload):
        self.analysis_calls += 1
        assert path.is_file()
        remember_upload("test-file-id")
        return SceneAnalysis(
            summary="Synthetic test pattern.",
            observations=[VisualObservation(id="obs1", start=0, end=min(duration, 1),
                                            description="A synthetic color pattern.", uncertainty="Test fixture.")],
            caveats=["Injected offline provider, not a real Muse result."],
        ), {"input_tokens": 12, "output_tokens": 20}

    def propose(self, session):
        self.proposal_calls += 1
        if self.fail_proposals:
            self.fail_proposals -= 1
            raise Problem("MUSE_TIMEOUT", "Test request timed out.", "Retry the step.", 504)
        if session.answers or "explicit" in session.inputs.user_request:
            return Proposal(candidates=[], explicit_constraints=["Preserve colors"] if "Preserve colors" in session.inputs.user_request else [])
        return Proposal(candidates=[Candidate(
            id="q1", dimension="appearance", target_key="look_strength", scope="whole clip",
            question="How strong should the treatment be?", evidence_ids=["obs1"], uncertainty_type="preference",
            decision_fork="Choose restrained or stronger global contrast and saturation.",
            impact="high", preservation_risk=False, answerability="high",
            options=[
                Option(id="natural", label="Natural", intended_outcome="Restrained treatment", required_operations=[self.operation]),
                Option(id="strong", label="Strong", intended_outcome="Stronger treatment", required_operations=[self.operation]),
            ],
        )])


@pytest.fixture(scope="session")
def video(tmp_path_factory):
    path = tmp_path_factory.mktemp("media") / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=192x108:rate=12",
        "-t", "1", "-vf", "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ], check=True)
    return path


@pytest.fixture
def settings(tmp_path):
    return Settings(workspace=tmp_path / "workspace", max_duration=120)


def client(settings, provider):
    return TestClient(create_app(settings, provider), base_url="http://127.0.0.1")


def wait(c, project_id, expected=None):
    for _ in range(250):
        state = c.get("/api/projects/" + project_id).json()
        if state["status"] not in {"ingesting", "analyzing"}:
            if expected:
                assert state["status"] == expected, state.get("error")
            return state
        time.sleep(0.02)
    raise AssertionError("Worker did not finish")


def upload(c, video):
    with video.open("rb") as stream:
        response = c.post("/api/projects", files={"file": ("source.mp4", stream, "video/mp4")})
    assert response.status_code == 202, response.text
    return wait(c, response.json()["id"], "ready")


def start(c, state, request="Make it cinematic"):
    response = c.post("/api/projects/" + state["id"] + "/analyze", json={
        "command_id": str(uuid4()), "revision": state["revision"], "request": request,
    })
    assert response.status_code == 202, response.text
    return wait(c, state["id"])


def test_upload_previews_and_original_without_key(settings, video):
    with client(settings, None) as c:
        state = upload(c, video)
        assert c.get("/api/health").json()["muse_configured"] is False
        assert state["source"]["sha256"] == hashlib.sha256(video.read_bytes()).hexdigest()
        assert state["preview"]["width"] == state["source"]["width"]
        assert state["preview"]["height"] == state["source"]["height"]
        preview = c.get(state["preview"]["url"], headers={"Range": "bytes=0-99"})
        assert preview.status_code == 206
        assert len(preview.content) == 100
        assert c.get("/api/projects/" + state["id"] + "/source").content == video.read_bytes()
        denied = c.post("/api/projects/" + state["id"] + "/analyze", json={
            "command_id": str(uuid4()), "revision": state["revision"], "request": "warm",
        })
        assert denied.status_code == 503
        assert denied.json()["error"]["code"] == "MUSE_NOT_CONFIGURED"


def test_question_answer_idempotency_and_brief(settings, video):
    muse = FakeMuse()
    with client(settings, muse) as c:
        state = start(c, upload(c, video))
        assert state["status"] == "awaiting_answer"
        body = {"command_id": str(uuid4()), "revision": state["revision"],
                "answer": {"question_id": "q1", "response_type": "option", "response": "natural"}}
        first = c.post("/api/projects/" + state["id"] + "/answers", json=body)
        second = c.post("/api/projects/" + state["id"] + "/answers", json=body)
        assert first.status_code == second.status_code == 202
        done = wait(c, state["id"], "brief_ready")
        assert done["brief"]["directives"][0]["outcome"] == "Restrained treatment"
        assert len(done["brief"]["answers"]) == 1
        assert sum(e["title"] == "Answer saved" for e in done["events"]) == 1
        assert muse.analysis_calls == 1
        assert muse.proposal_calls == 2
        assert c.get("/api/projects/" + state["id"] + "/brief").json() == done["brief"]


def test_zero_question_and_literal_constraints(settings, video):
    with client(settings, FakeMuse()) as c:
        state = start(c, upload(c, video), "explicit: Preserve colors")
        assert state["status"] == "brief_ready"
        assert state["answered_count"] == 0
        assert state["brief"]["explicit_constraints"] == ["Preserve colors"]
        assert state["brief"]["initial_request"] == "explicit: Preserve colors"


@pytest.mark.parametrize("kind,response", [("free_text", "Keep it subdued."), ("skip", ""), ("delegate", "")])
def test_answer_forms(settings, video, kind, response):
    with client(settings, FakeMuse()) as c:
        state = start(c, upload(c, video))
        result = c.post("/api/projects/" + state["id"] + "/answers", json={
            "command_id": str(uuid4()), "revision": state["revision"],
            "answer": {"question_id": "q1", "response_type": kind, "response": response},
        })
        assert result.status_code == 202
        done = wait(c, state["id"], "brief_ready")
        assert done["brief"]["answers"][0]["response_type"] == kind
        if kind != "free_text":
            assert done["brief"]["unresolved"]


def test_stale_and_invalid_answers_do_not_advance(settings, video):
    muse = FakeMuse()
    with client(settings, muse) as c:
        state = start(c, upload(c, video))
        body = {"command_id": str(uuid4()), "revision": 0,
                "answer": {"question_id": "q1", "response_type": "option", "response": "missing"}}
        url = "/api/projects/" + state["id"] + "/answers"
        assert c.post(url, json=body).status_code == 409
        body["revision"] = state["revision"]
        assert c.post(url, json=body).status_code == 422
        current = c.get("/api/projects/" + state["id"]).json()
        assert current["answered_count"] == 0
        assert current["question"]["id"] == "q1"
        assert muse.proposal_calls == 1


def test_resume_after_restart_without_vision_resend(settings, video):
    first = FakeMuse()
    with client(settings, first) as c:
        state = start(c, upload(c, video))
    second = FakeMuse()
    with client(settings, second) as c:
        restored = c.get("/api/projects/" + state["id"]).json()
        assert restored["question"]["id"] == "q1"
        c.post("/api/projects/" + state["id"] + "/answers", json={
            "command_id": str(uuid4()), "revision": restored["revision"],
            "answer": {"question_id": "q1", "response_type": "delegate"},
        })
        wait(c, state["id"], "brief_ready")
        assert second.analysis_calls == 0
        assert second.proposal_calls == 1


def test_failed_proposal_retries_cached_analysis(settings, video):
    muse = FakeMuse()
    muse.fail_proposals = 1
    with client(settings, muse) as c:
        failed = start(c, upload(c, video))
        assert failed["status"] == "failed"
        assert failed["error"]["code"] == "MUSE_TIMEOUT"
        assert failed["analysis"] is not None
        result = c.post("/api/projects/" + failed["id"] + "/retry", json={
            "command_id": str(uuid4()), "revision": failed["revision"],
        })
        assert result.status_code == 202
        wait(c, failed["id"], "awaiting_answer")
        assert muse.analysis_calls == 1


def test_unavailable_operations_are_rejected(settings, video):
    muse = FakeMuse()
    muse.operation = "invented_relighting"
    with client(settings, muse) as c:
        state = start(c, upload(c, video))
        assert state["status"] == "failed"
        assert state["error"]["code"] == "RUBRIC_PROTOCOL_ERROR"
        assert state["question"] is None


def test_corrupt_and_empty_upload_errors(settings):
    with client(settings, None) as c:
        assert c.post("/api/projects", files={"file": ("empty.mp4", b"", "video/mp4")}).status_code == 422
        result = c.post("/api/projects", files={"file": ("bad.mp4", b"invalid media", "video/mp4")})
        state = wait(c, result.json()["id"], "failed")
        assert state["error"]["code"] == "MEDIA_TOOL_FAILED"
        assert (settings.workspace / state["id"] / "source.bin").read_bytes() == b"invalid media"


def test_source_and_api_limits(settings, video):
    with client(replace(settings, max_upload_bytes=2), None) as c:
        assert c.post("/api/projects", files={"file": ("large.mp4", b"123", "video/mp4")}).status_code == 413
    with client(settings, None) as c:
        assert c.post("/api/projects", headers={"Origin": "https://untrusted.invalid"},
                      files={"file": ("a.mp4", b"x", "video/mp4")}).status_code == 403
        assert c.get("/api/projects/not-a-uuid").status_code == 422


def test_base_global_tool_has_a_real_pixel_effect(settings, video, tmp_path):
    import shutil
    directory = tmp_path / "grade"
    directory.mkdir()
    shutil.copyfile(video, directory / "source.bin")
    output = global_grade(directory, settings, threading.Event(), saturation=0)
    assert probe(output, settings, threading.Event())["streams"][0]["codec_name"] == "h264"
    # Decode RGB and check near-neutral channels instead of accepting command exit alone.
    pixels = subprocess.run(["ffmpeg", "-v", "error", "-i", str(output), "-frames:v", "1",
                             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True, capture_output=True).stdout
    assert pixels
    assert sum(abs(pixels[i] - pixels[i+1]) + abs(pixels[i+1] - pixels[i+2])
               for i in range(0, len(pixels), 3)) / (len(pixels) // 3) < 5


def test_answer_is_durable_when_followup_fails(settings, video):
    muse = FakeMuse()
    with client(settings, muse) as c:
        state = start(c, upload(c, video))
        muse.fail_proposals = 1
        body = {"command_id": str(uuid4()), "revision": state["revision"],
                "answer": {"question_id": "q1", "response_type": "free_text", "response": "Keep it quiet."}}
        assert c.post("/api/projects/" + state["id"] + "/answers", json=body).status_code == 202
        failed = wait(c, state["id"], "failed")
        assert failed["answered_count"] == 1
        assert c.post("/api/projects/" + state["id"] + "/answers", json=body).status_code == 202
        c.post("/api/projects/" + state["id"] + "/retry",
               json={"command_id": str(uuid4()), "revision": failed["revision"]})
        done = wait(c, state["id"], "brief_ready")
        assert len(done["brief"]["answers"]) == 1
        assert done["brief"]["directives"][0]["outcome"] == "Keep it quiet."
        assert muse.analysis_calls == 1


def test_budget_stops_without_an_extra_model_proposal(settings, video):
    muse = FakeMuse()
    with client(replace(settings, max_questions=1), muse) as c:
        state = start(c, upload(c, video))
        c.post("/api/projects/" + state["id"] + "/answers", json={
            "command_id": str(uuid4()), "revision": state["revision"],
            "answer": {"question_id": "q1", "response_type": "delegate"},
        })
        done = wait(c, state["id"], "brief_ready")
        assert muse.proposal_calls == 1
        assert done["model_calls"] == 2


def test_usage_is_not_replayed_when_budget_finishes_without_a_call(settings, video):
    class UsageProvider(FakeMuse):
        def propose(self, session):
            result = super().propose(session)
            session.events.append({"event":"muse_proposal","usage":{"prompt_tokens":123,"completion_tokens":456}})
            return result
    muse = UsageProvider()
    with client(replace(settings,max_questions=1),muse) as c:
        state = start(c,upload(c,video))
        first_usage = [e for e in state["events"] if e["title"]=="Muse request usage"]
        assert len(first_usage)==1
        assert first_usage[0]["details"]["call"]==2
        c.post("/api/projects/"+state["id"]+"/answers",json={
            "command_id":str(uuid4()),"revision":state["revision"],
            "answer":{"question_id":"q1","response_type":"option","response":"natural"},
        })
        done = wait(c,state["id"],"brief_ready")
        assert [e for e in done["events"] if e["title"]=="Muse request usage"]==first_usage
        assert muse.proposal_calls==1
        assert done["model_calls"]==2


def test_later_no_question_proposal_gets_its_own_usage(settings, video):
    class UsageProvider(FakeMuse):
        def propose(self, session):
            result=super().propose(session)
            session.events.append({"event":"muse_proposal","usage":{
                "prompt_tokens":100*self.proposal_calls,"completion_tokens":10*self.proposal_calls}})
            return result
    muse=UsageProvider()
    with client(settings,muse) as c:
        state=start(c,upload(c,video))
        c.post("/api/projects/"+state["id"]+"/answers",json={
            "command_id":str(uuid4()),"revision":state["revision"],
            "answer":{"question_id":"q1","response_type":"delegate"},
        })
        done=wait(c,state["id"],"brief_ready")
        usages=[e["details"] for e in done["events"] if e["title"]=="Muse request usage"]
        assert [u["call"] for u in usages]==[2,3]
        assert [u["input_tokens"] for u in usages]==[100,200]


def test_provider_errors_do_not_expose_secrets(settings, video):
    class BrokenProvider(FakeMuse):
        def analyze(self, *args):
            raise RuntimeError("private-key-must-not-leak")
    with client(replace(settings, api_key="private-key-must-not-leak"), BrokenProvider()) as c:  # pragma: allowlist secret  # gitleaks:allow
        state = start(c, upload(c, video))
        assert state["status"] == "failed"
        assert "private-key-must-not-leak" not in str(state)
        assert "private-key-must-not-leak" not in c.get("/api/health").text


def test_only_one_backend_can_own_a_workspace(settings):
    from takeone.service import Service
    with client(settings, FakeMuse()) as c:
        with pytest.raises(Problem, match="Another backend"):
            Service(settings, FakeMuse())
        assert c.get("/api/health").status_code == 200


def test_queue_full_keeps_an_accepted_answer_recoverable(settings, video):
    muse = FakeMuse()
    with client(settings, muse) as c:
        state = start(c, upload(c, video))
        service = c.app.state.service
        for _ in range(4):
            assert service.slots.acquire(blocking=False)
        try:
            response = c.post("/api/projects/" + state["id"] + "/answers", json={
                "command_id": str(uuid4()), "revision": state["revision"],
                "answer": {"question_id": "q1", "response_type": "skip"},
            })
            assert response.status_code == 409
            failed = wait(c, state["id"], "failed")
            assert failed["answered_count"] == 1
        finally:
            for _ in range(4):
                service.slots.release()
        c.post("/api/projects/" + state["id"] + "/retry",
               json={"command_id": str(uuid4()), "revision": failed["revision"]})
        done = wait(c, state["id"], "brief_ready")
        assert len(done["brief"]["answers"]) == 1


def test_cancelled_vision_can_resume_from_its_completed_cache(settings, video):
    class SlowProvider(FakeMuse):
        entered = threading.Event()
        release = threading.Event()
        def analyze(self, *args):
            self.entered.set()
            assert self.release.wait(3)
            return super().analyze(*args)
    muse = SlowProvider()
    with client(settings, muse) as c:
        state = upload(c, video)
        response = c.post("/api/projects/" + state["id"] + "/analyze", json={
            "command_id": str(uuid4()), "revision": state["revision"], "request": "cinematic",
        })
        assert response.status_code == 202
        assert muse.entered.wait(3)
        c.post("/api/projects/" + state["id"] + "/cancel")
        muse.release.set()
        cancelled = wait(c, state["id"], "cancelled")
        assert cancelled["analysis"] is not None
        c.post("/api/projects/" + state["id"] + "/retry",
               json={"command_id": str(uuid4()), "revision": cancelled["revision"]})
        wait(c, state["id"], "awaiting_answer")
        assert muse.analysis_calls == 1


def test_initial_key_setup_is_detected_without_restart(settings, tmp_path):
    from takeone.provider import MuseProvider
    key_file = tmp_path / ".env"
    provider = MuseProvider(replace(settings, key_file=key_file))
    assert provider.available() is False
    key_file.write_text("MODEL_API_KEY=offline-key-placeholder\n")  # pragma: allowlist secret  # gitleaks:allow
    assert provider.available() is True
    # The public health response exposes a boolean, never the key itself.
    with client(replace(settings, key_file=key_file), None) as c:
        response = c.get("/api/health")
        assert response.json()["muse_configured"] is True
        assert "offline-key-placeholder" not in response.text


def test_truncated_rubric_has_a_specific_error_without_partial_content():
    from openai import LengthFinishReasonError
    from openai.types.chat import ChatCompletion
    from takeone.provider import MuseProvider
    completion = ChatCompletion.model_validate({
        "id": "test", "object": "chat.completion", "created": 0, "model": "muse-spark-1.3",
        "choices": [{"index": 0, "finish_reason": "length",
                     "message": {"role": "assistant", "content": "private incomplete content"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2400, "total_tokens": 2410},
    })
    def truncated():
        raise LengthFinishReasonError(completion=completion)
    with pytest.raises(Problem) as captured:
        MuseProvider.call(truncated)
    assert captured.value.code == "MUSE_RESPONSE_TRUNCATED"
    assert captured.value.details["output_tokens"] == 2400
    assert "private incomplete content" not in str(captured.value.public())
