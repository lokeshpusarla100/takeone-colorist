from concurrent.futures import ThreadPoolExecutor
import hashlib
import fcntl
import json
from pathlib import Path
import threading
from uuid import uuid4

from takeone_rubric_v1.rubric import Answer, CapabilityManifest, Directive, Observation, RememberedDefault, RubricInput, RubricSession
from .config import Settings
from .contracts import Event, State, GradeRecipe, RenderCandidate, ConsultationSummary, SegmentationRun
from .errors import Problem, safe_problem
from .media import capabilities, inspect_source, make_previews
from .provider import MuseProvider
from .store import Store
from . import rendering
from .preferences import FeedbackEvidence, PreferenceStore, PromptMemoryGate
from .preferences import context_values
from .usage import usage_details
from . import sam


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
        event = Event(title=title, reason=reason, actor=actor, kind=kind, details=details)
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
        state.status = "ingesting" if kind == "ingest" else "rendering" if kind == "render" else "segmenting" if kind=="segment" else "analyzing"
        state.stage = ("inspect_source" if kind == "ingest" else "plan_grade" if kind == "render"
                       else "verify_segmentation_input" if kind=="segment" else "analyze_scene" if not state.analysis else "select_question")
        state.error = None
        self.event(state, "Preparing source" if kind == "ingest" else "Grade queued" if kind == "render" else "Segmentation queued" if kind=="segment" else "Analysis queued")
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
            state.consultation = None
            state.brief = None
            state.answered_count = 0
            state.model_calls = 0
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

    def run_segmentation(self,project_id,cancel):
        state=self.store.get(project_id)
        target=next((r for r in state.segmentations if r.id==state.segmentation_target_id),None)
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
        if target.duration_seconds is not None:
            count=sum(f['time']<target.duration_seconds for f in frame_map['frames'])
            frame_map['source_frame_count']=frame_map['frame_count']
            frame_map['frames']=frame_map['frames'][:count];frame_map['frame_count']=count
            input_path=output/'input.mp4'
            if not input_path.is_file():
                temporary=output/'input.partial.mp4'
                sam.command([self.settings.ffmpeg,'-v','error','-y','-i',str(directory/'analysis.mp4'),
                    '-an','-frames:v',str(count),'-c:v','libx264','-preset','veryfast','-crf','26',
                    '-fps_mode','passthrough','-movflags','+faststart',str(temporary)],cancel,120)
                temporary.replace(input_path)
            inspected=sam.frame_info(input_path,self.settings,cancel)
            if len(inspected['pts'])!=count or any(abs(f['proxy_pts']-t)>0.002 for f,t in zip(frame_map['frames'],inspected['pts'])):
                raise Problem('SAM_EXCERPT_MAPPING','The excerpt changed frame mapping.','Inspect the prepared proxy.',422)
            frame_map['proxy_sha256']=sam.checksum(input_path)
        frame_map['scope']={'start_seconds':0,'requested_duration':target.duration_seconds,'full_source_frames':
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
        self.check_cancel(cancel)
        if manifest['mask_count']:
            update('overlay','render_mask_overlay')
            sam.overlay(input_path,output,manifest,self.settings,cancel)
        self.check_cancel(cancel)
        def ready(s):
            run=next(r for r in s.segmentations if r.id==target.id)
            run.status='ready' if manifest['mask_count'] else 'empty'
            run.frame_count=frame_map['frame_count'];run.mask_count=manifest['mask_count']
            run.frames_with_masks=manifest['frames_with_masks'];run.tracks=manifest['tracks'];run.error=None
            prefix=f'/api/projects/{project_id}/segmentations/{run.id}'
            run.preview_url=prefix+'/preview' if run.status=='ready' else None
            run.manifest_url=prefix+'/manifest'
            s.status=run.return_status;s.stage='segmentation_ready';s.active_job=None
            self.event(s,'Subject masks ready' if run.status=='ready' else 'No matching subject found',
                       'Review object IDs and the moving overlay. Regional grading is not enabled.',
                       mask_count=run.mask_count,frames_with_masks=run.frames_with_masks,frame_count=run.frame_count)
        self.mutate(project_id,ready)

    def count_call(self, project_id, title):
        request_id = str(uuid4())
        def change(state):
            if state.model_calls >= self.settings.max_questions + 4:
                raise Problem("MODEL_CALL_BUDGET", "The consultation model-call budget was reached.",
                              "Review the saved evidence and answers before starting another consultation.", 409)
            state.model_calls += 1
            self.event(state, title, actor="muse", model=self.settings.model, call=state.model_calls,
                       request_id=request_id)
        self.mutate(project_id, change)
        return request_id

    def record_usage(self, project_id, request_id, stage, usage, call):
        titles = {"analysis":"Scene analysis usage", "rubric":"Muse request usage", "colorist":"Colorist request usage"}
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
            else:
                self.analyze(project_id, cancel)
        except Exception as cause:
            failure = safe_problem(cause, kind)
            try:
                if failure.details.get("usage_reported"):
                    stages={"Inspecting video with Muse":"analysis","Selecting a useful clarification":"rubric",
                            "Muse is preparing a global grading recipe":"colorist"}
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
                    if kind == "render":
                        target = next((c for c in state.candidates if c.id == state.render_target_id), None)
                        if target:
                            target.status = "cancelled" if failure.code == "JOB_CANCELLED" else "failed"
                            target.error = failure.public()
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
                project_feedback=[f for f in state.feedback if f.get("text")],
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
        session.inputs.project_feedback = [f for f in state.feedback if f.get("text")]
        session.inputs.memory_defaults = [RememberedDefault.model_validate(v)
                                         for v in self.prompt_gate.default_data(state.id)]
        proposal_usage = None
        proposal_call = None
        if session.asked_count >= session.max_decisions:
            from takeone_rubric_v1.rubric import Proposal
            proposal = Proposal()
        else:
            request_id = self.count_call(project_id, "Selecting a useful clarification")
            proposal_call = self.store.get(project_id).model_calls
            event_start = len(session.events)
            proposal = self.provider.propose(session)
            proposal_usage = next((e.get("usage") for e in reversed(session.events[event_start:])
                                   if e.get("event") == "muse_proposal"), None)
            self.record_usage(project_id,request_id,"rubric",proposal_usage,proposal_call)
        self.check_cancel(cancel)
        rejection_start = len(session.rejected)
        question = session.next_question(proposal)
        if question:
            reason,message="question_selected","A clarification is ready. Your answer will inform the next decision."
        elif session.asked_count >= session.max_decisions:
            reason,message="question_limit","The question limit was reached. No additional clarification call was made."
        elif not proposal.candidates:
            reason="no_candidates"
            message="Muse proposed no further clarification questions." if session.answers else "Muse proposed no clarification questions."
        else:
            reason,message="no_valid_candidates","The proposed questions did not pass evidence, feasibility or repetition checks."
        summary=ConsultationSummary(reason=reason,message=message,
            model_explanation=proposal.completion_reason if reason=="no_candidates" else "",
            proposed_count=len(proposal.candidates),rejected_count=len(session.rejected)-rejection_start)
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
            request_id = self.count_call(project_id, "Muse is preparing a global grading recipe")
            call_number = self.store.get(project_id).model_calls
            proposal, usage = self.provider.grade(state.brief, state.analysis, state.source, measurements, images)
            self.record_usage(project_id,request_id,"colorist",usage,call_number)
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
