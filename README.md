# Take One

Local video preparation, a React editor, and a Muse-powered creative-direction consultation.

    Upload original → prepare preview → enter request → Start analysis
    → answer useful questions → save/download the creative brief

Automatic colorist execution and SAM masking are not connected yet. A bounded global SDR tool exists on the backend, but this UI currently stops at the creative brief.

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
4. **Clarify if needed.** The existing rubric validates evidence and available operations, then selects one question. Answer using an option, free text, Skip or You decide.
5. **Review the brief.** The original request, approved outcomes, preservation constraints and unresolved limitations are saved. Download the brief or full session log.

Default question budget: three, with a hard maximum of five. A clear request can receive zero questions. The five rubric dimensions are internal coverage categories.

Existing browser-only sessions remain compatible. Use **Prepare source** to upload their saved original and connect to the backend.

## Recovery and persistence

- Backend files live under workspace/<project-id>/: original, metadata, previews, cached scene analysis, consultation state, brief and timestamped events.
- Atomic state writes preserve pending questions and accepted answers. Refresh resumes the consultation.
- Follow-up questions reuse cached observations rather than resending the video.
- Command IDs and expected revisions protect against duplicate or stale answers.
- Answers are saved before follow-up model calls. A failed request can be retried without resubmitting the answer.
- Interrupted jobs are marked failed after backend restart. Billable requests are not automatically retried.
- Cancellation stops local media processing. An in-flight remote request may finish and incur usage; completed analysis can be cached for resumption.
- The frontend caches prepared previews in IndexedDB. It polls persisted backend state and deduplicates log events by ID.
- Exported JSON includes consultation/version metadata, events and diagnostics, without video blobs, thumbnails or credentials.

Browser sessions depend on the exact host/port. Localhost and 127.0.0.1, and ports 5173 and 4173, have separate storage. Backend files survive browser site-data clearing, but a backend-project browser is not implemented.

## Limits

- Default: 256 MiB per upload, 120 seconds, up to 4K.
- HDR conversion and non-square-pixel sources are rejected explicitly until verified.
- Source color interpretation remains known/unknown. Global grading capabilities are restricted to supported source metadata.
- Browser previews are compressed 8-bit working views. Original precision is retained in the upload.
- Scene understanding uses the complete silent proxy; it is not a calibrated color measurement.
- The base global tool supports SDR display-domain contrast, saturation and gamma. Local grading, relighting, reconstruction and shot matching are unavailable.
- Imported renders can be compared when dimensions and duration match; imported outputs have no verified recipe provenance.
- Final rendering and autonomous review are not connected to the brief yet.

## Check

From the project root:

    .venv/bin/python -m pytest -q tests takeone_rubric_v1
    npm --prefix frontend test
    npm --prefix frontend run build

Backend tests inject a fixture provider and exercise real upload, FFprobe/FFmpeg, question selection, answer forms, persistence, idempotency, retry, cancellation, limits and a global-tool pixel effect. They do not call Meta.

Frontend tests cover media diagnostics, cancellation, resource cleanup, storage validation and API contracts. Live Muse connectivity and question quality need a separate run with the configured key.

The test-only browser server is tests.offline_app:app. It returns explicitly labeled synthetic observations and is separate from normal startup. Passing offline tests does not establish live Muse quality.

## Troubleshooting

- **Backend unavailable:** start the Python server on port 8000. Vite proxies /api there.
- **Meta key missing:** add it to root .env, then refresh the editor. Health reports only whether a key is configured.
- **Model step failed:** inspect its code/action in the notice and log. Use Retry failed step; saved evidence and answers are retained.
- **Source preparation failed:** inspect the media-tool diagnostic. Use Resume source preparation or import another source.
- **Old session decoder failure:** use Prepare source to obtain a backend VP9 preview.
- **Save failed:** keep the page open, export the log, then retry saving.
- **Port 5173 occupied:** open the existing editor, or run npm run dev -- --port 5174 inside frontend. Each origin has separate sessions.
- **Workspace in use:** stop the other backend or configure another TAKEONE_WORKSPACE.

## Repository

- [Frontend notes](frontend/README.md)
- [Original project plan](Take_One_Hackathon_Project_Plan.md)
- [Creative-direction rubric](takeone_rubric_v1/README.md)
- takeone/: FastAPI application, media tools, Muse integration, jobs and persistence
- requirements-lock.txt: existing pinned Python dependencies

Credentials, working media, workspace state, dependencies, builds and caches are ignored by Git. Keep media and exports under workspace/ or outputs/. Source, schemas, documentation and lockfiles remain eligible for version control.
