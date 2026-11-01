# Rule-based logic inventory

This inventories application-level policies, heuristics, numerical processing and integrity checks in the current runtime. These are ordinary code paths; there is no separate validator-model call.

## Removed from creative question selection

In [rubric.py](../takeone_rubric_v1/rubric.py), removed:

- Keyword/regex inference of tonal readability, colour intensity, balance and contrast families.
- Adding an inferred tonal family to a model-labelled contrast question.
- Collapsing all global-tool scopes to one inferred scope for question rejection.
- Rejecting a question because its topic, family or normalized outcome wording overlaps an earlier choice.
- Python scoring/ranking by preservation risk, impact, answerability and ID.
- Automatically suppressing low-risk questions because a remembered topic is present.
- Filtering uncertainty type or judging whether two outcome descriptions are semantically distinct.
- Finalizing a brief merely because all proposed candidates failed these creative filters.

Muse now assesses meaning/relevance, repetition, priority and the six coverage areas. Its prompt requests zero or one question; the first is used for legacy lists. The model receives full previous questions, options, answers and supplied family metadata. Python no longer infers those meanings. The legacy resolved_keys snapshot field records addressed topics; it is not a rejection gate.

The full structured proposal, request ID, selection policy, selected question ID and any contract error are saved in State.question_proposals and exported with the session. Existing frozen runs and historical rejection logs are preserved.

## Remaining policies and heuristics

| Place | Remaining fixed behavior | Kind |
| --- | --- | --- |
| [rubric.py](../takeone_rubric_v1/rubric.py): DEFAULT_PROTECTIONS, CreativeBrief.review_rule | Preserve identity/materials, legibility and original media; fixed review instructions passed in the brief/prompt. Their text does not independently enforce pixel-level preservation. | Product policy |
| [rubric.py](../takeone_rubric_v1/rubric.py): record_answer/brief | Literal option/free-text outcomes become directives; skip/delegation remain unresolved. No model interprets free text here. | Provenance/assembly policy |
| [muse_adapter.py](../takeone_rubric_v1/muse_adapter.py), [provider.py](../takeone/provider.py) | Fixed model prompts: inspect up to eight observations, supported tools, conservative global grading, no invented measurements/permissions, no repeating answered choices. Muse makes the resulting creative decisions. | Prompt policy |
| [config.py](../takeone/config.py), [service.py](../takeone/service.py): count_call/generate | Default three questions, ceiling five; configured limits persist per project. Consultation call cap is configured question limit + four; at most three attempted render candidates per brief. | Resource/product limits |
| [rendering.py](../takeone/rendering.py): samples | Four fixed sample positions at 0%, 33%, 67%, 95%, bounded away from EOF; analysis dimensions up to 256 pixels per side. No content-aware frame/shot selection. | Sampling heuristic |
| [rendering.py](../takeone/rendering.py): samples/render | Encoded Rec.709-weighted brightness; near-black cutoff 0.02 and near-white 0.98. Warnings when their fractions increase by more than 0.08/0.03. No calibrated physical-exposure or aesthetic score. | Numerical measurement + warning thresholds |
| [contracts.py](../takeone/contracts.py): GradeParameters; [rendering.py](../takeone/rendering.py): filter_graph | Contrast 0.75–1.25, gamma 0.8–1.2, saturation 0–1.5, brightness gain 0.65–1.35 and cooling ±0.15. New v2 recipes use bounded sigmoid contrast/gain, explicit RGB balance and masked merge when planned. Old recipes retain their fixed-pivot operator. Muse chooses mode/controls; code executes them. | Tool implementation/bounds |
| [media.py](../takeone/media.py): inspect_source/capabilities; [rendering.py](../takeone/rendering.py): verify_source | Codec/pixel-format/colour-tag gates; 8-bit limited-range Rec.709 grading path; reject unsupported HDR/geometry and uncertain grading domains. | Supported-domain policy |
| [media.py](../takeone/media.py): make_previews | VP9 browser preview up to 1280 pixels, silent H.264 analysis proxy up to 640 pixels, fixed compression settings. | Preview policy |
| [config.py](../takeone/config.py), [app.py](../takeone/app.py), [media.py](../takeone/media.py) | Default 256 MiB, 120-second, up-to-4K source limits; explicit media/model timeouts and one media/model worker with four admitted job slots. | Resource limits |
| [sam.py](../takeone/sam.py): frame_info/mapping | CFR and upright-only initial path; max 15,000 frames; interval tolerance 0.00002s, source/proxy alignment tolerance 0.002s, aspect tolerance 0.005. No inferred temporal matching. | Current mapping support/tolerances |
| [sam.py](../takeone/sam.py): SamProvider; [service.py](../takeone/service.py): run_segmentation; [SamPanel.tsx](../frontend/src/components/SamPanel.tsx) | One explicit subject phrase; UI defaults to first five seconds, API null means full clip; one_bit masks, saved stream before decoding, 128 MiB raw-output limit and 30-minute local operation deadline. | Request/resource policy |
| [sam.py](../takeone/sam.py): persist_result/overlay | Manual diagnostics do not select a grading target or fill gaps. Nearest-neighbour crop placement, ID colours and 40% overlay tint remain diagnostic visualization. Automatic grading consumes masks separately as described below. | Mask/visualization policy |
| [local_grading.py](../takeone/local_grading.py), [service.py](../takeone/service.py) | Muse chooses global/local mode, subject and intervals. Code splits at scdet cuts (threshold 10), requires complete unambiguous current-frame masks, feathers edges, resets refinement at cuts/gaps, checks motion-compensated overlap and measured preservation bounds. Missing/unstable masks fail rather than becoming a global approximation. | Numerical/temporal execution and QA; no keyword-based creative selection |
| [preferences.py](../takeone/preferences.py): record/edit/delete | Literal evidence-based interpretations, automatic tentative/project scope, explicit manual confirmation, normalized-text deduplication, project support counts and deletion tombstones. No extraction-model call or automatic generalization. | Capture/storage policy |
| [preferences.py](../takeone/preferences.py): relevant/PromptMemoryGate | Exact context matching, confirmed-only entries, topic-key conflict grouping and explicit-topic overrides. Retrieval is disabled in this release. | Future personalization heuristic, currently off |
| [rubric.py](../takeone_rubric_v1/rubric.py): brief | In the prepared future application path, a current user directive in a dimension suppresses remembered defaults in that dimension. This is broad precedence logic and should be reviewed before enabling application. | Future precedence policy, currently off |
| [workflow.ts](../frontend/src/lib/workflow.ts), [useConsultation.ts](../frontend/src/lib/useConsultation.ts), editor components | Stage/progress mapping, mutation locks, one in-flight command, polling intervals, manual generation and cancellation. | Interaction/state policy |
| [useWorkspace.ts](../frontend/src/lib/useWorkspace.ts), [media.ts](../frontend/src/lib/media.ts) | Imported render compatibility checks; bounded browser metadata/decoder inspection and cancellation. | Media/UI compatibility policy |

## Remaining data and execution checks

| Place | Check and purpose |
| --- | --- |
| [rubric.py](../takeone_rubric_v1/rubric.py): _check_question_contract | Known evidence/memory/tool IDs, unique question/option IDs, nonempty fields and supported interaction types. Invalid contracts raise an explicit error rather than selecting another question or finalizing a brief. No ranking, regex meaning inference or semantic deduplication. |
| [rubric.py](../takeone_rubric_v1/rubric.py): next_question/record_answer | One pending question, configured ceiling and answer-to-question/option matching. Literal quoted user constraints cannot be invented by the model. |
| [contracts.py](../takeone/contracts.py), [api.ts](../frontend/src/lib/api.ts), [storage.ts](../frontend/src/lib/storage.ts) | JSON/schema/types, finite values, IDs, version/media references and bounded numeric parameters. |
| [service.py](../takeone/service.py): receipt/check_revision/submit | Command deduplication, stale-revision and active-job protection, source readiness, explicit retries, restart recovery and cancellation. |
| [store.py](../takeone/store.py), [preferences.py](../takeone/preferences.py), [sam.py](../takeone/sam.py) | Atomic writes, workspace/profile locks, source hashes, cache receipts and durable capture outboxes. |
| [preferences.py](../takeone/preferences.py), [preference_api.py](../takeone/preference_api.py) | Owner/profile isolation, evidence allowlists, delete replay protection and the capture/application gate. Capture remains on and personalization remains off by default. |
| [provider.py](../takeone/provider.py): analyze/grade | Observations must fall within video duration; grading decisions may cite only supplied source IDs. Invalid/truncated/failed requests retain structured errors and returned accounting when available. |
| [rendering.py](../takeone/rendering.py): verify_source/render | Original and brief hash binding, output decode, dimensions, duration/frame count, colour domain and audio checks. Passing does not establish artistic quality. |
| [sam.py](../takeone/sam.py): mapping/persist_result/overlay | Source/proxy identity, explicit frame/object IDs, valid mask bounds and parser completion, cache hashes, output decode and timing. Sparse frames remain gaps. The official parser's codec validation is also deterministic. |
| [app.py](../takeone/app.py) | Local-host/origin restrictions, request/upload limits, UUID-scoped file lookup and readiness checks before serving artifacts. Local namespaces are not multi-user authentication. |
| [usage.py](../takeone/usage.py), [tokenUsage.ts](../frontend/src/lib/tokenUsage.ts) | Normalize provider accounting, preserve unknown values, sum receipts without duplicates, keep cached/reasoning counts as subsets. No price/quality estimates are inferred. |

## What this change does not establish

Muse can still choose a repetitive, unhelpful or incomplete question. That is now visible as a model decision with its full output preserved; Python no longer decides creative equivalence. Offline checks verify ordering, accepted overlapping questions, persistence and protocol-error behavior. They do not validate revised live Muse question quality. No extra validator agent, inference call, new package or database is introduced.
