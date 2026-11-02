# Regions, revision convergence and mask checks

Follow-up to the monsoon RCA (see `HIGHLIGHT_GUARD.md`). Every threshold here is the shared,
footage-independent `tonal.VISIBLE_CHANGE` (0.02 encoded); nothing is fitted to one video.

## Critic evidence and the convergence rule (`convergence.py`)

The critic now receives, per sampled frame, `tonal_levels` for source, candidate and previous
candidate: p05/median/p95, median RGB, mean chroma and the brightest region's luma/RGB
(`tonal.highlight_mask`, the same definition as the highlight gate). `detail_retention` adds
pixel-paired before/after luma and RGB of the source's brightest region.

Each `CreativeIssue` may carry a `target` (`darker_highlights`, `cooler`, `deeper_shadows`,
`more_contrast`, ...; null for things like subject separation). Progress is measured on the renderer's
own sampled output frames, which every candidate of a brief shares:

* **Ineffective revision.** After a revision renders, its targeted issues are compared with its nearest
  reviewed ancestor. If none moved visibly in the asked-for direction, the loop stops with
  `revision_stop_reason=revision_ineffective` *before* another critic call: the chosen operation is not
  fixing the issue, so escalating it again is pointless. The measurement is saved in
  `candidate.revision_progress`.
* **Final choice by measurement.** When the loop stops with unresolved targeted issues and the critic
  named no preferred candidate, the candidate with the fewest visible wrong-way moves against the source,
  then the smallest worst one, then the most progress, is selected (event "Candidate selected by
  measured fit"), instead of the last render.

On the real monsoon session, labelling the critic's issues `darker_highlights` and `cooler`: V3 is
ineffective against V2, and V1 ranks best (its sky was pushed least past the source).

## Semantic mask check (`creative_review.mask_overlays`)

For every SAM concept a candidate used, up to three sampled frames are sent to the critic with the mask
tinted and outlined (`mask_overlay_<n>:<frame>`, with the concept name and coverage). The critic returns
`mask_assessments` (`matches` / `partial` / `wrong_object` / `not_evaluable`). `partial` or
`wrong_object` stops automatic revision with `revision_stop_reason=mask_needs_user_review` and an event
quoting the critic, because re-grading cannot fix a mask on the wrong object. Cited evidence IDs are
validated like all other review evidence.

## Multi-region localized plans (`LocalizedPlan.regions`)

`regions` (up to 4) adds SAM regions with their own `treatment`, e.g. sky, water, foliage and buildings
around a subject. All share the plan's intervals and feather.

* Precedence where masks overlap: subject, then regions in list order, then background.
* Each region uses the subject's mask path unchanged (cache reuse, coverage recovery, temporal QA) and
  gets its own validated lossless matte, `matte-region-<k>.mkv`, timed with the same native-PTS lookup.
* The FFmpeg graph merges regions over the faded background in reverse precedence order, then the
  subject on top (`rendering.filter_graph`).
* Every region layer goes through the clipping fit and the highlight gate (`region:<concept>`).
* `selection` defaults to `all_matches`, because regions are usually fragmented (sky between trees).
* Contract checks: distinct concepts (also from the subject), a region must differ from the background,
  single noun phrase, and v3 execution only.
* Look previews do not render regions (only the subject mask exists for the still); their description
  says so.

## Experiment: sky-only grade (`scripts/sky_only_experiment.py`)

Grades only the sky through the existing single-concept path (sky as the localized layer, everything
else neutral, `highlight_policy=subdue`). The default dry run is offline: it runs the real preflight on
the project's cached samples and predicts the sky change. On the monsoon project the gate passes and the
brightest region goes from about 0.85 to 0.57 luma, RGB ≈ (0.53, 0.58, 0.63). `--execute` renders a new
candidate; this runs SAM on "sky" (billed upload to Meta) and a full encode, so stop the backend first.
