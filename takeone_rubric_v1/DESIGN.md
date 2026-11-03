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
                        [Session + data/tool contract checks]
                          |  known evidence/tool IDs
                          |  unique question/option IDs
                          |  preserve exact answers
                          |  enforce interaction budget
                          |  show Muse's selected question
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
- `project_id`: optional application project reference for the central prompt gate.
- `project_feedback[]`: literal current-video feedback, independent of cross-project memory settings.
- `memory_defaults[]`: optional prepared future interface; the application supplies no defaults in capture-only mode.

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

When proposing zero candidates, Muse is asked for completion_reason: a concise user-facing explanation of why no supported preference fork remains. It is an outcome summary, not private reasoning, and never becomes an editing directive or preference-memory event. Questioning can also stop at the configured limit. Invalid data/tool contracts fail explicitly instead of becoming a completed consultation. Historical validation-rejection stops remain readable.

Broad adjectives alone do not necessarily settle treatment strength, saturation, readability or preservation tradeoffs. Muse considers those choices and the six coverage areas against actual capabilities and prior answers, and is asked to return zero or one question (one_at_a_time) or one round of independent questions (batch). The next call happens after the answer or round, not as a fixed sequence.

### Question modes (`TAKEONE_RUBRIC_MODE`)

- **`batch` (default for new consultations).** Muse returns, in one round, every consequential question that
  is independent of the others (never more than the remaining budget; zero is fine). The UI shows the round
  on one screen and submits all answers together (`POST /answers/round`, all or nothing). Muse then makes one
  call with every answer: it returns zero questions when they settle the edit, or only follow-ups for a
  conflict between answers or a choice that depended on them. A question whose relevance depends on another
  answer waits for that next round, so adaptivity is kept while calls drop from one per question to one per
  round. The round is cut only by the budget, in Muse's order. Round mates follow the same data rules as
  answered questions (unique IDs and decision IDs, no exact repeats).
- **`one_at_a_time`.** One question per call (the earlier behaviour). Sessions saved before batching restore
  in this mode.

The single-answer route `/answers` still works in both modes; inside a batch round it records the answer and
shows the next question of the round without a model call.

**Closing-call prediction.** Each proposal with questions also states `final_if_options_chosen` (whichever
offered options are chosen, no follow-up is needed) and `completion_reason_if_final`. The service records
`closing_prediction: {predicted_final, actual_final}` on every closing call. With
`TAKEONE_RUBRIC_SKIP_CLOSING=1` the closing call is skipped when the prediction holds and every answer of the
round was an offered option (free text, skip or delegate always get the call). Leave it off until
`scripts/rubric_replay.py` reports that predictions agree with the closing calls.

**Request content.** `previous_decisions` carries every answer (question ID, response type, response,
target key, question, alternatives, chosen outcome); the former separate `answers` and
`previously_addressed_keys` lists were exact duplicates and are no longer sent.

### Controller: `RubricSession`

- Check known evidence/capability/memory IDs, unique question/option IDs and nonempty required fields. Invalid contracts raise an error.
- Display Muse's first supplied question. Python does not infer overlap, compare outcome meaning, rank candidates or suppress repeated topics.
- After an answer, store exact wording/selected outcome with provenance and ask Muse to reassess the remaining choices. Its prompt owns relevance, repetition and priority.
- Stop when Muse supplies no question or the configured question budget is exhausted.
- `brief()` creates a typed `CreativeBrief` carrying approved outcomes, explicit constraints, default protections, technical limits, answer provenance and review rule. Downstream operations remain *suggestions* until a real renderer verifies them.

### Outputs

Follow-up requests now include the previous question, all alternatives, response type and selected
outcome. Question history persists in snapshots; older snapshots use their saved directives and
decision forks as a fallback. Preference-family tags are passed through only when supplied by the
model; there is no English keyword inference or same-scope family rejection. Muse decides whether
a follow-up repeats an earlier choice. A new question needs a unique protocol ID, but may reuse a topic.

The model is instructed to ask about visible outcomes, use ordinary language and stop when useful
preferences are resolved. No extra model verification call is added. Briefs coordinate related
directives and disclose when questioning ended at the budget limit.

Usage events are taken only from the newly completed proposal call. A local budget stop has no
usage event, and a real zero-question response still records its own returned usage. Existing saved
logs are retained as audit records and are not retroactively rewritten.

The brief passes to both the **Colorist** (plans edits) and **Critic** (checks compliance against source + user intent). Do **not** let the Critic change the brief to fit its preferred grade. If a hard constraint cannot be achieved, pause that particular transformation and report the limitation; do not simply improve an aesthetic score.

### Diagnostic events

`RubricSession.events` records questions, answers, stops and proposal usage. Legacy rejection history and literal-constraint/memory-provenance checks remain readable. The application persists full structured proposals and selected IDs or contract errors in State.question_proposals, included in exports. No private reasoning content is requested or stored. This package does not itself run a server.

## Example behavior: rainy motorcycle clip

User: "Make this feel cinematic, but keep the motorcycle recognizable."

Observations: `obs1`: motorcycle at night, signs in background. Available operations: `global_grade`, `regional_grade`.

Muse proposes:
- **Meaning fork:** documentary-like night vs dramatic commercial atmosphere.
- **Attention fork:** keep motorcycle readable vs permit silhouette.
- **Unsupported fork:** add physically accurate light reflections to the motorcycle using 3D relighting.

Muse chooses the attention question when preserving recognizability makes it most useful. An unavailable tool reference produces an explicit contract error rather than a Python-selected replacement or completed brief. When the user selects "Readable", the brief records that outcome with provenance rather than inventing an exposure adjustment.

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

**Limits:** A valid capability name does **not** prove a specific edit works; rendering trials remain essential. This module exposes snapshots; application-level JSON persistence lives in takeone/service.py and takeone/store.py. It does not implement preview rendering, general reference retrieval, dynamic interpretation of free-text choices, or production-grade authentication. Hard restrictions are **carried in the brief**, not independently enforced by this module. It remains a consultative decision module.

## Capture-only preference integration

The application records editing evidence and scoped interpretations separately in takeone/preferences.py. Raw rubric answers preserve option IDs/labels or literal text with question/project references. Skip and delegation are interaction evidence, not proof of a specific aesthetic preference. No extra extraction model call is made.

PromptMemoryGate filters the actual rubric context. With application disabled, remembered defaults and memory-derived directives are excluded while the current request, current-video answers and feedback remain available. The same boundary covers colorist context; future critic/look-selection/preview builders must use it as well.

Prepared future fields include Candidate.memory_refs and memory_conflict, Proposal.memory_used_ids, and memory-provenance brief directives/influence records. Relevant confirmed defaults may be advisory only after explicit new-project opt-in and the other application gates. Current instructions, constraints and tool limits take precedence; memory never grants permission. Existing frozen briefs are not rewritten when stored entries change. See [the application memory contract](../docs/PREFERENCE_MEMORY.md) for exact rollout limits.

## Verification status

- Offline checks verify model-order selection, acceptance of overlapping topics/wording, schema parsing, answer provenance, tool/ID contract errors, full proposal persistence and the question ceiling. They do not prove live creative quality.
- Preference integration tests use local fixtures and mocked SDK requests, with no billable calls. They verify prompt isolation and current-video feedback, not live model compliance, question relevance, or visual-quality improvement.
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

## Current location in Take One

The module remains in `takeone_rubric_v1/`; `takeone/service.py` orchestrates it and `takeone/provider.py` connects the Muse adapter. Do not duplicate it as a standalone agent. Application preference storage and its API live separately in `takeone/preferences.py` and `takeone/preference_api.py`.
