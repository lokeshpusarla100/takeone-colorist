# Progress display and automatic grading

## Automatic grading after the questions

When the consultation produces its brief, the editor starts the first grade by itself. Rules
(`frontend/src/lib/autoRender.ts`, pure and unit-tested; wired in `useConsultation`):

- **Armed only by watching.** This tab must have seen the project's consultation running (Muse thinking, or waiting
  for answers). A session that is merely opened or reloaded at "brief ready" never renders by itself. Reloading in the
  middle of the questions still works: the tab sees the consultation and arms.
- **Decided when the brief arrives.** With the switch off at that moment, that consultation never auto-renders, even
  if the switch is turned on afterwards.
- **Same conditions as the Generate button:** online, Muse and grading connected, direction text equal to the saved
  request, and no version yet for this brief.
- **Once per brief.** A rejected start may be tried once more; there is no retry loop. After Cancel or a failure
  nothing restarts; Retry render / Generate remain manual.
- **Switch:** "Start grading automatically when the questions are done" beside the direction, on by default, remembered
  in browser storage (`takeone.autoRender`). The label says it uses Muse and SAM, since it starts billed work.
- The questions dialog closes itself when the questions finish and auto-grading is on, so progress is visible.

The backend is unchanged for this: the start is the ordinary `POST /render` command.

## What the progress panel shows

The **Run** tab shows the whole run as one list of nine steps: Prepare source, Watch the video, Questions, Creative brief,
Plan the grade, Subject masks (SAM), Render, Quality check, Creative review (`frontend/src/lib/stepper.ts`, built on the
segments of `progressModel.ts`). The status strip over the video, the caption ("Step 6 of 9: Subject masks (SAM)"), the
overall bar and the list all come from the same rows (`useRunModel`), so they cannot disagree, and the encode is polled once.
Position comes from the backend stage. A fill inside the running step is shown **only where something is measured**;
nothing is estimated:

| Stage | Measured fill | Source |
|---|---|---|
| Render | encode position, speed, time left | FFmpeg `-progress`, via `GET /api/projects/{id}/progress` |
| Subject masks (SAM) | frames tracked of total | `segmentations[-1].received_frames` |
| Everything else | indeterminate stripe + elapsed time | - |

- Subject masks is "Not needed" (and leaves the count) when the plan is a whole-frame grade. A render that was never
  reviewed shows Review the same way. The total therefore can shrink once the plan is known (Step 3 of 9, then Step 5 of 8).
- A revision pass moves the run back to Plan; the steps after it restart.
- After Cancel the backend overwrites the stage with `cancelling`; the position is recovered from the last timing
  marker, so the stopped step is shown as stopped.
- The "Subject masks" time includes mask validation, which can be most of it on a long 4K clip (3m51s in one recorded run).

### Clocks

- **Active time** accumulates across the consultation, the render and review passes since this direction was
  submitted (`request_started_at`). Waiting for your answers is never counted; a new direction starts it again.
- **This step** is the running step's own clock, with **last time**: how long the previous finished visit to the same
  step took (including an earlier revision pass). It is a recorded fact, not a forecast. The running row in the list
  shows the same number.
- Each finished row shows its own time; "All attempts" at the foot is everything ever timed for the session.

### Live encode measurement (backend)

`media.command(..., on_progress=)` hands ffmpeg's progress fields to an observer (an error in it never affects the
run). `Service` keeps the latest encode position in memory (never persisted, no revision bump). The endpoint answers
from memory, once a second only while a render stage is running: a measurement is returned only while the job that
made it is still running, and each new stage or render pass clears it. A backend without the endpoint, or any failed
request, simply means no live fill.

## Fixed in the old panel

- The timer showed the previous attempt's time while a request was being sent, and restarted at every answer round.
- "Generating a grade is a separate action" was shown during and after rendering; the Clarify step still said "one
  question at a time"; the masks step said "Not started" for grades that need no masks.
- Token usage sat above the progress and pushed it below the fold; it is now below the steps.
- A second render pass briefly showed the first pass's final percentage (cleared on every new stage).
- Two step lists with different names (an 8-segment bar and a 7-row list) are now one list; the caption "Step 5 of 8" no longer disagrees with a list of nine rows.

## Source preparation time

The exact frame-timestamp probe of the original (`frames.probe_frames`, needed later by mask tracking and
rendering) is a full second decode of the file. It used to run *during* preparation, alongside the preview
encode. Both are CPU-bound on every core, so overlapping them saved almost nothing and added the probe's time to
what the user waits for: a 4K, 154 Mbps, 25 s clip went from ~18-20 s to ~25 s.

It now starts in the background when the source becomes ready (`Service.prefetch_frames`), while the user writes the
direction and answers questions: the same clip is ready in ~19 s and the probe finishes ~9 s later. `probe_frames`
is single-flight: a stage that needs the timing before the background run has finished waits for that run (honouring
its own cancel) instead of decoding again; if that run failed or was cancelled the stage probes for itself, exactly
as before. The background run is stopped by `Service.close()`, and a failure in it is never fatal. Output and cache
file are unchanged. The remaining cost is the single 4K decode (~9 s wall, 74 CPU-seconds) inside the preview encode.
