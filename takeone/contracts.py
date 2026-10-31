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


class State(StrictModel):
    schema_version: int = 1
    id: str
    revision: int = 0
    status: Literal["ingesting", "ready", "analyzing", "awaiting_answer", "brief_ready", "failed", "cancelled"]
    stage: str
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    filename: str
    source: Source | None = None
    preview: Preview | None = None
    analysis: SceneAnalysis | None = None
    question: Candidate | None = None
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

    def public(self):
        return self.model_dump(mode="json", exclude={"rubric", "receipts", "remote_file_id", "active_job", "job_kind", "history"})
