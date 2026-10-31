"""Take One creative-direction rubric v1.

Scope: preference elicitation + brief assembly; never edits video or authorizes
unsupported operations. The Muse adapter supplies proposals. This module validates
and ranks them, records user decisions and exports a traceable creative brief.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Dimension(str, Enum):
    MEANING = "meaning"
    APPEARANCE = "appearance"
    ATTENTION = "attention"
    BOUNDARIES = "boundaries"
    CONTINUITY = "continuity"


class Level(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Observation(StrictModel):
    id: str
    scope: str
    description: str
    source: Literal["muse_visual", "measurement", "user"]
    uncertainty: str = ""  # e.g., "reflectance vs clipping unknown"


class CapabilityManifest(StrictModel):
    """Operations currently verified to work in the editing toolchain."""
    operations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    supports_look_previews: bool = False


class RubricInput(StrictModel):
    user_request: str
    observations: list[Observation]
    capabilities: CapabilityManifest
    technical_envelope: list[str] = Field(default_factory=list)
    explicit_constraints: list[str] = Field(default_factory=list)


class Option(StrictModel):
    id: str
    label: str
    intended_outcome: str  # Outcome, not a guessed numeric knob setting.
    required_operations: list[str] = Field(default_factory=list)


class Candidate(StrictModel):
    id: str
    dimension: Dimension
    target_key: str  # semantic slot; avoids re-asking an already resolved preference
    scope: str
    question: str
    evidence_ids: list[str] = Field(min_length=1)
    uncertainty_type: Literal["preference", "observation", "execution", "knowledge"]
    decision_fork: str  # Explain exactly how alternative answers change the edit.
    impact: Level
    preservation_risk: bool
    answerability: Level
    interaction: Literal["question", "preview"] = "question"
    options: list[Option] = Field(min_length=2, max_length=3)


class Proposal(StrictModel):
    candidates: list[Candidate] = Field(default_factory=list, max_length=3)
    explicit_constraints: list[str] = Field(default_factory=list)
    observation_gap: str = ""  # Requires more inspection, not a user question.
    knowledge_gap: str = ""  # May justify skill/retrieval, not a user question.


class Answer(StrictModel):
    question_id: str = Field(min_length=1, max_length=160)
    response_type: Literal["option", "free_text", "skip", "delegate"]
    response: str = Field(default="", max_length=4000)


class Directive(StrictModel):
    dimension: Dimension
    scope: str
    outcome: str
    provenance: Literal["user_request", "user_answer", "model_default"]
    strength: Literal["hard", "strong", "optional"]
    source_id: str
    verification_hint: str
    operations_are_suggestions: list[str] = Field(default_factory=list)


class CreativeBrief(StrictModel):
    schema_version: str = "takeone-rubric-v1"
    initial_request: str
    directives: list[Directive]
    explicit_constraints: list[str]
    default_protections: list[str]
    technical_envelope: list[str]
    capabilities_used_for_questioning: list[str]
    answers: list[Answer]
    delegated_or_skipped: list[str]
    unresolved: list[str]
    review_rule: str = (
        "Compare graded video with the source and this brief. Verify hard boundaries "
        "first; check technical artifacts over time; report evidence-linked deviations. "
        "Do not substitute the reviewer's personal aesthetic preference for user intent."
    )


DEFAULT_PROTECTIONS = [
    "Keep original media unchanged; grades must be reversible.",
    "Do not alter identity, invent scene content or claim reconstruction of missing detail.",
    "Do not recolor meaningful products or markings without authorization.",
    "Do not sacrifice subject legibility or introduce temporal artifacts without approval.",
]


class RubricSession:
    """Deterministic controller: model proposes; controller permits or rejects."""

    def __init__(self, inputs: RubricInput, max_decisions: int = 5):
        if not 0 <= max_decisions <= 5:
            raise ValueError("max_decisions must be between 0 and 5")
        self.inputs = inputs
        self.max_decisions = max_decisions
        self.answers: list[Answer] = []
        self.directives: list[Directive] = []
        self.resolved_keys: set[str] = set()
        self.pending: Candidate | None = None
        self.asked_count = 0
        self.events: list[dict] = []
        self.rejected: list[dict] = []

    def _validate_candidate(self, c: Candidate) -> str | None:
        if c.uncertainty_type != "preference":
            return "not a preference uncertainty"
        if c.target_key in self.resolved_keys:
            return "preference already resolved"
        if c.id in {a.question_id for a in self.answers}:
            return "question ID already used"
        if c.interaction == "preview" and not self.inputs.capabilities.supports_look_previews:
            return "preview operation is not available"
        if not c.decision_fork.strip():
            return "no stated consequence for the edit"
        observations = {o.id for o in self.inputs.observations} | {"user_request"}
        if not set(c.evidence_ids).issubset(observations):
            return "question cites missing scene/user evidence"
        if len({o.id for o in c.options}) != len(c.options):
            return "repeated option IDs"
        outcomes = {o.intended_outcome.strip().casefold() for o in c.options}
        if len(outcomes) < 2 or "" in outcomes:
            return "options do not lead to distinct editing goals"
        if not any(o.required_operations for o in c.options):
            return "no executable edit operation described for this decision"
        capabilities = set(self.inputs.capabilities.operations)
        if any(not set(o.required_operations).issubset(capabilities) for o in c.options):
            return "an option requires unavailable editing operations"
        if any(not o.label.strip() for o in c.options) or not c.question.strip():
            return "empty question or option label"
        if not c.target_key.strip() or not c.scope.strip():
            return "missing target or scope"
        return None

    def next_question(self, proposal: Proposal) -> Candidate | None:
        """Choose at most one *consequential* question per cycle; never batch ask."""
        if self.pending is not None:
            raise RuntimeError("answer/skip the pending question before proposing another")
        for quote in proposal.explicit_constraints:
            if quote.strip() and quote in self.inputs.user_request:
                if quote not in self.inputs.explicit_constraints:
                    self.inputs.explicit_constraints.append(quote)
            else:
                self.rejected.append({"candidate_id": "constraint", "reason": "constraint is not a literal user quote"})
        if self.asked_count >= self.max_decisions:
            self.events.append({"event": "stop", "reason": "question budget exhausted"})
            return None
        approved: list[Candidate] = []
        for candidate in proposal.candidates:
            reason = self._validate_candidate(candidate)
            if reason:
                self.rejected.append({"candidate_id": candidate.id, "reason": reason})
            else:
                approved.append(candidate)
        if not approved:
            self.events.append({"event": "stop", "reason": "no actionable preference fork"})
            return None
        order = {Level.HIGH: 3, Level.MEDIUM: 2, Level.LOW: 1}
        # Ordinal heuristic, NOT calibrated expected value of information.
        approved.sort(
            key=lambda c: (
                int(c.preservation_risk),
                order[c.impact],
                order[c.answerability],
                c.id,
            ),
            reverse=True,
        )
        selected = approved[0]
        self.pending = selected
        self.asked_count += 1
        self.events.append({"event": "question_selected", "id": selected.id,
                            "reason": selected.decision_fork})
        return selected

    def record_answer(self, answer: Answer) -> None:
        if self.pending is None or answer.question_id != self.pending.id:
            raise ValueError("answer does not match a pending question")
        c = self.pending
        if answer.response_type == "option":
            option = next((o for o in c.options if o.id == answer.response), None)
            if option is None:
                raise ValueError("option not found")
            self.directives.append(Directive(
                dimension=c.dimension, scope=c.scope, outcome=option.intended_outcome,
                provenance="user_answer", strength="strong", source_id=c.id,
                verification_hint=f"Assess '{option.intended_outcome}' on {c.scope} against source and approved previews.",
                operations_are_suggestions=option.required_operations,
            ))
        elif answer.response_type == "free_text":
            if not answer.response.strip():
                raise ValueError("free_text cannot be blank")
            self.directives.append(Directive(
                dimension=c.dimension, scope=c.scope, outcome=answer.response.strip(),
                provenance="user_answer", strength="strong", source_id=c.id,
                verification_hint="Assess this literal user preference; clarify consequential ambiguity before execution.",
            ))
        elif answer.response_type not in ("skip", "delegate"):
            raise ValueError("invalid response type")
        # Even skips are marked resolved for now; re-open only explicitly.
        self.resolved_keys.add(c.target_key)
        self.answers.append(answer)
        self.events.append({"event": "answer", "id": c.id, "kind": answer.response_type})
        self.pending = None

    def brief(self) -> CreativeBrief:
        if self.pending is not None:
            raise RuntimeError("cannot finalize with an unanswered question")
        unresolved = [
            "Skipped or delegated question: " + a.question_id
            for a in self.answers if a.response_type in ("skip", "delegate")
        ]
        return CreativeBrief(
            initial_request=self.inputs.user_request,
            directives=self.directives,
            explicit_constraints=self.inputs.explicit_constraints,
            default_protections=DEFAULT_PROTECTIONS.copy(),
            technical_envelope=self.inputs.technical_envelope + self.inputs.capabilities.limitations,
            capabilities_used_for_questioning=self.inputs.capabilities.operations,
            answers=self.answers,
            delegated_or_skipped=[a.question_id for a in self.answers if a.response_type in ("skip", "delegate")],
            unresolved=unresolved,
        )

    def snapshot(self) -> dict:
        return RubricSnapshot(
            inputs=self.inputs, max_decisions=self.max_decisions, answers=self.answers,
            directives=self.directives, resolved_keys=sorted(self.resolved_keys),
            pending=self.pending, asked_count=self.asked_count, events=self.events, rejected=self.rejected,
        ).model_dump(mode="json")

    @classmethod
    def restore(cls, value: dict):
        state = RubricSnapshot.model_validate(value)
        session = cls(state.inputs, state.max_decisions)
        for name in ("answers", "directives", "pending", "asked_count", "events", "rejected"):
            setattr(session, name, getattr(state, name))
        session.resolved_keys = set(state.resolved_keys)
        return session


class RubricSnapshot(StrictModel):
    inputs: RubricInput
    max_decisions: int = Field(ge=0, le=5)
    answers: list[Answer]
    directives: list[Directive]
    resolved_keys: list[str]
    pending: Candidate | None
    asked_count: int = Field(ge=0, le=5)
    events: list[dict]
    rejected: list[dict]
