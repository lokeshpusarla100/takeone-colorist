# Take One
## Hackathon project plan and Codex handoff

**Version:** 1.1<br>
**Prepared:** 31 October 2026, IST  
**Implementation status updated:** 1 November 2026, IST<br>
**Project folder on Lokesh's computer:** /home/lokesh/takeone-colorist  
**Product:** Autonomous Creative Director + Precise Colorist  
**Purpose of this document:** Consolidate the project discussions into one implementation plan that a new Codex session can understand without this chat.

This is a design and delivery plan. It does not establish that the application is implemented, that the existing rubric is integrated, or that real footage has been graded successfully.

Sections 2.3 and 26 record the current implementation handoff. The original scope, schedule and historical evidence below remain design context; consult README.md for current run commands and limits. An implemented path is not evidence of real-footage visual acceptance.

---

## Contents

1. [Product and finalized direction](#1-product-and-finalized-direction)
2. [Current state and evidence](#2-current-state-and-evidence)
3. [Hackathon scope and delivery boundary](#3-hackathon-scope-and-delivery-boundary)
4. [Architecture and ownership](#4-architecture-and-ownership)
5. [Complete user journey](#5-complete-user-journey)
6. [Video evidence and technical analysis](#6-video-evidence-and-technical-analysis)
7. [Creative-Direction Rubric](#7-creative-direction-rubric)
8. [Creative Brief contract](#8-creative-brief-contract)
9. [Colorist and executable grading plan](#9-colorist-and-executable-grading-plan)
10. [Rendering, precision, and color management](#10-rendering-precision-and-color-management)
11. [SAM masks and temporal tracking](#11-sam-masks-and-temporal-tracking)
12. [Selective lighting](#12-selective-lighting)
13. [Review, repair, and stopping](#13-review-repair-and-stopping)
14. [Browser UI specification](#14-browser-ui-specification)
15. [Skills and knowledge](#15-skills-and-knowledge)
16. [Tools, orchestration, and persistence](#16-tools-orchestration-and-persistence)
17. [Model integration and efficiency](#17-model-integration-and-efficiency)
18. [Development environment](#18-development-environment)
19. [Implementation sequence](#19-implementation-sequence)
20. [Schedule and scope cuts](#20-schedule-and-scope-cuts)
21. [Validation and experiments](#21-validation-and-experiments)
22. [Failure handling](#22-failure-handling)
23. [Submission and demo](#23-submission-and-demo)
24. [Remaining choices and first Codex task](#24-remaining-choices-and-first-codex-task)
25. [Sources and earlier artifacts](#25-sources-and-earlier-artifacts)
26. [Preference memory: capture-only addition](#26-preference-memory-capture-only-addition)

---

## 1. Product and finalized direction

### 1.1 What Take One does

The user uploads a video and describes the desired appearance. Take One inspects the footage, identifies consequential gaps in creative intent, asks only useful questions, creates a structured brief, and produces a real graded video.

The system can shape global color and contrast, isolate subjects, apply localized corrections, and inspect its results before making bounded revisions. The user can inspect decisions, compare versions, adjust supported controls, and export the chosen result.

**Core proposition:** Translate an ordinary-language creative request into a controlled, reversible video treatment that follows the user's intent and preserves their explicit constraints.

This is general-purpose across subject matter: people, vehicles, products, landscapes, interiors, wildlife, personal memories, and other footage. General-purpose subject understanding does not imply unlimited codec, duration, color-format, or editing support.

### 1.2 Decisions already established

| Area | Established direction |
|---|---|
| Product identity | Take One: Creative Director + Precise Colorist |
| Visual scope | Color correction/grading, masking/tracking, and selective lighting |
| Main intelligence | Hosted Meta Muse Spark 1.3 |
| Segmentation | Hosted Meta SAM 3.1 |
| Other Meta models | Integrate later only if a demonstrated need justifies them |
| Rubric | A module within Creative Direction, not a separate autonomous agent |
| Intent dimensions | Meaning, Appearance, Attention, Boundaries, Continuity |
| Feasibility | A separate technical envelope constrains all creative choices |
| Brief outputs | Creative intent and editing constraints remain independent |
| Execution | Deterministic tools perform pixel processing; model proposes and reviews |
| Tool independence | FFmpeg first; OpenCV and OpenColorIO where justified; FilmCraft optional |
| UI | Browser editor inspired by Lightroom, with live activity, previews, controls, and history |
| Visual design | Restrained, muted palette; no neon colors or glow effects |
| Working method | Incremental end-to-end milestones; inspect actual output at each stage |
| Preference memory | Capture editing evidence and editable scoped preferences; cross-project application stays disabled |
| Project origin | New hackathon project, separate from Video Story Indexer and its benchmark repo |

### 1.3 Status language used in this plan

- **Established:** Explicit user direction or a design decision already carried through the discussion.
- **Reported working:** Supported by the user's terminal output or reported API result, not independently reproduced here.
- **Prior proposal:** Previously suggested architecture detail; not necessarily explicitly approved.
- **Recommended implementation:** A concrete choice supplied here to make the plan buildable.
- **Unverified:** Requires inspecting the local repository, running the actual workflow, or checking participant-specific rules.

Exact routes, internal file names, UI tokens, numerical limits, and schedule allocations below are recommended implementation details unless marked otherwise. They must not be presented as already implemented.

## 2. Current state and evidence

### 2.1 Original planning evidence (31 October)

| Component | Evidence available | Correct interpretation |
|---|---|---|
| Linux Mint project folder | User opened /home/lokesh/takeone-colorist in VS Code | Local project environment exists |
| Python | Terminal printed 3.12.13 | Interpreter available |
| FFmpeg | Terminal printed n9.0.2-36-g6882969ce9-20261025 | This exact development build was installed |
| Node.js / npm | Terminal printed v24.22.2 / 11.19.1 | Frontend toolchain available |
| Python dependency resolution | uv pip check: 32 packages, compatible | Declared requirements have no detected dependency conflict |
| FFmpeg filters | zscale, lut3d, curves, blend, overlay, libplacebo listed | Filters are compiled in; successful rendering and GPU execution remain unverified |
| Muse Spark request | User reported the connectivity test ready | At least one small request reportedly succeeded |
| SAM image request | User reported duck output | Image segmentation smoke test reportedly succeeded |
| SAM video tracking | No successful run recorded | Still unverified |
| First real grade | No successful end-to-end output recorded | Still unverified |
| Browser application | No implementation inspected | State unknown |
| Existing rubric artifact | DESIGN.md and earlier implementation package were created | Consultative module exists as an artifact; local integration is unknown |

The earlier rubric answer reported six offline tests passing. Its DESIGN.md explicitly says that Meta integration and real-video results had not been tested. Preserve that distinction.

### 2.2 What the next Codex session must inspect

Inspect the actual project files, Python lockfile, frontend package files, environment configuration names, installed FFmpeg path, and any copied rubric files. Determine which code exists before creating replacements.

Do not repeat the installation sequence merely because a new chat lacks context. A dependency consistency check is not evidence that every processing path or API feature works.

### 2.3 Implementation handoff (1 November)

- The React editor and local FastAPI backend now handle upload/previews, rubric questions and answers, persisted Creative Briefs, version comparison and session logs.
- An explicit global-grade generation path connects the brief to a bounded contrast/gamma/saturation recipe and original-source render. Localized grading, SAM and automatic critique remain pending. Real-footage visual acceptance is not established by this handoff.
- Current-video feedback creates a brief revision independently of cross-project memory. Applying it to pixels requires an explicit subsequent generation action.
- Capture-only preference memory is implemented as described in Section 26: empty initial collection, separate feedback/interpretation layers, owner/profile isolation, atomic JSON, deduplication, edit/delete controls and replay-safe tombstones.
- Capture defaults on; application defaults off and is unavailable through the normal API. Prepared future application requires deployment capability, profile enablement and explicit opt-in for a new project. No iteration count enables it.
- The recorded feature verification passed 62 Python tests, 15 frontend tests, 12 offline browser checks and the frontend build. No billable model calls were made for memory verification. These checks do not establish live personalization quality or autonomous grading quality.

Working code and future interfaces are distinguished in docs/PREFERENCE_MEMORY.md. Manual adjustment/lock evidence has a capture contract; actual manual controls/execution remain a future integration point. Existing project artifacts were preserved during the feature's startup checks.

## 3. Hackathon scope and delivery boundary

### 3.1 First supported media envelope

**Recommended first delivery:** One approximately 5–8-second, up-to-1080p SDR clip, one continuous shot, with a clear main subject. This is a deliberate engineering boundary for the first working pipeline.

Support one local project and one active render at a time. Begin with global grading. Add one tracked regional correction, then expand toward up to three independently controlled regions only if performance and quality permit.

Preserve orientation, geometry, duration, and source timing relationships. Preserve the original audio in the export where practical; audio understanding and editing are outside this phase.

If footage is HDR, log-encoded, has uncertain color metadata, or exceeds the supported envelope, identify the issue before applying a normal SDR recipe. Offer a documented conversion only when that path has been implemented and validated.

### 3.2 First complete hackathon experience

The intended submission demonstrates:

1. Upload and real source inspection.
2. A scene-dependent rubric interaction.
3. An inspectable creative brief.
4. A real global grade.
5. At least one stable tracked local adjustment using SAM, if video tracking passes the acceptance gate.
6. A source-aware review and a justified corrective revision when needed.
7. Synchronized before/after comparison, version history, and export.

An executable global pipeline is the first milestone. It alone does not demonstrate the full masking and lighting ambition.

### 3.3 Excluded from this phase

Cutting, montage assembly, transitions, captions, motion graphics, speed changes, music selection, speech editing, image/video generation, object insertion/removal, face reconstruction, training/fine-tuning, and old Rust-engine integration are outside scope.

Multi-shot matching remains a rubric and data-model requirement. A production multi-shot workflow is an expansion after the single-shot pipeline works, not a dependency of the first demo.

## 4. Architecture and ownership

### 4.1 Logical architecture

~~~mermaid
flowchart TD
  UI["Browser editor"] --> ORCH["Take One orchestrator"]
  ORCH --> EVID["Video evidence and capabilities"]
  EVID --> DIR["Creative Director and rubric"]
  DIR --> BRIEF["Versioned Creative Brief"]
  BRIEF --> PLAN["Colorist: validated edit plan"]
  PLAN --> TOOLS["SAM and deterministic editing tools"]
  TOOLS --> PREVIEW["Rendered preview and measurements"]
  PREVIEW --> REVIEW["Critic: compare against brief"]
  REVIEW -->|"Targeted repair"| PLAN
  REVIEW -->|"Accept best candidate"| EXPORT["Final render and export"]
  ORCH <--> STORE["Artifacts, history, events, usage"]
  STORE --> UI
~~~

This is a logical responsibility map. It does not require separate services or continuously running agents for every box.

### 4.2 Model and component responsibilities

| Component | Owns | Must not be treated as authority for |
|---|---|---|
| Muse visual pass | Scene descriptions, candidate subjects, visual relationships, uncertainty | Exact signal values, user preferences, guaranteed mask quality |
| Rubric controller | Evidence/capability validation, question budget, deduplication, session state | Knowing that a listed operation will look convincing |
| User | Intent, permissions, important preservation requirements, final preference | Technical frame measurements or mask debugging |
| Muse colorist call | Interpret brief; propose supported operations and expected outcomes | Executing arbitrary shell commands or inventing unsupported operations |
| SAM adapter | Segmentation results, returned object/track records | Creative direction or successful compositing by itself |
| Editing engine | Reproducible transforms, compositing, media handling, render artifacts | Inferring the user's intended story |
| Technical QA | Measured changes and suspected defects | A universal numerical score for cinematic quality |
| Muse critic call | Brief compliance and source-aware visual diagnosis | Rewriting the brief to justify its preferred result |
| Orchestrator | State transitions, budgets, cancellation, persistence, best-version selection | Claiming success without a usable artifact and review evidence |

Director, colorist, and critic are separate prompts/calls with distinct contracts, using the same Muse Spark model initially. They do not imply three independent models, independent human judgments, or a large multi-agent framework.

### 4.3 Application shape

**Recommended implementation:** One React frontend, one Python/FastAPI backend, one bounded local job worker, and a filesystem artifact store. Long renders and heavy image operations run outside request handling. No distributed queue, vector database, or microservice deployment is required for the hackathon.

Keep model adapters, rubric logic, typed data contracts, and editing tools behind clear interfaces. A general model router can wait; two explicit adapters are sufficient.

## 5. Complete user journey

| Stage | User experience | System result |
|---|---|---|
| Create project | Upload video; optionally describe desired result and add a reference | Source asset, request, project ID |
| Inspect | See source preview and real processing status | Metadata, evidence, capability envelope |
| Clarify | Answer one relevant question, write freely, skip, or delegate | Answer records and resolved preference slots |
| Review direction | See a concise goal/constraint summary; optionally edit it | Versioned brief; explicit preferences distinguished from defaults |
| First preview | See actual rendered footage and compact operation summaries | Candidate V1, recipe, measurements |
| Refine | Watch review status; compare revisions; give feedback if desired | Targeted candidate revisions |
| Take control | Adjust supported controls or lock a choice | New plan version with manual provenance |
| Choose | Compare source and candidates at the same timestamp | Selected version |
| Export | Download the final video and project report | Full render, brief, recipe, history, metrics |

The direction summary should not introduce another compulsory questionnaire. Existing answers and delegated defaults can authorize a first pass. Ask again only if a new consequential conflict or permission remains unresolved.

## 6. Video evidence and technical analysis

### 6.1 Ingestion contract

Record the source file checksum, original filename, byte size, container, streams, dimensions, rotation, sample aspect ratio, duration, frame-rate information, timestamps/time base, pixel format, bit depth, color primaries, transfer characteristics, matrix coefficients, range, and audio presence where available.

Missing or inconsistent tags remain unknown. A smartphone recording must not be assumed to be SDR Rec.709 just because it looks plausible in a browser.

Generate:

- A browser-compatible source preview when needed.
- A silent analysis proxy for visual model input.
- Representative frames from the beginning, middle, end, and meaningful lighting/motion changes.
- Lightweight signal measurements and optional scope images.
- An explicit mapping between proxy time/frame coordinates and source time/frame coordinates.

Avoid extracting every full-resolution frame to disk by default. Stream full-quality frames for processing and retain selected evidence.

### 6.2 Evidence representation

Every observation needs an ID, scope, source, statement, and uncertainty. Examples of source types are media metadata, numerical measurement, Muse visual observation, and explicit user instruction.

Keep measured facts separate from interpretations. “The displayed shadow region is dark” differs from “the user wants that region dark.” A model's “this looks clipped” is a hypothesis until technical inspection supports it.

### 6.3 Capability manifest

For each operation record its identifier, implementation version, allowed input domain, parameter bounds, required artifacts, and verification status.

Distinguish:

- Implemented and exercised on a representative input.
- Implemented but not yet exercised.
- Temporarily unavailable for this clip.
- Unsupported.

The rubric must use the capabilities actually available for the current clip. An installed package or compiled filter does not enable a product feature automatically.

## 7. Creative-Direction Rubric

### 7.1 Position

The rubric lives within Creative Direction, after evidence collection and before the executable edit plan. It consumes cached observations and technical capabilities rather than uploading the complete video again after each answer.

Its job is to identify what the video cannot establish about the user's desired outcome, ask when the answer matters, and preserve the resulting intent in a brief.

### 7.2 Five creative dimensions and one technical envelope

| Dimension | Establishes | What may need asking | Autonomous responsibility |
|---|---|---|---|
| Meaning | Purpose, emotional effect, narrative relationship | What should this clip communicate when that changes the treatment? | Propose a plausible visual interpretation without inventing story context |
| Appearance | Tonal/chromatic character and degree of departure | Which feasible direction or strength fits the request? | Choose interacting exposure, contrast, palette, and saturation operations |
| Attention | Subject and region hierarchy | Which element matters when prominence is ambiguous? | Use controlled separation and local shaping |
| Boundaries | Protected identity, colors, materials, details, scene meaning | What important content may not change? | Apply conservative defaults and reject unauthorized transformations |
| Continuity | Within-shot stability and relationships across shots | Is a visible difference intentional or should it be matched? | Preserve motivated changes; prevent introduced instability |
| Technical envelope | Source limitations, verified tools, delivery requirements | Ask only consequential missing technical information | Measure, choose a valid pipeline, disclose unsupported requests |

These are coverage categories, not six required questions.

### 7.3 Four types of uncertainty

| Missing information | Correct response |
|---|---|
| Observation | Inspect more frames, crops, metadata, or measurements |
| Preference | Ask if competing answers change a feasible edit |
| Execution | Trial the operation, inspect it, simplify, or disable it |
| Knowledge | Load narrow guidance or retrieve a trustworthy source |

Users should not be asked to solve a tracking failure or guess whether the image is genuinely clipped.

### 7.4 Candidate question contract

Muse proposes structured candidate records containing:

- Candidate ID and unresolved preference slot.
- Rubric dimension.
- Evidence IDs and subject/time scope.
- One ordinary-language question with distinct options.
- The editing consequence of each option.
- Required operation IDs for each alternative.
- Ordinal preservation consequence, visual impact, and answerability.
- Interaction form: text choice or a real rendered comparison.

Schema validation is necessary but insufficient. Semantic validation must also reject invalid evidence references, unsupported operations, duplicate slots, conflicting permissions, and options whose outcomes are effectively identical.

### 7.5 Selection policy

1. Extract preferences and constraints already supplied.
2. Identify consequential unresolved forks.
3. Remove forks resolved by a conservative default or previous answer.
4. Reject questions promising unavailable operations.
5. Prioritize preservation consequences, then visual impact and answerability.
6. Ask one question.
7. Record the response and refresh candidates.
8. Stop when no meaningful eligible fork remains or the budget is exhausted.

The ranking is an understandable heuristic. It is not a calibrated value-of-information estimator.

**Actionability rule:** The controller should receive an explanation of how answer A and answer B would lead to meaningfully different, feasible treatments. If no difference can be stated, do not ask.

### 7.6 Interaction rules

Normally ask **0–3 consequential questions**, with a hard consultation budget of **five decisions**. Preview choices and material technical clarifications count toward this budget.

Each question supports selectable options, free text/Other, Skip, and You decide. Skip does not authorize an unrestricted transformation. Delegation uses visible defaults within existing boundaries.

Free-text answers require interpretation and provenance. If interpretation changes a consequential permission, reflect that interpretation back or retain the stricter boundary. Do not force free text into an arbitrary option.

A missing hard permission after the budget ends blocks that operation; it does not justify ignoring the constraint. Continue with a feasible conservative treatment where possible.

### 7.7 Preview policy

When language is ambiguous, two comparable previews on the actual uploaded footage may be more useful than another verbal question. Include neither/source/delegation where relevant.

Previews must use the real rendering engine. Generated reference imagery can promise geometry or lighting that the renderer cannot reproduce; image generation is outside initial scope.

### 7.8 Existing rubric limitations to address

The earlier module carries typed outcomes, evidence, limitations, and protections, but its own design excludes robust free-text interpretation, persistence, preview generation, advanced conflict resolution, and enforcement inside the downstream renderer.

Treat it as an integration starting point. Inspect its actual implementation before assuming those missing features exist.

### 7.9 Scenario coverage checks

These are illustrative routing checks derived from the rubric research, not a fixed questionnaire. Ask only when the initial request leaves the listed issue consequentially unresolved.

| Footage | High-value preference fork | Autonomous work and protected risk |
|---|---|---|
| Rainy motorcycle at night | Vehicle readability versus silhouette; paint preservation | Protect wet highlights and motion consistency; avoid changing paint to fit a preset |
| Indoor portrait | Keep expressive facial shadow or soften it; neutral or warmer rendering | Preserve complexion and texture; avoid assumed beautification |
| Sunset landscape | Keep foreground silhouette or reveal detail; restrained or stronger sky | Protect gradients and the intended sky/land relationship |
| Daytime automotive ad | Clean/bright presentation or sculpted contrast; approved paint reference | Preserve materials, logos, and reflection continuity |
| Wedding/personal memory | Faithful memory or stylized retelling; personally important colors | Preserve clothing and meaningful venue lighting |
| Dark noisy phone clip | Which detail must become readable; brighter/noisier or darker/cleaner | Work within captured signal; avoid promising missing detail recovery |
| Color-critical product | What reference defines acceptable color; may surroundings change? | Preserve the product relative to the stated authority; avoid physical-accuracy claims without calibration |
| Forest/wildlife | Documentary fidelity or expressive atmosphere; what subject matters? | Preserve meaningful markings and natural light relationships |
| Travel montage | One unified visual world or distinct times/places? | Match accidental differences without erasing motivated variation; full multi-shot execution is deferred |
| Experimental/music video | Which unconventional changes are intentional and what must remain legible? | Avoid normalizing away an authorized cast or discontinuity |

## 8. Creative Brief contract

### 8.1 Two independent outputs

**Creative intent** specifies the desired result. **Editing constraints** specify what must be preserved or prohibited. A visually appealing result can fail if it violates the constraints.

| Brief component | Required contents |
|---|---|
| Identity/version | Brief ID, project ID, schema version, revision, timestamp |
| Original request | User's exact wording |
| Creative intent | Meaning, appearance, attention hierarchy, continuity direction |
| Editing constraints | Protected subjects/colors/details; prohibited or conditional transformations |
| Scope | Clip, shot, subject, region, time range |
| Permissions | Explicitly allowed, delegated, conditional, prohibited |
| Priorities | Which goals prevail when they conflict |
| Technical envelope | Source interpretation, delivery target, supported operations, limitations |
| Provenance | User answer, user-approved preview, supplied reference, observation, or default |
| Uncertainty | Unresolved interpretation, consequence, conservative fallback |
| Review criteria | Observable evidence needed to judge each directive |

Each consequential directive gets an ID so operations and review findings can reference it.

### 8.2 Authority and change control

Explicit user constraints outrank stylistic suggestions and model defaults. A later explicit user change can revise the brief, with provenance and a new version.

The critic cannot loosen a protected constraint or invent a new creative target. If an authorized objective cannot be achieved, it reports a limitation and proposes a feasible alternative.

### 8.3 Example: rainy motorcycle

| Item | Brief entry |
|---|---|
| Intent | Restrained, dramatic nighttime presentation |
| Attention | Motorcycle should remain the first readable subject |
| Appearance | Dark surroundings with controlled reflections |
| Constraint | Preserve recognizable paint appearance relative to the approved source rendering |
| Permission | Global grading and stable regional exposure/color separation |
| Prohibition | Recoloring paint, inventing new lamps/reflections, reconstructing unseen detail |
| Priority | Recognizability and paint fidelity outrank stronger styling |
| Review | Inspect readability through motion, paint drift, wet highlights, seams, and pulsing |
| Fallback | Reduce environmental palette changes or local correction if isolation fails |

“Readable motorcycle” is the objective. The colorist decides whether a specific exposure or contrast adjustment achieves it.

## 9. Colorist and executable grading plan

### 9.1 Responsibilities

The colorist receives the brief, source evidence, capability manifest, available masks, existing candidate history, and any manual locks. It proposes a versioned plan using only supported operations.

| Plan field | Meaning |
|---|---|
| Parent/version | Which plan is being revised |
| Brief version | Exact creative contract being fulfilled |
| Source ID | Original media asset |
| Operation ID/type | Allowlisted operation |
| Scope | Time range and global/subject/region applicability |
| Parameters | Typed values, units, and allowed ranges |
| Color domain | Required input interpretation and processing space |
| Dependencies | Mask/track IDs, LUT/config IDs, upstream operations |
| Rationale | Short expected visible effect tied to a brief directive |
| Checks | What should change and what must remain protected |

### 9.2 Initial operation set

Recommended first operations are exposure, RGB balance, a restrained tonal curve/contrast control, and saturation. Add local equivalents after the mask pipeline works.

LUT support is optional until input/output assumptions are explicit. Avoid a “cinematic LUT” standing in for understanding the brief.

An exposure value in EV must correspond to a declared exposure operation; RGB gains are dimensionless; a tone curve needs a stated domain. Do not label an arbitrary image brightness control as physically meaningful lighting.

### 9.3 Plan validation

Reject unknown operation types, invalid numerical values, missing masks, unsupported color domains, invalid time ranges, conflicting locks, and references to nonexistent evidence.

Validate preservation constraints separately from schema validity. Where a constraint cannot be mechanically proven, retain a visual/human check and an explicit unknown status.

The model returns structured operation proposals. The application constructs commands from allowlisted parameters. Model-generated shell command strings are not the editing contract.

## 10. Rendering, precision, and color management

### 10.1 Processing contract

Record the source interpretation, working representation, operation order, and output transform. Make the same choices for comparable previews and final output.

Recommended order:

1. Decode with known timing, orientation, color range, and bit-depth interpretation.
2. Convert into the declared working representation.
3. Apply primary/global treatment.
4. Apply mask-based regional treatment in its declared domain.
5. Apply the chosen output transform and encoding.
6. Verify the resulting media and color metadata.

The exact placement of a particular operation must follow its domain contract. Exposure multiplication belongs in an appropriate linear-light representation; other creative curves may be defined in an encoded or log-like domain.

### 10.2 Precision policy

Use a validated higher-precision path for grading and compositing; do not silently reduce a 10-bit source to an 8-bit OpenCV array before making the final grade.

Small 8-bit analysis thumbnails are acceptable when explicitly tagged as analysis proxies. They are not the source of the final render.

The capability check must inspect actual negotiated pixel formats through the processing chain. FFmpeg listing a filter does not prove that an arbitrary chain preserves precision.

### 10.3 Practical backend choice

FFmpeg/FFprobe handle media inspection, decode/encode, timing, and supported filter operations. OpenCV/NumPy handle measurements, masks, selected image operations, and diagnostics. OpenColorIO can provide explicit transforms where the pipeline needs them.

Choose one verified rendering path for v1. Do not build several equivalent backends before obtaining one good result.

ACES is not an automatic improvement for every input. Do not assume that an already display-rendered phone video has recoverable scene-linear camera data. A camera IDT or physical color-accuracy claim requires evidence.

The installed libplacebo filter is optional. GPU acceleration and Vulkan availability have not been demonstrated and must not be on the critical path.

### 10.4 Reproducibility

Every candidate is rendered from the original source or an explicitly defined high-quality working source, using its complete recipe. Never stack a new grade on a previous compressed preview.

Retain tool versions, adapter versions, transforms/configuration hashes, recipe version, source hash, mask version, and encoding settings. The goal is repeatable execution within the recorded environment; cross-platform bit-identical encoding is not assumed.

## 11. SAM masks and temporal tracking

### 11.1 Actual integration boundary

Meta's documented hosted SAM workflow supports image requests and uploaded MP4 segmentation with streamed frame/track records and an official parser. A successful image request does not validate video tracking in Take One. [S2]

The first video test must establish:

1. Upload and response parsing.
2. Correct object/track association.
3. Complete time/frame coverage or explicit gaps.
4. Correct mask rasterization, dimensions, orientation, and source mapping.
5. A playable overlay showing tracking quality.

### 11.2 Selective use

Call SAM only when an intended operation needs isolation. Global grading does not require segmentation.

A concept may match multiple objects. Use returned track IDs and visual evidence to select the intended instance. Ask for a subject selection only when the creative target remains ambiguous.

### 11.3 Mask artifact contract

Store source/proxy identity, concept, selected instance, track ID, timestamps, frame mapping, dimensions, raw result, mask artifacts, visibility ranges, refinement settings, and derived QA flags.

Do not invent a confidence value if the API does not supply one. Derived stability measurements must be labeled as application diagnostics.

Refinement may include restrained feathering, hole treatment, and edge handling. Preserve the raw mask for diagnosis. Geometric alignment must be established before edge smoothing.

### 11.4 Failure behavior

Inspect movement, occlusion, exits/re-entry, reflections, fine detail, and fast boundaries. Do not blur masks across cuts or blindly smooth an actual occlusion into a ghost.

If local treatment exposes an unstable mask, reduce its strength, simplify its scope, or omit it. Preserve the original artifact and the reason for the fallback.

Exact transparent edges, hair, glass, and physically correct reflections are not guaranteed by semantic segmentation.

## 12. Selective lighting

The initial feature means shaping the appearance of already captured illumination: local exposure, balance, contrast, and soft falloff within a tracked region.

It does not establish physically accurate 3D relighting, new shadows, unseen surface recovery, or reconstruction of missing detail.

The planning sequence is:

1. Tie the light-shaping objective to Attention or Appearance.
2. Check boundaries and source latitude.
3. Obtain a stable region/window.
4. Apply restrained local changes with plausible transitions.
5. Inspect in motion for halos, spill, color/material distortion, and pulsing.
6. Reduce or remove the treatment when it harms the subject.

Start with one clear subject/background separation. Add more regions only after the first path is stable.

## 13. Review, repair, and stopping

### 13.1 Evidence supplied to the critic

Supply the frozen brief version, original/rendered clips or synchronized representative motion evidence, operations, source-aware measurements, protected-region crops, and the previous best candidate.

A contact sheet alone cannot establish temporal stability. Full video or sufficiently dense motion inspection is required for a temporal claim; sparse review must be disclosed.

### 13.2 Two review outputs

| Review type | Checks |
|---|---|
| Technical | Decode/export validity, timing, clipping changes, banding/noise symptoms, mask alignment/leakage, flicker, metadata |
| Intent and constraints | Desired appearance, hierarchy, protected colors/details, continuity, unauthorized departures |

Measurements identify symptoms; they do not determine whether an expressive shadow or lighting change is artistically wrong.

### 13.3 Repair finding contract

Each finding identifies its brief directive, timestamp/region, evidence, severity, proposed cause, allowed correction, and uncertainty.

Good finding: “During the turn the motorcycle becomes less readable than in the approved direction; reduce the local darkening or revise the mask.”

Insufficient finding: “Make this more cinematic.”

Corrections change the smallest relevant part of the plan and preserve manual locks.

### 13.4 Iteration budget

The previous discussion consistently called for a bounded loop, but used “passes” and “revisions” inconsistently.

**Recommended explicit setting for implementation:** One initial candidate and at most three corrective candidate renders. Target zero or one repair on an ordinary short clip. Final export is not another creative iteration.

Stop earlier when:

- The brief is satisfied sufficiently for the current evidence.
- No supported correction is identified.
- A proposed change regresses a protected requirement.
- Cost or elapsed-time budget is reached.
- Repeated repair makes no supported improvement.
- The user cancels or selects a version.

### 13.5 Best-version policy

Keep the best acceptable candidate rather than automatically selecting the newest. Compare hard constraints first, then intent, then technical defects. Do not collapse these into one unvalidated beauty score.

A separate call to the same model is not an independent aesthetic judge. Human acceptance remains necessary for claims of creative success.

### 13.6 Final QA

Render the selected recipe at the chosen final settings, then verify the actual final artifact. Preview approval does not guarantee that a different codec, resolution, or output transform is correct.

Use pass/fail/unknown outcomes. A known failed protected constraint must not be reported as a successful compliant result.

## 14. Browser UI specification

### 14.1 Visual direction

Use a calm editing workspace: neutral video surroundings, readable typography, restrained accents, and clear state changes.

**Suggested tokens, not previously finalized:** graphite #252729, warm surface #343638, cream text #ECE8DF, muted sage #A8B6A0, dusty blue #9DADB8, and restrained terracotta #C58D77.

Use muted colors for controls and status. Keep them away from the video image so they do not distract from grading judgments. Avoid neon gradients, glowing outlines, sci-fi styling, and dense decorative dashboards.

### 14.2 Desktop layout

| Region | Contents |
|---|---|
| Header | Project name, save/status indicator, Analyze/Generate, Cancel, Export |
| Main viewer | Source/selected version, before-after wipe or split, synchronized playback, fit/zoom, mask overlay |
| Context panel | Tabs for Direction, Controls, Activity, and Review |
| Bottom strip | Source and candidate thumbnails, version names, chosen/best markers, timestamps |

Keep the video dominant. Show detailed logs on demand. Do not make the user watch every API token to understand progress.

### 14.3 Initial upload screen

Provide upload/drop area, supported-input guidance, a single free-text request, optional reference attachment, and a clear start action. Source limitations should appear before the user spends time answering creative questions.

The interface may explain that uploaded analysis media is sent to Meta for processing. Keep the explanation concise and relevant to the user's choice.

### 14.4 Rubric interaction

Show one question at a time beside the preview. Include options, Other/free text, Skip, and You decide. A brief “why this matters” should describe the visual consequence, not internal scoring.

Display the current intent summary and protected details so the user can correct a misunderstanding. Do not expose all six rubric fields as a compulsory form.

### 14.5 Viewer behavior

Offer Original, V1, V2, and later version selection; same-timestamp comparison; synchronized before/after; optional tracked-mask overlay; and full-video playback.

Both sides of a comparison need compatible display rendering and scale. Avoid comparing a raw HDR source preview against a tone-mapped SDR output without labeling that transformation.

Do not use browser CSS filters as proof of the actual FFmpeg grade. Slider previews should come from the actual processing path, potentially first as a rendered current-frame preview.

### 14.6 Controls and manual overrides

Expose only implemented, verified controls. Initial controls can cover exposure, RGB balance or a clearly defined warmth/tint interface, contrast, saturation, and reset.

Regional controls appear only for available tracks and operations. Show the affected subject and region.

Manual changes create a new recipe version and record provenance. A locked manual choice cannot be changed silently by the next automatic repair.

Begin with debounced frame/short-preview updates. Do not promise continuous real-time full-resolution playback while dragging a slider.

### 14.7 Live activity and usage

Show stage, actual completion/progress when available, concise declared action, tool/API name, duration, warnings, API usage, and known/estimated cost.

Example activity: “Inspecting source metadata”; “Asked about motorcycle readability”; “Tracking selected subject”; “Rendering V1”; “Reducing background suppression after review.”

These are structured decisions and activity summaries. The product does not promise access to hidden model reasoning.

Never invent progress percentages or count a cache hit as a new model call. Label unknown cost explicitly.

### 14.8 Version history

Each version includes its parent, brief/recipe revisions, timestamp, thumbnail, changed controls, review summary, render status, usage, and user/best selection.

Allow selection, compare, revert, and create-from-version. Retain the best output and export provenance even if expendable previews are cleaned up.

### 14.9 Responsive and failure states

For tablet/narrow widths, use one column with the viewer first and large controls below. Avoid cramming three desktop panels into small cards.

Define visible states for awaiting answer, analyzing, rendering, tracking unavailable, retryable API failure, unsupported input, cancelled, completed with limitations, and export failed.

Browser refresh should restore persisted state and current artifacts. Disconnecting the UI must not by itself terminate the worker.

## 15. Skills and knowledge

### 15.1 Four compact domain guides

| Guide | Content | Load condition |
|---|---|---|
| Color grading | Color-management boundaries, primary treatment, reference use, material preservation | Planning a grade |
| Masking | Instance selection, tracking, edge handling, alignment, failure fallbacks | Regional treatment |
| Lighting | Restrained local shaping, plausible falloff, source-latitude limits | Selective light changes |
| Quality review | Brief compliance, time-coded diagnosis, technical checks, stopping | Candidate review |

The rubric/controller policy belongs in the core creative-direction instructions. Keep specialized guidance short and procedural, not a catalogue of arbitrary cinematic recipes.

### 15.2 Development guidance versus runtime guidance

AGENTS.md can tell Codex how to work in the repository. It is not automatically sent to Muse.

Runtime knowledge must be explicitly loaded by the backend into the relevant model request, with its version recorded. A local SKILL.md file has no effect on Meta inference unless the application actually supplies its relevant content.

### 15.3 Retrieval and web research

No vector database or generic RAG system is required for v1. Retrieve project-specific references, approved frames, or delivery documents only when useful.

Web research remains optional and outside the critical path. Use it for an unfamiliar named reference, technique, or consequential current specification. Do not browse merely because the footage contains a familiar subject.

Do not automatically convert one user's feedback into a universal grading rule. The capture-only
preference-memory addition in Section 26 records scoped evidence and editable interpretations.
It does not train weights, and cross-project application remains disabled.

## 16. Tools, orchestration, and persistence

### 16.1 Three logical tool groups

| Group | Functions |
|---|---|
| Video analysis | Metadata, proxies, frames, timing map, measurements, evidence |
| Cinematic editing | Supported transforms, SAM adapter, masks, local compositing, preview/final renders |
| Quality control | Output validation, source comparisons, diagnostics, review artifacts |

These groups were proposed as Tool/MCP services. Implement ordinary typed local functions first. Add an MCP wrapper only when a real external consumer or integration requires it; wrappers do not improve image quality by themselves.

### 16.2 Orchestrator state

Recommended states are Uploaded, Analyzing, Awaiting intent, Brief ready, Planning, Segmenting, Rendering preview, Reviewing, Revising, Ready to export, Exporting, Completed, Failed, and Cancelled.

Record state and artifacts before announcing a stage complete. A retry continues the failed operation using existing valid artifacts where possible.

Use one active worker per project initially. Guard against stale answers or manual changes applying to an older brief/recipe. Cancellation must stop local subprocess work where supported and identify any remote request that may already have completed or incurred cost.

### 16.3 Persistence

For v1, versioned JSON artifacts plus an append-only event log are sufficient. Use atomic writes and stable IDs. Keep large media separate from model-facing compact metadata.

| Artifact | Purpose |
|---|---|
| Project manifest | Source identity, current state, selected/best versions |
| Source metadata | Original media interpretation |
| Evidence records | Semantic observations and measured facts |
| Rubric session | Questions, rejections, exact answers, skips/delegation |
| Creative Brief | Versioned intent and constraints |
| Edit plans | Full recipes and parent relationships |
| Mask/track manifests | SAM results, source mapping, refinements |
| Candidate artifacts | Previews, thumbnails, comparisons |
| Review reports | Findings and dispositions |
| Event/usage records | Stage, call/tool identity, timing, tokens, cost status |
| Project preference outbox | Durable, idempotent handoff of user evidence to the separate preference store |
| Owner/profile preference JSON | Feedback ledger, editable scoped entries, settings and deletion tombstones; separate from project media |
| Final export | Selected rendered video and accompanying metadata |

### 16.4 Cache

Key cache entries by source hash, relevant proxy/time mapping, model/prompt version, operation parameters, mask/concept selection, tool versions, and color configuration.

Reuse observations across questions, tracks across unchanged masks, and measurements across unchanged assets. Uploaded-file reuse avoids repeated uploads where supported; it does not prove inference is free or internally cached.

Invalidate artifacts when their dependencies change. Retain source, selected output, recipes, masks needed for reproduction, and review evidence; clean disposable previews deliberately.

### 16.5 Backend interaction surface

Recommended routes support project creation/upload, analysis start, state retrieval, event stream, current question retrieval, answer submission, brief retrieval/update, candidate generation, manual plan revision, candidate selection, export, and cancellation.

Use SSE for one-way activity updates; use ordinary requests for answers and commands. WebSockets are unnecessary unless an actual bidirectional interaction needs them.

Persist event IDs so the UI can reconnect and catch up. Show real render progress only when the subprocess supplies a usable duration/time basis.

### 16.6 Conceptual repository organization

| Location | Responsibility |
|---|---|
| docs/ | This plan, decisions, technical contracts, known limitations |
| takeone/creative_direction/ | Rubric controller, question/answer state, brief generation |
| takeone/models/ | Muse and SAM adapters |
| takeone/analysis/ | Metadata, proxies, frames, measurements |
| takeone/grading/ | Typed recipes, validation, supported operations |
| takeone/masks/ | Track artifacts, mapping, refinement |
| takeone/rendering/ | Concrete rendering adapter and subprocess management |
| takeone/review/ | Diagnostics, critic requests, candidate comparison |
| takeone/orchestration/ | Jobs, states, budgets, cancellation |
| takeone/api/ | HTTP and event interface |
| knowledge/ | Runtime domain guides and prompt versions |
| frontend/ | Browser editor |
| tests/ | Focused contract/processing checks |
| workspace/ | Ignored per-project media and artifacts |

This organization is a proposal. Adapt to the existing repository rather than reorganizing working code solely to match these names.

## 17. Model integration and efficiency

### 17.1 Initial configuration

| Setting | Agreed value |
|---|---|
| API base | https://api.meta.ai/v1 |
| Main model | muse-spark-1.3 |
| Segmentation model | sam-3.1 |
| Credential name used in setup | MODEL_API_KEY |
| SDK | OpenAI-compatible Python client, pointed explicitly at Meta |

The SDK name does not select OpenAI inference. Explicit endpoint and model configuration determine the destination used by this integration.

Keep credentials in backend configuration. Frontend responses, logs, downloadable reports, and tracked files must not contain the key.

### 17.2 API contracts

Meta documents video upload/understanding and schema-constrained output. Confirm the precise request shape for the chosen endpoint and installed SDK; do not mix Chat Completions response_format with Responses parameters by assumption. [S1, S3]

SAM has its own streamed output grammar; use the official parser rather than treating segmentation output as an ordinary JSON grading plan. [S2]

Strip audio from visual analysis proxies. Audio is not needed for this scope, and Meta's current guide warns that Muse Spark 1.3 audio understanding is not fully supported. [S3]

### 17.3 Call discipline

Use one initial semantic observation pass when sufficient. Subsequent rubric calls use compact evidence and current intent. The colorist receives the brief and relevant guides. Review calls receive the actual comparison evidence.

Do not call SAM for every region before the brief establishes that it is useful. Reuse media uploads and valid artifacts where supported.

Call count varies with questions, retries, masks, and revisions. Do not advertise a fixed per-video cost before measuring it.

### 17.4 Usage and budget

Record model, request purpose, input/output usage when returned, wall time, success/error, retry count, and cost attribution status.

An earlier target was approximately US$7 for baseline experiments; treat this as a planning target, not a measured budget guarantee. Verify current pricing and available credit balance before fixing run caps. The US$150 credit application does not itself prove funds are available.

Set explicit per-run call/revision/time limits and a user-configured spending ceiling where usage can be estimated reliably. Unknown SAM or media charges remain unknown until documented or observed.

Retry transient failures with a small bounded policy. Do not retry a validation error or a billable model call indefinitely.

## 18. Development environment

### 18.1 Reported installed toolchain

| Tool | Reported version/location |
|---|---|
| Python | 3.12.13 |
| Virtual environment | /home/lokesh/takeone-colorist/.venv |
| FFmpeg | n9.0.2-36-g6882969ce9-20261025 |
| FFmpeg installation | ~/.local/opt/ffmpeg-9.0.2, linked through ~/.local/bin |
| Node.js | 24.22.2 via nvm |
| npm | 11.19.1 |
| Frontend dependencies | Project-local frontend/node_modules |

Record the exact detected FFmpeg build. Do not relabel this development build as an untouched stable release or upgrade it because a new version exists.

### 18.2 Pins requested by the earlier setup script

These are historical requested pins. The local requirements-lock.txt and frontend package-lock.json are authoritative for what actually installed. They have not been inspected in this planning session.

| Python dependency | Script pin | Role |
|---|---|---|
| openai | 3.41.0 | Meta-compatible API client |
| meta-sam-parser | 0.1.0 | SAM stream parsing |
| numpy | 2.5.4 | Numerical image/mask operations |
| opencv-python-headless | 4.14.0.94 | Measurements and image/mask processing |
| opencolorio | 2.6.0 | Explicit color transforms where required |
| scipy | 1.18.1 | Optional numerical/mask utilities |
| pydantic | 2.14.2 | Data contracts |
| python-dotenv | 1.2.4 | Local configuration |
| fastapi | 0.162.0 | Backend API |
| uvicorn | 0.54.0 | ASGI server |
| python-multipart | 0.0.32 | Upload parsing |
| pytest | 9.2.0 | Focused verification |

| Frontend dependency | Script pin |
|---|---|
| react / react-dom | 19.3.1 |
| typescript | 7.0.2 |
| vite | 8.3.5 |
| @vitejs/plugin-react | 6.1.2 |
| tailwindcss / @tailwindcss/vite | 4.3.3 |
| @types/react / @types/react-dom | 19.3.0 |

This table is not a fresh recommendation to install unverified versions. Preserve a functioning environment and change a dependency only for an observed compatibility or feature requirement.

### 18.3 Compute and storage

Hosted models avoid local model-weight downloads. CPU rendering is the initial target; GPU acceleration is optional.

Approximately 20 GB free was the earlier planning allowance for short-clip development, not a measured requirement. Mask sequences, working frames, and multiple renders dominate storage. Inspect actual disk usage and bound caches.

No PyTorch/CUDA, local Muse weights, Muse Code, LangChain/LangGraph, vector store, or separate MCP servers are required for the initial path.

## 19. Implementation sequence

### Milestone 0 — Inspect and preserve the actual project

Inspect source files, lockfiles, configuration, existing rubric artifacts, and current startup commands. Record known-good dependencies. Create a clean version-control checkpoint if the project is already initialized.

**Exit:** A short factual inventory identifies existing code, missing parts, and the first supported media/operation contract.

### Milestone 1 — Real global grading path

Build ingestion, metadata inspection, evidence sampling, a Muse observation call, minimal rubric integration, Creative Brief output, an allowlisted global recipe, and an actual rendered preview.

A minimal browser shell must upload the clip and play source/output. Full editor polish waits.

**Exit:** One real short SDR clip goes from request to playable graded video with saved brief, recipe, and activity records. No mocked render.

### Milestone 2 — Complete rubric interaction

Implement one-question-at-a-time state, options/free text/skip/delegation, budget, refreshed candidates, provenance, constraint handling, and brief versioning. Connect it to the UI.

**Exit:** A sufficiently explicit request can receive zero questions; an ambiguous request receives relevant questions; unsupported operations are not promised.

### Milestone 3 — SAM video and one local treatment

First exercise the official video example, then the actual sample clip. Create a tracked overlay, source/frame mapping, and one restrained regional adjustment.

**Exit:** The selected moving subject is treated consistently across the entire clip, or the system visibly disables the failed local operation and preserves a usable global candidate.

### Milestone 4 — Review and bounded correction

Add measurements, critic comparison, time-coded findings, targeted recipe revision, stopping/budgets, and best-version retention.

**Exit:** Demonstrate a real diagnosed defect and a correction, or an evidence-based decision to stop when no correction is justified. Do not manufacture an unnecessary repair for the demo.

### Milestone 5 — Editor controls and persistence

Complete comparison, history, manual controls/locks, reconnectable activity, cancellation, final export, and run report. Make the muted UI coherent.

**Exit:** Refreshing the page preserves project history; manual edits remain traceable; exported media corresponds to the selected recipe.

### Milestone 6 — Evaluation, packaging, and demo

Exercise diverse clips, record limitations and actual costs, prepare the repository/README, and record a concise demonstration.

**Exit:** A reproducible demo with real output, explicit model roles, measured runtime/usage, and honest limitations.

## 20. Schedule and scope cuts

### 20.1 Deadline

The public event page lists submission at 9:00 PM Eastern on 4 November 2026. With Eastern standard time on that date, this is **5 November 2026, 7:30 AM IST**. Earlier 6:30 AM references in the conversation should not be used. Recheck the participant dashboard before submitting because the event page allows schedule changes. [S5]

### 20.2 Recommended remaining schedule

| Date, IST | Delivery target |
|---|---|
| 31 October | Preserve working setup; inspect repository; start first global path |
| 1 November | End-to-end real global preview and working rubric interaction |
| 2 November | SAM video mapping, overlay, and one tracked regional correction |
| 3 November | Critic/repair loop, best-version retention, history, core controls |
| 4 November | Feature freeze, small evaluation, final UI cleanup, README and demo |
| 4 November, 11:00 PM | Internal submission target with buffer |
| 5 November, 7:30 AM | Publicly listed submission deadline converted to IST |

This allocation is a recommendation, not a guarantee that all features fit the available time. Preserve an executable result at every milestone.

### 20.3 Scope cuts in order

Drop optional reference retrieval, extra region counts, complex manual curve controls, multiple render backends, and multi-shot support before sacrificing the end-to-end path.

If SAM video fails, preserve the global product and explain the missing tracked-local feature. Do not substitute a static mask and call it successful video tracking.

If autonomous review is unreliable, expose the measured findings and a manual compare/select workflow. Do not claim that iteration improves quality without evidence.

## 21. Validation and experiments

This section defines future checks. No application tests or footage evaluation were run while producing this plan.

### 21.1 Engineering acceptance checks

| Area | Meaningful checks |
|---|---|
| Source handling | Orientation, VFR/timestamp mapping, metadata interpretation, unsupported HDR behavior |
| Rubric | Zero-question path, max budget, duplicate answers, invalid evidence, unavailable operations, free-text ambiguity |
| Brief/plan | Constraint precedence, version references, invalid values, missing mask, manual locks |
| Processing | Known transform effect in its declared domain; precision path; neutral recipe sanity check |
| Masks | Known moving target, alignment/scale, occlusion gaps, multiple-instance selection |
| Review | Detect a deliberately introduced defect without inventing a new creative target |
| History | Revert uses the original source and chosen full recipe |
| Failure/recovery | API failure, cancellation, refresh, worker restart behavior, out-of-space response |
| Export | Playable output, timing, audio behavior, metadata, correspondence to selected version |

A compressed neutral round trip is not necessarily pixel-identical; its check must account for encoding. Test the actual property, not a brittle implementation detail.

### 21.2 Small hackathon comparison

Use approximately three short clips covering distinct problems: a human/interior, a moving subject such as a motorcycle, and a landscape/product/low-light case. This produces nine initial outputs across three conditions:

| Condition | Interaction |
|---|---|
| A | Same basic request, no clarification questions |
| B | Competent fixed questionnaire covering goal, appearance, subject priority, and preservation |
| C | Adaptive rubric, normally 0–3 decisions, maximum five |

Keep model, renderer, allowed operations, source interpretation, and correction allowance comparable. Preserve technical safeguards in every condition. Log the extra calls and user effort caused by each policy.

Do not secretly supply C's answers to A or B. If the same user answers multiple conditions, counterbalance order and report carryover as a limitation.

### 21.3 Human evaluation

The clip owner judges intent fidelity and unacceptable changes. A nonexpert viewer can judge clarity and perceived quality. A colorist or experienced editor, if available, examines technical defects, material/skin appearance, and temporal behavior.

Use randomized labels and full motion playback under similar viewing conditions. If no expert is available, report that limitation; a model score does not replace expert review.

Record:

- Intent match and owner preference.
- Question relevance, usefulness, and answer time.
- Constraint violations and time-coded defect severity.
- Number of corrective iterations.
- Calls, usage, latency, render time, and actual/unknown cost.

SSIM/PSNR differences do not establish aesthetic improvement in an intentionally changed image. Color-difference metrics require a defined reference/domain and are most useful for specific preservation checks.

### 21.4 Acceptance gates and claims

The minimum gate is a reproducible end-to-end output, no known critical unauthorized transformation, correct stated limitations, and an owner willing to use at least one demonstrated result.

To claim the rubric helps, show the actual owner comparisons, interaction time, and costs. Three clips provide directional evidence only. Do not claim general superiority or statistical validation.

The larger research report proposes a later 12-participant, 24-case pilot. That is a post-hackathon validation option, not a prerequisite for shipping the demo.

## 22. Failure handling

| Failure | Required behavior |
|---|---|
| Unknown source color interpretation | Retain uncertainty; inspect/clarify or decline the unsupported path |
| Desired detail absent from source | Explain the limit; avoid invented reconstruction claims |
| Several matching subjects | Use track/evidence selection; ask only if the target is still ambiguous |
| Unstable mask | Reduce/simplify/omit local correction; retain usable global version |
| Introduced flicker | Compare with source lighting changes; repair processing instability |
| Product/skin drift | Reassess protected regions and treatment; reduce changes if uncertain |
| Conflicting preferences | Resolve scope/priority; ask only the consequential conflict |
| Malformed or semantically invalid proposal | Reject with a bounded correction request; never execute it blindly |
| API restriction/rate failure | Preserve state; bounded retry for transient failures; show a useful error |
| Budget exhausted | Stop further model work; preserve the best usable artifacts |
| Render error | Keep source/recipes; report failure and retry the failed stage |
| Disk pressure | Stop before corrupting artifacts; clean only disposable intermediates |
| Model critique prefers drama over brief | Reject target drift and compare against the frozen brief |
| No candidate satisfies a hard constraint | Report failed/unknown compliance; do not label a candidate fully approved |
| UI disconnect | Worker continues where possible; reconnect via persisted state/events |

## 23. Submission and demo

### 23.1 Positioning

Demonstrate an intent-aware colorist with real output: scene evidence informs useful questions; questions create a brief; the renderer follows that brief; review tests the actual result.

Do not pitch grading, segmentation, or LLM self-review as individually novel inventions. A stronger submission shows that their combination follows user direction better and exposes useful control.

Do not assume that calling the project “Push the Frontier” establishes that track fit. The public page's frontier theme emphasizes new capabilities and understanding/control of real systems. The safer domain-product framing is Build for Your World unless the demonstrated result supports a stronger claim. [S5]

### 23.2 Proposed three-minute demo

| Time | Demonstration |
|---|---|
| 0:00–0:20 | Problem, source clip, short creative request |
| 0:20–0:45 | Relevant adaptive question and protected detail |
| 0:45–1:05 | Creative Brief and expected treatment |
| 1:05–1:45 | Actual global/local preview and moving mask |
| 1:45–2:15 | Review evidence, justified correction, version comparison |
| 2:15–2:40 | Manual control/lock and selected export |
| 2:40–3:00 | Meta roles, measured usage/runtime, limitations |

Long operations may be edited for the demo, but disclose elapsed time and distinguish recorded output from live processing.

### 23.3 Submission package

Prepare a runnable repository with setup/start instructions, environment variable names, pinned dependencies, sample instructions, architecture explanation, model roles, known limitations, actual result media, and measured run data.

A public repo and approximately three-minute demo were earlier submission targets. Verify the exact artifact, licensing, and eligibility requirements in the accepted-participant dashboard before publishing/submitting; this planning review did not establish every participant-specific condition.

Keep the new project separate from the pre-existing Rust engine. Attribute borrowed libraries, examples, research, and any reused material accurately.

### 23.4 Per-project downloadable artifacts

Intended outputs are the final video, Creative Brief, full executable recipe, review summary, version history metadata, and usage/limitation report.

“Model reasoning” in older architecture images should be interpreted as concise declared decisions and evidence-linked rationales, not hidden chain-of-thought.

## 24. Remaining choices and first Codex task

### 24.1 Choices still requiring local evidence

| Choice | Recommended default | Evidence needed |
|---|---|---|
| Exact renderer path | One FFmpeg-centered implementation | Pixel formats, successful neutral/graded render, local operation support |
| Working color representation | Explicit SDR pipeline appropriate to the actual source | Metadata and verified transform behavior |
| SAM suitability | One tracked subject first | Actual video overlay and composited output |
| Full-source/proxy analysis trade-off | Small silent proxy plus time mapping | Latency, model understanding, mask boundaries |
| Output codec/profile | Browser-compatible preview; explicit final profile | Browser playback and source-precision requirements |
| Review budget | Initial candidate + at most three corrective candidates | Actual cost, latency, observed usefulness |
| Deployment | Local browser app first | Required judge access, upload limits, compute availability |
| Extra masks/multi-shot | Defer until core path works | Demonstrated capacity and remaining build time |

These are engineering choices to resolve during the stated milestones, not reasons to restart broad research.

### 24.2 First instruction for a new Codex session

Read this complete plan, then inspect the existing takeone-colorist repository and its lockfiles. Report what is present, what is verified only by the earlier setup, and what is missing for Milestone 1. Preserve the functioning environment and existing useful rubric code. Propose the smallest Milestone 1 implementation from the actual repository state; proceed with implementation when requested.

### 24.3 What this document deliberately does not claim

This document does not prove professional grading quality, reliable SAM tracking on arbitrary footage, improved outcomes from more questions/skills/RAG, independent model review, a fixed cost per clip, or complete competition eligibility.

The next evidence that matters is a real short clip processed through the agreed intent-to-render path.

## 25. Sources and earlier artifacts

### User-provided project records

- **Creative-Direction Rubric DESIGN.md:** The current consultative-module contract, interaction budget, provenance, and stated implementation limitations. [Open artifact](https://chatgpt.com/library?file_id=libfile_508a884ea5f881918774ffe20c91cf9c&account_id=personal).
- **Universal rubric research:** Five dimensions, uncertainty separation, actionability policy, human validation, and limits of the evidence. Relevant sections were consulted; its recommendations are not Muse experiment results. [Open report](https://chatgpt.com/library?file_id=libfile_ced333144ac481918a19a7e4d5031cb3&account_id=personal).
- **Professional Color Science and Workflows:** Earlier design research covering modular guidance, deterministic tools, caches, and inspection. Its technical suggestions require source-specific validation; it is not an executed pipeline. [Open report](https://chatgpt.com/library?file_id=libfile_80a0aafcea0881918de0953b9057f8f7&account_id=personal).
- **Earlier architecture image:** Visual communication reference. Precise contracts in this plan take precedence over ambiguous “three passes,” “production-ready,” or “model reasoning” labels in the image. [Open image](https://chatgpt.com/library?file_id=libfile_492880b8049481919f557100b29295fc&account_id=personal).
- The user's terminal output and reported Spark/SAM smoke tests establish the setup status summarized in Section 2. The actual local repository was not accessible in this planning workspace.

### Official documentation checked on 31 October 2026

- **[S1] Meta structured output:** Schema-constrained model output; exact endpoint integration still needs validation against the selected SDK. https://dev.meta.ai/docs/cookbook/structured-output
- **[S2] Meta SAM API basics:** Hosted image/video paths, official parser, streamed frame/track results, sample video workflow. https://dev.meta.ai/docs/cookbook/sam-api-basics
- **[S3] Meta video/audio understanding:** Uploaded video references and the current Muse Spark 1.3 audio-support caveat. https://dev.meta.ai/docs/video-understanding
- **[S4] FFmpeg filter documentation:** Source for concrete filter semantics and format requirements; the installed build's behavior still needs exercising. https://ffmpeg.org/ffmpeg-filters.html
- **[S5] Official hackathon event page:** Public themes, track descriptions, schedule, and schedule-change caveat. Participant-only terms require a separate check. https://events.cerebralvalley.ai/e/meta-ai-developer-hackathon

The architecture, budgets, UI layout, implementation milestones, and acceptance gates in this document are project design decisions or recommendations. They are not empirical findings supplied by the API documentation.


## 26. Preference memory: capture-only addition

### 26.1 Scope and settings

This addition is application-level memory, independent of ChatGPT/Codex memory and model training.
It uses filesystem JSON and existing user interactions. No vector store, database framework,
additional model, or per-interaction extraction call is added.

Each profile starts with an empty collection and independent settings:

    capture_enabled = true
    application_enabled = false

The normal application also has memory_application_available=false. It deliberately has no
environment variable that can activate that rollout capability. The current API rejects application
enablement and new-project personalization requests. Capturing more evidence never changes either flag.

### 26.2 Evidence and preference entries

Two layers remain distinct:

- Feedback events preserve user wording/selection, event/project/question/version references,
  timestamp and editing context. Selection records do not imply approval of every adjustment.
- Preference entries hold scoped interpretations, dimension, topic, basis, status, supporting
  references and revision history. Automatic interpretations are tentative and project-scoped.
  Explicit reusable instructions can be added and confirmed by the user.

Capture covers submitted direction, rubric choices/free text, explicit current-video feedback,
version selections/rejections, and an evidence contract for manual adjustments/locks.
Skips/delegation may be retained as interaction evidence but create no specific aesthetic preference.
Model critique and automatic revisions are not capture sources. Source analysis, personal profile
information and unrelated media metadata are not mined for reusable preferences.

Scope is project, matching category/scene/purpose conditions, or explicitly general.
Context labels are user-supplied editing labels. Repetition within one video remains one supporting
project and never promotes an entry to general or confirmed. Conflicts are preserved separately.

### 26.3 Persistence and isolation

Reusable memory lives in .takeone-memory/, outside workspace/<project-id>/ artifacts and ignored by Git.
An owner manifest assigns a stable local owner ID; each profile has an independent JSON document.
Documents contain settings, feedback events, preferences, processed-event receipts and fingerprint
tombstones. File locks serialize updates; temporary writes, fsync and replacement make each update atomic.

Projects bind to their profile at upload. Changing the profile in the memory view affects new uploads,
not an existing video's ownership. APIs reject cross-profile supporting-project references and
copied owner/profile records. This is local filesystem namespacing, not multi-user authentication.

A project outbox persists user evidence before the separate memory write. Retried delivery is
idempotent. A memory write failure does not cancel accepted answers or ordinary current-video feedback.

Deleted entries disappear from the active view and cannot be resurrected by old event replays.
The tombstone retains identity/reference metadata and hashes; its interpreted text/history is cleared.
The separate feedback audit and project history remain retained. Deleting an interpretation is not
a request to erase the underlying project's event history.

### 26.4 Current-video behavior

Feedback on the current video explicitly creates a new brief revision, keeping the previous brief
in project history. It works with capture paused and application off. It does not automatically call
a model, generate a render, or change personalization settings.

Explicit version selections are captured; automatically selecting a newly rendered candidate is not
user preference evidence. Manual-adjustment/lock capture endpoints record requested actions only.
Their UI and execution hooks must be connected when actual manual controls are implemented.

### 26.5 Central prompt boundary and future application

PromptMemoryGate is the single reusable-preference projection boundary. With the rollout capability,
profile application flag, or project opt-in disabled, it supplies no applicable preference data.
Implemented rubric and colorist prompts pass through this gate. Factual scene analysis receives only
the media input and inspection instructions. Critic/look-selection/preview-planning use cases have
a prepared gate interface; no new model pipelines are built for them.

A future rollout requires all three explicit actions:

1. Deploy with memory_application_available=true after validating the future mode.
2. Explicitly enable application for the desired profile.
3. Opt a newly uploaded project in using use_preferences=true, with its editing context.

There is no endpoint to retrofit an existing frozen brief or project binding. Bindings freeze
eligible confirmed preference values/revisions. Later edits do not rewrite that snapshot or the brief;
deleted/contradicted entries stop being supplied to subsequent prompts.

Retrieval matches context, excludes explicit overridden topics and suppresses conflicting defaults.
Conflicting entries are labeled for clarification rather than resolved by last-write-wins.
Current requests, current-video answers/feedback, explicit constraints and tool limitations dominate
advisory defaults. Memory cannot grant permission to change protected content.

The prepared rubric protocol accepts memory references for influenced questions and IDs of defaults
actually used. Defaults become optional memory-provenance directives; current answers take precedence.
Questions/defaults/grade decisions retain entry IDs and revisions. Unknown or disabled references
are rejected. This protocol and future opt-in path are checked with local fixtures, not enabled in
the current release or validated for model quality.

### 26.6 Minimal UI and verification

The existing editor adds a Preferences view with:

- Recording preferences — personalization off.
- Profile and recording controls, stored entries, scope, basis and evidence.
- Explicit reusable-instruction creation and edit/delete controls.
- Supporting project counts and revision history.

A small current-video feedback field updates the current brief independently of memory flags.

Focused offline checks cover persistence/deduplication, one-video evidence counting, profile/owner
isolation, edits/deletion replay, context-sensitive retrieval, frozen bindings, current-instruction
precedence, actual SDK prompt construction while disabled, and current feedback while capture is paused.
No billable API calls are part of this feature's verification.
