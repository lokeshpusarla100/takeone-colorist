"""Take One creative-direction rubric v1.

Scope: preference elicitation + brief assembly; never edits video or authorizes
unsupported operations. The Muse adapter supplies proposals. This module checks data/tool contracts,
records user decisions and exports a traceable creative brief. Muse chooses the next question.
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


class PreferenceFamily(str, Enum):
    TONAL_READABILITY = "tonal_readability"
    CONTRAST_TEXTURE = "contrast_texture"
    COLOR_INTENSITY = "color_intensity"
    COLOR_BALANCE = "color_balance"
    SUBJECT_PRIORITY = "subject_priority"
    PRESERVATION = "preservation"
    CONTINUITY = "continuity"
    PURPOSE = "purpose"
    OTHER = "other"


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


class RememberedDefault(StrictModel):
    id: str
    revision: int
    dimension: Dimension
    key: str
    outcome: str
    conflicted: bool = False


class RubricInput(StrictModel):
    user_request: str
    observations: list[Observation]
    capabilities: CapabilityManifest
    technical_envelope: list[str] = Field(default_factory=list)
    explicit_constraints: list[str] = Field(default_factory=list)
    project_id: str | None = None
    memory_defaults: list[RememberedDefault] = Field(default_factory=list)
    project_feedback: list[dict] = Field(default_factory=list)
    recent_contract_errors: list[str] = Field(default_factory=list,max_length=3)


class Option(StrictModel):
    id: str
    label: str
    intended_outcome: str  # Outcome, not a guessed numeric knob setting.
    required_operations: list[str] = Field(default_factory=list)


class Candidate(StrictModel):
    id: str
    dimension: Dimension
    target_key: str  # Model-supplied topic metadata; not a Python rejection key.
    preference_family: PreferenceFamily = PreferenceFamily.OTHER
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
    memory_refs: list[str] = Field(default_factory=list, max_length=10)
    memory_conflict: bool = False
    decision_id: str | None = Field(default=None,min_length=1,max_length=160)
    followup_to: str | None = None
    clarification_reason: str = Field(default='',max_length=800)
    unresolved_preference: str = Field(default='',max_length=1200)


class Proposal(StrictModel):
    candidates: list[Candidate] = Field(default_factory=list, max_length=3)
    explicit_constraints: list[str] = Field(default_factory=list)
    observation_gap: str = ""  # Requires more inspection, not a user question.
    knowledge_gap: str = ""  # May justify skill/retrieval, not a user question.
    memory_used_ids: list[str] = Field(default_factory=list, max_length=10)
    completion_reason: str = Field(default="", max_length=1200)


class Answer(StrictModel):
    question_id: str = Field(min_length=1, max_length=160)
    response_type: Literal["option", "free_text", "skip", "delegate"]
    response: str = Field(default="", max_length=4000)


class Directive(StrictModel):
    dimension: Dimension
    scope: str
    outcome: str
    provenance: Literal["user_request", "user_answer", "model_default", "memory_default"]
    strength: Literal["hard", "strong", "optional"]
    source_id: str
    verification_hint: str
    operations_are_suggestions: list[str] = Field(default_factory=list)
    memory_refs: list[str] = Field(default_factory=list)


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
    project_id: str | None = None
    memory_influences: list[dict] = Field(default_factory=list)
    review_rule: str = (
        "Translate the directives into one coordinated treatment; do not compound the strength "
        "when several statements describe the same look. "
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
    """One-question session and provenance. Creative selection belongs to Muse."""

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
        self.questions: list[Candidate] = []
        self.used_memory_ids: set[str] = set()
        self.blocked_memory_ids: set[str] = set()

    def answered_decisions(self) -> list[dict]:
        questions = {q.id: q for q in self.questions}
        directives = {d.source_id: d for d in self.directives}
        forks = {e["id"]: e.get("reason", "") for e in self.events if e.get("event") == "question_selected"}
        decisions = []
        for answer in self.answers:
            question = questions.get(answer.question_id)
            directive = directives.get(answer.question_id)
            fork = question.decision_fork if question else forks.get(answer.question_id, "")
            outcome = directive.outcome if directive else None
            operations = (sorted({op for o in question.options for op in o.required_operations}) if question
                          else directive.operations_are_suggestions if directive else [])
            scope = question.scope if question else directive.scope if directive else ""
            groups = [question.preference_family.value] if question and question.preference_family != PreferenceFamily.OTHER else []
            decisions.append({
                "question_id": answer.question_id,
                "question": question.question if question else "",
                "target_key": question.target_key if question else "",
                "scope": scope, "required_operations": operations,
                "preference_families": sorted(groups),
                "decision_fork": fork, "response_type": answer.response_type,
                "response": answer.response, "selected_outcome": outcome,
                "options": [o.model_dump(mode="json") for o in question.options] if question else [],
                "decision_id":question.decision_id if question else None,
            })
        return decisions

    def _check_question_contract(self, c: Candidate) -> None:
        """Data/tool boundary only; never infer creative overlap, relevance or priority."""
        if c.id in {a.question_id for a in self.answers}:
            raise ValueError("question ID already used; a follow-up needs a unique ID")
        def wording(q):
            norm=lambda value:' '.join(value.casefold().strip().rstrip('?.!').split())
            return (norm(q.scope),norm(q.question),sorted((norm(o.label),norm(o.intended_outcome)) for o in q.options))
        answered={a.question_id for a in self.answers}
        history={q.id:q for q in self.questions if q.id in answered}
        if c.followup_to and c.followup_to not in history:raise ValueError('Follow-up references an unknown answered question')
        for previous in history.values():
            if wording(c)==wording(previous):raise ValueError('Question and alternatives exactly repeat an answered question')
            if c.decision_id and c.decision_id==previous.decision_id:
                if c.followup_to!=previous.id or not c.clarification_reason.strip():
                    raise ValueError('Decision ID already addressed; a genuine clarification needs followup_to and new-information reason')
                if {o.intended_outcome.strip().casefold() for o in c.options}=={o.intended_outcome.strip().casefold() for o in previous.options}:
                    raise ValueError('Clarification repeats the same outcome alternatives')
        if len({o.id for o in c.options}) != len(c.options):
            raise ValueError("option IDs must be unique")
        if any(not value.strip() for value in [c.id,c.question,c.scope,c.target_key,c.decision_fork,
                                               *(o.label for o in c.options),*(o.intended_outcome for o in c.options)]):
            raise ValueError("required question fields cannot be empty")
        if set(c.evidence_ids) - ({o.id for o in self.inputs.observations} | {"user_request"}):
            raise ValueError("question cites an unknown evidence ID")
        if any(set(o.required_operations) - set(self.inputs.capabilities.operations) for o in c.options):
            raise ValueError("question references an unavailable tool operation")
        if c.interaction == "preview" and not self.inputs.capabilities.supports_look_previews:
            raise ValueError("preview interaction is unavailable")
        if set(c.memory_refs) - {m.id for m in self.inputs.memory_defaults}:
            raise ValueError("question cites unavailable preference memory")
        if c.memory_conflict and not c.memory_refs:
            raise ValueError("memory conflict reference is missing")

    def next_question(self, proposal: Proposal) -> Candidate | None:
        """Choose at most one *consequential* question per cycle; never batch ask."""
        if self.pending is not None:
            raise RuntimeError("answer/skip the pending question before proposing another")
        allowed_memories = {m.id for m in self.inputs.memory_defaults if not m.conflicted}
        self.blocked_memory_ids.update(ref for c in proposal.candidates[:1] if c.memory_conflict for ref in c.memory_refs
                                       if ref in {m.id for m in self.inputs.memory_defaults})
        allowed_memories -= self.blocked_memory_ids
        self.used_memory_ids.update(set(proposal.memory_used_ids) & allowed_memories)
        if set(proposal.memory_used_ids) - allowed_memories:
            self.rejected.append({"candidate_id": "memory", "reason": "unavailable or conflicting memory cannot supply a default"})
        for quote in proposal.explicit_constraints:
            if quote.strip() and quote in self.inputs.user_request:
                if quote not in self.inputs.explicit_constraints:
                    self.inputs.explicit_constraints.append(quote)
            else:
                self.rejected.append({"candidate_id": "constraint", "reason": "constraint is not a literal user quote"})
        if self.asked_count >= self.max_decisions:
            self.events.append({"event": "stop", "reason": "question budget exhausted"})
            return None
        if not proposal.candidates:
            self.events.append({"event": "stop", "reason": "Muse proposed no further question",
                                "explanation":proposal.completion_reason})
            return None
        # Muse owns selection/order. No Python ranking, topic inference or semantic rejection.
        selected = proposal.candidates[0]
        self._check_question_contract(selected)
        self.pending = selected
        self.questions.append(selected.model_copy(deep=True))
        self.asked_count += 1
        self.events.append({"event": "question_selected", "id": selected.id,
                            "reason": selected.decision_fork, "memory_refs": selected.memory_refs})
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
                memory_refs=c.memory_refs,
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
        if not any(q.id == c.id for q in self.questions):
            self.questions.append(c.model_copy(deep=True))
        # Legacy snapshot field records addressed keys; it no longer rejects follow-ups.
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
        if any(e.get("event") == "stop" and e.get("reason") == "question budget exhausted" for e in self.events):
            unresolved.append("Consultation ended at the question limit; additional preference gaps were not evaluated.")
        directives = list(self.directives)
        for feedback in self.inputs.project_feedback:
            if feedback.get("text"):
                directives.append(Directive(dimension=feedback.get("dimension","appearance"),scope="whole clip",
                    outcome=feedback["text"],provenance="user_answer",strength="strong",
                    source_id=feedback["event_id"],
                    verification_hint="Latest current-video feedback takes priority over earlier conflicting direction."))
        influences = []
        for memory in self.inputs.memory_defaults:
            if memory.id not in self.used_memory_ids or memory.id in self.blocked_memory_ids or memory.conflicted:
                continue
            # Explicit project answers supersede remembered defaults in that dimension.
            if any(d.dimension == memory.dimension and d.provenance == "user_answer" for d in directives):
                continue
            source_id = f"memory:{memory.id}:{memory.revision}"
            directives.append(Directive(dimension=memory.dimension, scope="whole clip", outcome=memory.outcome,
                provenance="memory_default", strength="optional", source_id=source_id,
                verification_hint="Advisory default only; current instructions and protections take priority.",
                memory_refs=[memory.id]))
            influences.append({"entry_id":memory.id,"revision":memory.revision,"stage":"brief","target":source_id})
        return CreativeBrief(
            initial_request=self.inputs.user_request,
            directives=directives,
            explicit_constraints=self.inputs.explicit_constraints,
            default_protections=DEFAULT_PROTECTIONS.copy(),
            technical_envelope=self.inputs.technical_envelope + self.inputs.capabilities.limitations,
            capabilities_used_for_questioning=self.inputs.capabilities.operations,
            answers=self.answers,
            delegated_or_skipped=[a.question_id for a in self.answers if a.response_type in ("skip", "delegate")],
            unresolved=unresolved,
            project_id=self.inputs.project_id,
            memory_influences=influences,
        )

    def snapshot(self) -> dict:
        return RubricSnapshot(
            inputs=self.inputs, max_decisions=self.max_decisions, answers=self.answers,
            directives=self.directives, resolved_keys=sorted(self.resolved_keys),
            pending=self.pending, asked_count=self.asked_count, events=self.events, rejected=self.rejected,
            questions=self.questions,
            used_memory_ids=sorted(self.used_memory_ids),
            blocked_memory_ids=sorted(self.blocked_memory_ids),
        ).model_dump(mode="json")

    @classmethod
    def restore(cls, value: dict):
        state = RubricSnapshot.model_validate(value)
        session = cls(state.inputs, state.max_decisions)
        for name in ("answers", "directives", "pending", "asked_count", "events", "rejected", "questions"):
            setattr(session, name, getattr(state, name))
        session.resolved_keys = set(state.resolved_keys)
        session.used_memory_ids = set(state.used_memory_ids)
        session.blocked_memory_ids = set(state.blocked_memory_ids)
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
    questions: list[Candidate] = Field(default_factory=list)
    used_memory_ids: list[str] = Field(default_factory=list)
    blocked_memory_ids: list[str] = Field(default_factory=list)
