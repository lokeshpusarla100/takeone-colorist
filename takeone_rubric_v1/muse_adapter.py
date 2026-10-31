"""Muse Spark 1.3 adapter for proposing rubric questions.

Expects an *existing* cached scene analysis. Video upload, render and color grading
are deliberately outside the rubric boundary. Requires MODEL_API_KEY.
"""
import json
import os

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
A system controller will decide which ONE question to ask. Output only the requested schema.
Extract explicit preservation constraints as exact quotes from user_request in explicit_constraints.
Never invent or paraphrase those quotes. Normally ask 0-3 questions. A clear request needs none.
"""


def propose(session: RubricSession, model: str = "muse-spark-1.3", *, client=None) -> Proposal:
    if session.pending is not None:
        raise RuntimeError("Finish current user question first")
    context = {
        "user_request": session.inputs.user_request,
        "observations": [o.model_dump() for o in session.inputs.observations],
        "editing_capabilities": session.inputs.capabilities.model_dump(),
        "explicit_constraints": session.inputs.explicit_constraints,
        "technical_envelope": session.inputs.technical_envelope,
        "answers": [a.model_dump() for a in session.answers],
        "resolved_preference_keys": sorted(session.resolved_keys),
        "rubric_budget_remaining": session.max_decisions - session.asked_count,
    }
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
        max_completion_tokens=2400,
    )
    result = completion.choices[0].message.parsed
    if result is None:
        raise RuntimeError("Muse did not return a usable rubric proposal")
    session.events.append({
        "event": "muse_proposal",
        "model": model,
        "candidate_count": len(result.candidates),
        "usage": completion.usage.model_dump() if completion.usage else None,
    })
    return result
