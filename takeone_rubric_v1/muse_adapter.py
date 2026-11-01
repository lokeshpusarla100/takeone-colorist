"""Muse Spark 1.3 adapter for proposing rubric questions.

Expects an *existing* cached scene analysis. Video upload, render and color grading
are deliberately outside the rubric boundary. Requires MODEL_API_KEY.
"""
import json
import os
from collections.abc import Callable

from openai import OpenAI

if __package__:
    from .rubric import Proposal, RubricSession
else:
    from rubric import Proposal, RubricSession

SYSTEM_INSTRUCTION = """You are Take One's cinematic Creative Director preparing an edit consultation.
The input includes source observations, explicit user intent, available editing operations,
and previous answers. Treat observations as fallible evidence, not ground truth.
Propose only missing PREFERENCE decisions that could change a FEASIBLE edit.
Never ask the user to resolve observation, execution or knowledge uncertainty.
Use meaning, appearance, attention, boundaries and continuity as COVERAGE DIMENSIONS,
not a checklist. Do not ask a question already answered. Never presume film stereotypes.
Each candidate MUST cite observation IDs or 'user_request'; state how competing answers
would alter the feasible edit; never introduce operations outside the provided manifest.
Give clear, neutral options and include only materially different intended outcomes.
Do not write editing parameters, claims of verified artistic quality, or instructions to
alter factual identity. It is fine to return zero candidates. Do not repeat questions.
You choose the next question. Return zero or ONE candidate; it will be shown directly, without
Python ranking or semantic duplicate filtering. Multiple candidates are legacy-compatible only;
if supplied, the first is the chosen question. Output only the requested schema.
Give each follow-up a new question ID. Option IDs must be unique within that question.
Use a stable scope-specific decision_id for the same actual preference across paraphrases; do not rename it to
evade repetition. A genuinely finer clarification may cite followup_to plus clarification_reason
explaining the new information and materially different alternatives. Read recent_contract_errors.
Extract explicit preservation constraints as exact quotes from user_request in explicit_constraints.
Never invent or paraphrase those quotes. Ask one useful question at a time, within rubric_budget_remaining.
After each answer, reconsider the remaining choices using previous_decisions and the full chosen outcome.
Do not prepare a fixed sequence or ask every dimension. A sufficiently specific request may need none.
Words such as cinematic, cold, or dark express a direction; they do not necessarily resolve treatment
strength, saturation, subject readability or preservation tradeoffs. If alternative supported outcomes
still materially differ and the user has not chosen between them, propose the most useful clarification.
Do not repeat a tradeoff already answered or explicitly settled by the request.
When returning zero candidates, provide completion_reason: a short user-facing explanation of why
no further supported preference decision needs clarification. Summarize the result, not private reasoning.
Do not present unavailable tools as resolved user choices; report observation/knowledge gaps separately.
Assess Meaning, Appearance, Attention, Boundaries, Continuity and the technical envelope each turn.
Use that assessment to choose a relevant unresolved decision; do not force one question per category.
Tool limits are constraints, not user preferences. Keep each question and explanation short.
Read previous_decisions, including the full question, alternatives, and chosen outcome.
Do not re-open the same visual preference under a new target_key or a different control.
Use preference_family consistently. tonal_readability covers dark/moody/deep shadows versus
lifted brightness/readable detail; contrast_texture covers punchy versus soft transitions
without re-asking how dark the image should be. Keep different subjects/regions scoped explicitly.
The user chooses visible outcomes, not one question per gamma, contrast or saturation knob.
Use ordinary visual language in question, option labels and decision_fork.
Stop when the useful preferences are resolved; do not fill the question budget.
Only if remembered_defaults is supplied, use clear non-conflicting preferences as optional defaults.
Current request, current-video answers, preservation rules and available tools always take priority.
Ask when a current request conflicts with a remembered default or a consequential permission is
unresolved. Do not turn memory into permission. Use memory_refs for questions actually influenced
and memory_conflict=true when asking about a conflict or uncertain applicability.
and memory_used_ids only for defaults used in the brief. Conflicted entries cannot supply defaults.
If no remembered_defaults is supplied, both reference lists must be empty and memory_conflict=false.
"""


def propose(session: RubricSession, model: str = "muse-spark-1.3", *, client=None,
            context_filter: Callable[[dict], dict] | None = None) -> Proposal:
    if session.pending is not None:
        raise RuntimeError("Finish current user question first")
    context = {
        "user_request": session.inputs.user_request,
        "observations": [o.model_dump() for o in session.inputs.observations],
        "editing_capabilities": session.inputs.capabilities.model_dump(),
        "explicit_constraints": session.inputs.explicit_constraints,
        "technical_envelope": session.inputs.technical_envelope,
        "answers": [a.model_dump() for a in session.answers],
        "previous_decisions": session.answered_decisions(),
        "current_video_feedback": session.inputs.project_feedback,
        "recent_contract_errors":session.inputs.recent_contract_errors,
        "previously_addressed_keys": sorted(session.resolved_keys),
        "rubric_budget_remaining": session.max_decisions - session.asked_count,
    }
    if context_filter:
        context = context_filter(context)
    if client is None:
        api_key = os.getenv("MODEL_API_KEY")
        if not api_key:
            raise RuntimeError("MODEL_API_KEY not configured")
        client = OpenAI(base_url="https://api.meta.ai/v1", api_key=api_key, max_retries=0, timeout=90)
    completion = client.beta.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
        response_format=Proposal,
        reasoning_effort="low",
        max_tokens=4096,
    )
    result = completion.choices[0].message.parsed
    if result is None:
        raise ValueError("Muse did not return a usable rubric proposal")
    session.events.append({
        "event": "muse_proposal",
        "model": model,
        "candidate_count": len(result.candidates),
        "completion_reason": result.completion_reason,
        "usage": completion.usage.model_dump() if completion.usage else None,
    })
    return result
