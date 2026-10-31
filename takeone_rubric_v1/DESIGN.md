# Take One — Creative Direction Rubric v1

**Scope:** The Meta Muse Spark-powered **consultation/intent-elicitation component only**. No editing application, renderer, segmentation integration, generic RAG, or agent framework is included.

## Architectural position

```text
 [cached Muse visual observations]   [source signal/metadata]   [user prompt]
                 \                         |                         /
                  \                        |                        /
                   [Rubric input: evidence + capability manifest]
                                        |
                         [Muse Spark 1.3: propose forks]
                                        |
                        [Deterministic rubric controller]
                          |  reject invented evidence
                          |  reject non-preference gaps
                          |  reject unsupported operations
                          |  reject duplicate/resolved questions
                          |  select highest-consequence fork
                                        |
                           [one question OR no question]
                                        |
                           [answer / skip / delegate]
                                        |
                             [versioned CreativeBrief]
                               /                     \
                      [Colorist consumes]       [Critic consumes]
```

A video-understanding module **upstream** may invoke Muse Spark with video. This rubric only consumes cached visual observations; it does not resend the entire clip after each answer. A signal-analysis module supplies technical measurements separately. Only a user can establish their own preferences; model observations do not count as approval.

## Five dimensions + technical envelope

| Dimension | Purpose | Example of a consequential fork |
|---|---|---|
| Meaning | Intended emotion/purpose | Inviting memory vs isolated tension |
| Appearance | Look and degree of departure | Natural warm balance vs restrained cool mood |
| Attention | Viewer hierarchy | Keep bike readable vs silhouette |
| Boundaries | Preserve identities, materials, factual integrity | Protect product paint vs permit stylization |
| Continuity | Temporal/shot relationship | Match shots vs preserve different times of day |
| Technical envelope | Feasibility and output constraints, **not** a creative question category | Localized mask unavailable; ask no question promising it |

These are **coverage fields**, not a mandatory form. Ideally ask **0–3**, at most **5**, consequential decisions across the entire consultation. Each question permits *option / free text / skip / delegate*. Preview selection may be used only if a preview tool exists, and counts toward the same budget.

## Exact module contract

### Inputs: `RubricInput`

- `user_request`: original wording, retained verbatim.
- `observations[]`: evidence IDs, time/subject scope, descriptions, source type, stated uncertainty.
- `capabilities.operations[]`: operation IDs **actually supported by the current editing pipeline**, such as `global_grade`, `regional_grade`, `shot_match`—not aspirational abilities.
- `capabilities.limitations[]`: relevant failure risks or unsupported operations.
- `technical_envelope[]`: source/render constraints, known only from metadata or authoritative measurements.
- `explicit_constraints[]`: e.g. preserve appearance of vehicle paint, preserve factual content.

### Muse Spark request: `propose(session)`

Request **structured** `Proposal` JSON (official `muse-spark-1.3` structured-output support). Each `Candidate` must identify:

- which dimension/target preference slot it concerns;
- an evidence ID and source/time scope;
- the actual question, ordinary-language options and distinct intended outcomes;
- **the editing fork**: how competing answers change the appearance;
- which editing operations each option would require;
- whether preserving meaningful content makes the question consequential;
- ordinal visual impact, user answerability and interaction kind.

Muse may return zero candidates. Observation gaps require more visual/signal inspection; execution gaps require technical trials; knowledge gaps may justify reference lookup. **Only missing preferences become rubric questions.**

### Controller: `RubricSession`

- Reject candidates unsupported by listed evidence or capability IDs.
- Reject candidates with identical outcomes or a preference slot already answered.
- Rank eligible candidates using ordinal preservation risk → visual impact → answerability. This ranking is a **heuristic, not calibrated value of information**.
- Select **one** candidate; UI shows the question/options plus *Other*, *Skip*, *You decide*.
- After an answer, store exact wording/selected outcome with provenance, mark the slot resolved, and request **fresh candidates** if more input is genuinely useful. Do not ask a pre-generated batch.
- Stop if no eligible candidate remains or the question budget is exhausted.
- `brief()` creates a typed `CreativeBrief` carrying approved outcomes, explicit constraints, default protections, technical limits, answer provenance and review rule. Downstream operations remain *suggestions* until a real renderer verifies them.

### Outputs

The brief passes to both the **Colorist** (plans edits) and **Critic** (checks compliance against source + user intent). Do **not** let the Critic change the brief to fit its preferred grade. If a hard constraint cannot be achieved, pause that particular transformation and report the limitation; do not simply improve an aesthetic score.

### Diagnostic events

`RubricSession.events` records `question_selected`, `answer`, `stop`, and (when the Meta adapter is used) `muse_proposal`, including usage metadata when available. `RubricSession.rejected` records candidate IDs and rejection reasons. The larger application may relay these as SSE events; this package does not run a server or store persistent histories.

## Example behavior: rainy motorcycle clip

User: "Make this feel cinematic, but keep the motorcycle recognizable."

Observations: `obs1`: motorcycle at night, signs in background. Available operations: `global_grade`, `regional_grade`.

Muse proposes:
- **Meaning fork:** documentary-like night vs dramatic commercial atmosphere.
- **Attention fork:** keep motorcycle readable vs permit silhouette.
- **Unsupported fork:** add physically accurate light reflections to the motorcycle using 3D relighting.

Controller rejects the unsupported fork and can prioritize the attention question if preserving recognizability is important. When the user selects "Readable", the brief records this as a user-approved *outcome*, not a hardcoded `+0.6 EV` adjustment.

The colorist later determines feasible adjustments; the critic checks readability against the same brief, on moving footage.

## Responsibilities & safeguards

| Responsibility | Owner |
|---|---|
| Scene semantics / possible subjects | Muse visual pass |
| Actual signal values / clipping / metadata | Video and color tools |
| Creative preferences / permissions | User (or visible, conservative delegation) |
| Candidate wording / interpretations | Muse rubric prompter |
| Question budget, evidence IDs, basic capability allowlisting | Deterministic rubric controller |
| Real feasibility, masks, render quality | Renderer / colorist |
| Verify constraints and diagnose failures | Reviewer + technical checks |

**Limits:** A valid capability name does **not** prove a specific edit works; rendering trials remain essential. This v1 doesn't implement advanced conflict resolution, preview generation, persistence, reference retrieval, dynamic interpretation of free-text choices, or production-grade authentication. Hard restrictions are **carried in the brief**, not independently enforced by the colorist here. This package is a consultative decision module only.

## Verification status

- Offline automated tests validate selection, schema parsing, answer provenance, unsupported operations, missing evidence, duplication and budget.
- **No Meta API call or real-video evaluation has been performed.** Actual model compliance, question relevance, and visual-quality improvement remain unverified.
- Meta API docs: https://dev.meta.ai/docs/cookbook/structured-output and https://dev.meta.ai/docs/video-understanding
- Research basis: user-supplied report *Deep Research Mission — Designing a Universal Creative-Direction Rubric for Autonomous Cinematic Color Grading*, particularly its “Final universal rubric”, “Adaptive question selection”, “Executable creative direction” and “Recommended v1” sections.

## Simple usage

```python
from rubric import RubricInput, CapabilityManifest, Observation, RubricSession, Answer
from muse_adapter import propose

inputs = RubricInput(
    user_request="Make the motorcycle cinematic, but preserve its paint appearance",
    observations=[Observation(id="scene1", scope="00:00-00:08",
                              description="Motorcycle in a night street scene",
                              source="muse_visual")],
    capabilities=CapabilityManifest(operations=["global_grade", "regional_grade"]),
    explicit_constraints=["Do not recolor motorcycle paint"],
)
session = RubricSession(inputs)
question = session.next_question(propose(session))  # Requires MODEL_API_KEY
# UI presents question with options, skip and delegate.
if question:
    session.record_answer(Answer(question_id=question.id,
                                 response_type="option", response=question.options[0].id))
brief = session.brief()  # In an app, repeat propose/ask only when needed.
print(brief.model_dump_json(indent=2))
```

## Suggested location when integrating into Take One

`takeone/creative_direction/rubric.py` and `takeone/creative_direction/muse_adapter.py`. Do not make a standalone rubric agent or duplicate editing responsibilities.
