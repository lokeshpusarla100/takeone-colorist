# Take One editor

A React + TypeScript + Vite workspace for reviewing video, preparing grading recipes, and keeping a readable session log. It uses the dependencies already installed in this project.

## Run

From the project root:

```bash
cd frontend
npm run dev
```

Open **http://127.0.0.1:5173/**. Keep the terminal running; Ctrl+C stops the server. On a fresh checkout, run `npm ci` inside `frontend/` before starting. Use Node.js 20.x (20.19.0 or newer), or 22.12.0 or newer; Node.js 24 is installed in the original development environment.

For a production build, run `npm run build` in this directory, then `npm run preview`. Preview serves the build at **http://127.0.0.1:4173/**.

## Test

Follow the [manual testing checklist](../README.md#test-the-ui-manually) in the project README. It also includes optional FFmpeg commands to create a source/output pair for comparison.

Start with a local H.264 MP4 or WebM, save a creative direction and recipe draft, inspect the corresponding log entries, refresh after “Saved locally,” then export the log. Draft controls currently save values without changing the displayed pixels.

`npm run build` runs TypeScript checking and bundling. No frontend automated test runner is configured yet.

## Available now

- Import a local video by choosing a file or dropping it on the viewer.
- Play, pause, seek, mute, loop, change playback speed, and use fullscreen.
- Save a creative direction and exposure, contrast, saturation, and RGB gain recipe drafts.
- Select original footage, saved recipe drafts, and imported rendered versions.
- Import an existing render with the source's dimensions and duration, then compare it with a synchronized before/after wipe.
- Follow timestamped activity, filter decisions or issues, search the log, and inspect event details.
- Save and reopen multiple sessions in the same browser, including their source media and imported renders.
- Download the selected imported video or export a JSON session record containing direction, recipes, version relationships, media metadata, and events.

The UI is responsive and supports keyboard playback controls. The shortcuts dialog is available in the desktop rail.

## Connection boundary

Muse, SAM, FFmpeg rendering, and the backend API are **not connected** to this frontend yet. No model requests or media uploads are made.

Controls edit a **recipe draft**. They do not change the displayed pixels. A selected draft is explicitly labeled as showing source footage. Imported renders are labeled as imported outputs and have no claimed recipe provenance. Compatibility checks use browser media metadata, not FFprobe or technical color analysis.

The session panel is ready to display Muse decisions, including a short explanation from the normal edit response. It does not request or display hidden thinking. Its initial activity is real local application activity; no Muse decisions, model usage, render progress, or costs are fabricated.

## Local storage and exports

`src/lib/storage.ts` uses IndexedDB to keep project records and video blobs in separate object stores. Autosave writes project metadata after changes. The active session ID is kept in localStorage. Replacing source footage starts another session and keeps the previous one.

Storage belongs to the exact browser origin. Development at port 5173 and preview at port 4173 have different sessions; so do localhost and 127.0.0.1. Clearing site data removes locally stored media and sessions.

Exported JSON includes no video blobs, thumbnails, credentials, or object URLs. The log records are portable; restoring a workspace from exported JSON is not implemented. Sessions are reopened from this browser's storage.

## Backend integration points

- src/lib/types.ts: Project, Version, Recipe, and SessionEvent.
- src/lib/useWorkspace.ts: project updates, version selection, and appendEvent.
- src/components/SessionPanel.tsx: activities, decisions, brief reasons, details, search, and filtering.
- src/components/Viewer.tsx: actual media playback and before/after comparison.

A future backend adapter should attach actual rendered media and validated events to the current project. The recipe contract still needs the renderer's explicit color domain, operation order, parameter semantics, and source/version provenance.

Example **event contract**, not a generated result:

    {
      "id": "event-unique-id",
      "timestamp": "2026-10-31T16:00:00.000Z",
      "actor": "muse",
      "kind": "decision",
      "title": "Reduce background saturation",
      "reason": "Keep the motorcycle visually prominent while preserving its paint.",
      "versionId": "candidate-unique-id",
      "details": {
        "operation": "saturation",
        "region": "background",
        "from": 1,
        "to": 0.85
      }
    }

Use the existing response's operation rationale as reason. The log itself needs no additional model call. Emit a completed action only after the backend confirms it succeeded; report failures as error events.
