# Take One frontend

React + TypeScript + Vite. The screen contains video playback, creative direction/rubric interaction, version selection and a session log.

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

Health and job status come from the backend. No simulated questions, progress or usage are included in normal startup. Automatic colorist generation, SAM and final rendering remain unavailable.

Legacy browser-only sessions can use Prepare source. Existing saved recipe drafts remain readable.

## Preference capture

Preferences opens the profile-based memory view. It starts empty and shows **Recording preferences — personalization off.** Users can inspect evidence, add/edit/delete scoped entries, pause automatic capture, and choose a profile/context for the next upload. There is no personalization toggle in this release.

Submitted direction, rubric answers, feedback and explicit version choices are captured through backend APIs. Selecting a version records that choice; it does not imply approval of all its adjustments. Current-video feedback revises the brief even when capture is paused. Saving feedback does not automatically render a new version.

Reusable records live on the backend, separate from IndexedDB and project artifacts. See [the memory contract](../docs/PREFERENCE_MEMORY.md) for endpoints, deletion semantics and the prepared future application gate.

## Main files

- api.ts: requests, errors, response validation, upload and polling.
- useConsultation.ts: health, model commands, reconnect and log deduplication.
- useWorkspace.ts: uploads, source preparation and local persistence.
- media.ts: bounded first-frame/thumbnail inspection.
- errors.ts: stable codes, messages, recovery actions and diagnostics.
- storage.ts: IndexedDB transactions and state validation.
- RubricPanel.tsx: prompt, question/answer controls and brief.
- MemoryView.tsx: scoped preference management and supporting evidence.
- preferenceApi.ts / preferenceContext.ts: memory API and next-upload profile/context defaults.
- ErrorBoundary.tsx: recovery screen for rendering exceptions.

IndexedDB remains at schema version 1; backend state is optional, preserving legacy sessions. Export excludes thumbnails, video blobs and backend-private credentials.

## Verification boundary

Automated frontend tests use Node's test library and the installed Vite compiler. Browser integration should exercise upload → Start → question → answer → brief.

tests.offline_app:app is a separate fixture-backed test server. Normal startup always uses the real Muse provider. Offline checks verify wiring and state behavior, not live model quality.
