# Take One

A video color grading workspace with a session log for editing activity, decisions, and short explanations.

The React editor UI is implemented. **Muse, SAM, the rendering pipeline, and the backend API are not connected to it yet.** You can test the UI without an API key, Python server, or model calls.

## Run the editor

From the project root:

```bash
cd frontend
npm run dev
```

Open **http://127.0.0.1:5173/** in your browser. Keep the terminal running; press **Ctrl+C** to stop the server.

On a fresh checkout, install the locked frontend dependencies first:

```bash
cd frontend
npm ci
npm run dev
```

The installed Vite package requires Node.js 20.x (20.19.0 or newer), or Node.js 22.12.0 or newer. Node.js 24 is available in the original development environment; with nvm, you can select it using `nvm use 24`. Existing installations with `frontend/node_modules` can start directly with `npm run dev`.

## Test the UI manually

Use a short browser-compatible H.264 MP4 or WebM. MOV playback depends on the codecs inside the file.

| Action | Expected behavior |
| --- | --- |
| Open the editor | The empty viewer appears, controls are disabled, and the session log contains “Workspace created.” |
| Click **Import video** or drop a video on the viewer | The video opens, its dimensions and duration appear, an Original version is added, and the import is logged. |
| Play, pause, seek, mute, change speed, loop, or enter fullscreen | The local video responds. On narrow screens, some transport controls are hidden. |
| Open **Direction**, enter an instruction, and click **Save direction** | The direction is saved and appears in the session log. It is not sent to Muse yet. |
| Change exposure, contrast, saturation, or RGB balance | Recipe values change and the committed adjustment is logged. **The video pixels stay unchanged until a renderer is connected.** |
| Click **Save recipe draft** | A new draft appears in Version history. Selecting it shows the original footage with an explicit unrendered draft label. |
| Refresh after **Saved locally** appears | The footage, direction, recipe drafts, versions, and log return in the same browser. |
| Open **Sessions**, then **New session**, and reopen the previous session | Each session retains its own footage and activity. Sessions is in the desktop rail and the mobile footer. |
| Search the log or select **Issues** / **Decisions** | The corresponding activity is shown. Decisions remains empty until real Muse decision events are connected. |
| Click **Export log** | A JSON file downloads with direction, recipes, version relationships, media metadata, and events. It excludes video data and thumbnails. |

Playback shortcuts: **Space** plays/pauses, **← / →** seeks one second, **M** toggles mute, and **F** toggles fullscreen. Shortcuts do not interrupt typing in form fields.

### Compare a rendered video

Import the source first, then click **Import render** beside Version history. Choose an existing output with the same dimensions and duration as the source; the UI allows up to 0.15 seconds of duration difference.

Select the imported render and click the comparison icon beside the Original/Render buttons. Drag the wipe to compare the source and render at the same timestamp. **Download video** downloads that imported output.

Imported renders have no verified recipe provenance. This UI does not generate a new graded video yet.

### Optional test clips

If FFmpeg is installed, these commands create a three-second synthetic source and a desaturated output. Run them from the project root. These are UI test fixtures, not Muse-generated results.

```bash
mkdir -p outputs/ui-test
ffmpeg -f lavfi -i testsrc2=size=640x360:rate=24 -t 3 -c:v libx264 -pix_fmt yuv420p outputs/ui-test/source.mp4
ffmpeg -i outputs/ui-test/source.mp4 -vf eq=saturation=0.4 -c:v libx264 -pix_fmt yuv420p outputs/ui-test/render.mp4
```

Import `outputs/ui-test/source.mp4` as the source and `outputs/ui-test/render.mp4` using **Import render**. FFmpeg asks before overwriting existing test files.

## Check a production build

From the project root:

```bash
cd frontend
npm run build
npm run preview
```

The build command runs TypeScript checking and creates `frontend/dist/`. Preview serves the build at **http://127.0.0.1:4173/**. The frontend currently has no committed automated test runner; use the manual checklist above for behavior checks.

## Sessions and local files

Sessions and media are saved in IndexedDB; localStorage identifies the active session. Use the same browser, host, and port to reopen them: `localhost`, `127.0.0.1`, the development port, and the preview port each have separate browser storage. Clearing site data removes the locally saved sessions and footage.

Exported logs provide a portable activity record. Importing exported JSON to restore a project is not implemented yet.

The root `.gitignore` excludes credentials, virtual environments, dependency folders, builds, caches, logs, and generated media directories. Put working footage, frames, masks, renders, and exported sessions under `workspace/` or `outputs/`. Lockfiles, schemas, source code, and documentation remain eligible for version control.

## Project files

- [Project plan](Take_One_Hackathon_Project_Plan.md): architecture, scope, and milestones.
- [Frontend README](frontend/README.md): UI implementation details and future backend integration points.
- [Creative-direction rubric](takeone_rubric_v1/README.md): standalone question/brief module; currently separate from the editor.
- `requirements-lock.txt`: existing pinned Python environment dependencies.

## Troubleshooting

- **Port 5173 is already in use:** open the existing editor first. To run another instance, use `npm run dev -- --port 5174` from `frontend/`; it will have separate session storage.
- **Video will not open:** try an H.264 MP4 or WebM. Browser playback compatibility is checked during import.
- **Controls do not change the picture:** expected at this stage. They save recipe drafts; backend rendering is still pending.
- **Decisions is empty:** expected until Muse is connected. Imports, saved direction, control changes, and errors appear under All activity.
- **A session does not reappear:** check that you are using the same browser and exact URL, and that browser storage is available.
