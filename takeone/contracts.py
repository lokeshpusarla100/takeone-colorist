from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
from pydantic import Field, field_validator

from takeone_rubric_v1.rubric import StrictModel, Candidate, CreativeBrief


def now():
    return datetime.now(timezone.utc).isoformat()


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


class GradeDecision(StrictModel):
    parameter: Literal["contrast", "saturation", "gamma"]
    reason: str = Field(min_length=1, max_length=500)
    source_ids: list[str] = Field(min_length=1, max_length=10)


class GradeProposal(StrictModel):
    parameters: GradeParameters
    summary: str = Field(min_length=1, max_length=1000)
    decisions: list[GradeDecision] = Field(min_length=1, max_length=3)
    limitations: list[str] = Field(max_length=8)

    @field_validator("decisions")
    @classmethod
    def unique_parameters(cls, decisions):
        if len({d.parameter for d in decisions}) != len(decisions):
            raise ValueError("Each parameter has one coordinated decision")
        return decisions


class GradeRecipe(StrictModel):
    schema_version: Literal["takeone-global-v1"] = "takeone-global-v1"
    source_sha256: str
    brief_sha256: str
    model: str
    prompt_version: Literal["colorist-v1"] = "colorist-v1"
    color_domain: Literal["display-encoded-rec709"] = "display-encoded-rec709"
    working_pixel_format: Literal["gbrp16le"] = "gbrp16le"
    output_pixel_format: Literal["yuv420p"] = "yuv420p"
    operation_order: list[str] = Field(default_factory=lambda: ["gamma", "contrast", "saturation"])
    proposal: GradeProposal
    source_samples: list[dict]
    created_at: str = Field(default_factory=now)


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
    status: Literal["ingesting", "ready", "analyzing", "awaiting_answer", "brief_ready", "rendering", "render_ready", "segmenting", "failed", "cancelled"]
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
        return self.model_dump(mode="json", exclude={"rubric", "receipts", "remote_file_id", "active_job", "job_kind", "history", "render_target_id", "memory_binding", "memory_outbox", "sam_upload", "segmentation_target_id"})
