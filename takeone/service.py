from concurrent.futures import ThreadPoolExecutor
import hashlib
import fcntl
import json
from pathlib import Path
import threading
from uuid import uuid4

from takeone_rubric_v1.rubric import Answer, CapabilityManifest, Observation, RubricInput, RubricSession
from .config import Settings
from .contracts import Event, State, GradeRecipe, RenderCandidate
from .errors import Problem, safe_problem
from .media import capabilities, inspect_source, make_previews
from .provider import MuseProvider
from .store import Store
from . import rendering


class Service:
    def __init__(self, settings: Settings, provider=None):
        self.settings = settings
        self.store = Store(settings.workspace)
        self.process_lock = (self.store.root / ".backend.lock").open("a")
        try:
            fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.process_lock.close()
            raise Problem("WORKSPACE_IN_USE", "Another backend is using this workspace.",
                          "Run one backend worker, or select a separate workspace.", 503)
        self.provider = provider or MuseProvider(settings)
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="takeone-job")
        self.slots = threading.BoundedSemaphore(4)
        self.cancels: dict[str, threading.Event] = {}
        self.closed = False
        # Reconnectable state must not claim an interrupted job is still running.
        for path in self.store.root.glob("*/state.json"):
            try:
                state = self.store.get(path.parent.name)
            except Problem:
                continue  # One damaged record must not make other projects inaccessible.
            if state.active_job:
                state.active_job = None
                state.status = "failed"
                failure = Problem("JOB_INTERRUPTED", "The backend restarted during this operation.",
                                  "Retry the saved step. An in-flight Meta call may already have incurred usage.", 409,
                                  stage=state.stage)
                state.error = failure.public()
                state.events.append(Event(kind="error", title=failure.message, reason=failure.action, details=failure.details))
                self.store.save(state)

    def close(self):
        self.closed = True
        for event in list(self.cancels.values()):
            event.set()
        self.worker.shutdown(wait=True, cancel_futures=False)
        fcntl.flock(self.process_lock, fcntl.LOCK_UN)
        self.process_lock.close()

    def event(self, state: State, title: str, reason="", actor="app", kind="activity", **details):
        state.events.append(Event(title=title, reason=reason, actor=actor, kind=kind, details=details))

    @staticmethod
    def fingerprint(kind, value):
        return hashlib.sha256(json.dumps([kind, value], sort_keys=True).encode()).hexdigest()

    def receipt(self, state: State, command_id: str, fingerprint: str):
        previous = state.receipts.get(command_id)
        if previous and previous != fingerprint:
            raise Problem("COMMAND_CONFLICT", "This request ID was already used for different content.",
                          "Refresh the project before submitting another answer.", 409)
        return previous is not None

    @staticmethod
    def check_revision(state: State, revision: int):
        if revision != state.revision:
            raise Problem("STALE_PROJECT", "The project changed since this request was prepared.",
                          "Refresh the current state, then submit again.", 409, revision=state.revision)
        if state.active_job:
            raise Problem("PROJECT_BUSY", "An operation is already running.", "Wait or cancel the current operation.", 409)

    def submit(self, state: State, kind: str):
        if self.closed or not self.slots.acquire(blocking=False):
            failure = Problem("WORKER_BUSY", "The local worker queue is full.",
                              "Wait for another operation to finish, then retry.", 409)
            state.status, state.stage, state.job_kind = "failed", "queue", kind
            state.error = failure.public()
            self.event(state, failure.message, failure.action, kind="error", code=failure.code)
            self.store.save(state)
            raise failure
        job_id = str(uuid4())
        cancel = threading.Event()
        state.active_job = job_id
        state.job_kind = kind
        state.status = "ingesting" if kind == "ingest" else "rendering" if kind == "render" else "analyzing"
        state.stage = ("inspect_source" if kind == "ingest" else "plan_grade" if kind == "render"
                       else "analyze_scene" if not state.analysis else "select_question")
        state.error = None
        self.event(state, "Preparing source" if kind == "ingest" else "Grade queued" if kind == "render" else "Analysis queued")
        try:
            self.store.save(state)
            self.cancels[state.id] = cancel
            self.worker.submit(self.run, state.id, job_id, kind, cancel)
        except Exception:
            self.slots.release()
            self.cancels.pop(state.id, None)
            raise

    def mutate(self, project_id, operation):
        with self.store.lock:
            state = self.store.get(project_id)
            operation(state)
            self.store.save(state)
            return state

    def start(self, project_id, request: str, revision: int, command_id: str):
        request = request.strip()
        fingerprint = self.fingerprint("start", request)
        with self.store.lock:
            state = self.store.get(project_id)
            if self.receipt(state, command_id, fingerprint):
                return state
            self.check_revision(state, revision)
            if not state.preview or not state.source:
                raise Problem("SOURCE_NOT_READY", "The source preview is not ready.", "Wait for source preparation or retry the failed upload.", 409)
            if not self.provider.available():
                raise Problem("MUSE_NOT_CONFIGURED", "The Meta API key is not configured.",
                              "Set MODEL_API_KEY in the root .env file, then refresh the editor.", 503)
            if state.request == request and state.status in {"awaiting_answer", "brief_ready", "render_ready", "failed", "cancelled"}:
                return state
            if state.rubric:
                state.history.append({"request": state.request, "rubric": state.rubric,
                                      "brief": state.brief.model_dump(mode="json") if state.brief else None})
            state.request = request
            state.rubric = None
            state.question = None
            state.brief = None
            state.answered_count = 0
            state.model_calls = 0
            state.render_target_id = None
            state.receipts[command_id] = fingerprint
            self.event(state, "Creative direction submitted", request or "Use a conservative direction.", actor="user")
            self.submit(state, "analysis")
            return state

    def answer(self, project_id, answer: Answer, revision: int, command_id: str):
        fingerprint = self.fingerprint("answer", answer.model_dump())
        with self.store.lock:
            state = self.store.get(project_id)
            if self.receipt(state, command_id, fingerprint):
                return state
            self.check_revision(state, revision)
            if state.status != "awaiting_answer" or not state.rubric:
                raise Problem("NO_PENDING_QUESTION", "There is no pending question to answer.", "Refresh the consultation.", 409)
            session = RubricSession.restore(state.rubric)
            try:
                session.record_answer(answer)
            except ValueError as error:
                raise Problem("ANSWER_INVALID", str(error), "Answer the displayed question using a valid option or free text.", 422)
            state.rubric = session.snapshot()
            state.question = None
            state.answered_count = len(session.answers)
            state.receipts[command_id] = fingerprint
            self.event(state, "Answer saved", answer.response or answer.response_type, actor="user",
                       question_id=answer.question_id, response_type=answer.response_type)
            # Commit the answer before making another billable model request.
            self.store.save(state)
            self.submit(state, "analysis")
            return state

    def retry(self, project_id, revision: int, command_id: str):
        with self.store.lock:
            state = self.store.get(project_id)
            fingerprint = self.fingerprint("retry", state.job_kind)
            if self.receipt(state, command_id, fingerprint):
                return state
            self.check_revision(state, revision)
            if state.status not in {"failed", "cancelled"}:
                raise Problem("RETRY_NOT_NEEDED", "There is no failed step to retry.", "Continue with the current state.", 409)
            kind = state.job_kind or ("ingest" if not state.preview else "analysis")
            target = next((c for c in state.candidates if c.id == state.render_target_id), None)
            if (kind == "analysis" or (kind == "render" and (not target or not target.recipe))) and not self.provider.available():
                raise Problem("MUSE_NOT_CONFIGURED", "The Meta API key is not configured.",
                              "Set MODEL_API_KEY in .env, then refresh the editor.", 503)
            state.receipts[command_id] = fingerprint
            self.submit(state, kind)
            return state

    def generate(self, project_id, revision: int, command_id: str):
        with self.store.lock:
            state = self.store.get(project_id)
            if not state.brief:
                raise Problem("BRIEF_NOT_READY", "Complete the creative brief before generating a grade.",
                              "Finish the pending consultation.", 409)
            brief_hash = rendering.fingerprint(state.brief.model_dump(mode="json"))
            fingerprint = self.fingerprint("render", brief_hash)
            if self.receipt(state, command_id, fingerprint):
                return state
            self.check_revision(state, revision)
            if state.status not in {"brief_ready", "render_ready"}:
                raise Problem("RENDER_NOT_READY", "The project is not ready for a new render.",
                              "Retry the failed step or finish the current operation.", 409)
            if not state.source or "global_grade" not in capabilities(state.source)["operations"]:
                raise Problem("GRADE_DOMAIN_UNSUPPORTED", "This source has no supported global grading domain.",
                              "Use known 8-bit SDR Rec.709 footage.", 422)
            if not self.provider.available():
                raise Problem("MUSE_NOT_CONFIGURED", "The Meta API key is not configured.", "Set MODEL_API_KEY in .env.", 503)
            if sum(c.brief_sha256 == brief_hash for c in state.candidates) >= 3:
                raise Problem("RENDER_BUDGET", "Three candidates have already been attempted for this brief.",
                              "Review the saved candidates before changing the direction.", 409)
            candidate = RenderCandidate(id=str(uuid4()), name="V" + str(len(state.candidates) + 1),
                                        brief_sha256=brief_hash, source_sha256=state.source.sha256)
            state.candidates.append(candidate)
            state.render_target_id = candidate.id
            state.receipts[command_id] = fingerprint
            self.event(state, "Grade requested", actor="user", candidate=candidate.name)
            self.submit(state, "render")
            return state

    def cancel(self, project_id):
        with self.store.lock:
            state = self.store.get(project_id)
            cancel = self.cancels.get(project_id)
            if cancel and state.active_job:
                cancel.set()
                state.stage = "cancelling"
                self.event(state, "Cancellation requested", "An in-flight Meta request may still finish and incur usage.")
                self.store.save(state)
            return state

    def count_call(self, project_id, title):
        def change(state):
            if state.model_calls >= self.settings.max_questions + 4:
                raise Problem("MODEL_CALL_BUDGET", "The consultation model-call budget was reached.",
                              "Review the saved evidence and answers before starting another consultation.", 409)
            state.model_calls += 1
            self.event(state, title, actor="muse", model=self.settings.model, call=state.model_calls)
        self.mutate(project_id, change)

    @staticmethod
    def check_cancel(cancel):
        if cancel.is_set():
            raise Problem("JOB_CANCELLED", "Operation cancelled.", "The saved source and answers can be resumed.", 409)

    def run(self, project_id: str, job_id: str, kind: str, cancel: threading.Event):
        try:
            self.check_cancel(cancel)
            if kind == "ingest":
                self.ingest(project_id, cancel)
            elif kind == "render":
                self.render_grade(project_id, cancel)
            else:
                self.analyze(project_id, cancel)
        except Exception as cause:
            failure = safe_problem(cause, kind)
            try:
                def failed(state):
                    if state.active_job != job_id:
                        return
                    state.status = "cancelled" if failure.code == "JOB_CANCELLED" else "failed"
                    state.error = failure.public()
                    state.active_job = None
                    if kind == "render":
                        target = next((c for c in state.candidates if c.id == state.render_target_id), None)
                        if target:
                            target.status = "cancelled" if failure.code == "JOB_CANCELLED" else "failed"
                            target.error = failure.public()
                    self.event(state, failure.message, failure.action, kind="activity" if state.status == "cancelled" else "error",
                               code=failure.code, **failure.details)
                self.mutate(project_id, failed)
            except Problem:
                # Disk failure may prevent recording the problem; the last durable state is kept.
                import logging
                logging.getLogger("takeone").error("Unable to persist failed job %s", job_id)
        finally:
            if self.cancels.get(project_id) is cancel:
                self.cancels.pop(project_id, None)
            self.slots.release()

    def ingest(self, project_id, cancel):
        state = self.store.get(project_id)
        directory = self.store.directory(project_id)
        digest = hashlib.sha256()
        with (directory / "source.bin").open("rb") as source:
            while chunk := source.read(1024 * 1024):
                self.check_cancel(cancel)
                digest.update(chunk)
        source, raw = inspect_source(directory / "source.bin", state.filename, digest.hexdigest(), self.settings, cancel)
        self.store.artifact(project_id, "metadata.json", raw)
        def preparing(state):
            state.source = source
            state.stage = "prepare_preview"
            self.event(state, "Source inspected", video_codec=source.video_codec, duration=source.duration, sha256=source.sha256)
        self.mutate(project_id, preparing)
        preview = make_previews(directory, source, project_id, self.settings, cancel)
        self.check_cancel(cancel)
        def ready(state):
            state.preview = preview
            state.status, state.stage, state.active_job = "ready", "ready", None
            self.event(state, "Preview ready", "VP9 display preview and a silent analysis proxy were prepared. No media has been sent to Meta.")
        self.mutate(project_id, ready)

    def analyze(self, project_id, cancel):
        state = self.store.get(project_id)
        if not state.source or not state.preview:
            raise Problem("SOURCE_NOT_READY", "Source preparation is incomplete.", "Retry source preparation.", 409)
        if not state.analysis:
            self.count_call(project_id, "Inspecting video with Muse")
            def remember(remote_id):
                self.mutate(project_id, lambda s: setattr(s, "remote_file_id", remote_id))
            analysis, usage = self.provider.analyze(self.store.directory(project_id) / "analysis.mp4",
                                                    state.source.duration, state.remote_file_id, remember)
            self.store.artifact(project_id, "analysis.json", analysis.model_dump(mode="json"))
            def analyzed(state):
                state.analysis = analysis
                state.stage = "select_question"
                self.event(state, "Scene analysis saved", analysis.summary, actor="muse",
                           input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"))
            self.mutate(project_id, analyzed)
        self.check_cancel(cancel)
        state = self.store.get(project_id)
        if state.rubric:
            session = RubricSession.restore(state.rubric)
        else:
            session = RubricSession(RubricInput(
                user_request=state.request,
                observations=[Observation(id=o.id, scope=f"{o.start:.3f}-{o.end:.3f}s", source="muse_visual",
                                          description=o.description, uncertainty=o.uncertainty) for o in state.analysis.observations],
                capabilities=CapabilityManifest(**capabilities(state.source)),
                technical_envelope=[
                    f"Source: {state.source.width}x{state.source.height}, {state.source.duration:.3f}s; "
                    f"{state.source.video_codec}; {state.source.pixel_format}.",
                    "Analysis used the complete silent proxy; source/proxy timing is recorded.",
                    *state.analysis.caveats,
                ],
            ), max_decisions=state.max_questions)
        proposal_usage = None
        proposal_call = None
        if session.asked_count >= session.max_decisions:
            from takeone_rubric_v1.rubric import Proposal
            proposal = Proposal()
        else:
            self.count_call(project_id, "Selecting a useful clarification")
            proposal_call = self.store.get(project_id).model_calls
            event_start = len(session.events)
            proposal = self.provider.propose(session)
            proposal_usage = next((e.get("usage") for e in reversed(session.events[event_start:])
                                   if e.get("event") == "muse_proposal"), None)
        self.check_cancel(cancel)
        question = session.next_question(proposal)
        brief = None if question else session.brief()
        if brief:
            if proposal.observation_gap:
                brief.unresolved.append("Observation gap: " + proposal.observation_gap)
            if proposal.knowledge_gap:
                brief.unresolved.append("Knowledge gap: " + proposal.knowledge_gap)
            self.store.artifact(project_id, "brief.json", brief.model_dump(mode="json"))
        def selected(state):
            state.rubric = session.snapshot()
            state.question = question
            state.brief = brief
            state.answered_count = len(session.answers)
            state.status = "awaiting_answer" if question else "brief_ready"
            state.stage = state.status
            state.active_job = None
            if question:
                self.event(state, question.question, question.decision_fork, actor="muse", kind="decision", question_id=question.id)
            else:
                self.event(state, "Creative brief ready", "The original request, answers, boundaries and limitations have been saved.", actor="muse")
            if session.rejected:
                self.event(state, "Question validation completed", rejected_count=len(session.rejected))
            if proposal_usage:
                self.event(state, "Muse request usage", actor="muse",
                           input_tokens=proposal_usage.get("prompt_tokens"),
                           output_tokens=proposal_usage.get("completion_tokens"), call=proposal_call)
        self.mutate(project_id, selected)

    def render_grade(self, project_id, cancel):
        state = self.store.get(project_id)
        candidate = next((c for c in state.candidates if c.id == state.render_target_id), None)
        if not candidate or not state.source or not state.analysis or not state.brief:
            raise Problem("RENDER_STATE_INVALID", "The render has no matching source, analysis or brief.",
                          "Refresh the consultation before rendering.", 409)
        if candidate.brief_sha256 != rendering.fingerprint(state.brief.model_dump(mode="json")):
            raise Problem("BRIEF_CHANGED", "The brief changed after this candidate was requested.",
                          "Generate a new candidate from the current brief.", 409)
        directory = self.store.directory(project_id)
        output = directory / "candidates" / candidate.id
        output.mkdir(parents=True, exist_ok=True)
        original = directory / "source.bin"
        rendering.verify_source(original, state.source, self.settings, cancel)
        if not candidate.recipe:
            measurements, images = rendering.samples(original, state.source, self.settings, cancel)
            self.check_cancel(cancel)
            self.count_call(project_id, "Muse is preparing a global grading recipe")
            call_number = self.store.get(project_id).model_calls
            proposal, usage = self.provider.grade(state.brief, state.analysis, state.source, measurements, images)
            recipe = GradeRecipe(source_sha256=state.source.sha256, brief_sha256=candidate.brief_sha256,
                                 model=self.settings.model, proposal=proposal, source_samples=measurements)
            self.store.candidate_artifact(project_id, candidate.id, "recipe.json", recipe.model_dump(mode="json"))
            def planned(state):
                target = next(c for c in state.candidates if c.id == candidate.id)
                target.recipe = recipe
                target.status = "rendering"
                target.error = None
                state.stage = "render_grade"
                self.event(state, "Grading recipe saved", proposal.summary, actor="muse", kind="decision",
                           candidate=candidate.name, contrast=proposal.parameters.contrast,
                           saturation=proposal.parameters.saturation, gamma=proposal.parameters.gamma)
                for decision in proposal.decisions:
                    self.event(state, decision.parameter.capitalize() + " decision", decision.reason,
                               actor="muse", kind="decision", candidate=candidate.name)
                if usage:
                    self.event(state, "Colorist request usage", actor="muse", call=call_number,
                               input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"))
            self.mutate(project_id, planned)
        else:
            recipe = candidate.recipe
            def resuming(state):
                target = next(c for c in state.candidates if c.id == candidate.id)
                target.status, target.error = "rendering", None
                state.stage = "render_grade"
                self.event(state, "Resuming saved recipe", "No additional model call is needed.", candidate=candidate.name)
            self.mutate(project_id, resuming)
        self.check_cancel(cancel)
        result = rendering.render(original, output, recipe, state.source, self.settings, cancel)
        self.check_cancel(cancel)
        self.store.candidate_artifact(project_id, candidate.id, "validation.json", result)
        def accepted(state):
            target = next(c for c in state.candidates if c.id == candidate.id)
            target.status, target.error = "ready", None
            target.width, target.height, target.duration = result["width"], result["height"], result["duration"]
            target.preview_url = f"/api/projects/{project_id}/candidates/{target.id}/preview"
            target.download_url = f"/api/projects/{project_id}/candidates/{target.id}/download"
            target.validation = result
            state.selected_candidate_id = target.id
            state.status, state.stage, state.active_job = "render_ready", "render_ready", None
            self.event(state, "Rendered " + target.name, "The video is ready for comparison. Visual approval is still required.",
                       actor="renderer", candidate=target.name, warnings=len(result["warnings"]))
        self.mutate(project_id, accepted)
