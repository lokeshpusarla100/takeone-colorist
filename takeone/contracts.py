from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
import hashlib,json
from pydantic import Field, field_validator, model_validator

from takeone_rubric_v1.rubric import StrictModel, Candidate, CreativeBrief


def now():
    return datetime.now(timezone.utc).isoformat()


def current_feedback(state):
    epoch=getattr(state,'request_started_at',None)
    return [f for f in state.feedback if not epoch or f.get('timestamp','')>=epoch]


class VisualObservation(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    description: str = Field(min_length=1, max_length=1200)
    uncertainty: str = Field(max_length=500)


class SceneAnalysis(StrictModel):
    summary: str = Field(min_length=1, max_length=1500)
    observations: list[VisualObservation] = Field(min_length=1, max_length=16)
    caveats: list[str] = Field(max_length=12)

    @field_validator("observations")
    @classmethod
    def unique_ids(cls, observations):
        if len({o.id for o in observations}) != len(observations):
            raise ValueError("Duplicate observation IDs")
        if any(o.end < o.start for o in observations):
            raise ValueError("Invalid observation time range")
        return observations


class Event(StrictModel):
    id: str = Field(default_factory=lambda: "server:" + str(uuid4()))
    timestamp: str = Field(default_factory=now)
    actor: Literal["app", "user", "muse", "renderer"] = "app"
    kind: Literal["activity", "decision", "error"] = "activity"
    title: str
    reason: str = ""
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class Source(StrictModel):
    name: str
    bytes: int
    sha256: str
    width: int
    height: int
    duration: float
    video_codec: str
    audio_codec: str | None
    pixel_format: str
    color_transfer: str | None
    color_primaries: str | None
    color_space: str | None
    start_time: float
    rotation: float
    technical_notes: list[str]


class Preview(StrictModel):
    width: int
    height: int
    duration: float
    bytes: int
    url: str
    analysis_url: str
    source_start_time: float
    preview_start_time: float


class GradeParameters(StrictModel):
    contrast: float = Field(ge=0.75, le=1.25, strict=True, allow_inf_nan=False)
    saturation: float = Field(ge=0, le=1.5, strict=True, allow_inf_nan=False)
    gamma: float = Field(ge=0.8, le=1.2, strict=True, allow_inf_nan=False)
    brightness_gain: float = Field(default=1.0,ge=0.65,le=1.35,allow_inf_nan=False)
    cooling: float = Field(default=0.0,ge=-0.15,le=0.15,allow_inf_nan=False)
    black_point: float = Field(default=0.0,ge=0,le=.15,allow_inf_nan=False)
    white_point: float = Field(default=1.0,ge=.75,le=1,allow_inf_nan=False)


class CreativeIntent(StrictModel):
    description: str = Field(default='',max_length=1600)
    shadow_policy: Literal['preserve_detail','intentional_darkness','intentional_black','review'] = 'review'
    highlight_policy: Literal['preserve_detail','intentional_clipping','review'] = 'review'


class ExecutionLimits(StrictModel):
    """Declared bounds, not confidence or universal aesthetic targets."""
    subject_min_luma_ratio: float | None = Field(default=None,ge=0,le=2,allow_inf_nan=False)
    subject_max_chroma_change: float | None = Field(default=None,ge=0,le=1,allow_inf_nan=False)
    max_new_black_fraction: float | None = Field(default=None,ge=0,le=1,allow_inf_nan=False)
    max_new_white_fraction: float | None = Field(default=None,ge=0,le=1,allow_inf_nan=False)


class GradeInterval(StrictModel):
    start: float = Field(ge=0,allow_inf_nan=False)
    end: float = Field(gt=0,allow_inf_nan=False)

    @model_validator(mode='after')
    def ordered(self):
        if self.end<=self.start:raise ValueError('Interval end must follow start')
        return self


class LocalizedPlan(StrictModel):
    concept: str = Field(min_length=1,max_length=120)
    selection: Literal['single_instance','all_matches'] = 'single_instance'
    intervals: list[GradeInterval] = Field(min_length=1,max_length=12)
    foreground: GradeParameters
    background: GradeParameters
    preserve_subject_color: bool = True
    feather_px: float = Field(default=1.0,ge=0.5,le=2.0,allow_inf_nan=False)

    @model_validator(mode='after')
    def executable(self):
        ordered=sorted(self.intervals,key=lambda i:i.start)
        if any(a.end>b.start for a,b in zip(ordered,ordered[1:])):raise ValueError('Local intervals overlap')
        if any(c in self.concept for c in [',',';','\n','<','>']):raise ValueError('Use one subject phrase')
        if self.foreground==self.background:raise ValueError('Localized treatments must differ; otherwise SAM is unnecessary')
        if self.preserve_subject_color and any(abs(getattr(self.foreground,k)-v)>1e-8 for k,v in [('saturation',1),('cooling',0)]):
            raise ValueError('Colour-protected subjects require neutral saturation/cooling; luminance-only tone is supported')
        return self


class GradeDecision(StrictModel):
    parameter: Literal["contrast", "saturation", "gamma","brightness_gain","cooling","black_point","white_point","local_treatment"]
    reason: str = Field(min_length=1, max_length=500)
    source_ids: list[str] = Field(min_length=1, max_length=10)


class GradeProposal(StrictModel):
    mode: Literal['global','localized'] = 'global'
    parameters: GradeParameters
    local: LocalizedPlan | None = None
    summary: str = Field(min_length=1, max_length=1000)
    decisions: list[GradeDecision] = Field(min_length=1, max_length=6)
    limitations: list[str] = Field(max_length=8)
    unfulfilled_requests: list[str] = Field(default_factory=list,max_length=8)
    intent: CreativeIntent = Field(default_factory=CreativeIntent)
    execution_limits: ExecutionLimits = Field(default_factory=ExecutionLimits)
    review_required: bool = False

    @field_validator("decisions")
    @classmethod
    def unique_parameters(cls, decisions):
        if len({d.parameter for d in decisions}) != len(decisions):
            raise ValueError("Each parameter has one coordinated decision")
        return decisions

    @model_validator(mode='after')
    def mode_matches(self):
        if (self.mode=='localized') != (self.local is not None):raise ValueError('Localized mode requires exactly one local plan; global mode has none')
        return self


class GradeRecipe(StrictModel):
    schema_version: Literal["takeone-global-v1","takeone-grade-v2","takeone-grade-v3"] = "takeone-global-v1"
    source_sha256: str
    brief_sha256: str
    model: str
    prompt_version: Literal["colorist-v1","colorist-v2","colorist-v3"] = "colorist-v1"
    color_domain: Literal["display-encoded-rec709"] = "display-encoded-rec709"
    working_pixel_format: Literal["gbrp16le"] = "gbrp16le"
    output_pixel_format: Literal["yuv420p"] = "yuv420p"
    operation_order: list[str] = Field(default_factory=lambda: ["gamma", "contrast", "saturation"])
    proposal: GradeProposal
    source_samples: list[dict]
    created_at: str = Field(default_factory=now)
    tone_operator: Literal['legacy_fixed_pivot','bounded_sigmoid_v2','luminance_ocio_v3'] = 'legacy_fixed_pivot'
    timeline: dict | None = None
    evidence_selection: dict | None = None

    @model_validator(mode='after')
    def legacy_controls(self):
        if self.tone_operator!='luminance_ocio_v3':
            layers=[self.proposal.parameters]+([self.proposal.local.foreground,self.proposal.local.background] if self.proposal.local else [])
            if any(p.black_point!=0 or p.white_point!=1 for p in layers):raise ValueError('Black/white points require v3 execution')
            if self.proposal.local and self.proposal.local.preserve_subject_color and any(
                getattr(self.proposal.local.foreground,k)!=1 for k in ['contrast','gamma']):raise ValueError('Protected luminance tone requires v3 execution')
        return self


class CreativeIssue(StrictModel):
    id: str = Field(min_length=1,max_length=80)
    description: str = Field(min_length=1,max_length=1200)
    evidence_ids: list[str] = Field(min_length=1,max_length=12)
    correctable: bool


class CreativeReview(StrictModel):
    decision: Literal['sufficient','revise','needs_user','not_evaluable']
    summary: str = Field(min_length=1,max_length=1600)
    issues: list[CreativeIssue] = Field(default_factory=list,max_length=8)
    revision_plan: GradeProposal | None = None
    preferred_candidate_id: str | None = None


class RenderCandidate(StrictModel):
    id: str
    name: str
    brief_sha256: str
    source_sha256: str
    status: Literal["planning", "rendering", "ready", "failed", "cancelled"] = "planning"
    created_at: str = Field(default_factory=now)
    recipe: GradeRecipe | None = None
    preview_url: str | None = None
    download_url: str | None = None
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    validation: dict | None = None
    error: dict | None = None
    mask_run_ids: list[str] = Field(default_factory=list)
    generation_id: str | None = None
    parent_id: str | None = None
    iteration: int = 1
    creative_review: CreativeReview | None = None
    review_brief_sha256: str | None = None
    review_error: dict | None = None
    revision_stop_reason: str | None = None
    output_sha256: str | None = None
    review_attempts: int = 0
    review_history: list[dict] = Field(default_factory=list)
    review_evidence: list[dict] = Field(default_factory=list)


class ConsultationSummary(StrictModel):
    reason: Literal["question_selected", "question_limit", "no_candidates", "no_valid_candidates"]
    message: str
    model_explanation: str = ""
    proposed_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)


class SegmentationRun(StrictModel):
    id: str
    concept: str
    model: str
    created_at: str = Field(default_factory=now)
    status: Literal["queued","preparing","streaming","overlay","ready","empty","failed","cancelled"] = "queued"
    return_status: Literal["brief_ready","render_ready"] = "brief_ready"
    duration_seconds: float | None = None
    start_seconds: float = 0
    first_frame: int | None = None
    last_frame: int | None = None
    received_frames: int = 0
    frame_count: int = 0
    frames_with_masks: int = 0
    mask_count: int = 0
    tracks: list[dict] = Field(default_factory=list)
    preview_url: str | None = None
    manifest_url: str | None = None
    error: dict | None = None


class State(StrictModel):
    schema_version: int = 1
    id: str
    revision: int = 0
    status: Literal["ingesting", "ready", "analyzing", "awaiting_answer", "brief_ready", "rendering", "render_ready", "segmenting", "reviewing", "failed", "cancelled"]
    stage: str
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    filename: str
    source: Source | None = None
    preview: Preview | None = None
    analysis: SceneAnalysis | None = None
    question: Candidate | None = None
    consultation: ConsultationSummary | None = None
    brief: CreativeBrief | None = None
    request: str = ""
    request_started_at: str | None = None
    render_iteration_limit: int = 3
    answered_count: int = 0
    max_questions: int = 3
    events: list[Event] = Field(default_factory=list)
    error: dict | None = None
    rubric: dict | None = None
    receipts: dict[str, str] = Field(default_factory=dict)
    remote_file_id: str | None = None
    active_job: str | None = None
    job_kind: str | None = None
    model_calls: int = 0
    critic_calls: int = 0
    question_proposals: list[dict] = Field(default_factory=list)
    history: list[dict] = Field(default_factory=list)
    candidates: list[RenderCandidate] = Field(default_factory=list)
    selected_candidate_id: str | None = None
    render_target_id: str | None = None
    owner_id: str | None = None
    profile_id: str = "default"
    preference_context: dict[str, str] = Field(default_factory=dict)
    memory_binding: dict | None = None
    memory_outbox: list[dict] = Field(default_factory=list)
    memory_capture_error: dict | None = None
    memory_influences: list[dict] = Field(default_factory=list)
    feedback: list[dict] = Field(default_factory=list)
    brief_revision: int = 0
    segmentations: list[SegmentationRun] = Field(default_factory=list)
    segmentation_target_id: str | None = None
    sam_upload: dict | None = None
    sam_calls: int = 0

    def public(self):
        value=self.model_dump(mode="json", exclude={"rubric", "receipts", "remote_file_id", "active_job", "job_kind", "history", "render_target_id", "memory_binding", "memory_outbox", "sam_upload", "segmentation_target_id"})
        value['brief_sha256']=hashlib.sha256(json.dumps(self.brief.model_dump(mode='json'),sort_keys=True,separators=(',',':')).encode()).hexdigest() if self.brief else None
        return value
