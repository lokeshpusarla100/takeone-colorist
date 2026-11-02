# Highlight feasibility gate

## Why it exists

A monsoon run asked for a dark, subdued sky. Muse lowered `white_point` from 0.92 to 0.85 over three
revisions "to subdue the bright sky", and every revision made the sky brighter (frame p95 luma
0.839 → 0.909 → 0.926 → 0.939 while the median fell). The v3 operator
(`color_processing.apply_rgb`) maps source luma at or above `white_point` to pure white *before* tone
and colour, so lowering it brightens highlights. Contrast above 1 lifts upper tones too, and
`gamma`/`brightness_gain` both keep white at white. No supported control makes near-white pixels dark:
a 0.90 highlight reaches about 0.74 at best (contrast 0.75) or 0.88 (contrast 1.25).

`tests/test_tone_semantics.py` pins these facts. `provider.CONTROL_SEMANTICS` and
`provider.HIGHLIGHT_HANDLING` tell the planner and critic the same thing.

## What the gate does

`render_preflight.prepare` runs the existing clipping fit, then `assess_highlights` on the bounded
source samples (the same 256 px frames the clipping preflight uses; no encode, no SAM):

* A layer **darkens** when its mean sampled median luma falls by ≥ 0.03.
* A darkening layer **violates** when its mean sampled p95 luma rises by > 0.03.
* Brightening layers (for example the subject) are exempt; the base, foreground and background layers
  of a localized plan are all assessed.
* `intent.highlight_policy` of `intentional_lift` or `intentional_clipping` opts out. `review` (the
  default) and `preserve_detail` are enforced.

A violation blocks the plan (`GRADE_PREFLIGHT_FAILED`, `blocked_by=highlight_lift`). The report names
the measured change per layer and which controls drive it (`white_point`, `contrast`, `black_point`,
by how much neutralizing each would remove). Nothing is adjusted silently.

`Service.guard_grade` then makes one Muse replan with that evidence. If Muse reports the request as
`unfulfilled_requests`, the job fails with that text in `error.details.unfulfilled_instructions`
and no render starts. A replan that still violates fails rather than looping.

The gate also runs when no clipping fraction was declared, and on critic revisions: a revision that
would lift highlights is not rendered (`revision_stop_reason=revision_would_lift_highlights`, event
"Revision not rendered") and the candidate it came from is kept.

`new_channel_clip_fraction` is reported per layer but never blocks: a cooled pale sky saturates its
blue/green channel while luma stays under the 254/255 white test, which is why the luma-based
`max_new_white_fraction` bound did not notice the cyan-white sky.

## Limits

* Whole-frame statistics on sampled frames, not per-region pixels: a sky mask would be more exact.
* The neutral-level recovery after a dense clipping failure skips the gate so recovery stays possible.
* The thresholds (0.03 / 0.03) were chosen against this run's measurements (background lift +0.077 to
  +0.107 with median −0.07 to −0.12); revisit them with more footage.
