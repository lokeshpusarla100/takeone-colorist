# Rendering timing: preserve source PTS

## Confirmed failure

The rejected 4K `VID20261004090239.mp4` render was available for direct inspection. Both source and
rejected master decoded to 741 frames. The source video used a 1/90000 time base, started at 0.0768
seconds with audio starting at zero, and had slightly irregular intervals. Its last normalized PTS
was 24.6618777778 seconds. It is classified as near-CFR; that classification does not authorize retiming.

The rejected master used a 1/15360 time base with an exact 30 FPS timeline. The maximum normalized
PTS error was **4.8 ms**, and the last-frame error was **4.7888889 ms**. The first video frame moved
to 0.0666666667 seconds, changing the source audio/video lead by **10.1333333 ms**. The previous
floating-point 2 ms comparison first failed at frame 310. This was an encoding-timing defect, separate
from the global look choice; SAM was not involved.

FFmpeg's default video encoder time base follows the output frame rate. `-fps_mode passthrough`
does not override that quantization, and `-copyts -start_at_zero` alone cannot preserve every PTS.
See the official [encoder/time synchronization options](https://ffmpeg.org/ffmpeg.html#Advanced-options)
and [MP4 track/movie timescales](https://ffmpeg.org/ffmpeg-formats.html).

## Execution policy

- Preserve the source's integer presentation timestamps in presentation order. Never snap near-CFR
  or VFR input to a nominal rate, and never independently reset video/audio to zero.
- Set `-enc_time_base:v` explicitly from the source stream for master and preview. Set MP4 video
  timescale to the source time-base denominator. Choose a bounded common movie timescale for video,
  audio and millisecond container origins. Keep `-copyts -start_at_zero` and passthrough scheduling.
- Record integer PTS, time base and frame durations in evidence manifests. Old saved recipes are
  supported by rereading source timing before execution. Source-analysis caches use a new version.
- Classify CFR/near-CFR/VFR for diagnostics only: exact nominal lattice within a tick is CFR;
  otherwise intervals within max(two source ticks, 1% nominal interval) are near-CFR; other patterns
  are VFR. No classification relaxes the preservation contract. Local SAM eligibility is unchanged.

## Validation and diagnostics

`takeone/timing.py` compares rational timestamps rather than rounded decimal seconds. Frame count
must match, and output timestamps must be unique and increasing. Normalized per-frame deviation is
bounded by two output ticks plus a two-microsecond allowance, with the original **2 ms ceiling** and
a quarter of the shortest source interval as additional caps. This covers container quantization
without admitting cumulative drift. The master also checks absolute PTS against the common container
origin, final frame duration when available, audio start offset and copied audio duration.

Preview timing is checked independently. WebM has millisecond precision and codec/muxer delay may
shift its shared origin; this shift is reported, while normalized frame timing/count remain guarded.
Container preview duration is compared with source container duration, rather than video-only
duration, so an existing audio lead/tail does not become a false duration failure.

Failures retain `RENDER_TIMING_CHANGED` and include frame counts, source classification, time bases,
first failing index, maximum/last-frame deviation and initial offset. Accepted candidate validation
exports include `timing.encoding`, `timing.master`, `timing.preview` and `timing.audio`. These checks
establish timing correspondence, not pixel equality under a colour transform or aesthetic quality.
If subsequent clipping QC fails, its error details retain the passing timing report, measured clipping
and declared bounds, so that an independent grading failure is not mistaken for another timestamp bug.
Structured errors stay structured in the API/state; their event-log projection is JSON text to respect
the existing scalar-only event contract. A failure cannot be lost merely because it contains a timing
report. A regression verifies the failed state clears its job and retries the same saved recipe without
another planning or SAM request.

## Reproduction and regression coverage

    .venv/bin/python -m pytest -q tests/test_render_timing.py

The synthetic 741-frame regression has a 76.8 ms video start, 1/90000 source time base, irregular
intervals and the same normalized last timestamp as the reported source. It reproduces the old
failure and verifies the new renderer. Additional real-FFmpeg fixtures cover true VFR, nonzero
container origins and audio leads/tails. Binary frame IDs survive the grade in exact order, detecting
missing, duplicate or reordered pictures independently of PTS. Tests also reject altered counts,
duplicate/reordered PTS, cumulative drift, origin changes, final duration changes and audio timing
errors. No model calls are needed.

The focused timing/error-propagation checks passed **13 tests**. Python compilation and
`git diff --check` also passed. No frontend code changed for this fix; its **24 tests** passed.
The final complete backend/rubric suite passed **137 tests in 124.05 seconds**, including the
failure-state and saved-recipe retry integration check. The idle development backend was restarted
with the final changes and its health endpoint passed.

The saved real 4K recipe was replayed in an isolated ignored output directory. The master retained
**741/741 frames**, **zero per-frame normalized PTS error**, the exact **76.8 ms video start**, and
**zero audio start/duration error**. WebM retained all frames with maximum normalized error of
**0.689 ms**, within its container precision. Full master decoding passed.

The same replay then stopped on a separate **GRADE_EXECUTION_LIMIT**: at frame 70 (2.332867 seconds),
the saved global plan introduced **3.7408 percentage points of encoded-black pixels**, exceeding its
declared **3 percentage point** allowance. That guard remains enabled. The rejected grade is not an
accepted candidate. Retrying the unchanged recipe is expected to hit this clipping limit; revising
the grade/brief is a separate step, rather than raising a limit or claiming visual success.

Local evidence is in `outputs/timing-validation/real-4k/validation.json`, with the rejected replay
media retained as `.partial` files. These files are ignored by Git. The failed project, original
rejected files, source, brief and saved plan are preserved. No creative planning change, boat-specific
protection fix, new Meta calls or aesthetic approval is implied by this timing fix.
