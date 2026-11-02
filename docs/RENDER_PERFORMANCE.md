# Render performance: unchanged quality, fewer repeated passes

The master remains original resolution, Rec.709 `yuv420p`, libx264 **CRF 17 / preset fast / four encoder
threads**. The browser preview remains the same bounded geometry and VP9 settings. No source frames,
SAM checks, output timing checks, clipping bounds or human creative approval were removed.

## Changes

- A strict `ffmpeg -xerror` decode now obtains every video frame's integer PTS/duration through
  `framecrc`, while decoding the audio too. It replaces the separate master null decode plus another
  decoded FFprobe pass. Explicit validation encoder time base and passthrough scheduling prevent the
  validator from normalizing irregular timestamps. The CRC stream is bounded text, not retained raw
  full-resolution frames. Preview integrity/timing is checked independently.
- Master/preview metadata and subsequent full decode checks run in at most two workers. Decoder
  threads are bounded at four and validation rawvideo encoding uses one thread per worker. No nested
  full-render workers or full-video raw frame buffers were introduced.
- The shared filter graph uses at most four workers, bounded by available CPUs. Encoder quality
  controls stay fixed. A reference mode retains the previous two filter workers and separate QC
  passes for an explicit A/B measurement.
- Paired creative-review evidence reuses each extracted RGB batch for JPEGs, measurements and
  source-defined shadow/highlight detail checks. This removes two duplicate decode/extract passes.
- Older source evidence with decimal timestamps can be upgraded onto the source's verified tick
  lattice only when its recorded precision identifies a unique tick. The final decoded PTS/duration
  is checked with a bounded last-GOP seek; ambiguity falls back to the full source inspection. The
  upgraded manifest is cached with source identity and evidence identity. No nominal-FPS timestamps
  or final frame durations are invented.
- Global clipping preflight can encode at most twelve selected original-resolution frames through
  the same colour/LUT/master encoder path. Sparse temporal reference context differs from a dense
  encode, so preflight is an early rejection/repair check; final dense QC remains authoritative.
  A mathematical per-layer clipping envelope now runs for localized grades before any SAM
  request or encode (each layer assessed over the bounded samples; see render_preflight.py),
  but a sparse *encoded* localized probe remains unavailable because dropping frames before
  the compositor would break frame-index mask conditions. Dense localized validation remains intact.
  Probe reuse is keyed by source identity, selected indices, executable spatial graph/LUT identity,
  colour/working/output representation, encoder controls, FFmpeg build and OCIO/NumPy versions.
  Prose, timestamps and preflight-report metadata do not invalidate unchanged executable pixels.

`render(..., qc_mode='optimized')` is the normal path. `qc_mode='reference'` exists for offline
comparison. Both paths return actual monotonic-clock `performance` measurements for source identity,
source timeline, encoding, metadata, full decode/timing and output samples. Failures retain the same
measurements in their structured error details. A missing reference source-timeline entry means the
recipe already held the integer manifest and no upgrade was performed.

The final optimized master decode also captures its selected RGB evidence in the same FFmpeg pass.
The full-resolution integrity branch still consumes every video frame and audio packet; only the
independent evidence branch is scaled. It uses a bounded temporary RGB file with strict byte-count
validation and cleanup. Source/candidate evidence pixels and clipping checks are unchanged.

## Measured scope

A serial A/B used the same saved 4K boat-video recipe and a two-second input bound, which produced
61 decoded video frames in each output. Filter workers were the only changed render option:

| Filter workers | Master + preview encode elapsed |
| --- | ---: |
| 2 | 19.164 s |
| 4 | 15.461 s |

This was **19.3% shorter / 1.24x** for this single bounded development measurement. Both the master and
preview had identical decoded frame MD5 rows, including frame timing/order. It is not a promise of
the same improvement for every clip or a full-run speedup benchmark. The raw local evidence is ignored
by Git at `outputs/performance-validation/filter-benchmark.json`.

Twenty focused tests passed in 14.54 seconds: timing/performance fixtures cover CFR, the 741-frame
near-CFR pattern, genuine VFR, nonzero starts, exact integer timing/final-duration agreement, corrupt
media rejection, source timeline cache reuse, identical decoded reference/optimized master+preview
pixels, stable selected-frame IDs, sparse probe reuse and shared critic pixels. The command was:

    .venv/bin/python -m pytest -q tests/test_render_performance.py tests/test_render_timing.py

No live model calls were used. Full actual-footage encode results and clipping-repair acceptance must
be assessed separately using the final effective recipe; these focused measurements do not establish
aesthetic success.

After the recovered full clip passed, a read-only combined-QC audit on its completed master/preview
took **24.676 s**, retained all 741 timestamps/durations with zero master drift, decoded audio, and
produced RGB sample records exactly equal to the earlier separate extraction. Four new focused
combined-QC/optimized-output comparisons passed. This is a QC measurement on the same file, not a
second complete encode benchmark. The full recovered replay before this final merge took 235.16 s;
its encode was159.37 s, so there is no measured full-clip encode speedup claim.
