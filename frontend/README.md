# Take One frontend

React + TypeScript + Vite. The screen contains video playback, creative direction/rubric interaction, global-grade generation, version selection, current-video feedback, preference management and a session log.

## Run

Start the backend from the project root:

    .venv/bin/python -m uvicorn takeone.app:app --host 127.0.0.1 --port 8000

Then run in this directory:

    npm run dev

Open http://127.0.0.1:5173/. On a fresh checkout, run npm ci first. Dev and preview proxy /api to port 8000; TAKEONE_API_TARGET can override the target for isolated tests.

Other commands:

    npm test
    npm run build
    npm run preview

See the [project README](../README.md) for configuration, tests and limits.

## Connected behavior

Source uploads reach the local backend before browser decoding. Source acceptance uses verified backend metadata; it does not fetch or decode the prepared preview. The preview URL is saved in IndexedDB and playback streams independently from the backend. The original is retained unchanged on the backend. Pending preparation can resume; imported rendered files use local browser storage.

A preview playback failure never invalidates a prepared source or blocks the rubric. Playback metadata has its own timeout and Retry playback action. A draft prompt is preserved when source preparation completes.

Start analysis submits the request to the actual API. The panel presents one validated question at a time, supports options/free text/skip/delegation, and displays a downloadable brief. It discloses when content is sent to Meta. Upload or typing alone makes no model call.

Polling merges authoritative state/events into the browser session. Stable IDs prevent duplicate log rows. Refresh restores the pending question or brief. Backend errors preserve their code, recovery action and technical details.

The Run dashboard defaults to Progress, with a separate Session log tab and shared token panel. Progress follows actual processing stages, distinguishes waiting for a user answer, and displays the persisted consultation stopping reason. It does not estimate a completion percentage. The tabs support keyboard navigation.

Question flow is one at a time: saving an answer triggers a fresh relevance check with prior decisions and cached observations. Zero questions remains valid for sufficiently specified requests; a short completion explanation is requested and stored separately from the brief directives. Existing sessions without that explanation remain compatible.

Muse owns question relevance, order and semantic repetition. Python no longer ranks or drops creative questions by keywords or topic overlap. Data/tool-reference errors fail visibly for retry. Session exports include full structured question proposals, selected IDs and contract errors; historical completed sessions retain their original logs. See [the rule-based inventory](../docs/RULE_BASED_LOGIC.md).

Conflicting workspace changes and model commands disable while local work, backend jobs, selections or cancellation commands are pending. Repeated submit/cancel/selection requests are also guarded in the consultation hook. Playback, tab navigation and exports remain available. Mutating controls unlock after success/failure/cancellation; editing a brief does not mark an earlier rendered version as a render of the new brief.

Health and job status come from the backend. No simulated questions, progress or usage are included in normal startup. After a brief is ready, Generate graded video explicitly requests a bounded global recipe and render. Selected source frames and the brief go to Meta; FFmpeg renders the original. Completed versions can be compared and downloaded. Subject masks use hosted SAM as a separate explicit action; regional edits and automatic critique remain unavailable. Render/media checks do not establish visual quality.

Subject masks defaults to a five-second excerpt, with an explicit full-clip option. It accepts one subject phrase, shows received versus validated frames, supports cancellation and saved runs, and provides a playable overlay plus manifest. All matched object IDs are shown; none is silently selected for grading. Network streaming, local decoding and overlay generation are separate progress stages. Completed streams retry locally without another model call; partial streams require an explicit remote retry. See [SAM integration](../docs/SAM_INTEGRATION.md).

Legacy browser-only sessions can use Prepare source. Existing saved recipe drafts remain readable.

## Preference capture

Preferences opens the profile-based memory view. It starts empty and shows **Recording preferences — personalization off.** Users can inspect evidence, add/edit/delete scoped entries, pause automatic capture, and choose a profile/context for the next upload. There is no personalization toggle in this release.

Submitted direction, rubric answers, feedback and explicit version choices are captured through backend APIs. Selecting a version records that choice; it does not imply approval of all its adjustments. Current-video feedback revises the brief even when capture is paused. Saving feedback does not automatically render a new version.

Reusable records live on the backend, separate from IndexedDB and project artifacts. See [the memory contract](../docs/PREFERENCE_MEMORY.md) for endpoints, deletion semantics and the prepared future application gate.

The selected profile and category/scene/purpose labels are next-upload defaults only. Existing videos retain their bound profile. Automatic entries are tentative and project-scoped; reusable scope and confirmation require explicit management. Deleting an entry prevents replay resurrection while retaining the separate feedback audit. Capture errors are shown alongside the current-video workflow; an outbox retry endpoint is documented in the contract.

## Main files

- api.ts: requests, errors, response validation, upload and polling.
- useConsultation.ts: health, model commands, reconnect and log deduplication.
- useWorkspace.ts: uploads, source preparation and local persistence.
- media.ts: bounded first-frame/thumbnail inspection.
- errors.ts: stable codes, messages, recovery actions and diagnostics.
- storage.ts: IndexedDB transactions and state validation.
- RubricPanel.tsx: prompt, question/answer controls and brief.
- RenderPanel.tsx: explicit global-grade request, progress, recipe and render checks.
- SamPanel.tsx: explicit subject/scope selection, tracking progress, saved mask runs, diagnostic overlay and manifest.
- TokenUsage.tsx / tokenUsage.ts: project usage totals, per-request breakdown, pending/unknown counts and legacy event compatibility.
- ProgressPanel.tsx / workflow.ts: backend-stage progress, waiting/completion states and processing locks.
- SessionPanel.tsx: keyboard-accessible Progress/Session log tabs and activity filtering.
- MemoryView.tsx: scoped preference management and supporting evidence.
- preferenceApi.ts / preferenceContext.ts: memory API and next-upload profile/context defaults.
- ErrorBoundary.tsx: recovery screen for rendering exceptions.

IndexedDB remains at schema version 1; backend state is optional, preserving legacy sessions. Export excludes thumbnails, video blobs and backend-private credentials.

The token panel uses the current backend project's persisted events rather than counting merged log rows or the consultation's resettable call counter. Existing polling updates it automatically. Input/output/total are provider-reported; cached and reasoning tokens remain included subsets. Running requests show a waiting status, and missing usage stays unknown. No extra model or token-count requests are made. Exact counts arrive after a response, not continuously during generation; this is not an account balance or context-occupancy meter.

## Verification boundary

Automated frontend tests use Node's test library and the installed Vite compiler. Browser integration should exercise upload → Start → question → answer → brief, preference add/edit/delete and profile isolation, and feedback with capture paused. A real generated render requires a separate visual review; offline memory verification makes no model calls.

tests.offline_app:app is a separate fixture-backed test server. Normal startup always uses the real Muse provider. Offline checks verify wiring and state behavior, not live model quality.
