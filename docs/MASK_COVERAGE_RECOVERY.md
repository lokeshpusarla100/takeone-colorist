# Missing-mask coverage recovery

## Native instance tracking (current)

Recovery now uses the hosted SAM3.1 object-prompt API. Existing masks supply validated boxes and
interior positive points on three source frames, converted to excerpt-local indices by the official
`meta-sam-parser` builder. The request contains object prompts only (no noun phrase concatenation),
with `propagation_direction="both"` and `include_confidence="true"`. Both directions return anchor
context for validation and the missing prefix. Explicit frame markers handle backward output order.

New repair clips stay in original frame order. The earlier physical reverse/phrase-detection method
below is historical; old artifacts are retained but cannot satisfy a new native prompt cache lookup.
The two-request budget includes earlier repairs, so the saved boat run has one remaining native
attempt. Completed response receipts bind the input bytes, prompt and direction. A changed seed or
direction cannot replay another result. Concept-only requests retain their existing cache contract.

The response must preserve the prompted object ID. Existing three-anchor/spatial-agreement checks,
full coverage, geometry, native timing and final rendering QC remain enabled. Confidence is available
as model output, not treated as objective accuracy. No new framework, model, CUDA dependency or UI
workflow was introduced. No live SAM call was made during implementation.

For the saved boat, a dry run builds seeds at source frames245,252,259 (excerpt frames184,191,198),
on the original-order199-frame clip61–259. The new request tracks natively toward earlier frames,
rather than asking the detector to rediscover the boat in reversed media. Existing forward masks are
preserved. Live success/semantic quality still requires checking the actual result.

Official references: [Meta prompting and propagation](https://dev.meta.ai/docs/sam/segmenting),
[frame/object output semantics](https://dev.meta.ai/docs/sam/reading-segmentation).

Focused verification: 24 provider/parser/recovery tests passed, plus a legacy-repair migration check, including concept-path compatibility,
out-of-order native propagation, wrong-ID/subject rejection, cache reuse and native VFR/jitter timing.

## Historical recovery and incident

The saved boat render requested source frames 61–740, but SAM's completed response contained masks
only for excerpt frames184–679 (source245–740). Source61–244 remained uncovered even though sampled
frames showed the moving boat. The raw provider deltas began at184; the parser did not discard the
prefix. Full encoding correctly stopped before applying unverified selective grading.

The renderer now collects partial masks without removing the final completeness requirement. For a
single-instance track, it can recover missing spans with up to15 existing overlap frames. Prefixes
are tracked in reverse, beginning on the already-visible subject; other gaps use preceding overlap.
Reverse proxy order has an explicit ordinal-to-original-frame map, including native PTS. Original
video, audio, plans and masks are never retimed or overwritten.

Recovery requires at least3 overlap frames. At least80% of planned overlap frames (and at least3)
must have mask IoU≥0.7 against the original track. These are conservative spatial association gates,
not aesthetic scores or semantic certainty. Multiple IDs, disagreement, missing anchors or remaining
gaps stop execution. Verified repair IDs are aliased explicitly to the original track ID; raw model
IDs and mask files remain unchanged. Only missing source frames receive new returned masks.

Budgets: at most2 new repair requests per original track and per candidate, each covering at most300
proxy frames including overlap. Existing repair artifacts are reused and reverified. An already-started
repair request without a completed response is not automatically reissued on retry. Cache lookup
cannot treat a recovery run as an independent full-range track, bypassing its overlap check. Existing
matte geometry, cut resets, temporal stability and full output checks remain enabled.

For the saved boat case the first proposed recovery spans source61–259 (199 frames), reversed from
the visible overlap259–245 back through the missing244–61 prefix. This avoids repeating the full680-frame
request. It has not been called against live Meta during development. Retry in the editor may incur
this bounded additional SAM usage; successful coverage is not guaranteed by synthetic tests.

Verification: synthetic parser/FFmpeg tests cover successful recovery, immutable original masks,
verified-cache reuse, wrong-subject overlap rejection, no-anchor cases, still-missing responses,
network-failure retry limits, and moving subjects with genuine VFR and near-CFR jitter. Native output
timestamps and moving-edge pixel protection are checked. Forty frontend tests and build passed.

    .venv/bin/python -m pytest -q tests/test_mask_recovery.py tests/test_local_grading.py tests/test_sam.py tests/test_native_mask_timing.py
