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
from . import evidence as visual_evidence
from . import creative_review
from . import grade_guard


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
                       else 'creative_review' if kind=='review' else "verify_segmentation_input" if kind=="segment" else "analyze_scene" if not state.analysis else "select_question")
        state.error = None
        self.event(state, "Preparing source" if kind == "ingest" else "Grade queued" if kind == "render" else "Segmentation queued" if kind=="segment" else "Analysis queued",
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
            if (kind == "analysis" or (kind == "render" and (not target or not target.recipe))) and not self.provider.available():
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
        input_path=directory/'analysis.mp4'
        if target.duration_seconds is not None or target.start_seconds or target.first_frame is not None:
            selected=([f for f in frame_map['frames'] if target.first_frame<=f['index']<=target.last_frame]
                      if target.first_frame is not None else
                      [f for f in frame_map['frames'] if target.start_seconds<=f['time']<target.start_seconds+(target.duration_seconds or state.source.duration)])
            if not selected:raise Problem('SAM_INTERVAL_EMPTY','The requested mask interval has no frames.','Use a source interval.',422)
            count=len(selected);first=selected[0]['index'];last=selected[-1]['index']
            frame_map['source_frame_count']=frame_map['frame_count']
            frame_map['frames']=selected;frame_map['frame_count']=count
            input_path=output/'input.mp4'
            if not input_path.is_file():
                temporary=output/'input.partial.mp4'
                sam.command([self.settings.ffmpeg,'-v','error','-y','-i',str(directory/'analysis.mp4'),
                    '-an','-vf',fr'select=between(n\,{first}\,{last}),setpts=PTS-STARTPTS','-frames:v',str(count),'-c:v','libx264','-preset','veryfast','-crf','26',
                    '-fps_mode','passthrough','-movflags','+faststart',str(temporary)],cancel,120)
                temporary.replace(input_path)
            inspected=sam.frame_info(input_path,self.settings,cancel)
            origin=selected[0]['proxy_pts']
            if len(inspected['pts'])!=count or any(abs(f['proxy_pts']-origin-t)>0.002 for f,t in zip(frame_map['frames'],inspected['pts'])):
                raise Problem('SAM_EXCERPT_MAPPING','The excerpt changed frame mapping.','Inspect the prepared proxy.',422)
            frame_map['proxy_sha256']=sam.checksum(input_path)
        frame_map['scope']={'start_seconds':target.start_seconds,'requested_duration':target.duration_seconds,'full_source_frames':
                            frame_map.get('source_frame_count',frame_map['frame_count'])}
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
                    self.event(s,'Tracking subject with SAM',actor='muse',model=self.settings.sam_model,
                               request_id=request_id,segmentation_id=target.id)
                self.mutate(project_id,change)
                update('streaming','segment_subject',frame_count=frame_map['frame_count'])
            def usage(value):
                self.mutate(project_id,lambda s:self.event(s,'SAM request usage',actor='muse',
                    model=self.settings.sam_model,request_id=request_id,video_frames_processed=value.get('video_frames_processed'),
                    token_usage=True,**usage_details(value)))
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
            prefix=f'/api/projects/{project_id}/segmentations/{run.id}'
            run.preview_url=prefix+'/preview' if run.status=='ready' and not automatic else None
            run.manifest_url=prefix+'/manifest'
            if not automatic:s.status=run.return_status;s.stage='segmentation_ready';s.active_job=None
            self.event(s,'Subject masks ready' if run.status=='ready' else 'No matching subject found',
                       'Masks will be validated before localized compositing.' if automatic else 'Review object IDs and the moving overlay; no diagnostic mask is applied without a grading plan.',
                       mask_count=run.mask_count,frames_with_masks=run.frames_with_masks,frame_count=run.frame_count)
        self.mutate(project_id,ready)
        return manifest,output

    def local_masks(self,project_id,candidate_id,evidence,plan,cancel):
        state=self.store.get(project_id);source=state.source;directory=self.store.directory(project_id)
        ranges=local_grading.plan_ranges(plan,evidence,source);records={};step=float(1/Fraction(evidence['frame_map']['proxy']['rate']))
        for first,last in ranges:
            self.check_cancel(cancel);state=self.store.get(project_id);found=None
            for run in reversed(state.segmentations):
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
            records.update(local_grading.records_for_range(manifest,mask_directory,source,first,last,plan.selection))
        return records,ranges

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

    def guard_grade(self,project_id,candidate,recipe,original,output,source,cancel):
        """Resolve input-cutoff mistakes before encoding, retaining the original plan/version."""
        limits=recipe.proposal.execution_limits
        if recipe.tone_operator!='luminance_ocio_v3' or (
            limits.max_new_black_fraction is None and limits.max_new_white_fraction is None):return candidate,recipe,output
        self.mutate(project_id,lambda s:setattr(s,'stage','preflight_grade'))
        known_failure=bool(candidate.error and candidate.error.get('code')=='GRADE_EXECUTION_LIMIT' and recipe.execution_preflight)
        all_layers=[recipe.proposal.parameters]+([recipe.proposal.local.foreground,recipe.proposal.local.background] if recipe.proposal.local else [])
        if known_failure and recipe.proposal.mode=='global' and all(p.black_point==0 and p.white_point==1 for p in all_layers):
            raise Problem('GRADE_PREFLIGHT_FAILED','The previous full render failed even without input clipping cutoffs.',
                          'Revise gamma, brightness or palette choices. The same failed recipe will not be encoded again.',422,
                          previous_failure=candidate.error)
        effective,report=grade_guard.prepare_execution(original,output/'preflight',recipe,source,self.settings,cancel,
            cache_dir=self.store.directory(project_id),force_neutral_levels=known_failure)
        self.check_cancel(cancel)
        if report.get('changes'):
            state=self.store.get(project_id)
            if sum(c.brief_sha256==candidate.brief_sha256 for c in state.candidates)>=self.settings.max_render_iterations:
                raise Problem('RENDER_BUDGET','A clipping correction is available, but this brief has reached its candidate limit.',
                              'Review or revise the brief before another attempt. No full encode was started.',409,preflight=report)
            child=RenderCandidate(id=str(uuid4()),name='V'+str(len(state.candidates)+1),source_sha256=source.sha256,
                brief_sha256=candidate.brief_sha256,generation_id=candidate.generation_id or str(uuid4()),
                parent_id=candidate.id,iteration=candidate.iteration+1,recipe=effective,status='rendering')
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
        technical={'operations':['global_grade','cooling','brightness_gain'],
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
        if evidence:technical['operations']+=['regional_grade','selective_gain']
        if not candidate.recipe:
            # Unchanged brief + source with a ready candidate: reuse its saved
            # plan instead of resending the same brief, analysis and sample
            # images to the planner. Any feedback changes the brief hash, so
            # a changed brief always takes the full planning path below.
            prior=next((c for c in reversed(state.candidates) if c.id!=candidate.id and c.status=='ready'
                        and c.brief_sha256==candidate.brief_sha256 and c.source_sha256==state.source.sha256
                        and c.recipe and c.recipe.evidence_selection),None)
            if prior is not None:
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
                              'Clarify these instructions or choose a supported treatment. No replacement was rendered.',422,
                              unfulfilled_count=len(proposal.unfulfilled_requests),unfulfilled_instructions='; '.join(proposal.unfulfilled_requests)[:1500])
            if proposal.mode=='localized' and not evidence:
                raise Problem('LOCAL_GRADE_UNAVAILABLE','The requested localized plan has no supported frame mapping.',
                              'Use upright CFR SDR footage with a verified source/proxy frame mapping. No global fallback was made.',422,limitation=local_limitation)
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
        candidate,recipe,output=self.guard_grade(project_id,candidate,recipe,original,output,state.source,cancel)
        records=None;mask_qa=None
        if recipe.proposal.mode=='localized':
            if evidence is None or recipe.timeline['key']!=evidence['key']:
                raise Problem('LOCAL_TIMELINE_CHANGED','The cached recipe has a different local source mapping.','Create a new candidate.',409)
            self.mutate(project_id,lambda s:setattr(s,'stage','verify_segmentation_input'))
            records,ranges=self.local_masks(project_id,candidate.id,evidence,recipe.proposal.local,cancel)
            # Frame-derived interval boundaries, never guessed request timestamps.
            step=float(1/Fraction(evidence['frame_map']['proxy']['rate']))
            recipe.timeline['active_ranges']=[[evidence['frame_map']['frames'][a]['time'],evidence['frame_map']['frames'][b]['time']+step] for a,b in ranges]
            recipe.timeline['active_frame_ranges']=[list(pair) for pair in ranges]
            def validating(s):
                s.stage='validate_local_masks';self.event(s,'Validating localized masks',required_frames=len(records),shot_intervals=len(ranges))
            self.mutate(project_id,validating)
            mask_qa=local_grading.prepare_matte(directory/'analysis.mp4',output,state.source,evidence,records,
                                               recipe.proposal.local,self.settings,cancel,adaptive=recipe.tone_operator=='luminance_ocio_v3')
            mask_qa['selection_policy']=recipe.proposal.local.selection
            mask_qa['selected_objects_by_interval']=[{'first_frame':a,'last_frame':b,
                'object_ids':sorted({r['object_id'] for i in range(a,b+1) for r in records[i]})} for a,b in ranges]
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
                if any(set(i.evidence_ids)-allowed for i in review.issues) or review.preferred_candidate_id not in {None,candidate.id,previous['id'] if previous else candidate.id}:
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
            state=self.store.get(project_id);proposal=review.revision_plan;stop=None
            if proposal and candidate.brief_sha256==brief_hash:
                # A model revision cannot erase an execution bound on this same
                # frozen brief. New explicit user feedback creates a new brief.
                prior=candidate.recipe.proposal.execution_limits
                for field in ['max_new_black_fraction','max_new_white_fraction','subject_max_chroma_change','subject_min_luma_ratio']:
                    old=getattr(prior,field);new=getattr(proposal.execution_limits,field)
                    if old is not None and (new is None or (new<old if field=='subject_min_luma_ratio' else new>old)):
                        setattr(proposal.execution_limits,field,old)
            if review.decision!='revise':stop=review.decision
            elif preferred!=candidate.id:stop='previous_candidate_preferred'
            elif not proposal or not any(i.correctable for i in review.issues):stop='no_supported_targeted_revision'
            elif proposal.unfulfilled_requests:stop='revision_requires_unavailable_operation'
            elif sum(c.brief_sha256==brief_hash for c in state.candidates)>=self.settings.max_render_iterations or candidate.iteration>=state.render_iteration_limit:
                stop='render_iteration_limit'
            elif any(c.recipe and c.brief_sha256==brief_hash and creative_review.execution_identity(c.recipe.proposal,c.recipe.tone_operator,state.source.duration)==creative_review.execution_identity(proposal,duration=state.source.duration) for c in state.candidates):
                stop='unchanged_or_repeated_execution_plan'
            elif candidate.recipe.proposal.local and candidate.recipe.proposal.local.preserve_subject_color and (not proposal.local or not proposal.local.preserve_subject_color):
                stop='revision_would_weaken_subject_protection'
            if stop:
                self.mutate(project_id,lambda s:setattr(next(c for c in s.candidates if c.id==candidate.id),'revision_stop_reason',stop))
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
