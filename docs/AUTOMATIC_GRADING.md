# Automatic global and localized grading

Version note: this document records the v2 SAM compositor integration. [Phase 2](PHASE2_ADAPTIVE_INTELLIGENCE.md) supersedes new-recipe sampling, colour processing and review/QC policy with v3. The source mapping, raw-mask cache and compositor described here remain in use; old recipes retain their versioned interpretation.

## Root cause and implemented path

Previously Muse was instructed to emit only three global controls, GradeProposal contained no local plan, and Service.render_grade always called the global renderer. Manual SAM artifacts were diagnostic outputs with no path into the grading filter graph.

Generation now uses the same Muse Spark 1.3 call to choose mode=global or mode=localized from the frozen Creative Brief, original request, answers, literal latest feedback, measured encoded-RGB/luma diagnostics, current tools and detected shot intervals. Selection is model-owned, not a keyword classifier or another validator agent. Current execution capabilities supersede historical tool-availability notes in old briefs; user constraints are never superseded that way.

A global plan calls no SAM. A localized plan names one concrete concept, selection policy, necessary intervals, absolute foreground/background controls, preservation flag and feather width. Base controls apply outside those intervals; local layers are not compounded on the base. Unsupported required instructions are reported in unfulfilled_requests and stop rendering. The full plan is retained in plan.json.

The existing Creative-Direction Rubric remains intact. Cross-project memory still passes through PromptMemoryGate and is disabled by default. Explicit current-video feedback remains active. No new agent framework, database, model or dependency was introduced.

## Automatic mask acquisition and reuse

1. Verify original hash, source/proxy geometry and CFR timestamp mapping.
2. Detect cuts using FFmpeg scdet (threshold 10). Split requested intervals at detected cuts.
3. Reuse a completed project-local, same-source, same-concept, same-model/parser/encoding mask cache covering the required frames. Raw mask refinement can be recomputed without another API call. Original v1 diagnostic artifacts use their pinned 0.1.0 parser compatibility contract.
4. Otherwise create a SegmentationRun attached to the candidate and use the existing streamed SAM provider, saved event receipts, local decoder and mask persistence. Only required frame intervals are encoded/uploaded. Explicit first/last source indices avoid fractional-time boundary drift.
5. Require every active frame to have valid masks. single_instance requires one object ID within each shot interval; all_matches must be explicitly planned. Missing, empty, malformed, ambiguous or unstable data stops the candidate. No held mask, invented absence mask or global fallback is produced.

Finished streams retry locally after interruption. A failed incomplete stream needs an explicit remote retry. Recipe retries reuse the saved plan; valid mask caches avoid repeated segmentation. Source, brief and prior candidates remain preserved. Manual SAM diagnostics remain available.

## Matte refinement and FFmpeg compositing

The proxy crop masks are decoded through the official parser, placed using half-open bounds, and mapped to original dimensions. Each frame supplies its own geometry. Spatial Gaussian feathering is bounded to current-mask support. Within a detected shot, verified optical flow can contribute 20% motion-compensated alpha smoothing in the current boundary band. It never fills a missing frame and resets at cuts, gaps and ID changes.

A lossless FFV1 gray16 matte is encoded with one frame per original frame. Matroska millisecond timestamps are checked and then rebased to the exact verified CFR rational time base by frame index. FFmpeg applies separate RGB treatments and uses maskedmerge to choose between background and foreground pixels. Frame-index activation selects only planned intervals. A short bounded blend at within-shot interval edges avoids abrupt changes; no cross-cut smoothing is used. Neutral base pixels apply elsewhere.

Current support is one segmentation concept with foreground/background layers, up to twelve requested intervals, split at detected cuts. It supports upright, known limited-range 8-bit SDR/Rec.709 sources with verified CFR mapping; a nonzero container start is handled by first-decoded-frame PTS normalization with absolute PTS retained per frame, never by blindly resetting timestamps. VFR, rotation metadata, HDR/log/unknown colour interpretation, physical relighting, reconstruction, multiple independently graded concepts and shot matching remain unsupported.

## Safer tonal and colour controls

New recipes use takeone-grade-v2 / colorist-v2 and tone_operator=bounded_sigmoid_v2. Old recipes default to legacy_fixed_pivot, so retrying an old recipe does not silently reinterpret it.

The old operation multiplied RGB around 0.5 and clipped negative results. Contrast 1.12 sent values below roughly 0.054 to zero before later operations. The new contrast curve uses x^c / (x^c + (1-x)^c), with bounded input and positive denominators. Gamma precedes contrast; a rational bounded brightness gain follows it, then saturation and explicit RGB cooling. Source samples now include low/high luma quantiles, encoded-black fraction and median RGB as well as earlier diagnostics. These are display-domain measurements, not calibrated scene exposure.

Cooling is a bounded red/blue gain with Rec.709-weighted normalization, not a Kelvin white-balance estimate. Foreground colour protection requires neutral contrast/gamma/saturation/cooling; a common RGB brightness gain is allowed with peak limiting to retain channel proportions and light colours. A sky/region being changed can instead use preserve_subject_color=false while the other layer stays neutral. Physical illumination is not reconstructed.

## Validation and truthful failure

- Original duration, dimensions, frame count and actual per-frame normalized timestamps must match.
- Mask bounds, source identity, required-frame coverage and object selection must be valid before rendering.
- Low motion-compensated overlap is recorded as a diagnostic, not confidence. Three consecutive flagged frames or more than 10% (with a two-frame allowance) stop the candidate. Detected-cut boundaries reset refinement. Cut detection and flow are heuristics and can miss difficult transitions.
- Protected mask interiors are checked across active frames for new encoded blacks, luma loss, chroma change and highlight chroma change. A neutral unmasked layer receives analogous checks. Measurements use eroded mask cores and quantization tolerance, not semantic material recognition.
- Excessive background near-black increases are warned about. A localized result with no measured change beyond encoding noise is rejected.
- Encoder/decode failures, invalid timing, missing masks and failed protection checks leave the candidate failed and unavailable through ready-preview/download routes. Completed source/mask/plan artifacts remain for explicit retry.

Passing produces review_status=needs_user_review. It does not establish correct semantic masks, perfect paint/light preservation, invisible edges, absence of all flicker or an aesthetically successful look. Inspect the moving result before acceptance.

## UI and code map

Generate grade shows planning → automatic SAM tracking → mask validation → localized render → output checks → candidate ready. Persistent events and existing usage accounting report tool work. Candidate details show mode, subject/background controls, intervals and mask-run references. The manual diagnostic panel remains available. A previous selected version is labelled as earlier when the newest attempt fails.

- takeone/contracts.py: versioned recipes, bounded controls, local plan/intervals and mask references.
- takeone/provider.py: capability-aware Muse planning with brief/feedback/memory priority.
- takeone/service.py: automatic orchestration, cache acquisition, retry and publication.
- takeone/sam.py: existing stream/cache/mapping primitives with explicit processing configuration.
- takeone/local_grading.py: timeline/cuts, frame-bound masks, refinement, matte and preservation QA.
- takeone/rendering.py: bounded transforms and FFmpeg masked compositing.
- frontend/src/components/RenderPanel.tsx and progress/API code: visible execution and review.

## Verification and actual-footage boundary

The automated suite uses mocked providers and actual synthetic FFmpeg footage. It covers global bypass, automatic local activation, localized pixel differences and foreground/background separation, incomplete/invalid masks, ambiguity/instability, scoped intervals, cut-separated tracking, frame timing, cache reuse, newest feedback, legacy recipe interpretation and actionable SAM/FFmpeg failures. Ordinary tests make no Meta API calls.

Final verification passed 103 Python tests, 24 frontend tests, seven offline automatic-workflow browser checks and the production build. A separate local browser playback check covered the actual-footage validation interval, cut, fade and neutral remainder.

A separate live-planner validation used an isolated copy of the latest motorcycle brief, including the feedback asking for dark/cold surroundings with the bike as the main subject. The validation explicitly limited local effects to 0–5 seconds and kept the rest neutral. Muse chose localized mode, cited that feedback, kept the motorcycle neutral and chose a darker/cooler background. Existing completed first-five-second masks were reused with zero new SAM calls.

The original-length validation output retains 1,197 frames and 39.9 seconds. All 150 active subject frames passed measured core checks; refinement reset at the detected cut at frame 77. The output was inspected at sampled timestamps and played in Chrome through the treated interval, cut, fade and neutral remainder. The original source, real project brief and previous render files were not modified. This demonstrates real localized pixels on actual footage, not a diagnostic overlay.

This is a bounded demonstration, not a full motorcycle-video fix. The revised full-video automatic SAM path, every later cut/occlusion, live sky segmentation, semantic boundary accuracy and aesthetic acceptance remain unverified. The prior incomplete full-clip SAM attempt remains failed. No full-clip success is claimed from the five-second cache.

Local validation artifacts are under outputs/local-grading-validation/ and ignored by Git. Run the offline regression checks with:

    .venv/bin/python -m pytest -q tests takeone_rubric_v1
    npm --prefix frontend test
    npm --prefix frontend run build
