# Highlight feasibility gate

## Why it exists

A monsoon run asked for a dark, subdued sky. Muse lowered `white_point` from 0.92 to 0.85 over three
revisions "to subdue the bright sky", and every revision made the sky brighter (frame p95 luma
0.839 → 0.909 → 0.926 → 0.939 while the median fell). The v3 operator
(`color_processing.apply_rgb`) maps source luma at or above `white_point` to pure white *before* tone
and colour, so lowering it brightens highlights. Contrast above 1 lifts upper tones too, and
`gamma`/`brightness_gain` both keep white at white. Before `highlight_ceiling` existed, no control made
near-white pixels dark: a 0.90 highlight reached about 0.74 at best (contrast 0.75) or 0.88 (contrast 1.25).

## The highlight control: `highlight_ceiling`

`GradeParameters.highlight_ceiling` (0.6–1.0, neutral 1.0) is a smooth output shoulder applied after
tone and before peak limiting / CDL colour (`color_processing.apply_rgb`):

    tone = tone - (1 - c) * tone**2

* White lands exactly on `c`; a 0.80 tone drops by 0.64·(1−c), a 0.50 tone by 0.25·(1−c), and
  shadows under 0.2 move by at most 0.016.
* Strictly increasing, with slope 2c−1 ≥ 0.2 at white, so bright texture is compressed, never inverted
  or clipped. That bound is why 0.6 is the minimum.
* Luminance-only, so it is allowed on colour-protected subjects (channel ratios are kept).
* Bringing a pale sky down before CDL cooling stops the blue/green channel saturating: a cooled 0.9 grey
  becomes steel blue instead of cyan-white.
* With contrast 1.25, gamma 0.8, gain 0.65 and ceiling 0.6, a 0.90 highlight reaches about 0.57.
* At 1.0 it is an exact no-op: existing v3 recipes render bit-for-bit as before
  (`test_neutral_ceiling_reproduces_every_existing_v3_recipe_bit_for_bit`). The LUT identity version is
  4, so LUT caches regenerate once. Legacy operators reject any ceiling below 1.
* A global shoulder also lowers upper midtones and a bright subject, so a strong ceiling belongs on the
  sky/surroundings layer of a localized plan when the subject must stay bright.

On a real frame of the monsoon footage, ceiling 0.6 on a cooling/darkening layer took the sky from 0.82
to 0.54 luma (RGB ≈ 0.48/0.55/0.64) while p05 shadows were unchanged and the sky kept 42% of its tonal
spread.

`tests/test_tone_semantics.py` pins these facts. `provider.CONTROL_SEMANTICS` and
`provider.HIGHLIGHT_HANDLING` tell the planner and critic the same thing.

## What the gate does

`render_preflight.prepare` runs the existing clipping fit, then `assess_highlights` on the bounded
source samples (the same 256 px frames the clipping preflight uses; no encode, no SAM). Nothing in it is
fitted to a particular video; every constant is a property of display-encoded Rec.709 or of perception,
and every measurement uses the footage's own pixels:

| Constant | Value | Meaning |
|---|---|---|
| `VISIBLE_CHANGE` | 0.02 | Smallest tone shift treated as visible (~5 8-bit codes, about one just-noticeable step). |
| `MID_GREY` | 0.409 | Photographic 18% grey, Rec.709-encoded. A layer **darkens** when it lowers this by ≥ `VISIBLE_CHANGE`. This is a property of the plan, so bright footage (beach sand filling the "midtones") cannot hide a darkening grade. |
| `HIGHLIGHT_QUANTILE` / `HIGHLIGHT_FLOOR` | 0.9 / 0.6 | Highlights are each frame's brightest 10%, and only pixels ≥ 0.6 encoded. A night frame contributes only its real lights; a beach its brightest sand and sky. |
| `MIN_HIGHLIGHT_FRACTION` | 0.5% | Frames with fewer highlight pixels are not assessed. |
| `WHITE_POINT_SHARE` | 0.5 | Without `subdue`, a lift is blocked only when the lowered `white_point` causes at least half of it. |

The highlight change is **pixel-paired**: the same source pixels before and after, not a quantile of
the output. A whole-frame p95 (the first version) missed a beach (p95 already near white) and night
city lights (under 5% of the frame).

Rules, for darkening layers only (a brightening grade such as "make the beach brighter", or a lifted
subject, is never blocked here; clipping has its own bound):

* `subdue`: blocked whenever highlights visibly brighten, by any control.
* `preserve_detail` / `review` (default): blocked when the lift comes mainly from a lowered
  `white_point`, which flattens and brightens highlights on any footage. A contrast-only S-curve lift is
  a legitimate look and passes.
* `intentional_lift` / `intentional_clipping`: never blocked.

`tests/test_render_preflight.py` pins this matrix across beach, city day, city night and hazy-sky scene
classes, and replaying every saved workspace project gives the same verdicts as the monsoon analysis.

A violation blocks the plan (`GRADE_PREFLIGHT_FAILED`, `blocked_by=highlight_lift`). The report names
the measured change per layer and which controls drive it (`white_point`, `contrast`, `black_point`,
by how much neutralizing each would remove), plus `suggested_highlight_ceiling`: the ceiling that only just
keeps the lift below visibility with the other controls unchanged. Nothing is adjusted silently; the
suggestion is evidence for the replan, because the ceiling is a creative control.

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

* Highlights are defined by brightness, not semantics: the gate knows "the brightest pixels", not
  "the sky". A sky mask (multi-region work) would be more exact.
* `subdue` and `intentional_lift` depend on Muse reading the brief correctly; without either, only the
  white_point mechanism is enforced.
* Sampled frames (up to 12) can miss a brief bright shot in a long, varied video.
* The neutral-level recovery after a dense clipping failure skips the gate so recovery stays possible.
