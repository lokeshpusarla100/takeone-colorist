from concurrent.futures import ThreadPoolExecutor
import hashlib
import fcntl
import json
from pathlib import Path
import threading
from uuid import uuid4
from fractions import Fraction

from takeone_rubric_v1.rubric import Answer, CapabilityManifest, Directive, Observation, RememberedDefault, RubricInput, RubricSession
from .config import Settings
from .contracts import Event, State, GradeRecipe, RenderCandidate, ConsultationSummary, SegmentationRun, current_feedback,now
from .errors import Problem, safe_problem
from .media import capabilities, inspect_source, make_previews
from .provider import MuseProvider
from .store import Store
from . import rendering
from .preferences import FeedbackEvidence, PreferenceStore, PromptMemoryGate
from .preferences import context_values
from .usage import usage_details
from . import sam
from . import local_grading
from . import looks as look_previews_module
from .contracts import LookPreview
from . import evidence as visual_evidence
from . import creative_review
from . import grade_guard
from . import render_preflight
from . import convergence
from . import concepts as sam_concepts


def display_oriented(selection,source):
    """Saved evidence whose frame geometry matches the source's display orientation. Evidence saved
    before rotated sources were measured in decoded (auto-rotated) geometry is not reused."""
    manifest=(selection or {}).get('manifest') or {}
    width,height=manifest.get('width'),manifest.get('height')
    if not width or not height:return False
    return abs(width/height-source.width/source.height)<.01


HIGHLIGHT_REPLAN_INSTRUCTION=('Revise the executable plan using the actual images and the measured highlight failure. Keep the same creative brief and execution limits. '
    'Do not just resubmit the failed controls. The failed plan darkens midtones but BRIGHTENS highlights: lowering white_point (an input cutoff that maps bright pixels to pure white) '
    'and raising contrast both lift highlights and cannot subdue bright areas (sky, sand, snow, windows, glare). gamma/brightness_gain below 1 darken only upper midtones. '
    'highlight_ceiling is the control that darkens highlights; the measured evidence includes the suggested_highlight_ceiling that would remove the lift. '
    'If the brief requires darker or subdued highlights than highlight_ceiling 0.6 can deliver, list that in unfulfilled_requests instead of approximating it. '
    'Keep intent.highlight_policy faithful to the brief: subdue when it asks for darker or controlled highlights, intentional_lift only if brighter highlights are genuinely wanted. Do not call this a review of a completed candidate.')


class Service:
    def __init__(self, settings: Settings, provider=None, sam_provider=None):
        self.settings = settings
        self.store = Store(settings.workspace)
        self.process_lock = (self.store.root / ".backend.lock").open("a")
        try:
            fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.process_lock.close()
            raise Problem("WORKSPACE_IN_USE", "Another backend is using this workspace.",
                          "Run one backend worker, or select a separate workspace.", 503)
        self.memory_startup_error = None
        try:
            self.preferences = PreferenceStore(
                settings.memory_root or settings.workspace.parent / (settings.workspace.name + "-preferences"),
                application_available=settings.memory_application_available)
        except Exception as cause:
            self.preferences = None
            self.memory_startup_error = {"code":"MEMORY_UNAVAILABLE","message":"Preference storage is unavailable.",
                "action":"Current-video workflows can continue. Check preference storage permissions.",
                "details":{"exception_type":type(cause).__name__}}
        self.prompt_gate = PromptMemoryGate(self.preferences, self.store.get)
        self.provider = provider or MuseProvider(settings, self.prompt_gate)
        self.sam_provider = sam_provider or sam.SamProvider(settings)
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
                for run in state.segmentations:
                    if run.status in {"queued","preparing","streaming","overlay"}:
                        run.status="failed"
                state.active_job = None
                state.status = "failed"
                failure = Problem("JOB_INTERRUPTED", "The backend restarted during this operation.",
                                  "Retry the saved step. An in-flight Meta call may already have incurred usage.", 409,
                                  stage=state.stage)
                state.error = failure.public()
                state.events.append(Event(kind="error", title=failure.message, reason=failure.action, details=failure.details))
                self.store.save(state)
            if state.memory_outbox:
                self.drain_memory(state)

    def close(self):
        self.closed = True
        for event in list(self.cancels.values()):
            event.set()
        self.worker.shutdown(wait=True, cancel_futures=False)
        fcntl.flock(self.process_lock, fcntl.LOCK_UN)
        self.process_lock.close()

    def event(self, state: State, title: str, reason="", actor="app", kind="activity", **details):
        # Event rows have a scalar-only contract. Preserve structured diagnostics
        # in state.error, and encode their log projection instead of failing the
        # failure transition itself and leaving the project stuck processing.
        flat={k:v if v is None or isinstance(v,(str,int,float,bool)) else json.dumps(v,sort_keys=True)
              for k,v in details.items()}
        event = Event(title=title, reason=reason, actor=actor, kind=kind, details=flat)
        state.events.append(event)
        return event

    def require_memory(self):
        if self.preferences is None:
            raise Problem("MEMORY_UNAVAILABLE","Preference storage is unavailable.",
                          "Current-video feedback is still available. Check preference storage permissions.",503)
        return self.preferences

    def queue_memory(self, state: State, evidence: FeedbackEvidence):
        pending = None
        try:
            memory = self.require_memory()
            if state.owner_id not in {None, memory.owner_id}:
                raise Problem("MEMORY_OWNER_MISMATCH","Project belongs to a different memory owner.",
                              "Start a new project under the current owner.",409)
            state.owner_id = memory.owner_id
            pending = {"owner_id":memory.owner_id,"profile_id":state.profile_id,
                       "evidence":evidence.model_dump(mode="json")}
            # Preserve delivery if reading settings fails. Retry checks capture again.
            state.memory_outbox.append(pending)
            if not memory.settings(state.profile_id).capture_enabled:
                state.memory_outbox.remove(pending)
        except Exception as cause:
            if pending is None and self.preferences is None and state.owner_id:
                # Only replay under the owner already bound to this project.
                state.memory_outbox.append({"owner_id":state.owner_id,"profile_id":state.profile_id,
                                            "evidence":evidence.model_dump(mode="json")})
            state.memory_capture_error = safe_problem(cause,"memory.capture").public()

    def drain_memory(self, state: State):
        if not state.memory_outbox:
            return
        remaining = []
        failure = None
        for item in state.memory_outbox:
            try:
                memory = self.require_memory()
                if item["owner_id"] != memory.owner_id or item["profile_id"] != state.profile_id:
                    raise Problem("MEMORY_OWNER_MISMATCH","Capture ownership changed.","Inspect the saved outbox.",409)
                memory.record(item["profile_id"], FeedbackEvidence.model_validate(item["evidence"]))
            except Exception as cause:
                remaining.append(item)
                failure = safe_problem(cause,"memory.capture").public()
        state.memory_outbox = remaining
        state.memory_capture_error = failure
        self.store.save(state)

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

    def submit(self, state: State, kind: str, *, retry=False):
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
        state.status = "ingesting" if kind == "ingest" else "rendering" if kind == "render" else "segmenting" if kind=="segment" else 'reviewing' if kind=='review' else "analyzing"
        state.stage = ("inspect_source" if kind == "ingest" else "plan_grade" if kind == "render"
                       else 'creative_review' if kind=='review' else "verify_segmentation_input" if kind=="segment"
                       else "select_looks" if kind=="looks" else "analyze_scene" if not state.analysis else "select_question")
        state.error = None
        self.event(state, "Preparing source" if kind == "ingest" else "Grade queued" if kind == "render" else "Segmentation queued" if kind=="segment" else "Look previews queued" if kind=="looks" else "Analysis queued",
                   progress_job=job_id,progress_retry=retry)
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
            state.request_started_at=now()
            state.rubric = None
            state.question = None
            state.consultation = None
            state.brief = None
            state.looks = []
            state.selected_look_id = None
            state.looks_brief_sha256 = None
            state.looks_frame_index = None
            state.looks_frame_time = None
            state.looks_limitation = None
            state.answered_count = 0
            state.model_calls = 0
            state.critic_calls = 0
            state.render_target_id = None
            state.receipts[command_id] = fingerprint
            event = self.event(state, "Creative direction submitted", request or "Use a conservative direction.", actor="user")
            if request:
                self.queue_memory(state,FeedbackEvidence(event_id=event.id,project_id=state.id,kind="feedback",
                    original_wording=request,preference_key="initial_request",context=state.preference_context))
            self.submit(state, "analysis")
            self.drain_memory(state)
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
            state.consultation = None
            state.answered_count = len(session.answers)
            state.receipts[command_id] = fingerprint
            event = self.event(state, "Answer saved", answer.response or answer.response_type, actor="user",
                       question_id=answer.question_id, response_type=answer.response_type)
            answered = next(q for q in session.questions if q.id == answer.question_id)
            option = next((o for o in answered.options if o.id == answer.response), None) if answer.response_type == "option" else None
            self.queue_memory(state, FeedbackEvidence(event_id=event.id,project_id=state.id,kind="rubric_answer",
                original_wording=answer.response,dimension=answered.dimension,
                preference_key=answered.preference_family.value if answered.preference_family.value!="other" else answered.target_key,
                question_id=answered.id,context=state.preference_context,
                details={"response_type":answer.response_type,"selected_label":option.label if option else "",
                         "selected_outcome":option.intended_outcome if option else answer.response,"scope":answered.scope}))
            # Commit the answer before making another billable model request.
            self.store.save(state)
            self.drain_memory(state)
            self.submit(state, "analysis")
            return state

    def feedback_event(self, project_id, evidence: FeedbackEvidence, revision:int, command_id:str):
        fingerprint = self.fingerprint("feedback",evidence.model_dump(mode="json",exclude={"timestamp","event_id"}))
        with self.store.lock:
            state = self.store.get(project_id)
            if self.receipt(state,command_id,fingerprint):
                return state
            metadata_only = evidence.kind in {"version_selected","manual_adjustment","manual_lock"} or (
                evidence.kind=="version_rejected" and not evidence.original_wording.strip())
            if not metadata_only:
                self.check_revision(state,revision)
            evidence.project_id = state.id
            evidence.event_id = "feedback:" + command_id
            evidence.context = {**state.preference_context,**evidence.context}
            evidence.context = context_values(evidence.context)
            if evidence.version_id and evidence.version_id!="source":
                known = {c.id for c in state.candidates}
                if evidence.version_id not in known and not evidence.version_id.startswith("client:"):
                    raise Problem("VERSION_REFERENCE_INVALID","Version is not part of this project.",
                                  "Select a version from this project.",422)
                if evidence.version_id.startswith("client:"):
                    from uuid import UUID
                    try:
                        UUID(evidence.version_id[7:])
                    except ValueError:
                        raise Problem("VERSION_REFERENCE_INVALID","Invalid client version reference.","Select a valid version.",422)
            titles={"feedback":"Feedback saved","version_selected":"Version selected","version_rejected":"Version rejected",
                    "manual_adjustment":"Manual adjustment recorded (not executed)","manual_lock":"Manual lock recorded (not executed)",
                    "reusable_instruction":"Reusable instruction recorded"}
            if evidence.kind=="rubric_answer":
                raise Problem("FEEDBACK_INVALID","Rubric answers must use the answers endpoint.","Answer the pending question.",422)
            current_text = evidence.original_wording.strip() if evidence.kind in {"feedback","version_rejected"} else ""
            if evidence.kind=="feedback" and not current_text:
                raise Problem("FEEDBACK_INVALID","Enter editing feedback.","Describe the change you want.",422)
            if current_text and not state.brief:
                raise Problem("BRIEF_NOT_READY","Feedback needs an existing brief.","Start the consultation first.",409)
            body=evidence.model_dump(mode="json")
            state.feedback.append({"event_id":evidence.event_id,"text":current_text,
                "dimension":evidence.dimension.value,"preference_key":evidence.preference_key,
                "kind":evidence.kind,"version_id":evidence.version_id,"timestamp":evidence.timestamp})
            self.event(state,titles[evidence.kind],evidence.original_wording,actor="user",
                       feedback_id=evidence.event_id,version_id=evidence.version_id)
            if current_text:
                state.history.append({"brief":state.brief.model_dump(mode="json"),
                                      "brief_revision":state.brief_revision,"reason":"user_feedback"})
                updated=state.brief.model_copy(deep=True)
                updated.directives.append(Directive(dimension=evidence.dimension,scope="whole clip",
                    outcome=evidence.original_wording,provenance="user_answer",
                    strength="hard" if evidence.dimension.value=="boundaries" else "strong",source_id=evidence.event_id,
                    verification_hint="Latest current-video feedback overrides earlier conflicting direction."))
                state.brief=updated
                # A changed brief invalidates look previews made for the old one.
                state.looks=[];state.selected_look_id=None;state.looks_brief_sha256=None
                state.looks_frame_index=None;state.looks_frame_time=None;state.looks_limitation=None
                state.brief_revision=max(1,state.brief_revision)+1
                state.status,state.stage="brief_ready","brief_ready"
                self.store.artifact(state.id,"brief.json",updated.model_dump(mode="json"))
            self.queue_memory(state,FeedbackEvidence.model_validate(body))
            state.receipts[command_id]=fingerprint
            self.store.save(state)
            self.drain_memory(state)
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
            if (kind in {"analysis", "looks"} or (kind == "render" and (not target or not target.recipe))) and not self.provider.available():
                raise Problem("MUSE_NOT_CONFIGURED", "The Meta API key is not configured.",
                              "Set MODEL_API_KEY in .env, then refresh the editor.", 503)
            sam_target=next((r for r in state.segmentations if r.id==state.segmentation_target_id),None)
            sam_directory=self.store.directory(project_id)/'segmentations'/sam_target.id if sam_target else None
            cached_sam=sam_directory is not None and any((sam_directory/name).is_file() for name in ['manifest.json','stream-complete.json'])
            if kind=="segment" and not cached_sam and not self.sam_provider.available():
                raise Problem("SAM_NOT_CONFIGURED","The Meta key is not configured.","Set MODEL_API_KEY in .env.",503)
            state.receipts[command_id] = fingerprint
            self.submit(state, kind,retry=True)
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
            if sum(c.brief_sha256 == brief_hash for c in state.candidates) >= self.settings.max_render_iterations:
                raise Problem("RENDER_BUDGET", "Three candidates have already been attempted for this brief.",
                              "Review the saved candidates before changing the direction.", 409)
            candidate = RenderCandidate(id=str(uuid4()), name="V" + str(len(state.candidates) + 1),
                                        brief_sha256=brief_hash, source_sha256=state.source.sha256)
            candidate.generation_id=str(uuid4());state.render_iteration_limit=self.settings.max_render_iterations
            state.candidates.append(candidate)
            state.render_target_id = candidate.id
            state.receipts[command_id] = fingerprint
            self.event(state, "Grade requested", actor="user", candidate=candidate.name)
            self.submit(state, "render")
            return state

    def render_supported(self, project_id, candidate_id, revision: int, command_id: str):
        """Render the executable part of a plan whose planner listed unfulfillable instructions.

        Never automatic: the user explicitly chooses it after seeing exactly what will be left out.
        The omitted instructions move into the plan's limitations and onto the new candidate.
        """
        with self.store.lock:
            state = self.store.get(project_id)
            fingerprint = self.fingerprint("render_supported", candidate_id)
            if self.receipt(state, command_id, fingerprint):
                return state
            self.check_revision(state, revision)
            if state.status not in {"failed", "brief_ready", "render_ready"} or state.active_job:
                raise Problem("RENDER_NOT_READY", "The project is busy.", "Wait for the current operation to finish.", 409)
            failed = next((c for c in state.candidates if c.id == candidate_id), None)
            if not failed or failed.status != "failed" or (failed.error or {}).get("code") != "GRADE_REQUEST_UNFULFILLED":
                raise Problem("SUPPORTED_PLAN_UNAVAILABLE", "That version has no plan waiting for approval.",
                              "Generate a new version from the current brief.", 409)
            brief_hash = rendering.fingerprint(state.brief.model_dump(mode="json")) if state.brief else None
            plan_path = self.store.directory(project_id) / "candidates" / failed.id / "plan.json"
            if failed.brief_sha256 != brief_hash or not plan_path.is_file():
                raise Problem("SUPPORTED_PLAN_STALE", "The brief changed since that plan was made.",
                              "Generate a new version from the current brief.", 409)
            if sum(c.brief_sha256 == brief_hash for c in state.candidates) >= self.settings.max_render_iterations:
                raise Problem("RENDER_BUDGET", "Three candidates have already been attempted for this brief.",
                              "Review the saved candidates before changing the direction.", 409)
            from .contracts import GradeProposal
            proposal = GradeProposal.model_validate(json.loads(plan_path.read_text()))
            omitted = list(proposal.unfulfilled_requests)
            note = ("Not executed, by explicit user choice: " + "; ".join(omitted))[:1500]
            proposal.limitations = [*proposal.limitations[:7], note]
            proposal.unfulfilled_requests = []
            proposal.summary = (proposal.summary + " Rendered without: " + "; ".join(omitted))[:1000]
            candidate = RenderCandidate(id=str(uuid4()), name="V" + str(len(state.candidates) + 1), brief_sha256=brief_hash,
                                        source_sha256=state.source.sha256, parent_id=failed.id, generation_id=str(uuid4()),
                                        approved_proposal=GradeProposal.model_validate(proposal.model_dump()), approved_omissions=omitted)
            state.render_iteration_limit = self.settings.max_render_iterations
            state.candidates.append(candidate)
            state.render_target_id = candidate.id
            state.error = None
            state.status = "render_ready" if state.status == "failed" else state.status
            state.receipts[command_id] = fingerprint
            self.event(state, "Rendering the supported part", "Approved by the user; these instructions are not executed: " + "; ".join(omitted),
                       actor="user", candidate=candidate.name, parent_id=failed.id)
            self.submit(state, "render")
            return state

    def use_all_matches(self, project_id, candidate_id, revision: int, command_id: str):
        """User-approved: treat every SAM match of an ambiguous phrase as that area, then resume the render."""
        with self.store.lock:
            state = self.store.get(project_id)
            fingerprint = self.fingerprint("use_all_matches", candidate_id)
            if self.receipt(state, command_id, fingerprint):
                return state
            self.check_revision(state, revision)
            if state.active_job:
                raise Problem("RENDER_NOT_READY", "The project is busy.", "Wait for the current operation to finish.", 409)
            target = next((c for c in state.candidates if c.id == candidate_id), None)
            details = (target.error or {}).get("details", {}) if target else {}
            if (not target or target.status != "failed" or (target.error or {}).get("code") != "LOCAL_SUBJECT_AMBIGUOUS"
                    or not details.get("all_matches_available") or not target.recipe or not target.recipe.proposal.local):
                raise Problem("ALL_MATCHES_UNAVAILABLE", "That version has no ambiguous mask waiting for a choice.",
                              "Generate a new version or rephrase the area.", 409)
            if target.brief_sha256 != rendering.fingerprint(state.brief.model_dump(mode="json")):
                raise Problem("BRIEF_CHANGED", "The brief changed after this candidate was planned.", "Generate a new version.", 409)
            local = target.recipe.proposal.local
            planned = details.get("planned_concept")
            layers = [local] if local.concept == planned else [r for r in local.regions if r.concept == planned]
            if not layers:
                raise Problem("ALL_MATCHES_UNAVAILABLE", "The ambiguous area is no longer part of this plan.", "Generate a new version.", 409)
            for layer in layers:
                layer.selection = "all_matches"
            target.recipe.execution_adjustment = {**(target.recipe.execution_adjustment or {}), "kind": "user_selected_all_matches",
                                                  "concept": planned, "object_count": details.get("object_count")}
            target.status, target.error = "rendering", None
            state.render_target_id = target.id
            state.error = None
            state.receipts[command_id] = fingerprint
            self.store.candidate_artifact(project_id, target.id, "recipe.json", target.recipe.model_dump(mode="json"))
            self.event(state, "Grading all matches", f'Approved by the user: every SAM match of "{planned}" is graded as that area.',
                       actor="user", candidate=target.name, concept=planned, object_count=details.get("object_count"))
            self.submit(state, "render")
            return state

    def request_looks(self, project_id, revision: int, command_id: str):
        with self.store.lock:
            state = self.store.get(project_id)
            if not state.brief:
                raise Problem("BRIEF_NOT_READY", "Complete the creative brief before generating look previews.",
                              "Finish the pending consultation.", 409)
            brief_hash = rendering.fingerprint(state.brief.model_dump(mode="json"))
            fingerprint = self.fingerprint("looks", brief_hash)
            if self.receipt(state, command_id, fingerprint):
                return state
            self.check_revision(state, revision)
            if state.status not in {"brief_ready", "render_ready"}:
                raise Problem("LOOKS_NOT_READY", "The project is not ready for look previews.",
                              "Finish the current operation first.", 409)
            if not state.source or not state.analysis:
                raise Problem("SOURCE_NOT_READY", "The source analysis is not ready.", "Retry source preparation.", 409)
            if not self.provider.available():
                raise Problem("MUSE_NOT_CONFIGURED", "The Meta API key is not configured.", "Set MODEL_API_KEY in .env.", 503)
            state.looks_return_status = state.status
            state.receipts[command_id] = fingerprint
            self.event(state, "Look previews requested", actor="user")
            self.submit(state, "looks")
            return state

    def select_look(self, project_id, look_id: str, revision: int, command_id: str):
        with self.store.lock:
            state = self.store.get(project_id)
            fingerprint = self.fingerprint("look_select", look_id)
            if self.receipt(state, command_id, fingerprint):
                return state
            self.check_revision(state, revision)
            look = next((item for item in state.looks if item.id == look_id), None)
            if look is None:
                raise Problem("LOOK_NOT_FOUND", "That look preview is not part of this project.",
                              "Generate look previews and select one from the current brief.", 422)
            if look.preview_only:
                raise Problem('LOOK_MASK_REQUIRED','This palette preview does not execute the required subject protection.',
                              'Track the subject and regenerate localized previews, or use full generation with automatic SAM.',409)
            brief_hash = rendering.fingerprint(state.brief.model_dump(mode="json")) if state.brief else None
            if state.looks_brief_sha256 != brief_hash or look.brief_sha256 != brief_hash:
                raise Problem("LOOK_STALE", "These look previews belong to an earlier brief.",
                              "Generate look previews for the current brief before selecting.", 409)
            state.selected_look_id = look_id
            state.receipts[command_id] = fingerprint
            self.event(state, "Look selected", look.name, actor="user", look_id=look_id)
            self.store.save(state)
            return state

    def generate_looks(self, project_id, cancel):
        state = self.store.get(project_id)
        if not state.brief or not state.source or not state.analysis:
            raise Problem("LOOKS_STATE_INVALID", "Look previews have no matching brief, source or analysis.",
                          "Refresh the consultation before generating previews.", 409)
        directory = self.store.directory(project_id)
        original = directory / "source.bin"
        rendering.verify_source(original, state.source, self.settings, cancel)
        brief_hash = rendering.fingerprint(state.brief.model_dump(mode="json"))
        evidence = None
        local_error = None
        try:
            evidence = local_grading.timeline(directory, state.source, self.settings, cancel)
        except Problem as error:
            if error.code in {'SOURCE_CHANGED', 'JOB_CANCELLED'}:
                raise
            local_error=error.public()
        technical = {'operations': ['global_grade', 'cooling', 'brightness_gain', 'highlight_ceiling'],
                     'localized_available': evidence is not None,
                     'limitations': [] if evidence else [local_error['message'] if local_error else 'No verified local timeline.'],
                     'localized_blocker':local_error,
                     'purpose':'representative_still_look_preview',
                     'shots': evidence['shots'] if evidence else [],
                     'current_video_feedback': [f for f in current_feedback(state) if f.get('text')],
                     'previous_candidates': [], 'available_masks': []}
        for run in state.segmentations:
            manifest_path = directory / 'segmentations' / run.id / 'manifest.json'
            if run.status != 'ready' or not manifest_path.is_file():
                continue
            saved = json.loads(manifest_path.read_text())
            if saved['frame_map']['source_sha256'] == state.source.sha256:
                technical['available_masks'].append({'concept': run.concept, 'start': run.start_seconds,
                    'duration': run.duration_seconds or state.source.duration,
                    'object_ids': [t['object_id'] for t in run.tracks]})
        if evidence:
            technical['operations'] += ['regional_grade', 'selective_gain']
        selection = visual_evidence.plan(original, state.source, self.settings, cancel,
            proxy=directory / 'analysis.mp4', shots=evidence['shots'] if evidence else None,
            cache=directory / 'source-evidence-v3.json')
        measurements, images = rendering.samples(original, state.source, self.settings, cancel, selection=selection)
        self.check_cancel(cancel)
        request_id = self.count_call(project_id, "Muse is planning supported adjustments")
        call_number = self.store.get(project_id).model_calls
        proposal, usage = self.provider.grade(state.brief, state.analysis, state.source, measurements, images,
                                              technical_context=technical)
        self.record_usage(project_id, request_id, "colorist", usage, call_number)
        self.store.artifact(project_id,'looks-plan.json',{'brief_sha256':brief_hash,
            'proposal':proposal.model_dump(mode='json'),'localized_blocker':local_error})
        if proposal.unfulfilled_requests:
            raise Problem('GRADE_REQUEST_UNFULFILLED', 'The planner reported required instructions it cannot execute.',
                          'Clarify these instructions or choose a supported treatment. No look was generated.', 422,
                          unfulfilled_count=len(proposal.unfulfilled_requests),
                          unfulfilled_instructions='\n'.join(proposal.unfulfilled_requests),
                          localized_blocker=local_error)
        times = selection['manifest']['times']
        selected_indices = selection['selected_indices']
        frame_index = min(selected_indices, key=lambda i: abs(times[i] - state.source.duration / 2))
        limitation = None
        alpha = None
        if proposal.mode == 'localized':
            inside = [i for i in range(len(times))
                      if any(iv.start <= times[i] < iv.end for iv in proposal.local.intervals)]
            if inside:
                frame_index = inside[len(inside) // 2]
            alpha = self._look_mask(directory, state, proposal, frame_index, cancel)
            if alpha is None:
                limitation = ('Localized previews need SAM masks covering the representative frame; '
                              'these are palette-only references and cannot be selected as a protected-subject recipe. Run subject tracking '
                              'first, or the full render will plan localized grading separately.')
                proposal = proposal.model_copy(deep=True)
                proposal.mode = 'global'
                proposal.local = None
                proposal = type(proposal).model_validate(proposal.model_dump())
        frame = visual_evidence.extract(original, selection['manifest'], [frame_index], self.settings, cancel, width=256)[frame_index]
        if alpha is not None:
            import cv2
            alpha = cv2.resize(alpha, (frame.shape[1], frame.shape[0]), interpolation=cv2.INTER_LINEAR)
        built = look_previews_module.build(proposal, frame, brief_sha256=brief_hash,
                                           frame_index=frame_index, frame_time=times[frame_index],
                                           project_id=project_id, alpha=alpha)
        import cv2
        looks_directory = directory / 'looks'
        looks_directory.mkdir(parents=True, exist_ok=True)
        records = []
        for item in built:
            image = item.pop('image')
            item['preview_only']=limitation is not None
            path = looks_directory / (item['id'] + '.jpg')
            if not cv2.imwrite(str(path), image[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, 88]):
                raise Problem('LOOK_IMAGE_FAILED', 'A look preview image could not be saved.',
                              'Check local storage and retry look generation.', 507)
            records.append(LookPreview(**item))
        def ready(s):
            s.looks = records
            s.selected_look_id = None
            s.looks_brief_sha256 = brief_hash
            s.looks_frame_index = frame_index
            s.looks_frame_time = times[frame_index]
            s.looks_limitation = limitation
            s.status = s.looks_return_status
            s.stage = 'looks_ready'
            s.active_job = None
            self.event(s, 'Look previews ready', 'Eight graded previews on one representative frame; select one before rendering.',
                       look_count=len(records), frame_index=frame_index, uses_local=any(r.uses_local for r in records))
        self.mutate(project_id, ready)

    def _look_mask(self, directory, state, proposal, frame_index, cancel):
        """Soft alpha for the representative frame from a cached SAM run, or None."""
        try:
            for run in reversed(state.segmentations):
                if run.status != 'ready' or run.concept.casefold() != proposal.local.concept.strip().casefold():
                    continue
                if run.recovery_parent_id:
                    proof=directory/'segmentations'/run.id/'coverage-verification.json'
                    if not proof.is_file() or json.loads(proof.read_text()).get('source_sha256')!=state.source.sha256:continue
                manifest_path = directory / 'segmentations' / run.id / 'manifest.json'
                if not manifest_path.is_file():
                    continue
                manifest = json.loads(manifest_path.read_text())
                if manifest['frame_map']['source_sha256'] != state.source.sha256:
                    continue
                by_frame = local_grading.records_for_range(manifest, manifest_path.parent, state.source,
                                                           frame_index, frame_index, proposal.local.selection)
                binary, _ids = local_grading.raster(by_frame[frame_index],
                                                    manifest['frame_map']['proxy']['width'],
                                                    manifest['frame_map']['proxy']['height'])
                return binary.astype('float32')
        except Problem:
            return None
        return None

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

    def segment(self,project_id,concept,revision,command_id,duration_seconds=None):
        concept=" ".join(concept.split())
        if not concept or len(concept)>120 or any(c in concept for c in [',',';','\n','<','>']):
            raise Problem('SAM_CONCEPT_INVALID','Enter one short subject phrase.','For example: motorcycle. Use one concept per request.',422)
        with self.store.lock:
            state=self.store.get(project_id)
            fingerprint=self.fingerprint('segment',[concept,duration_seconds])
            if self.receipt(state,command_id,fingerprint):return state
            self.check_revision(state,revision)
            prior=next((r for r in state.segmentations if r.id==state.segmentation_target_id),None)
            recoverable=state.status in {'failed','cancelled'} and state.job_kind=='segment' and prior is not None
            if (state.status not in {'brief_ready','render_ready'} and not recoverable) or not state.preview or not state.source:
                raise Problem('SAM_NOT_READY','Prepare the source and finish the brief before tracking a subject.','Finish the consultation first.',409)
            if not self.sam_provider.available():
                raise Problem('SAM_NOT_CONFIGURED','The Meta API key is not configured.','Use the same MODEL_API_KEY as Muse.',503)
            run=SegmentationRun(id=str(uuid4()),concept=concept,model=self.settings.sam_model,
                                return_status=prior.return_status if recoverable else state.status,
                                duration_seconds=duration_seconds)
            state.segmentations.append(run);state.segmentation_target_id=run.id
            state.receipts[command_id]=fingerprint
            self.event(state,'Subject tracking requested',concept,actor='user',segmentation_id=run.id)
            self.submit(state,'segment')
            return state

    def run_segmentation(self,project_id,cancel,run_id=None,automatic=False):
        state=self.store.get(project_id)
        target=next((r for r in state.segmentations if r.id==(run_id or state.segmentation_target_id)),None)
        if not target or not state.source:raise Problem('SAM_STATE_INVALID','Tracking has no matching source/run.','Refresh the project.',409)
        directory=self.store.directory(project_id);output=directory/'segmentations'/target.id
        output.mkdir(parents=True,exist_ok=True)
        def update(status,stage,**values):
            def change(s):
                run=next(r for r in s.segmentations if r.id==target.id)
                run.status=status;s.stage=stage
                for name,value in values.items():setattr(run,name,value)
            self.mutate(project_id,change)
        update('preparing','verify_segmentation_input')
        frame_map=sam.mapping(directory,state.source,self.settings,cancel)
        input_path=directory/frame_map.get('proxy_filename','analysis.mp4')
        full_proxy=input_path
        if target.duration_seconds is not None or target.start_seconds or target.first_frame is not None:
            selected=([f for f in frame_map['frames'] if target.first_frame<=f['index']<=target.last_frame]
                      if target.first_frame is not None else
                      [f for f in frame_map['frames'] if target.start_seconds<=f['time']<target.start_seconds+(target.duration_seconds or state.source.duration)])
            if not selected:raise Problem('SAM_INTERVAL_EMPTY','The requested mask interval has no frames.','Use a source interval.',422)
            count=len(selected);first=selected[0]['index'];last=selected[-1]['index']
            frame_map['source_frame_count']=frame_map['frame_count']
            frame_map['frames']=list(reversed(selected)) if target.reverse_tracking else selected;frame_map['frame_count']=count
            input_path=output/'input.mp4'
            if not input_path.is_file():
                temporary=output/'input.partial.mp4'
                filters=fr'select=between(n\,{first}\,{last}),'+('reverse,' if target.reverse_tracking else '')+'setpts=PTS-STARTPTS'
                sam.command([self.settings.ffmpeg,'-v','error','-y','-i',str(full_proxy),
                    '-an','-vf',filters,'-frames:v',str(count),'-c:v','libx264','-preset','veryfast','-crf','26',
                    '-fps_mode','passthrough','-movflags','+faststart',str(temporary)],cancel,120)
                temporary.replace(input_path)
            inspected=sam.frame_info(input_path,self.settings,cancel)
            origin=selected[0]['proxy_pts']
            expected=[i/float(Fraction(frame_map['proxy']['rate'])) for i in range(count)]
            if len(inspected['pts'])!=count or any(abs(t-e)>0.002 for t,e in zip(inspected['normalized'],expected)):
                raise Problem('SAM_EXCERPT_MAPPING','The excerpt changed frame mapping.','Inspect the prepared proxy.',422)
            frame_map['proxy_sha256']=sam.checksum(input_path)
        frame_map['scope']={'start_seconds':target.start_seconds,'requested_duration':target.duration_seconds,'full_source_frames':
                            frame_map.get('source_frame_count',frame_map['frame_count'])}
        if target.reverse_tracking:frame_map['scope']['direction']='reverse_for_coverage_recovery'
        previous_map=json.loads((output/'frame-map.json').read_text()) if (output/'frame-map.json').exists() else None
        if (output/'stream-complete.json').exists() and previous_map!=frame_map:
            raise Problem('SAM_SOURCE_CHANGED','The completed stream belongs to a different source mapping.','Start a new tracking run.',409)
        sam.atomic(output/'frame-map.json',frame_map)
        if (output/'manifest.json').is_file():
            manifest=json.loads((output/'manifest.json').read_text())
            if manifest['frame_map']!=frame_map:
                raise Problem('SAM_SOURCE_CHANGED','The saved masks refer to different media.','Start a new tracking request.',409)
        else:
            upload=state.sam_upload or {}
            remote=upload.get('file_id') if upload.get('sha256')==frame_map['proxy_sha256'] else None
            def remember(file_id):
                self.mutate(project_id,lambda s:setattr(s,'sam_upload',{'file_id':file_id,'sha256':frame_map['proxy_sha256']}))
            def progress(snapshot):
                if snapshot is None:
                    update('preparing','decode_segmentation_masks');return
                if isinstance(snapshot,dict):
                    update('streaming','segment_subject',received_frames=snapshot['received_frames']);return
                masks=[r for r in snapshot.records if r.kind=='mask']
                update('preparing','decode_segmentation_masks',mask_count=len(masks),frame_count=frame_map['frame_count'],
                       frames_with_masks=len({r.frame.frame_index for r in masks if r.frame}))
            request_id=str(uuid4())
            def requested():
                def change(s):
                    s.sam_calls+=1
                    next(r for r in s.segmentations if r.id==target.id).request_count+=1
                    self.event(s,'Tracking subject with SAM',actor='muse',model=self.settings.sam_model,
                               request_id=request_id,segmentation_id=target.id)
                self.mutate(project_id,change)
                update('streaming','segment_subject',frame_count=frame_map['frame_count'])
            def usage(value):
                self.mutate(project_id,lambda s:self.event(s,'SAM request usage',actor='muse',
                    model=self.settings.sam_model,request_id=request_id,video_frames_processed=value.get('video_frames_processed'),
                    token_usage=True,**usage_details(value)))
            if target.object_prompt:
                method=getattr(self.sam_provider,'segment_prompted',None)
                if method is None:raise Problem('SAM_PROMPTING_UNAVAILABLE','This SAM provider cannot perform instance-prompted tracking.',
                    'Use the hosted SAM provider with box/point prompting support.',503)
                result=method(input_path,target.concept,remote,remember,cancel,progress,output/'raw.txt',usage,requested,
                    object_prompt=target.object_prompt,propagation_direction=target.propagation_direction)
            else:
                result=self.sam_provider.segment(input_path,target.concept,remote,remember,cancel,progress,
                    output/'raw.txt',usage,requested)
            manifest=sam.persist_result(result,frame_map,output,cancel)
            manifest['processing_config']=sam.processing_config(target.model)
            sam.atomic(output/'manifest.json',manifest)
        self.check_cancel(cancel)
        if manifest['mask_count'] and not automatic:
            update('overlay','render_mask_overlay')
            sam.overlay(input_path,output,manifest,self.settings,cancel)
        self.check_cancel(cancel)
        def ready(s):
            run=next(r for r in s.segmentations if r.id==target.id)
            run.status='ready' if manifest['mask_count'] else 'empty'
            run.frame_count=frame_map['frame_count'];run.mask_count=manifest['mask_count']
            run.frames_with_masks=manifest['frames_with_masks'];run.tracks=manifest['tracks'];run.error=None
            run.received_frames=manifest['frames_with_masks']
            prefix=f'/api/projects/{project_id}/segmentations/{run.id}'
            run.preview_url=prefix+'/preview' if run.status=='ready' and not automatic else None
            run.manifest_url=prefix+'/manifest'
            if not automatic:s.status=run.return_status;s.stage='segmentation_ready';s.active_job=None
            self.event(s,('Subject masks partial' if run.frames_with_masks<run.frame_count else 'Subject masks ready') if run.status=='ready' else 'No matching subject found',
                       'Masks will be validated before localized compositing.' if automatic else 'Review object IDs and the moving overlay; no diagnostic mask is applied without a grading plan.',
                       mask_count=run.mask_count,frames_with_masks=run.frames_with_masks,frame_count=run.frame_count)
        self.mutate(project_id,ready)
        return manifest,output

    def local_masks(self,project_id,candidate_id,evidence,plan,cancel):
        """Masks for plan.concept, falling back to simpler phrases only when SAM finds nothing at all.

        Returns (records, ranges, concept actually used, selection actually used). At most
        concepts.MAX_ATTEMPTS phrases are tried; a partial gap is never "fixed" by switching phrase,
        it goes through coverage recovery. A plural or material phrase ("rocks", "water") denotes every
        match, so single_instance is not demanded of it; a singular countable phrase stays strict.
        """
        phrases=sam_concepts.candidates(plan.concept,plan.concept_alternatives)
        for number,phrase in enumerate(phrases):
            selection=('all_matches' if plan.selection=='single_instance' and sam_concepts.names_a_class(phrase) else plan.selection)
            attempt=plan.model_copy(update={'concept':phrase,'selection':selection})
            try:
                records,ranges=self._local_masks(project_id,candidate_id,evidence,attempt,cancel)
                if phrase!=plan.concept:
                    self.mutate(project_id,lambda s:self.event(s,'Using a simpler SAM phrase',
                        f'SAM found nothing for "{plan.concept}"; "{phrase}" matched.',planned_concept=plan.concept,concept=phrase))
                if selection!=plan.selection:
                    self.mutate(project_id,lambda s:self.event(s,'Using every match of a class phrase',
                        f'"{phrase}" names a material or several objects, so every SAM match is graded rather than one chosen instance.',
                        concept=phrase,object_count=len({r['object_id'] for rows in records.values() for r in rows})))
                return records,ranges,phrase,selection
            except Problem as failure:
                if failure.code=='LOCAL_SUBJECT_AMBIGUOUS':
                    # Never guess one instance: offer the user an explicit "grade all matches" choice instead.
                    failure.details.update(candidate_id=candidate_id,planned_concept=plan.concept,concept=phrase,all_matches_available=True)
                    failure.action=(f'SAM found {failure.details.get("object_count")} separate "{phrase}" objects. Choose "Grade all matches" to '
                                    'treat every one as this area, or use a more specific phrase. No object was guessed.')
                    raise
                if not failure.details.get('no_masks_found'):raise
                if number+1<len(phrases):
                    self.mutate(project_id,lambda s:self.event(s,'SAM found no match',
                        f'No mask for "{phrase}" in any required frame; trying "{phrases[number+1]}".',concept=phrase))
                    continue
                raise Problem('LOCAL_CONCEPT_NOT_FOUND','SAM found none of: '+', '.join(f'"{p}"' for p in phrases)+'.',
                    'Name the area with a short common noun SAM can see (for example "rocks", "sky", "water", "boat"), or revise the '
                    'direction. No global fallback was rendered.',422,planned_concept=plan.concept,tried=' | '.join(phrases),
                    missing_frames=failure.details.get('missing_frames'),first_missing_frame=failure.details.get('first_missing_frame'))

    def _local_masks(self,project_id,candidate_id,evidence,plan,cancel):
        state=self.store.get(project_id);source=state.source;directory=self.store.directory(project_id)
        ranges=local_grading.plan_ranges(plan,evidence,source);records={};step=float(1/Fraction(evidence['frame_map']['proxy']['rate']))
        for first,last in ranges:
            self.check_cancel(cancel);state=self.store.get(project_id);found=None
            for run in reversed(state.segmentations):
                if run.recovery_parent_id or run.reverse_tracking:continue
                if run.concept.casefold()!=plan.concept.strip().casefold() or run.model!=self.settings.sam_model:continue
                path=directory/'segmentations'/run.id/'manifest.json'
                if not path.is_file():continue
                manifest=json.loads(path.read_text());mapping=manifest['frame_map']
                # Original v1 diagnostics shipped with the pinned 0.1.0 parser.
                config=manifest.get('processing_config',{'model':run.model,'parser':'0.1.0','mask_encoding':'one_bit','prompt':'concept'})
                if config!=sam.processing_config(self.settings.sam_model):continue
                covered={f['index'] for f in mapping['frames']}
                if (mapping['source_sha256']==source.sha256 and set(range(first,last+1))<=covered
                    and mapping['proxy']==evidence['frame_map']['proxy']
                    and all(abs(f['time']-evidence['frame_map']['frames'][f['index']]['time'])<=.002 for f in mapping['frames'])):
                    found=(run.id,manifest,path.parent);break
            if found:
                run_id,manifest,mask_directory=found
                self.mutate(project_id,lambda s:self.event(s,'Reusing cached subject masks',concept=plan.concept,first_frame=first,last_frame=last))
            else:
                pending=next((r for r in reversed(state.segmentations) if r.id in next(c for c in state.candidates if c.id==candidate_id).mask_run_ids
                    and r.concept==plan.concept and abs(r.start_seconds-evidence['frame_map']['frames'][first]['time'])<.002
                    and r.status not in {'ready','empty'}),None)
                run_id=pending.id if pending else str(uuid4())
                def attach(s):
                    if not pending:
                        s.segmentations.append(SegmentationRun(id=run_id,concept=plan.concept,model=self.settings.sam_model,
                            start_seconds=evidence['frame_map']['frames'][first]['time'],duration_seconds=(last-first+1)*step,
                            first_frame=first,last_frame=last))
                    target=next(c for c in s.candidates if c.id==candidate_id)
                    if run_id not in target.mask_run_ids:target.mask_run_ids.append(run_id)
                    self.event(s,'Automatic SAM masks required',concept=plan.concept,first_frame=first,last_frame=last,segmentation_id=run_id)
                self.mutate(project_id,attach)
                try:
                    if not self.sam_provider.available() and not (directory/'segmentations'/run_id/'stream-complete.json').exists():
                        raise Problem('SAM_NOT_CONFIGURED','The localized plan requires SAM, but the segmentation key is unavailable.',
                                      'Configure SAM access or explicitly revise the brief; no global fallback was made.',503)
                    manifest,mask_directory=self.run_segmentation(project_id,cancel,run_id,automatic=True)
                except Exception as error:
                    failure=safe_problem(error,'segment')
                    def failed(s):
                        r=next(r for r in s.segmentations if r.id==run_id);r.status='cancelled' if failure.code=='JOB_CANCELLED' else 'failed';r.error=failure.public()
                    self.mutate(project_id,failed);raise
            def attached(s):
                target=next(c for c in s.candidates if c.id==candidate_id)
                if run_id not in target.mask_run_ids:target.mask_run_ids.append(run_id)
            self.mutate(project_id,attached)
            current=local_grading.records_for_range(manifest,mask_directory,source,first,last,plan.selection,require_complete=False)
            if not current:
                # Nothing to anchor a recovery on: the phrase itself did not match (local_masks may try a simpler one).
                raise Problem('LOCAL_MASK_GAP',f'SAM found no "{plan.concept}" in the required frames.',
                    'Try a shorter common noun for this area. No global fallback was rendered.',422,
                    missing_frames=last-first+1,first_missing_frame=first,no_masks_found=True,concept=plan.concept)
            if len(current)<last-first+1:
                current=self.recover_mask_coverage(project_id,candidate_id,run_id,current,evidence,plan,first,last,cancel)
            records.update(current)
        return records,ranges

    def recover_mask_coverage(self,project_id,candidate_id,parent_id,current,evidence,plan,first,last,cancel):
        """At most two source-bound repairs; overlap establishes identity, never fills a hole."""
        import numpy as np
        state=self.store.get(project_id);directory=self.store.directory(project_id);source=state.source
        missing=[i for i in range(first,last+1) if i not in current]
        last_repair_id=None
        def stop(reason):
            failure=Problem('LOCAL_MASK_GAP','Required local frames still have no verified subject mask.',
                'Coverage recovery stopped: '+reason+'. No global fallback was rendered.',422,
                missing_frames=len(missing),first_missing_frame=missing[0],recovery_reason=reason)
            if last_repair_id:
                def rejected(s):
                    run=next(r for r in s.segmentations if r.id==last_repair_id);run.status='failed';run.error=failure.public()
                self.mutate(project_id,rejected)
            raise failure
        if not current or plan.selection!='single_instance':stop('a single visible anchor subject is required')
        source_ids={r['object_id'] for rows in current.values() for r in rows}
        if len(source_ids)!=1:stop('the original track is ambiguous')
        canonical=next(iter(source_ids));mapping=evidence['frame_map'];width=mapping['proxy']['width'];height=mapping['proxy']['height']
        parent=next(r for r in state.segmentations if r.id==parent_id)
        # Reuse prior repair artifacts before considering another billable request.
        runs=[next(r for r in state.segmentations if r.id==rid) for rid in parent.recovery_run_ids]
        for attempt in range(2):
            self.check_cancel(cancel)
            missing=[i for i in range(first,last+1) if i not in current]
            if not missing:return current
            begin=missing[0];end=begin
            while end+1<=last and end+1 not in current:end+=1
            reverse=begin==first
            anchor=[i for i in range(end+1,min(last,end+15)+1) if i in current] if reverse else [i for i in range(max(first,begin-15),begin) if i in current]
            if len(anchor)<3:stop('fewer than three overlap frames establish identity')
            lo=begin if reverse else min(anchor);hi=max(anchor) if reverse else end
            if hi-lo+1>300:stop('missing span exceeds the 300-frame repair budget')
            from .sam_prompting import seed_from_masks
            prompt,seeds=seed_from_masks(current,anchor,lo,width,height,canonical)
            # Native bidirectional propagation returns the anchor context AND
            # the missing prefix. Never reverse/re-detect the video itself.
            run=next((r for r in runs if r.first_frame==lo and r.last_frame==hi and not r.reverse_tracking
                and r.object_prompt==prompt and r.propagation_direction=='both'),None)
            if run is None:
                state=self.store.get(project_id);parent=next(r for r in state.segmentations if r.id==parent_id)
                if len(parent.recovery_run_ids)>=2:stop('the two-request recovery budget is exhausted')
                target=next(c for c in state.candidates if c.id==candidate_id)
                repair_ids={r.id for r in state.segmentations if r.recovery_parent_id}
                if len(set(target.mask_run_ids)&repair_ids)>=2:stop('the candidate recovery request budget is exhausted')
                run=SegmentationRun(id=str(uuid4()),concept=plan.concept,model=self.settings.sam_model,
                    first_frame=lo,last_frame=hi,start_seconds=mapping['frames'][lo]['time'],
                    duration_seconds=(hi-lo+1)/float(Fraction(mapping['proxy']['rate'])),recovery_parent_id=parent_id,
                    object_prompt=prompt,propagation_direction='both')
                def attach(s):
                    next(r for r in s.segmentations if r.id==parent_id).recovery_run_ids.append(run.id)
                    s.segmentations.append(run);next(c for c in s.candidates if c.id==candidate_id).mask_run_ids.append(run.id)
                    self.event(s,'Recovering missing SAM coverage','Instance-prompted native propagation from existing subject masks; original masks remain unchanged.',
                        first_frame=lo,last_frame=hi,propagation_direction='both',seed_frames=[v['source_frame'] for v in seeds],segmentation_id=run.id)
                self.mutate(project_id,attach);runs.append(run)
            def link_repair(s):
                target=next(c for c in s.candidates if c.id==candidate_id)
                if run.id not in target.mask_run_ids:target.mask_run_ids.append(run.id)
            self.mutate(project_id,link_repair)
            last_repair_id=run.id
            repair_directory=directory/'segmentations'/run.id
            if run.request_count and not (repair_directory/'manifest.json').exists() and not (repair_directory/'stream-complete.json').exists():
                stop('the prior repair request did not complete; no automatic paid retry was made')
            try:manifest,folder=self.run_segmentation(project_id,cancel,run.id,automatic=True)
            except Exception as error:
                failure=safe_problem(error,'segment')
                def failed(s):
                    target=next(r for r in s.segmentations if r.id==run.id)
                    target.status='cancelled' if failure.code=='JOB_CANCELLED' else 'failed';target.error=failure.public()
                self.mutate(project_id,failed);raise
            if manifest['frame_map']['proxy']!=mapping['proxy'] or any(
                abs(f['time']-mapping['frames'][f['index']]['time'])>.002 for f in manifest['frame_map']['frames']):stop('repair frame mapping changed')
            proposed=local_grading.records_for_range(manifest,folder,source,lo,hi,plan.selection,require_complete=False)
            ids={r['object_id'] for rows in proposed.values() for r in rows}
            if len(ids)!=1:stop('repair subject identity is ambiguous')
            if ids!={canonical}:stop('the response did not preserve the explicitly prompted object ID')
            overlap=[i for i in anchor if i in proposed]
            if len(overlap)<3:stop('the repair did not recover enough anchor frames')
            ious=[]
            for i in overlap:
                a,_=local_grading.raster(current[i],width,height);b,_=local_grading.raster(proposed[i],width,height)
                ious.append(float(np.logical_and(a,b).sum()/max(1,np.logical_or(a,b).sum())))
            if sum(v>=.7 for v in ious)<max(3,int(np.ceil(.8*len(anchor)))):stop('repair masks disagree with the existing subject in overlap')
            added=[]
            for i in missing:
                if i in proposed:
                    current[i]=[dict(r,object_id=canonical,recovery_object_id=r['object_id'],recovery_run_id=run.id) for r in proposed[i]];added.append(i)
            sam.atomic(folder/'coverage-verification.json',{'source_sha256':source.sha256,'parent_run_id':parent_id,
                'canonical_object_id':canonical,'repair_object_id':next(iter(ids)),
                'method':'native_object_prompt_v1','seeds':seeds,'propagation_direction':'both',
                'overlap_frames':overlap,'overlap_iou':ious,'min_iou':.7,'required_overlap_fraction':.8,'added_source_frames':added})
            if not added:stop('the repair returned no new missing-frame masks')
        missing=[i for i in range(first,last+1) if i not in current]
        if missing:stop('coverage remains incomplete after bounded recovery')
        return current

    def count_call(self, project_id, title,kind='consultation'):
        request_id = str(uuid4())
        def change(state):
            if kind!='critic' and state.model_calls-state.critic_calls >= self.settings.max_questions + 4:
                raise Problem("MODEL_CALL_BUDGET", "The consultation model-call budget was reached.",
                              "Review the saved evidence and answers before starting another consultation.", 409)
            state.model_calls += 1
            if kind=='critic':state.critic_calls+=1
            self.event(state, title, actor="muse", model=self.settings.model, call=state.model_calls,
                       request_id=request_id)
        self.mutate(project_id, change)
        return request_id

    def record_usage(self, project_id, request_id, stage, usage, call):
        titles = {"analysis":"Scene analysis usage", "rubric":"Muse request usage", "colorist":"Colorist request usage","critic":"Critic request usage"}
        def record(state):
            # A receipt is tied to the request, not a call number that resets with direction.
            if any(e.details and e.details.get("token_usage") and e.details.get("request_id")==request_id for e in state.events):
                return
            self.event(state,titles[stage],actor="muse",token_usage=True,request_id=request_id,
                       usage_stage=stage,model=self.settings.model,call=call,**usage_details(usage))
        self.mutate(project_id,record)

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
            elif kind == "segment":
                self.run_segmentation(project_id,cancel)
            elif kind == "looks":
                self.generate_looks(project_id, cancel)
            elif kind=='review':
                self.review_and_revise(project_id,cancel,force=True)
            else:
                self.analyze(project_id, cancel)
        except Exception as cause:
            failure = safe_problem(cause, kind)
            try:
                if failure.details.get("usage_reported"):
                    stages={"Inspecting video with Muse":"analysis","Selecting a useful clarification":"rubric",
                            "Muse is preparing a global grading recipe":"colorist","Muse is planning supported adjustments":"colorist"}
                    current=self.store.get(project_id)
                    request=next((e for e in reversed(current.events) if e.title in stages
                                  and e.details and e.details.get("request_id")),None)
                    if request:
                        self.record_usage(project_id,request.details["request_id"],stages[request.title],
                                          failure.details,request.details["call"])
                def failed(state):
                    if state.active_job != job_id:
                        return
                    state.status = "cancelled" if failure.code == "JOB_CANCELLED" else "failed"
                    state.error = failure.public()
                    state.active_job = None
                    if kind in {"render","review"}:
                        target = next((c for c in state.candidates if c.id == state.render_target_id), None)
                        if target and (target.status!='ready' or failure.code in creative_review.TECHNICAL_ERRORS):
                            target.status = "cancelled" if failure.code == "JOB_CANCELLED" else "failed"
                            target.error = failure.public()
                            if state.selected_candidate_id==target.id:
                                state.selected_candidate_id=next((c.id for c in reversed(state.candidates) if c.status=='ready' and c.id!=target.id),None)
                        elif target and target.status=='ready' and not state.selected_candidate_id:
                            state.selected_candidate_id=target.id
                    if kind=="segment":
                        target=next((r for r in state.segmentations if r.id==state.segmentation_target_id),None)
                        if target:
                            target.status='cancelled' if failure.code=='JOB_CANCELLED' else 'failed'
                            target.error=failure.public()
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
        # The exact frame-timestamp probe of the original (needed by masks, evidence and rendering) runs
        # while the previews encode, so later stages read it from cache instead of decoding again.
        from .frames import probe_frames
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='takeone-frame-probe') as pool:
            probe = pool.submit(probe_frames, directory / "source.bin", self.settings, cancel)
            preview = make_previews(directory, source, project_id, self.settings, cancel)
            try:
                probe.result()
            except Problem as error:
                if error.code == "JOB_CANCELLED":
                    raise
                # Not fatal here: the same probe runs (and reports) where frame timing is actually required.
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
            request_id = self.count_call(project_id, "Inspecting video with Muse")
            call_number = self.store.get(project_id).model_calls
            def remember(remote_id):
                self.mutate(project_id, lambda s: setattr(s, "remote_file_id", remote_id))
            analysis, usage = self.provider.analyze(self.store.directory(project_id) / "analysis.mp4",
                                                    state.source.duration, state.remote_file_id, remember)
            self.record_usage(project_id,request_id,"analysis",usage,call_number)
            self.store.artifact(project_id, "analysis.json", analysis.model_dump(mode="json"))
            def analyzed(state):
                state.analysis = analysis
                state.stage = "select_question"
                self.event(state, "Scene analysis saved", analysis.summary, actor="muse")
            self.mutate(project_id, analyzed)
        self.check_cancel(cancel)
        state = self.store.get(project_id)
        if state.rubric:
            session = RubricSession.restore(state.rubric)
        else:
            session = RubricSession(RubricInput(
                user_request=state.request,
                project_id=state.id,
                project_feedback=[f for f in current_feedback(state) if f.get("text")],
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
        session.inputs.project_id = state.id
        session.inputs.project_feedback = [f for f in current_feedback(state) if f.get("text")]
        session.inputs.recent_contract_errors=[p['error'] for p in state.question_proposals[-3:]
            if p.get('error') and p.get('direction_epoch')==state.request_started_at]
        session.inputs.memory_defaults = [RememberedDefault.model_validate(v)
                                         for v in self.prompt_gate.default_data(state.id)]
        proposal_usage = None
        proposal_call = None
        proposal_request_id = None
        if session.asked_count >= session.max_decisions:
            from takeone_rubric_v1.rubric import Proposal
            proposal = Proposal()
        else:
            request_id = self.count_call(project_id, "Selecting a useful clarification")
            proposal_request_id = request_id
            proposal_call = self.store.get(project_id).model_calls
            event_start = len(session.events)
            proposal = self.provider.propose(session)
            proposal_usage = next((e.get("usage") for e in reversed(session.events[event_start:])
                                   if e.get("event") == "muse_proposal"), None)
            self.record_usage(project_id,request_id,"rubric",proposal_usage,proposal_call)
            self.mutate(project_id,lambda s:s.question_proposals.append({
                "request_id":request_id,"proposal":proposal.model_dump(mode="json"),
                "selection_policy":"muse_order","selected_id":None,"error":None,"direction_epoch":state.request_started_at}))
        self.check_cancel(cancel)
        rejection_start = len(session.rejected)
        try:
            question = session.next_question(proposal)
        except ValueError as error:
            detail=str(error)
            def invalid(s):
                if proposal_request_id:
                    next(p for p in s.question_proposals if p['request_id']==proposal_request_id)['error']=detail
            self.mutate(project_id,invalid)
            raise Problem('RUBRIC_PROTOCOL_ERROR','Muse returned a question with an invalid data or tool reference.',
                          'Retry the saved question step. Answers are kept and no brief was finalized.',502,contract_error=detail)
        if question:
            reason,message="question_selected","A clarification is ready. Your answer will inform the next decision."
        elif session.asked_count >= session.max_decisions:
            reason,message="question_limit","The question limit was reached. No additional clarification call was made."
        else:
            reason="no_candidates"
            message="Muse proposed no further clarification questions." if session.answers else "Muse proposed no clarification questions."
        summary=ConsultationSummary(reason=reason,message=message,
            model_explanation=proposal.completion_reason if reason=="no_candidates" else "",
            proposed_count=len(proposal.candidates),rejected_count=len(session.rejected)-rejection_start)
        brief = None if question else session.brief()
        if brief:
            if proposal.observation_gap:
                brief.unresolved.append("Observation gap: " + proposal.observation_gap)
            if proposal.knowledge_gap:
                brief.unresolved.append("Knowledge gap: " + proposal.knowledge_gap)
            self.mutate(project_id,lambda s:setattr(s,'stage','save_brief'))
            self.store.artifact(project_id, "brief.json", brief.model_dump(mode="json"))
        def selected(state):
            state.rubric = session.snapshot()
            state.question = question
            if proposal_request_id:
                next(p for p in state.question_proposals if p['request_id']==proposal_request_id)['selected_id']=question.id if question else None
            state.consultation = summary
            state.brief = brief
            if brief:
                state.brief_revision += 1
                state.memory_influences += brief.memory_influences
            if question:
                for memory in session.inputs.memory_defaults:
                    if memory.id in question.memory_refs:
                        state.memory_influences.append({"entry_id":memory.id,"revision":memory.revision,
                                                        "stage":"question","target":question.id})
            state.answered_count = len(session.answers)
            state.status = "awaiting_answer" if question else "brief_ready"
            state.stage = state.status
            state.active_job = None
            if question:
                self.event(state, question.question, question.decision_fork, actor="muse", kind="decision", question_id=question.id)
            else:
                self.event(state, "Creative brief ready", "The original request, answers, boundaries and limitations have been saved.", actor="muse")
                self.event(state,"Consultation completed",summary.message,stop_reason=summary.reason,
                           model_explanation=summary.model_explanation,proposed_count=summary.proposed_count,
                           rejected_count=summary.rejected_count)
            if session.rejected:
                self.event(state, "Question validation completed", rejected_count=len(session.rejected))
        self.mutate(project_id, selected)

    def render_grade(self,project_id,cancel):
        self._render_one(project_id,cancel,keep_active=True)
        self.review_and_revise(project_id,cancel)

    def guard_grade(self,project_id,candidate,recipe,original,output,source,cancel,technical=None):
        """Resolve input-cutoff mistakes before encoding, retaining the original plan/version."""
        # Runs for every v3 plan: clipping fit when bounds are declared, and always the highlight feasibility gate.
        if recipe.tone_operator!='luminance_ocio_v3':return candidate,recipe,output
        self.mutate(project_id,lambda s:setattr(s,'stage','preflight_grade'))
        known_failure=bool(candidate.error and candidate.error.get('code')=='GRADE_EXECUTION_LIMIT' and recipe.execution_preflight)
        all_layers=[recipe.proposal.parameters]+([recipe.proposal.local.foreground,recipe.proposal.local.background] if recipe.proposal.local else [])
        if known_failure and recipe.proposal.mode=='global' and all(p.black_point==0 and p.white_point==1 for p in all_layers):
            raise Problem('GRADE_PREFLIGHT_FAILED','The previous full render failed even without input clipping cutoffs.',
                          'Revise gamma, brightness or palette choices. The same failed recipe will not be encoded again.',422,
                          previous_failure=candidate.error)
        try:
            effective,report=grade_guard.prepare_execution(original,output/'preflight',recipe,source,self.settings,cancel,
                cache_dir=self.store.directory(project_id),force_neutral_levels=known_failure)
        except Problem as failure:
            if failure.code!='GRADE_PREFLIGHT_FAILED':raise
            state=self.store.get(project_id)
            target=next(c for c in state.candidates if c.id==candidate.id)
            if target.preflight_replan_attempted:raise
            if sum(c.brief_sha256==candidate.brief_sha256 for c in state.candidates)>=self.settings.max_render_iterations:raise
            # This is an executable-plan failure, not a rendered-image critique.
            # One Muse planning request may change creative controls; the tool
            # itself must not silently alter gamma/palette or relax constraints.
            def attempted(s):
                next(c for c in s.candidates if c.id==candidate.id).preflight_replan_attempted=True
                s.stage='plan_revision'
            self.mutate(project_id,attempted);self.check_cancel(cancel)
            selection,_,_=grade_guard.source_pixels(original,recipe,source,self.settings,cancel,self.store.directory(project_id))
            stats,images=rendering.samples(original,source,self.settings,cancel,selection=selection)
            highlight_block=failure.details.get('preflight',{}).get('blocked_by')=='highlight_lift'
            instruction=(HIGHLIGHT_REPLAN_INSTRUCTION if highlight_block else
                'Revise the executable plan using the actual images and failure measurements. Keep the same creative brief and execution limits. Do not just resubmit the failed controls. Input-cutoff removal was insufficient; supported tonal controls or local treatment may need revision. Do not call this a review of a completed candidate.')
            context={**(technical or {}),'current_video_feedback':current_feedback(state),
                'preflight_failure':{'failed_plan':recipe.proposal.model_dump(mode='json'),'evidence':failure.details,'instruction':instruction}}
            request_id=self.count_call(project_id,'Muse is revising a highlight-incompatible plan' if highlight_block else 'Muse is revising a clipping-incompatible plan')
            call=self.store.get(project_id).model_calls
            try:proposal,usage=self.provider.grade(state.brief,state.analysis,source,stats,images,technical_context=context)
            except Problem as error:
                if error.details.get('usage_reported'):self.record_usage(project_id,request_id,'colorist',error.details,call)
                raise
            self.record_usage(project_id,request_id,'colorist',usage,call);self.check_cancel(cancel)
            prior=recipe.proposal.execution_limits
            for field in ['max_new_black_fraction','max_new_white_fraction','subject_max_chroma_change','subject_min_luma_ratio']:
                old=getattr(prior,field);new=getattr(proposal.execution_limits,field)
                if old is not None and (new is None or (new<old if field=='subject_min_luma_ratio' else new>old)):
                    setattr(proposal.execution_limits,field,old)
            if (proposal.unfulfilled_requests or
                recipe.proposal.local and recipe.proposal.local.preserve_subject_color and (not proposal.local or not proposal.local.preserve_subject_color) or
                creative_review.execution_identity(proposal,duration=source.duration)==creative_review.execution_identity(recipe.proposal,duration=source.duration)):
                failure.details['replan_outcome']='No different supported plan with the same protections was returned.'
                failure.action='Muse could not repair this plan within the saved constraints. Refine the creative brief; repeated retry will not make another planning call.'
                if proposal.unfulfilled_requests:
                    failure.details['unfulfilled_instructions']='; '.join(proposal.unfulfilled_requests)[:1500]
                    failure.action=('Muse reports that the supported controls cannot execute: '+failure.details['unfulfilled_instructions']
                                    +'. Refine the brief or accept a limited result; repeated retry will not make another planning call.')[:1500]
                raise
            revised=recipe.model_copy(deep=True);revised.proposal=proposal;revised.execution_preflight=None;revised.execution_adjustment=None
            revised.timeline=(recipe.timeline or local_grading.timeline(self.store.directory(project_id),source,self.settings,cancel)) if proposal.local else None
            child=RenderCandidate(id=str(uuid4()),name='V'+str(len(state.candidates)+1),source_sha256=source.sha256,
                brief_sha256=candidate.brief_sha256,generation_id=candidate.generation_id or str(uuid4()),
                parent_id=candidate.id,iteration=candidate.iteration+1,recipe=revised,status='rendering',preflight_replan_attempted=True)
            def replanned(s):
                parent=next(c for c in s.candidates if c.id==candidate.id);parent.status='failed';parent.error=failure.public()
                parent.revision_stop_reason='preflight_replanned_by_muse';s.candidates.append(child);s.render_target_id=child.id
                self.event(s,'Muse revised the incompatible plan','A new candidate preserves the brief and clipping bounds. It must pass preflight before full encoding.',actor='muse',candidate=child.name,parent_id=candidate.id)
            self.mutate(project_id,replanned)
            self.store.candidate_artifact(project_id,child.id,'plan.json',proposal.model_dump(mode='json'))
            self.store.candidate_artifact(project_id,child.id,'recipe.json',revised.model_dump(mode='json'))
            return self.guard_grade(project_id,child,revised,original,self.store.directory(project_id)/'candidates'/child.id,source,cancel,technical)
        self.check_cancel(cancel)
        if report.get('highlight_gate_only'):
            self.mutate(project_id,lambda s:self.event(s,'Highlight feasibility check passed',
                'No layer darkens midtones while lifting highlights beyond the allowed margin; no clipping bound was declared, so nothing else was checked or adjusted.',
                candidate=candidate.name,elapsed_seconds=report['elapsed_seconds']))
            return candidate,recipe,output
        if report.get('changes'):
            state=self.store.get(project_id)
            if sum(c.brief_sha256==candidate.brief_sha256 for c in state.candidates)>=self.settings.max_render_iterations:
                raise Problem('RENDER_BUDGET','A clipping correction is available, but this brief has reached its candidate limit.',
                              'Review or revise the brief before another attempt. No full encode was started.',409,preflight=report)
            child=RenderCandidate(id=str(uuid4()),name='V'+str(len(state.candidates)+1),source_sha256=source.sha256,
                brief_sha256=candidate.brief_sha256,generation_id=candidate.generation_id or str(uuid4()),
                parent_id=candidate.id,iteration=candidate.iteration+1,recipe=effective,status='rendering',preflight_replan_attempted=candidate.preflight_replan_attempted)
            def corrected(s):
                parent=next(c for c in s.candidates if c.id==candidate.id)
                parent.status='failed';parent.revision_stop_reason='input_levels_corrected_before_encoding'
                parent.error=candidate.error or Problem('GRADE_PREFLIGHT_REVISED','Input cutoffs needed correction to meet the saved clipping bounds.',
                    'The corrected child candidate is being rendered; the original recipe is preserved.',422,preflight=report).public()
                s.candidates.append(child);s.render_target_id=child.id;s.stage='plan_revision'
                self.event(s,'Input level clipping correction','Preserved the original recipe and all clipping limits; only input black/white cutoffs changed.',
                    candidate=child.name,parent_id=candidate.id,changes=report['changes'],preflight_seconds=report['elapsed_seconds'])
            self.mutate(project_id,corrected)
            candidate=child;output=self.store.directory(project_id)/'candidates'/child.id;output.mkdir(parents=True,exist_ok=True)
            self.store.candidate_artifact(project_id,child.id,'plan.json',effective.proposal.model_dump(mode='json'))
        self.store.candidate_artifact(project_id,candidate.id,'recipe.json',effective.model_dump(mode='json'))
        self.mutate(project_id,lambda s:setattr(next(c for c in s.candidates if c.id==candidate.id),'recipe',effective))
        self.event_guard_pass(project_id,candidate,report)
        return candidate,effective,output

    def event_guard_pass(self,project_id,candidate,report):
        localized=bool(report.get('math',{}).get('layers')) if isinstance(report.get('math'),dict) else False
        title='Localized grading preflight passed' if localized else 'Encoded grading preflight passed'
        reason=('Each localized layer satisfies the saved bounds on bounded samples; SAM, matte and full output validation are still required.'
                if localized else 'Representative original-resolution encoded frames satisfy the saved bounds; full output validation is still required.')
        self.mutate(project_id,lambda s:self.event(s,title,reason,
            candidate=candidate.name,elapsed_seconds=report['elapsed_seconds']))

    def _render_one(self, project_id, cancel,keep_active=False):
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
        evidence=None;local_limitation=None
        try:evidence=local_grading.timeline(directory,state.source,self.settings,cancel)
        except Problem as error:
            if error.code in {'SOURCE_CHANGED','JOB_CANCELLED'}:raise
            local_limitation=error.message
        technical={'operations':['global_grade','cooling','brightness_gain','highlight_ceiling'],
                   'localized_available':evidence is not None,
                   'limitations':[] if evidence else [local_limitation or 'No verified local timeline.'],
                   'shots':evidence['shots'] if evidence else [],
                   'current_video_feedback':[f for f in current_feedback(state) if f.get('text')],
                   'previous_candidates':[{'id':c.id,'brief_sha256':c.brief_sha256,'plan':c.recipe.proposal.model_dump(mode='json') if c.recipe else None,
                       'technical_result':c.validation,'creative_opinion':c.creative_review.model_dump(mode='json') if c.creative_review else None,
                       'execution_error':{'code':c.error.get('code'),'message':c.error.get('message'),
                           'observed_clipping':c.error.get('details',{}).get('observed_clipping')} if c.error else None}
                       for c in state.candidates[-3:] if c.status in {'ready','failed'}]}
        technical['available_masks']=[]
        for run in state.segmentations:
            manifest_path=directory/'segmentations'/run.id/'manifest.json'
            if run.status!='ready' or not manifest_path.is_file():continue
            saved=json.loads(manifest_path.read_text())
            if saved['frame_map']['source_sha256']==state.source.sha256:
                technical['available_masks'].append({'concept':run.concept,'start':run.start_seconds,
                    'duration':run.duration_seconds or state.source.duration,'object_ids':[t['object_id'] for t in run.tracks]})
        if evidence:technical['operations']+=['regional_grade','selective_gain','multi_region_grade']
        if not candidate.recipe:
            # A look selected for this exact brief is the approved style
            # recipe: use its stored proposal verbatim, with no planner call.
            # Any feedback changes the brief hash, so a stale look never applies.
            selected_look=next((item for item in state.looks if item.id==state.selected_look_id),None)
            use_look=(selected_look is not None and state.looks_brief_sha256==candidate.brief_sha256
                    and selected_look.brief_sha256==candidate.brief_sha256) and candidate.approved_proposal is None
            if candidate.approved_proposal is not None:
                # The user approved this exact plan (render_supported): no planner call, no substitution.
                proposal=candidate.approved_proposal.model_copy(deep=True)
                selection=visual_evidence.plan(original,state.source,self.settings,cancel,proxy=directory/'analysis.mp4',
                    shots=evidence['shots'] if evidence else None,cache=directory/'source-evidence-v3.json')
                measurements,_approved_images=rendering.samples(original,state.source,self.settings,cancel,selection=selection)
                technical['sampling']={k:selection[k] for k in ['selected_indices','selection_reasons','scanned_frames','uncovered_shots','budget','uncertainty']}
                self.mutate(project_id,lambda s:self.event(s,'Using the approved supported plan',
                    'No planner call was made; the omitted instructions are recorded in the plan limitations.',candidate=candidate.name))
            elif use_look:
                proposal=selected_look.proposal.model_copy(deep=True)
                selection=visual_evidence.plan(original,state.source,self.settings,cancel,proxy=directory/'analysis.mp4',
                    shots=evidence['shots'] if evidence else None,cache=directory/'source-evidence-v3.json')
                measurements,_look_images=rendering.samples(original,state.source,self.settings,cancel,selection=selection)
                technical['sampling']={k:selection[k] for k in ['selected_indices','selection_reasons','scanned_frames','uncovered_shots','budget','uncertainty']}
                self.mutate(project_id,lambda s:self.event(s,'Using selected look',
                    'The approved look preview recipe is the render recipe; no planner call was made.',
                    candidate=candidate.name,look_id=selected_look.id,look_name=selected_look.name))
            # Unchanged brief + source with a ready candidate: reuse its saved
            # plan instead of resending the same brief, analysis and sample
            # images to the planner. Any feedback changes the brief hash, so
            # a changed brief always takes the full planning path below.
            approved=candidate.approved_proposal is not None
            prior=None if use_look or approved else next((c for c in reversed(state.candidates) if c.id!=candidate.id and c.status=='ready'
                        and c.brief_sha256==candidate.brief_sha256 and c.source_sha256==state.source.sha256
                        and c.recipe and c.recipe.evidence_selection and display_oriented(c.recipe.evidence_selection,state.source)),None)
            if use_look or approved:
                pass
            elif prior is not None:
                proposal=prior.recipe.proposal.model_copy(deep=True)
                selection=prior.recipe.evidence_selection
                measurements=prior.recipe.source_samples
                technical['sampling']={k:selection[k] for k in ['selected_indices','selection_reasons','scanned_frames','uncovered_shots','budget','uncertainty']}
                self.mutate(project_id,lambda s:self.event(s,'Reusing saved grading plan',
                    'The brief and source are unchanged, so no planner call or evidence resend was made.',
                    candidate=candidate.name,reused_from=prior.name))
            else:
                selection=visual_evidence.plan(original,state.source,self.settings,cancel,proxy=directory/'analysis.mp4',
                    shots=evidence['shots'] if evidence else None,cache=directory/'source-evidence-v3.json')
                measurements,images=rendering.samples(original,state.source,self.settings,cancel,selection=selection)
                technical['sampling']={k:selection[k] for k in ['selected_indices','selection_reasons','scanned_frames','uncovered_shots','budget','uncertainty']}
                self.check_cancel(cancel)
                request_id = self.count_call(project_id, "Muse is planning supported adjustments")
                call_number = self.store.get(project_id).model_calls
                proposal, usage = self.provider.grade(state.brief, state.analysis, state.source, measurements, images,technical_context=technical)
                self.record_usage(project_id,request_id,"colorist",usage,call_number)
            self.store.candidate_artifact(project_id,candidate.id,'plan.json',proposal.model_dump(mode='json'))
            if proposal.unfulfilled_requests:
                raise Problem('GRADE_REQUEST_UNFULFILLED','The planner reported required instructions it cannot execute.',
                              'Choose "Render the supported part" to render everything else (the listed items are recorded as not done), '
                              'or revise the direction. Nothing was rendered or substituted automatically.',422,
                              unfulfilled_count=len(proposal.unfulfilled_requests),unfulfilled_instructions='; '.join(proposal.unfulfilled_requests)[:1500],
                              candidate_id=candidate.id,supported_plan_available=True)
            if proposal.mode=='localized' and not evidence:
                raise Problem('LOCAL_GRADE_UNAVAILABLE','The requested localized plan has no supported frame mapping.',
                              'Use upright SDR footage with valid, frame-preserving source/proxy mapping. No global fallback was made.',422,limitation=local_limitation)
            recipe = GradeRecipe(schema_version='takeone-grade-v3',prompt_version='colorist-v3',
                                 source_sha256=state.source.sha256, brief_sha256=candidate.brief_sha256,
                                 model=self.settings.model, proposal=proposal, source_samples=measurements,
                                 tone_operator='luminance_ocio_v3',timeline=evidence if proposal.mode=='localized' else None,evidence_selection=selection,
                                 operation_order=['layer_treatment','mask_merge','interval_fade','output_rec709'] if proposal.mode=='localized'
                                 else ['luminance_levels','luminance_tone','peak_protection','OCIO_ASC_CDL_colour'])
            self.store.candidate_artifact(project_id, candidate.id, "recipe.json", recipe.model_dump(mode="json"))
            def planned(state):
                target = next(c for c in state.candidates if c.id == candidate.id)
                target.recipe = recipe
                target.status = "rendering"
                target.error = None
                state.stage = "plan_grade"
                self.event(state, "Grading recipe saved", proposal.summary, actor="muse", kind="decision",mode=proposal.mode,
                           candidate=candidate.name, contrast=proposal.parameters.contrast,
                           saturation=proposal.parameters.saturation, gamma=proposal.parameters.gamma)
                for decision in proposal.decisions:
                    self.event(state, decision.parameter.replace('_',' ').capitalize() + " decision", decision.reason,
                               actor="muse", kind="decision", candidate=candidate.name)
                    for reference in decision.source_ids:
                        if reference.startswith("memory:"):
                            parts=reference.split(":")
                            if len(parts)==3 and parts[2].isdigit():
                                state.memory_influences.append({"entry_id":parts[1],"revision":int(parts[2]),
                                    "stage":"grade","target":candidate.id+":"+decision.parameter})
            self.mutate(project_id, planned)
        else:
            recipe = candidate.recipe
            def resuming(state):
                target = next(c for c in state.candidates if c.id == candidate.id)
                target.status, target.error = "rendering", None
                state.stage = "plan_grade"
                self.event(state, "Resuming saved recipe", "No additional model call is needed.", candidate=candidate.name)
            self.mutate(project_id, resuming)
        self.check_cancel(cancel)
        candidate,recipe,output=self.guard_grade(project_id,candidate,recipe,original,output,state.source,cancel,technical)
        records=None;mask_qa=None
        if recipe.proposal.mode=='localized':
            if evidence is None or recipe.timeline['key']!=evidence['key']:
                raise Problem('LOCAL_TIMELINE_CHANGED','The cached recipe has a different local source mapping.','Create a new candidate.',409)
            self.mutate(project_id,lambda s:setattr(s,'stage','verify_segmentation_input'))
            records,ranges,subject_concept,subject_selection=self.local_masks(project_id,candidate.id,evidence,recipe.proposal.local,cancel)
            # Frame-derived interval boundaries, never guessed request timestamps.
            step=float(1/Fraction(evidence['frame_map']['proxy']['rate']))
            recipe.timeline['active_ranges']=[[evidence['frame_map']['frames'][a]['time'],
                evidence['frame_map']['frames'][b+1]['time'] if b+1<evidence['frame_map']['frame_count'] else state.source.duration] for a,b in ranges]
            recipe.timeline['active_frame_ranges']=[list(pair) for pair in ranges]
            def validating(s):
                s.stage='validate_local_masks';self.event(s,'Validating localized masks',required_frames=len(records),shot_intervals=len(ranges))
            self.mutate(project_id,validating)
            mask_qa=local_grading.prepare_matte(directory/evidence['frame_map'].get('proxy_filename','analysis.mp4'),output,state.source,evidence,records,
                                               recipe.proposal.local,self.settings,cancel,adaptive=recipe.tone_operator=='luminance_ocio_v3')
            mask_qa['selection_policy']=subject_selection
            mask_qa['subject_concept_used']=subject_concept
            mask_qa['selected_objects_by_interval']=[{'first_frame':a,'last_frame':b,
                'object_ids':sorted({r['object_id'] for i in range(a,b+1) for r in records[i]})} for a,b in ranges]
            # Additional regions (sky, water...): the same SAM cache/recovery/QA path as the subject,
            # one validated matte each, sharing the plan's intervals and feather.
            mask_qa['regions']=[]
            for k,region in enumerate(recipe.proposal.local.regions):
                self.check_cancel(cancel)
                region_plan=recipe.proposal.local.model_copy(update={'concept':region.concept,'selection':region.selection,'regions':[],
                    'concept_alternatives':region.concept_alternatives})
                region_records,region_ranges,region_concept,region_selection=self.local_masks(project_id,candidate.id,evidence,region_plan,cancel)
                if region_ranges!=ranges:
                    raise Problem('LOCAL_REGION_RANGES','A region does not cover the same frame ranges as the subject.',
                                  'Inspect the region masks; no partial regional grade was rendered.',422,concept=region.concept)
                region_qa=local_grading.prepare_matte(directory/evidence['frame_map'].get('proxy_filename','analysis.mp4'),output,state.source,evidence,
                    region_records,region_plan,self.settings,cancel,adaptive=recipe.tone_operator=='luminance_ocio_v3',name=f'matte-region-{k}')
                mask_qa['regions'].append({'concept':region.concept,'concept_used':region_concept,'selection':region_selection,'active_frames':region_qa['active_frames'],
                                           'unstable_frames':len(region_qa['unstable_frames']),'warnings':region_qa['warnings']})
                mask_qa['warnings']=mask_qa['warnings']+[f'{region.concept}: {w}' for w in region_qa['warnings']]
            self.store.candidate_artifact(project_id,candidate.id,'recipe.json',recipe.model_dump(mode='json'))
            def compositing(s):
                s.stage='render_local_grade';self.event(s,'Rendering localized foreground/background grade',candidate=candidate.name)
            self.mutate(project_id,compositing)
        def render_stage(stage):
            self.mutate(project_id,lambda s:setattr(s,'stage',stage))
        try:
            result = rendering.render(original, output, recipe, state.source, self.settings, cancel,on_stage=render_stage)
        except Problem as failure:
            if failure.code!='GRADE_EXECUTION_LIMIT':raise
            # Encoding context can differ from sparse probes. Preserve the failed
            # attempt and permit one bounded level-only fallback, never a limit
            # increase or another blind encode of identical parameters.
            self.store.candidate_artifact(project_id,candidate.id,'validation.json',{'review_status':'rejected','error':failure.public()})
            def rejected(s):
                target=next(c for c in s.candidates if c.id==candidate.id);target.status='failed';target.error=failure.public()
                self.event(s,'Full output clipping check stopped this candidate',failure.message,code=failure.code,candidate=candidate.name)
            self.mutate(project_id,rejected)
            current=self.store.get(project_id)
            if (recipe.execution_preflight and recipe.proposal.mode=='global'
                and (recipe.proposal.parameters.black_point>0 or recipe.proposal.parameters.white_point<1)
                and sum(c.brief_sha256==candidate.brief_sha256 for c in current.candidates)<self.settings.max_render_iterations):
                self._render_one(project_id,cancel,keep_active=keep_active);return
            raise
        result['execution_capabilities']={k:technical[k] for k in ['operations','localized_available','limitations','shots']}
        self.check_cancel(cancel)
        def checking(s):
            s.stage='check_output_quality';self.event(s,'Checking rendered output',candidate=candidate.name,mode=recipe.proposal.mode)
        self.mutate(project_id,checking)
        if records is not None:
            try:
                result['subject_protection']=local_grading.check_output(original,output/'grade.mp4',state.source,evidence,records,
                    recipe.proposal.local,self.settings,cancel,limits=recipe.proposal.execution_limits,
                    adaptive=recipe.tone_operator=='luminance_ocio_v3')
                result['mask_validation']=mask_qa;result['warnings']+=mask_qa['warnings']
                result['warnings']+=result['subject_protection']['warnings']
            except Problem as failure:
                self.store.candidate_artifact(project_id,candidate.id,'validation.json',{**result,'review_status':'rejected','error':failure.public()})
                raise
        self.store.candidate_artifact(project_id, candidate.id, "validation.json", result)
        def accepted(state):
            target = next(c for c in state.candidates if c.id == candidate.id)
            target.status, target.error = "ready", None
            target.width, target.height, target.duration = result["width"], result["height"], result["duration"]
            target.preview_url = f"/api/projects/{project_id}/candidates/{target.id}/preview"
            target.download_url = f"/api/projects/{project_id}/candidates/{target.id}/download"
            target.validation = result
            target.recipe=recipe
            target.output_sha256=sam.checksum(output/'grade.mp4')
            if not keep_active:
                state.selected_candidate_id = target.id
                state.status, state.stage, state.active_job = "render_ready", "render_ready", None
            self.event(state, "Rendered " + target.name, "The video is ready for comparison. Visual approval is still required.",
                       actor="renderer", candidate=target.name, warnings=len(result["warnings"]))
        self.mutate(project_id, accepted)

    def review_candidate(self,project_id,candidate_id,revision,command_id):
        with self.store.lock:
            state=self.store.get(project_id)
            fingerprint=self.fingerprint('review',[candidate_id,rendering.fingerprint(state.brief.model_dump(mode='json')) if state.brief else None])
            if self.receipt(state,command_id,fingerprint):return state
            self.check_revision(state,revision)
            target=next((c for c in state.candidates if c.id==candidate_id and c.status=='ready'),None)
            if not target or not state.brief:raise Problem('REVIEW_NOT_READY','Select a completed candidate with a brief.','Render a valid candidate first.',409)
            current_hash=rendering.fingerprint(state.brief.model_dump(mode='json'))
            if not (self.store.directory(project_id)/'source.bin').is_file() or sam.checksum(self.store.directory(project_id)/'source.bin')!=state.source.sha256:
                raise Problem('SOURCE_CHANGED','The source no longer matches its analysis.','Restore the original source before review.',409)
            path=self.store.directory(project_id)/'candidates'/target.id/'grade.mp4'
            if not path.exists() or target.output_sha256 and sam.checksum(path)!=target.output_sha256:
                raise Problem('REVIEW_OUTPUT_CHANGED','The candidate file is missing or changed.','Restore or re-render before review; no model call was made.',409)
            if target.creative_review and target.review_brief_sha256==current_hash:
                state.receipts[command_id]=fingerprint;self.store.save(state);return state
            state.render_target_id=target.id;state.receipts[command_id]=fingerprint
            self.submit(state,'review');return state

    @staticmethod
    def reviewed_ancestor(state,candidate):
        """Nearest ready ancestor whose critic review asked for this line of revisions (skips preflight-corrected parents)."""
        by_id={c.id:c for c in state.candidates};current=by_id.get(candidate.parent_id)
        while current is not None and not (current.status=='ready' and current.creative_review):
            current=by_id.get(current.parent_id)
        return current

    @staticmethod
    def metric_choice(state,brief_hash,issues):
        """(candidate_id, score) of the ready candidate that best fits the targeted issues, latest first on ties."""
        pool=[(c.id,c.validation.get('output_samples',[]),c.validation.get('source_samples',[])) for c in reversed(state.candidates)
              if c.status=='ready' and c.brief_sha256==brief_hash and c.validation]
        ranked=convergence.rank_candidates(pool,issues)
        return ranked[0] if ranked else None

    def revision_highlight_block(self,project_id,state,candidate,proposal,cancel):
        """Preflight report if a critic revision would lift highlights while darkening, else None. Fails open:
        a revision that cannot be assessed here is still checked by guard_grade before it is rendered."""
        recipe=candidate.recipe
        if recipe is None or recipe.tone_operator!='luminance_ocio_v3':return None
        try:
            directory=self.store.directory(project_id)
            _,pixels,_=grade_guard.source_pixels(directory/'source.bin',recipe,state.source,self.settings,cancel,directory)
            trial=recipe.model_copy(deep=True);trial.proposal=proposal
            report=render_preflight.prepare(trial,pixels)['report']
        except Problem as error:
            if error.code in {'JOB_CANCELLED','SOURCE_CHANGED'}:raise
            return None
        return report if report.get('blocked_by')=='highlight_lift' else None

    def review_and_revise(self,project_id,cancel,force=False):
        def finish(selected,stage='render_ready'):
            def done(s):s.selected_candidate_id=selected;s.status='render_ready';s.stage=stage;s.active_job=None
            self.mutate(project_id,done)
        while True:
            self.check_cancel(cancel);state=self.store.get(project_id)
            candidate=next(c for c in state.candidates if c.id==state.render_target_id)
            if not (self.store.directory(project_id)/'source.bin').is_file() or sam.checksum(self.store.directory(project_id)/'source.bin')!=state.source.sha256:
                raise Problem('SOURCE_CHANGED','The source no longer matches its analysis.','Restore the original source.',409)
            path=self.store.directory(project_id)/'candidates'/candidate.id/'grade.mp4'
            if not path.exists() or candidate.output_sha256 and sam.checksum(path)!=candidate.output_sha256:
                raise Problem('REVIEW_OUTPUT_CHANGED','The candidate file is missing or changed.','Restore or re-render; no creative opinion can validate corrupted media.',409)
            if not force and not candidate.recipe.proposal.review_required:
                finish(candidate.id);return
            brief_hash=rendering.fingerprint(state.brief.model_dump(mode='json'))
            if candidate.creative_review and candidate.review_brief_sha256==brief_hash:
                if candidate.creative_review.decision=='revise' and not candidate.revision_stop_reason:
                    self.mutate(project_id,lambda s:setattr(next(c for c in s.candidates if c.id==candidate.id),'revision_stop_reason','cached_review_retained_after_interruption'))
                finish(candidate.creative_review.preferred_candidate_id or candidate.id);return
            ancestor=self.reviewed_ancestor(state,candidate)
            if (ancestor and ancestor.creative_review.decision=='revise' and ancestor.review_brief_sha256==brief_hash
                    and candidate.brief_sha256==brief_hash and candidate.validation and ancestor.validation
                    and candidate.revision_stop_reason!='revision_ineffective'):
                # Measured before paying for another critic call: did this revision visibly move what was asked?
                progress=convergence.revision_progress(candidate.validation.get('output_samples',[]),
                    ancestor.validation.get('output_samples',[]),ancestor.creative_review.issues)
                if progress['ineffective']:
                    choice=self.metric_choice(state,brief_hash,ancestor.creative_review.issues)
                    selected=choice[0] if choice else candidate.id
                    def ineffective(s):
                        c=next(c for c in s.candidates if c.id==candidate.id)
                        c.revision_stop_reason='revision_ineffective';c.revision_progress=progress
                        self.event(s,'Revision did not move the requested issues',
                            'The targeted metrics did not change visibly in the asked-for direction, so the chosen operation is not '
                            'fixing them; no further critic call or revision was made.',candidate=c.name,
                            measured='; '.join(f"{r['target']} {r['progress']:+.3f}" for r in progress['rows']),
                            selected=next(x.name for x in s.candidates if x.id==selected))
                    self.mutate(project_id,ineffective);finish(selected);return
                self.mutate(project_id,lambda s:setattr(next(c for c in s.candidates if c.id==candidate.id),'revision_progress',progress))
            request_id=None
            try:
                if candidate.review_brief_sha256==brief_hash and candidate.review_attempts>=2:
                    raise Problem('REVIEW_CALL_LIMIT','Two creative review attempts were already made for this candidate and brief.',
                                  'Review the available video manually; automatic retries are stopped.',409)
                def reviewing(s):
                    s.status='reviewing';s.stage='creative_review';s.job_kind='review'
                    self.event(s,'Preparing paired rendered evidence',candidate=candidate.name)
                self.mutate(project_id,reviewing)
                data,previous,summary=creative_review.collect(self,state,candidate,cancel)
                review_candidate=candidate.model_copy(deep=True)
                if not review_candidate.validation.get('execution_capabilities'):
                    operations=capabilities(state.source)['operations']
                    try:
                        current_timeline=local_grading.timeline(self.store.directory(project_id),state.source,self.settings,cancel)
                    except Problem as error:
                        if error.code in {'JOB_CANCELLED','SOURCE_CHANGED'}:raise
                        current_timeline=None
                    review_candidate.validation={**candidate.validation,'execution_capabilities':{
                        'operations':operations if current_timeline else [o for o in operations if o!='regional_grade'],
                        'localized_available':current_timeline is not None,'shots':current_timeline['shots'] if current_timeline else []}}
                if not hasattr(self.provider,'review'):raise Problem('REVIEW_UNAVAILABLE','Creative review is unavailable for this provider.','The rendered candidate remains available for human review.',503)
                def attempt(s):
                    c=next(c for c in s.candidates if c.id==candidate.id)
                    c.review_attempts=(c.review_attempts if c.review_brief_sha256==brief_hash else 0)+1
                    c.review_brief_sha256=brief_hash;c.review_evidence=summary
                    c.output_sha256=data['rendered_sha256']
                    if previous:
                        prior=next(c for c in s.candidates if c.id==previous['id'])
                        if not prior.output_sha256:prior.output_sha256=previous['output_sha256']
                self.mutate(project_id,attempt)
                request_id=self.count_call(project_id,'Muse is reviewing rendered evidence',kind='critic')
                call=self.store.get(project_id).model_calls
                review,usage=self.provider.review(state.brief,review_candidate,data,previous,current_feedback(state))
                self.record_usage(project_id,request_id,'critic',usage,call)
                if sam.checksum(path)!=data['rendered_sha256']:
                    raise Problem('REVIEW_OUTPUT_CHANGED','The rendered file changed during creative review.','Restore the frozen candidate.',409)
                allowed={'source:'+str(f['frame_index']) for f in data['source_images']}|{'candidate:'+str(f['frame_index']) for f in data['candidate_images']}
                allowed|={'source_region:'+str(f['frame_index']) for f in data.get('source_region_images',[])}|{'candidate_region:'+str(f['frame_index']) for f in data.get('candidate_region_images',[])}
                if previous:allowed|={'previous:'+str(f['frame_index']) for f in previous['images']}
                allowed|={f"mask_overlay_{f['overlay']}:{f['frame_index']}" for f in data.get('mask_overlay_images',[])}
                if (any(set(i.evidence_ids)-allowed for i in review.issues) or any(set(m.evidence_ids)-allowed for m in review.mask_assessments)
                    or review.preferred_candidate_id not in {None,candidate.id,previous['id'] if previous else candidate.id}):
                    raise Problem('MUSE_INVALID_REVIEW','The review referenced unseen evidence or an unavailable candidate.','Review manually or retry explicitly.',502)
                def reviewed(s):
                    c=next(c for c in s.candidates if c.id==candidate.id)
                    c.creative_review=review;c.review_error=None
                    c.review_history.append({'at':now(),'brief_sha256':brief_hash,'request_id':request_id,'review':review.model_dump(mode='json')})
                    self.event(s,'Muse creative opinion',review.summary,actor='muse',candidate=c.name,decision=review.decision)
                self.mutate(project_id,reviewed)
                self.check_cancel(cancel)
            except Exception as error:
                if isinstance(error,Problem) and error.code in {'JOB_CANCELLED','SOURCE_CHANGED',*creative_review.TECHNICAL_ERRORS}:raise
                failure=safe_problem(error,'creative_review')
                if request_id and failure.details.get('usage_reported'):
                    self.record_usage(project_id,request_id,'critic',failure.details,self.store.get(project_id).model_calls)
                def failed(s):
                    c=next(c for c in s.candidates if c.id==candidate.id);c.review_error=failure.public()
                    c.review_history.append({'at':now(),'brief_sha256':brief_hash,'request_id':request_id,'error':failure.public()})
                    c.revision_stop_reason='creative_review_failed';self.event(s,'Creative review unavailable',failure.message,kind='error',code=failure.code)
                self.mutate(project_id,failed);self.check_cancel(cancel)
                finish(state.selected_candidate_id or candidate.id,'creative_review_failed');return
            preferred=review.preferred_candidate_id or candidate.id
            state=self.store.get(project_id);proposal=review.revision_plan;stop=None;blocked=None
            if proposal and candidate.brief_sha256==brief_hash:
                # A model revision cannot erase an execution bound on this same
                # frozen brief. New explicit user feedback creates a new brief.
                prior=candidate.recipe.proposal.execution_limits
                for field in ['max_new_black_fraction','max_new_white_fraction','subject_max_chroma_change','subject_min_luma_ratio']:
                    old=getattr(prior,field);new=getattr(proposal.execution_limits,field)
                    if old is not None and (new is None or (new<old if field=='subject_min_luma_ratio' else new>old)):
                        setattr(proposal.execution_limits,field,old)
            doubtful=[m for m in review.mask_assessments if m.verdict in {'partial','wrong_object'}]
            if doubtful:stop='mask_needs_user_review' # Re-grading a wrong mask cannot fix it; a person must look.
            elif review.decision!='revise':stop=review.decision
            elif preferred!=candidate.id:stop='previous_candidate_preferred'
            elif not proposal or not any(i.correctable for i in review.issues):stop='no_supported_targeted_revision'
            elif proposal.unfulfilled_requests:stop='revision_requires_unavailable_operation'
            elif sum(c.brief_sha256==brief_hash for c in state.candidates)>=self.settings.max_render_iterations or candidate.iteration>=state.render_iteration_limit:
                stop='render_iteration_limit'
            elif any(c.recipe and c.brief_sha256==brief_hash and creative_review.execution_identity(c.recipe.proposal,c.recipe.tone_operator,state.source.duration)==creative_review.execution_identity(proposal,duration=state.source.duration) for c in state.candidates):
                stop='unchanged_or_repeated_execution_plan'
            elif (blocked:=self.revision_highlight_block(project_id,state,candidate,proposal,cancel)) is not None:
                stop='revision_would_lift_highlights'
            elif candidate.recipe.proposal.local and candidate.recipe.proposal.local.preserve_subject_color and (not proposal.local or not proposal.local.preserve_subject_color):
                stop='revision_would_weaken_subject_protection'
            if stop:
                choice=None
                if review.decision=='revise' and review.preferred_candidate_id is None and stop!='previous_candidate_preferred':
                    # Unresolved issues and no stated preference: pick by measured fit, not by recency.
                    choice=self.metric_choice(self.store.get(project_id),brief_hash,review.issues)
                    if choice:preferred=choice[0]
                def stopped(s):
                    setattr(next(c for c in s.candidates if c.id==candidate.id),'revision_stop_reason',stop)
                    if doubtful:
                        self.event(s,'Mask may not match its concept','; '.join(f'{m.concept}: {m.verdict} - {m.description}' for m in doubtful)[:1500],
                                   actor='muse',kind='decision',candidate=candidate.name,stop_reason=stop)
                    if choice:
                        self.event(s,'Candidate selected by measured fit',
                            'The critic named no preferred candidate; the one with the fewest and smallest wrong-way moves on the targeted issues was selected.',
                            candidate=next(x.name for x in s.candidates if x.id==choice[0]),regressions=choice[1]['regressions'],
                            worst_regression=round(choice[1]['worst_regression'],4),progress=round(choice[1]['progress'],4))
                    if blocked is not None:
                        self.event(s,'Revision not rendered',blocked['reason'],actor='app',candidate=candidate.name,stop_reason=stop,
                                   blocked_by=blocked.get('blocked_by'),violating_layers=', '.join(blocked['highlights']['violations']))
                self.mutate(project_id,stopped)
                finish(preferred);return
            next_candidate=RenderCandidate(id=str(uuid4()),name='V'+str(len(state.candidates)+1),source_sha256=state.source.sha256,
                brief_sha256=brief_hash,generation_id=candidate.generation_id or str(uuid4()),parent_id=candidate.id,iteration=candidate.iteration+1)
            # The critic's typed plan is executable, not another planner/model round-trip.
            next_candidate.recipe=GradeRecipe(schema_version='takeone-grade-v3',prompt_version='colorist-v3',tone_operator='luminance_ocio_v3',
                source_sha256=state.source.sha256,brief_sha256=brief_hash,model=self.settings.model,proposal=proposal,
                source_samples=candidate.recipe.source_samples,evidence_selection=candidate.recipe.evidence_selection,
                timeline=candidate.recipe.timeline if proposal.mode=='localized' else None)
            if proposal.mode=='localized' and not next_candidate.recipe.timeline:
                next_candidate.recipe.timeline=local_grading.timeline(self.store.directory(project_id),state.source,self.settings,cancel)
            def revising(s):
                s.candidates.append(next_candidate);s.render_target_id=next_candidate.id;s.status='rendering';s.stage='plan_revision';s.job_kind='render'
                self.event(s,'Targeted revision queued','Rendering from the original source; earlier candidates are preserved.',candidate=next_candidate.name,parent_id=candidate.id)
            self.mutate(project_id,revising)
            self.store.candidate_artifact(project_id,next_candidate.id,'plan.json',proposal.model_dump(mode='json'))
            self.store.candidate_artifact(project_id,next_candidate.id,'recipe.json',next_candidate.recipe.model_dump(mode='json'))
            self._render_one(project_id,cancel,keep_active=True)
            force=True
