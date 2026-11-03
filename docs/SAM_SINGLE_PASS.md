# Single-pass SAM tracking (optional, off by default)

## Why

A localized grade names several areas (the subject, sky, water, trees, building, ...). Each area is its own SAM request, and
each request is billed for **every frame it tracks**: five areas over a 741-frame clip is 5 x 741 = 3,705 frames, $0.741 at
Meta's $0.20 per 1,000 frames. On the measured clip that was 92% of the whole video's cost.

SAM also accepts *object prompts* (a box and points per object, under an id we choose; many objects per request; answered under
the same ids). One request can therefore track every area at once.

## What it does (`TAKEONE_SAM_SINGLE_PASS=1`)

1. **Detect.** Each area gets its ordinary concept request over only its first 50 frames (billed at least 50 frames). That gives
   the area's objects and masks, and an ambiguous subject is reported to the user exactly as before.
2. **Track.** One object-prompt request over the whole range, seeded with every detected object at the frame it first appears
   (a box, up to three positive points inside it, up to three negative points on the part of the box that is not the object).
3. **Check, per area.** The tracked masks must reproduce the area's detection masks (mean overlap at least
   `TAKEONE_SAM_SINGLE_PASS_GATE`, default 0.85; no frame the detection saw may be missing; few frames far off) and leave no
   frame without a mask. An area that fails is **not used**: it is requested the ordinary way over the whole range.
4. The result has the same shape as the one-request-per-area path (`masks_for` in `service.py`), so rendering, matte
   preparation and output checks are unchanged. Masks are cached by the seeded prompt, so grading the same brief again makes no
   new SAM request.

It is used only when it can save something: at least 3 areas sharing one uninterrupted range of at least 150 frames. Otherwise
(and whenever the setting is off) nothing changes. If the single request could not be sent, each area is requested the ordinary
way. If it **was** sent and then failed, it is not silently repeated as one request per area (it may already be billed); the render
reports the failure and the user retries explicitly, as with every other SAM failure.

## Measured (scripts/sam_single_pass_probe.py, one real 4K clip, 741-frame proxy, 5 areas)

One object-prompt request, seeded from the first 50 frames only, compared with the saved one-request-per-area masks over the
**whole clip**:

| Area | Objects | Mean overlap (IoU) | Recall | Frames without a mask |
|---|---|---|---|---|
| Passenger boat | 4 | 0.925 | 0.986 | 0 |
| Sky | 1 | 0.965 | 0.965 | 0 |
| Water | 1 | 0.760 | 0.973 | 0 |
| Trees | 8 | 0.853 | 0.973 | 0 |
| Building | 1 | 0.965 | 0.977 | 0 |

- 15 objects in one request; all answered under their ids; none missing, none unexpected.
- Billed once for the frames: `video_frames_processed = 741` (about $0.148), not once per object.
- Objects that first appear after the detection window (more tree pieces, a second building piece) were not seeded, and no frame
  lost its mask.
- **Water differs by meaning, not location.** A box-and-point seed means "the object in this box", so floating plants inside the
  water's box are included; today's text prompt "water" excludes them. Plain seeds scored 0.46 on 150 frames, richer seeds 0.71
  (0.76 on the full clip). It stays under the gate, so on this clip water falls back to its own request, which is the check
  working as designed.

## Cost and time on that clip

| | Frames billed | Cost | SAM wall time (estimated from the measured pieces) |
|---|---|---|---|
| Today: 5 requests | 3,705 | $0.741 | about 3 min (3 run in parallel) |
| Single pass, water falls back | 5 x 50 + 741 + 741 = 1,732 | about $0.35 | about 6 to 7 min |
| Single pass, nothing falls back | 5 x 50 + 741 = 991 | about $0.20 | about 5 to 6 min |

**It is cheaper but slower.** The one 741-frame request took 326 s: about 165 s of SAM streaming and 161 s decoding 11,041 masks
locally. Today's three parallel runs finished in about 174 s. Choose it when cost matters more than the extra minutes.

## Not yet measured

- Footage with shot cuts (single-pass needs one uninterrupted range and is skipped otherwise), several subjects entering late,
  or very different areas. One clip, five areas, is a small sample; keep it off until more footage agrees.
- Whether Meta's invoice matches the usage it reports (the cost card uses the reported frames).
- Reproducing: `python scripts/sam_single_pass_probe.py <project-id>` is a free dry run; `--yes --budget-usd <n>` makes billed requests,
  refusing anything that could exceed the budget; `--reanalyze <folder>` re-reads saved responses without any request.
