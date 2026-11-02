# Clipping failure: RCA, prevention and offline verification

## What failed

The latest downloaded `VID20261004090239-session (1).json` and its matching saved project identify a
global v3 grade, not a SAM failure. The source is 3840×2160 H.264, 741 frames, approximately 24.6952
seconds of video. The current master timing check passed: zero per-frame normalized PTS error, the
original 76.8 ms video start, and unchanged copied audio timing. The preview's maximum normalized
deviation was 0.689 ms, within its container precision.

At frame 70 / 2.332867 seconds, the grade introduced **0.037407769 of the image as additional encoded
black**, while the plan allowed **0.03**. These are **3.7408 versus 3 percentage points**, an excess of
0.7408 percentage points. The clipping guard correctly rejected the candidate.

The saved plan used black_point=0.04 and described this as a “lift of black floor.” The implemented
control is an **input black cutoff**:

    level = clip((encoded_luma - black_point) / (white_point - black_point), 0, 1)

Input luma at or below 0.04 therefore maps to black. It does not lift output blacks. Gamma=0.94,
brightness_gain=0.94, contrast=0.9 and the colour treatment then act on the remaining range. The plan's
description and chosen control had different meanings. The planner receives explicit control semantics
after this correction; unsupported output-black lifting is not claimed as an available operation.

The second failure mechanism was workflow timing: execution limits were checked only after a complete
4K master and preview had been encoded. Retry resumed the same frozen recipe, so the same predictable
failure was expensive to rediscover. Raising the declared limit or calling the dark result successful
would conceal the problem rather than repair it.

## Why a long run is plausible, and what the log actually measures

The export's persisted progress markers confirm the reported **2m33s rendering interval** and isolate
the subsequent QC cost:

| Saved event interval | Elapsed | Included work |
| --- | ---: | --- |
| Retry plan start → render start | 57.445 s | Source identity, timeline/evidence preparation |
| Render start → output-QC start | 153.082 s | Master and preview encoding |
| Output-QC start → clipping rejection | 77.325 s | Decode, timing and sampled QC |
| Total recorded retry processing | 287.852 s | The three intervals above |

Those spans are measured stage-marker differences, not measurements of the individual subprocesses
inside each stage. The initial Muse-backed request separately took 77.394 seconds from grade request
to saved recipe. The latest retry reused that recipe and made no new planning or SAM request.
The machine has a Ryzen 5 5500U with six physical cores / twelve threads and about 14 GiB RAM. Creating more
parallel full-resolution encodes can increase memory use and contention rather than improve latency.

The pre-optimization renderer separately performed a strict master decode, a decoded-frame timestamp
scan, and another master traversal to extract the selected QC frames. A sparse selection near the end
still requires decoding the preceding H.264 frames. Bounded sample memory does not mean only eight
source frames must be decoded. A preview timestamp scan adds another decode, and older recipes also
need a source timestamp scan. These repeated traversals are concrete opportunities to reuse work.

## Recovery policy

The production guard operates before the expensive render. It uses the actual source samples and
versioned v3 math to check the existing declared clipping limits, then confirms a supported candidate
with an encoded full-resolution sample probe. Level-only correction moves an input black cutoff toward
zero or an input white cutoff toward one. Palette, saturation, gamma, brightness gain, intent and the
declared limits remain unchanged. It does not normalize a dark scene to a universal brightness target.

The original recipe remains frozen. Any supported adjustment is recorded in a separate effective
recipe / candidate with the measured reason and parent reference. If bounded correction cannot pass
the encoded probe, execution stops before a full render. The full rendered-output QC remains required;
the probe is sparse and does not prove every frame will meet a bound. No fallback silently changes a
localized plan into a global grade, invents masks or removes a preservation constraint.

## Performance verification policy

The reference and optimized QC paths must encode the **same effective recipe** with the same source,
geometry, pixel formats, timing policy and codec quality settings. A faster run using a different
black cutoff cannot establish quality equivalence to the rejected old plan; that change is an explicit
repair, with its own measurements. Reference/optimized comparison is performed after choosing that
same repaired recipe.

Actual timings are exported under each run's `performance` result. Single-pair comparisons are affected
by filesystem cache, temperature, background load and order; the runner labels them accordingly.
No statistical speedup or aesthetic quality claim follows from one pair. Dense decode/timing/audio
validation and final clipping checks remain enabled. Fewer redundant traversals are not fewer checks.

## Actual recovered-clip check

One isolated optimized replay of the saved 4K source passed all existing render checks, with zero Meta
calls and unchanged original source/project hashes. The corrected input black cutoff was
**0.02384765625**, reduced from **0.04**. Gamma, contrast, saturation, colour balance, brightness and
the **0.03** black-fraction limit remained unchanged. Final measured new-black fraction was
**0.028564453125** (2.8564 percentage points). All **741 frames** were retained with **zero master
PTS drift**, unchanged audio timing and the original geometry. This is technical acceptance, not
creative approval or evidence of boat-specific masking.

This first replay took **235.16 s including 23.25 s of uncached preflight**. Within rendering,
encoding took **159.37 s**, strict decode/timing **24.11 s**, and the separate sample traversal
**24.52 s**. The historical failed retry took **287.85 s**, but its black cutoff, thermal state and
background load differ; this is an observed total reduction, not a controlled full-clip encode
speedup. In particular the full encode was not faster in this measurement. A separate controlled
two-second comparison measured the filter-thread change; see [performance details](RENDER_PERFORMANCE.md).

Local evidence: `outputs/render-recovery/20261101T204359Z-df7e06ea/report.json`, the effective recipe,
preflight report, master/VP9 outputs and a source/result comparison at the previously failing frame.
These media and reports remain ignored by Git. Full-duration same-recipe reference/optimized pixel
comparison is supplied below for optional follow-up testing.

A final QC consolidation was then verified **on that same completed output**, without another full
encode. Full video/audio decoding, all PTS/duration checks and the same eight RGB measurements ran in
**24.68 s**, with sample records **exactly equal** to the separate extraction path. This replaces the
24.11 s decode plus 24.52 s sample traversal in the first replay. The original failed retry spent
77.33 s in QC. The final combined-QC result is in `combined-qc-validation.json`. A new end-to-end time
with this last consolidation has not been measured; do not subtract phase timings and call that an
observed complete run.

Basic verification included 25 focused preflight/service/performance tests, four additional combined
QC/pixel comparisons, 28 frontend tests, production build and compilation/diff checks. The remaining
full regression and two-render/full-duration pixel comparisons are provided as scripts below rather
than being repeatedly run during development. All development checks were offline.

## Commands for the remaining checks

The runner never constructs the application service or a Muse/SAM provider. It makes **zero Meta API
calls**. Real media is opt-in and all generated artifacts go to a new ignored output directory; it
does not change the saved project, original source or old candidate.

Inspect the saved failure and verify its source identity without rendering:

    .venv/bin/python scripts/verify_render_recovery.py --project 858b0f12-da67-4686-a349-e10a4764736b

Run focused synthetic preflight/performance/timestamp and SAM bypass/cache checks:

    .venv/bin/python scripts/verify_render_recovery.py --quick --max-seconds 300

Replay the saved global plan through the shared preflight guard and optimized renderer:

    .venv/bin/python scripts/verify_render_recovery.py --real --project 858b0f12-da67-4686-a349-e10a4764736b

For a controlled recipe comparison, run both QC modes and compare every decoded master/preview frame
hash plus decoded audio:

    .venv/bin/python scripts/verify_render_recovery.py --real --compare --max-seconds 1800 --project 858b0f12-da67-4686-a349-e10a4764736b

The last command performs **two full renders** and additional full decodes. It is intentionally not an
ordinary unit test. Inspect `report.json`, `effective-recipe.json`, `preflight.json` and each mode's
validation report in the printed output directory. A failure preserves its artifacts and returns a
nonzero status. Without an explicit project, the runner chooses the latest saved clipping failure.
The isolated real-media runner currently targets the global v3 path involved in this RCA; localized
SAM provenance should be tested through the application, with synthetic coverage included in `--quick`.

For the full offline regression suite when desired:

    .venv/bin/python -m pytest -q tests takeone_rubric_v1
    npm --prefix frontend test
    npm --prefix frontend run build

Final moving-video review remains a human decision. This failure and its repair do not establish
boat-specific masking, semantic subject preservation or professional cinematic quality.
