# Phase 2 — adaptive evidence, creative review and reliable execution

## Baseline and scope

Inspected commit 8e20d01 and ran the existing suite before edits: 103 Python tests, 24 frontend tests and the production build passed. The existing automatic SAM → lossless matte → FFmpeg masked-merge path worked and was retained.

Concrete gaps were four fixed samples, no rendered-evidence critic, fixed look-related QC thresholds and feedback from an old direction carrying into a new request. This change targets those gaps; it does not replace the editor, rubric, SAM provider, access controls, storage or worker infrastructure.

## Separate intent, execution and evidence

- CreativeIntent stores free visual intent plus shadow/highlight policy. There is no cinematic-style catalog.
- GradeProposal retains bounded executable global/local controls, preservation flags, declared execution limits and whether substantive creative review is required.
- GradeRecipe v3 binds source/brief identity, versioned math and reproducible evidence selection. Earlier v1/v2 recipes retain their operators.
- Technical validation reports measurements and uncertainty, not an aesthetic score.
- CreativeReview contains Muse's evidence-linked opinion, correctability, optional typed revision and preference between actually shown candidates. It never approves the result for the user.

Muse chooses the intended atmosphere, emphasis, local/global mode and supported revisions. Tools execute typed numeric operations. Shell/filter commands are never accepted from the model.

## Adaptive, bounded visual evidence

takeone/evidence.py uses actual source frame indices/timestamps. A bounded proxy scan measures luma distributions, endpoint populations, RGB medians, local contrast and temporal RGB changes. Selection includes distribution extremes, detected-shot representatives, relevant typed local intervals and temporal coverage. Rendered evidence can add measured output changes. Missed/uncovered shots and sparse temporal uncertainty are recorded.

No Muse call is made for each scanned frame. Dense SAM/flow processing remains restricted to required localized intervals. Source evidence is cached by source/proxy identity, processing version, scope and budget; unchanged frames are not reanalysed on each iteration. Rendered evidence is hash-bound to the actual candidate. Source/candidate images use corresponding frame indices, including paired subject-region crops when masks are available. Previous-candidate comparisons also verify timing and identity.

Defaults and environment controls:

| Setting | Default / scope |
| --- | --- |
| TAKEONE_SAMPLE_SCAN_FRAMES | 96 analyzed frames, clamped 8–512 |
| TAKEONE_SAMPLE_COUNT | 8 selected images per role before payload-budget omissions, clamped 2–12 |
| TAKEONE_SAMPLE_WIDTH | 160-pixel maximum scan dimension, clamped 64–256 |
| TAKEONE_SAMPLE_MEMORY_MB | 16 MiB raw evidence-buffer budget, clamped 1–64; not a total-process RSS promise |
| TAKEONE_SAMPLE_IMAGE_KB | 1,024 KiB total review image payload budget, shared among context/region/previous roles |
| TAKEONE_MAX_RENDER_ITERATIONS | 3, clamped 1–3 |

Frame metadata is limited to 15,000 frames. Decode operations have explicit deadlines and cancellation. JPEG evidence is bounded, compressed and private to workspace artifacts; session exports carry measurements/references rather than raw image payloads.

## Rendering mathematics

New v3 recipes use luminance_ocio_v3. Tone acts on normalized encoded Rec.709 luma, with explicit input black/white points, gamma, the existing bounded monotonic contrast curve and bounded brightness. A common RGB gain plus peak protection retains channel proportions for tonal changes. Colour-protected subjects can therefore receive tonal adjustment without being forced to neutral gamma/contrast; saturation and cooling remain neutral for them.

Saturation and red/blue balance use the installed [OpenColorIO ASC CDL implementation](https://opencolorio.readthedocs.io/en/latest/guides/authoring/authoring.html#cdltransform), evaluated through its CPU processor and baked into a bounded 65³ LUT. FFmpeg applies tetrahedral LUT interpolation before the existing localized compositor/output transform. LUT identities include parameters, OCIO/NumPy versions and processing version; hash receipts detect derived-file corruption. Tests compare the colour operation against OCIO reference output. This uses the current encoded SDR domain; it is not calibrated exposure, scene-linear relighting, Kelvin estimation, HDR conversion or an ACES pipeline.

Black/white points deliberately map endpoints and may discard information when explicitly chosen. There is no automatic histogram normalization or shadow brightening to pass QC. Intentional black regions are valid when the plan permits them. New measurements quantify source-defined shadow/highlight separation, endpoint changes, local contrast, RGB distribution changes and sampled temporal differences.

Old fixed .05/.85/.035 preservation heuristics and .01 visible-change rejection remain only for legacy recipe interpretation. New v3 execution enforces declared bounds, reports sub-resolution changes, and leaves creative suitability to the critic/human. Missing masks, wrong geometry, changed files, corrupt decode, unsupported colour representation and wrong timing remain hard failures.

## SAM and mask reliability

Existing source hashes, CFR/geometry verification, exact source/clip frame mapping, saved-stream receipts, mask persistence and cache configuration checks are preserved. No stale or invented mask fills a missing frame. Global plans still bypass SAM. Local iterations reuse compatible raw masks and rebuild the matte only as needed.

For v3, temporal mask assessment compares motion-compensated disagreement against a current-mask boundary allowance. Registration quality is evaluated against content-scaled photometric tolerance. Uncertain flow is reported and is not used for smoothing; it is not claimed as model confidence. Verified persistent mask inconsistency still stops execution. Cut/gap/ID resets and current-frame geometry remain authoritative. Sparse critic samples do not replace dense mapping and mask-integrity checks.

## Review and revision lifecycle

After technical QC, a substantive plan's review_required flag triggers a bounded Muse Spark 1.3 critic. Users can also explicitly Review this candidate. The critic receives the current brief, current corrections, actual source/rendered frames, paired region views, measured diagnostics, current capabilities and any available previous candidate images. It must cite only the evidence/candidate IDs it was shown.

A correctable, evidence-supported revise decision can directly supply the next typed plan, avoiding a separate replanning call. The next candidate renders from the original, never the previous compressed render. It gets a new ID, parent reference, plan/recipe artifacts and iteration number. Prior candidates remain unchanged. The critic re-evaluates each automatic revision.

Execution fingerprints include the operator and effective controls; text-only or base-only changes under full local coverage do not trigger another render. Automatic revisions cannot silently abandon an existing protected-local treatment. Unsupported corrections, non-correctable issues, repeated plans, a preferred earlier candidate or the render limit stop the loop for human judgement.

The default limit is three render attempts per brief/chain, including failed attempts. Review requests are cached by unchanged brief and output identity; at most two network attempts per candidate/current brief are allowed. An API review error retains the technically valid video. Technical evidence corruption fails before any critic call. Cancellation retains completed work and stops automatic revisions; an explicit retry can recover a cached review without repeating inference or rendering the same candidate. Final aesthetic approval remains with the user.

## Instruction precedence and memory

A newly submitted direction starts a request epoch. Older feedback remains raw evidence/history but is excluded from active brief/review feedback, fixing an old precedence defect. Current explicit direction and current corrections override earlier project choices; applicable memories are advisory below those instructions. Model defaults are last.

Capture/application settings remain independent. Personalization stays disabled by default and the central gate is enforced at the critic prompt boundary as well as rubric/planner boundaries. Model critiques, automatic revisions and recommendations are not captured as user aesthetic preferences. No per-interaction memory-model call was added.

## API and UI

POST /api/projects/{id}/review takes command_id, revision and candidate_id. It reviews an existing completed candidate without needlessly rerendering it. The existing Generate action handles automatic reviews/revisions. Status reviewing participates in processing locks and cancellation. Progress and usage show critic requests; candidate details show qualified opinions, evidence references, lineage and stop reasons. An older preferred/selected version remains available for comparison.

Local-host/origin restrictions, UUID paths, profile isolation, optimistic revisions, command receipts, atomic writes, job locks, source identity and actual provider usage remain deterministic. This remains a localhost application, not a newly authenticated multi-user service.

## Verification and real-footage boundary

Ordinary tests use synthetic media, real FFmpeg/OCIO operations and mocked providers. They test dark footage without forced brightening, legitimate intentional blacks, declared clipping bounds, adaptive real lighting/cut coverage, SAM bypass/cache reuse, corrupt-media/mask rejection, actual critic evidence, feedback epochs, immutable revisions, three-render limits, worse-version selection, duplicate suppression, cancellation and capture-only prompt isolation.

Final checks: `pytest -q tests takeone_rubric_v1` passed **125 tests in 122.61 seconds**. Frontend tests passed **24 tests**, and TypeScript checking/production build passed. Python compilation and `git diff --check` passed. Seven offline browser checks covered processing locks, immutable revision choices, qualified opinions, cached review controls, review/usage events, mobile width and unhandled errors; a separate real-footage preview playback check reached eight seconds without a decoder error. Browser fixtures used mocked providers. The original motorcycle source hash and stored brief were verified unchanged. The development backend was restarted with these changes and its health endpoint passed.

The existing motorcycle footage was available. An isolated **offline replay** reused a prior bounded plan and completed first-five-second SAM masks. It made zero new Meta API calls. Local effects remain limited to 0–5 seconds, with the remainder neutral; all 1,197 frames and 39.9 seconds were retained. Sampled source/output frames were inspected. This is not a new live Muse critique or full-video creative acceptance.

One local coverage measurement found 8/8 detected shots represented by eight adaptive samples, compared with 3/8 for the previous four timestamps. The scan analyzed 96 proxy frames. First scan including timeline work took about 11.1 seconds; a cached selection lookup took about 3.4 ms. The isolated full-length bounded replay took about 112.6 seconds. These are individual development measurements, not end-to-end speedup benchmarks. Lookup time excludes image extraction/rendering. No API-cost reduction or professional-quality improvement is claimed.

Live tests require explicit opt-in:

    .venv/bin/python -m takeone.verify --phase2 --live

That verifier uses generated footage only, temporary preferences/workspace, at most two renders, two critic calls and five total inference calls, with SAM forbidden. It can incur usage and was not run as part of ordinary verification. New live critic compliance, full motorcycle acceptance, difficult occlusions/cuts and semantic paint/boundary fidelity remain unverified. Human moving-video review is still required.

## Files

New modules: evidence.py (bounded mathematical evidence), color_processing.py (versioned luminance/CDL LUTs), creative_review.py (paired/region evidence and execution identity). Targeted changes connect them through contracts.py, rendering.py, service.py, provider.py, config.py, media.py, app.py, preferences.py, verify.py, rubric contracts/prompts and existing frontend controls. tests/test_phase2.py extends the same test framework; no new dependency/database/framework is required.
