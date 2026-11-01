# Take One

Local video preparation, a React editor, Muse-powered creative direction, and capture-only editing preference memory.

    Upload original → prepare preview → enter request → Start analysis
    → answer useful questions → save/download the creative brief
    → generate a global or localized grade → compare versions → download

**Generate graded video** asks Muse Spark 1.3 to choose an executable global or localized SDR/Rec.709 plan from the saved brief and latest feedback. Global correction bypasses SAM. Localized treatment automatically obtains SAM 3.1 masks, validates them, and composites separate foreground/background adjustments with FFmpeg. Manual SAM diagnostics remain available. Physical relighting, reconstruction, shot matching and automatic aesthetic approval remain unavailable.

Phase 2 adds bounded content-adaptive evidence, luminance-preserving tone with OpenColorIO CDL colour operations, and rendered-evidence creative review. Substantive candidates may receive a targeted revision, capped at three renders by default. Muse opinions are shown separately from technical measurements; final aesthetic approval remains yours. See [Phase 2 implementation and verification](docs/PHASE2_ADAPTIVE_INTELLIGENCE.md).

## Configure

Reuse the existing Python environment and frontend dependencies. For a fresh checkout:

    python3.12 -m venv .venv
    .venv/bin/python -m pip install -r requirements-lock.txt
    npm --prefix frontend ci

FFmpeg and FFprobe must be on PATH. Use Node.js 20.x (20.19.0 or newer), or 22.12.0 or newer.

Edit the private root .env file:

    MODEL_API_KEY=your_meta_api_key

Do not put the key in frontend code or commit .env. See [.env.example](.env.example) for other settings. If the backend started without a key, adding one to .env is detected automatically. Restart for changes to an already loaded key or other settings. Source preparation and offline tests do not require a key; Muse calls require one and may incur usage.

## Run

Terminal 1, from the project root:

    .venv/bin/python -m uvicorn takeone.app:app --host 127.0.0.1 --port 8000

Terminal 2:

    cd frontend
    npm run dev

Open http://127.0.0.1:5173/. API documentation: http://127.0.0.1:8000/docs.

Use one backend process per workspace, without multiple Uvicorn workers. The backend takes an exclusive workspace lock and runs one media/model worker with a bounded queue. Bind to localhost; multi-user authentication is not implemented.

For a frontend build preview, run npm run build and npm run preview inside frontend, while keeping the backend running. Open http://127.0.0.1:4173/.

## Flow

1. **Import video.** The unchanged original goes to the local backend. FFprobe records metadata/checksum; FFmpeg creates a VP9 display preview and a smaller silent MP4 for analysis.
2. **Enter creative direction.** Describe the changes and preservation requirements. Leave blank to delegate a conservative direction.
3. **Start analysis.** This sends the silent analysis video and request to Meta. Uploading or typing alone does not call Muse.
4. **Clarify if needed.** Muse chooses the next question using the request and prior answers. The app checks the data and tool references, then shows it directly. Answer using an option, free text, Skip or You decide.
5. **Review the brief.** The original request, approved outcomes, preservation constraints and unresolved limitations are saved. Download the brief or full session log.
6. **Generate when ready.** Muse receives source frames, measured diagnostics, shot intervals and the current brief. A localized plan automatically requests only the required mask intervals. The renderer uses original pixels, not proxy images. This action can incur Muse and SAM usage.
7. **Compare and revise.** Select a version, compare with Original, and download the chosen render. Save feedback to revise the current brief; generate again explicitly to apply that revision.

Default question budget: three, with a hard maximum of five. A clear request can receive zero questions. The five rubric dimensions are internal coverage categories.

Questions are adaptive: one question → save your answer → reevaluate remaining choices → ask one useful follow-up or finish. Follow-ups reuse cached video observations and include earlier questions, alternatives and chosen outcomes. The app does not ask five questions in one batch or render after every answer. Broad look words do not automatically settle treatment strength or preservation tradeoffs.

Creative question selection belongs to Muse. Python keyword/family inference and aesthetic ranking remain removed. Repetition checks now validate scope-specific model decision IDs and verbatim question/option repeats; a genuinely finer clarification needs an explicit parent and new-information reason. New outputs request zero or one question. Invalid contracts fail for retry rather than silently completing the consultation. Full structured proposals and errors remain exported. See [the rule-based logic inventory](docs/RULE_BASED_LOGIC.md).

Existing browser-only sessions remain compatible. Use **Prepare source** to upload their saved original and connect to the backend.

## Recovery and persistence

- Backend files live under workspace/<project-id>/: original, metadata, previews, cached scene analysis, consultation state, brief and timestamped events.
- Atomic state writes preserve pending questions and accepted answers. Refresh resumes the consultation.
- Follow-up questions reuse cached observations rather than resending the video.
- Command IDs and expected revisions protect against duplicate or stale answers.
- Answers are saved before follow-up model calls. A failed request can be retried without resubmitting the answer.
- Interrupted jobs are marked failed after backend restart. Billable requests are not automatically retried.
- Cancellation stops local media processing. An in-flight remote request may finish and incur usage; completed analysis can be cached for resumption.
- The frontend saves source metadata and the backend preview URL in IndexedDB. Preview playback streams independently from the backend with byte-range support; a browser decoder timeout cannot block rubric analysis.
- It polls persisted backend state and deduplicates log events by ID.
- Exported JSON includes consultation/version metadata, events and diagnostics, without video blobs, thumbnails or credentials.
- Preference records live separately in .takeone-memory/. A persisted project outbox supports idempotent capture retry; memory changes do not rewrite a frozen brief.

Browser sessions depend on the exact host/port. Localhost and 127.0.0.1, and ports 5173 and 4173, have separate storage. Backend files survive browser site-data clearing, but a backend-project browser is not implemented.

## Limits

- Default: 256 MiB per upload, 120 seconds, up to 4K.
- HDR conversion and non-square-pixel sources are rejected explicitly until verified.
- Source color interpretation remains known/unknown. Global grading capabilities are restricted to supported source metadata.
- Browser previews are compressed 8-bit working views. Original precision is retained in the upload.
- Global rendering preserves source presentation timestamps, including near-CFR jitter and VFR. Encoder/MP4 time bases are explicit; decoded frame counts, per-frame timing and the master's audio/video origin are validated. See [timing policy and RENDER_TIMING_CHANGED diagnosis](docs/RENDER_TIMING.md).
- Scene understanding uses the complete silent proxy; it is not a calibrated color measurement.
- Tools support bounded contrast, saturation, gamma, brightness gain and explicit red/blue cooling in the SDR/Rec.709 display domain. Local execution supports one segmentation concept with foreground/background controls and up to twelve requested intervals, split at detected cuts. Local paths require upright, zero-start CFR footage and complete unambiguous masks. Physical relighting, reconstruction and shot matching are unavailable.
- Imported renders can be compared when dimensions and duration match; imported outputs have no verified recipe provenance.
- Local mask gaps, ambiguity, instability or preservation failures stop the candidate with no silent global fallback. Technical checks and mask-core statistics do not establish semantic mask accuracy or aesthetic success; moving-video approval remains yours.

## Run dashboard

The sidebar has **Progress** and **Session log** tabs. Progress shows real source-preparation, video-analysis, clarification, brief and optional-grade stages. It distinguishes processing from waiting for your answer, and uses an indeterminate indicator rather than an estimated percentage. Questioning finishes when Muse returns no question or the configured limit is reached. Historical sessions can retain a validator-rejection stop reason from the previous implementation. New zero-question responses include a short Muse explanation when supplied; older sessions without one are labeled accordingly.

Each progress step shows elapsed backend time, with separate planning/render/check timings and an overall **Total processing** timer. Times accumulate across attempts for this video, survive refresh, and pause between jobs and while awaiting answers. Browser upload transfer is excluded. Older untimed stages show **—** rather than an invented duration. Interrupted work is explicitly marked partial; offline timers freeze at the last saved update.

Rubric questions appear one at a time in an automatic popup. **Answer question** reopens a pending question; **Review Q&A** shows saved questions and answers. Closing or pressing Escape leaves the question unanswered and preserves the current draft while the page stays open. The direction field and completed Creative Brief remain in the editor.

Importing/replacing media, switching sessions, renaming, changing versions/preferences, and submitting direction/feedback or another generation are disabled during conflicting processing or command submission. Cancel remains available for active work until cancellation is requested. Playback, dashboard tabs and log export remain usable. Controls unlock when processing finishes, fails or is cancelled.

## Subject tracking with SAM

After the brief is ready, use **Subject masks · SAM**. Enter one subject phrase, such as motorcycle, choose **First 5 seconds** (default) or **Full clip**, and click **Track subject**. It uses the existing MODEL_API_KEY with hosted sam-3.1 and may incur per-frame usage. No local model weights or second API key are required.

The backend verifies source/proxy frame counts, timestamps and scale-only geometry before upload. This path supports CFR, upright sources. SAM's explicit frame/object IDs are retained and gaps remain gaps. Manual diagnostics show every match without selecting an editing target. Automatic grading requires one unambiguous ID per shot interval, or an explicitly planned all-matches concept. Valid masks are refined into an original-resolution matte and consumed by FFmpeg's masked merge.

Network events are saved before local decoding, so a completed response can be replayed after a decode/overlay failure without another SAM request. Incomplete streams remain failed and require an explicit new remote attempt. Local decoding can take substantially longer than downloading the response. See [SAM integration and validation](docs/SAM_INTEGRATION.md) for APIs, limits and the bounded live test.

See [automatic grading architecture and validation](docs/AUTOMATIC_GRADING.md) for planner activation, safe tonal processing, cache rules, frame alignment, quality gates and the actual-footage validation boundary.

## Token usage

The session sidebar shows this project's reported input, output and total tokens, with an expandable per-request breakdown for video analysis, rubric and colorist requests. Counts update through normal state polling as responses return and survive refresh. Direction changes retain earlier project usage. Cached-input and reasoning-output counts are subsets of the totals, not additional tokens.

While a request is running, the panel waits for provider accounting. Missing usage is shown as unknown and totals are labeled partial. Returned usage is saved before downstream validation/cancellation, including known accounting from truncated responses. Legacy saved usage events remain readable; historical requests without counts cannot be reconstructed locally.

[Meta's token-counting documentation](https://dev.meta.ai/docs/token-counting) specifies final-response usage, including the final usage chunk for streamed Chat Completions. It does not document continuous exact usage during generation. The input-count endpoint measures context size rather than billing usage. The dashboard uses returned accounting without extra API calls, token estimates or credit-balance claims. Inference requests keep their existing non-streaming structured-output path.

## Check

From the project root:

    .venv/bin/python -m pytest -q tests takeone_rubric_v1
    npm --prefix frontend test
    npm --prefix frontend run build

Backend tests inject a fixture provider and exercise real upload, FFprobe/FFmpeg, question selection, answer forms, persistence, idempotency, retry, cancellation, limits and a global-tool pixel effect. They do not call Meta.

Preference tests also cover profile isolation, deletion/replay protection, recovery, context retrieval, current-instruction precedence and disabled memory at actual prompt construction. For a focused run:

    .venv/bin/python -m pytest -q tests/test_preferences.py

Frontend tests cover media diagnostics, cancellation, resource cleanup, storage validation and API contracts. Live Muse connectivity and question quality need a separate run with the configured key.

An opt-in live check uses a one-second synthetic clip and at most two inference calls:

    .venv/bin/python -m takeone.verify --live

It can incur Meta usage. It uses a temporary workspace, never uploads personal footage, and attempts to delete its synthetic remote upload afterward. Normal automated tests make no live requests.

The test-only browser server is tests.offline_app:app. It returns explicitly labeled synthetic observations and is separate from normal startup. Passing offline tests does not establish live Muse quality.

An additional opt-in generated-footage check exercises Phase 2 planning/render/review with at most two renders, two critic calls and five total inference calls, with SAM forbidden:

    .venv/bin/python -m takeone.verify --phase2 --live

This can incur Meta usage and is never part of ordinary tests. Verification preferences/workspaces are temporary, so synthetic evidence does not populate real user memory. No new live Phase 2 check was run for the offline motorcycle replay described in the Phase 2 report.

## Troubleshooting

- **Backend unavailable:** start the Python server on port 8000. Vite proxies /api there.
- **Meta key missing:** add it to root .env, then refresh the editor. Health reports only whether a key is configured.
- **Model step failed:** inspect its code/action in the notice and log. Use Retry failed step; saved evidence and answers are retained.
- **Source preparation failed:** inspect the media-tool diagnostic. Use Resume source preparation or import another source.
- **Old session decoder failure:** use Prepare source to obtain a backend VP9 preview.
- **Earlier import timed out in the browser:** refresh, then use Resume source preparation. The existing backend upload is reused; no re-upload or browser metadata inspection is needed.
- **Save failed:** keep the page open, export the log, then retry saving.
- **Port 5173 occupied:** open the existing editor, or run npm run dev -- --port 5174 inside frontend. Each origin has separate sessions.
- **Workspace in use:** stop the other backend or configure another TAKEONE_WORKSPACE.
- **Preference capture needs attention:** accepted current-video feedback is retained. Inspect the capture error; after fixing storage, use POST /api/projects/{id}/preference-capture/retry or restart to retry the persisted outbox. Do not delete profile files to clear an error.

## Preference memory (capture only)

Open **Preferences** in the editor. New collections start empty. Submitted requests, rubric answers,
editing feedback and explicit version choices are recorded with profile/project provenance.
Automatic interpretations are tentative and project-scoped. You can add, edit, confirm, scope or
delete reusable instructions. Version selection alone does not imply liking every adjustment.

The banner reads **Recording preferences — personalization off.** Capture and application are
independent; application is centrally disabled in this release and cannot be enabled through the API.
Current-video feedback still revises that video's brief with capture/application off.

To manage preferences:

1. Open Preferences and review stored entries, scope and supporting evidence.
2. Use Add instruction for a reusable statement; choose this project, matching context, or explicitly general scope. Edit an automatic entry to review its interpretation or status.
3. Pause Record new project feedback to stop automatic capture. Explicit memory management remains available.
4. Choose a profile and category/scene/purpose labels before the next upload. Existing videos retain their original profile.

Deleted entries cannot return through replay of previously processed events. Their raw supporting feedback remains in the separate audit history. Capturing, confirming or repeating preferences never enables application, and explicit version selection never proves approval of every adjustment.

Records live in the ignored .takeone-memory/ directory, separate from project artifacts.
Existing interactions are not backfilled. See [the memory contract](docs/PREFERENCE_MEMORY.md)
and Section 26 of the project plan for persistence, deletion semantics, APIs, prepared future
opt-in interfaces and verification boundaries.

## Repository

- [Frontend notes](frontend/README.md)
- [Original project plan](Take_One_Hackathon_Project_Plan.md)
- [Creative-direction rubric](takeone_rubric_v1/README.md)
- [Preference memory contract and API](docs/PREFERENCE_MEMORY.md)
- takeone/: FastAPI application, media tools, Muse integration, jobs and persistence
- requirements-lock.txt: existing pinned Python dependencies

Credentials, working media, workspace state, dependencies, builds and caches are ignored by Git. Keep media and exports under workspace/ or outputs/. Source, schemas, documentation and lockfiles remain eligible for version control.
