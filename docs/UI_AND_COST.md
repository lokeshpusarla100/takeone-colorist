# Editor design, cost display and robustness

## Principles

- **Readable first.** Body text is 16px; secondary text 14-15px; nothing meant to be read is below 13px (the earlier UI used 9-12px
  for labels). Contrast is WCAG AA or better for every text/background pair in `styles.css`.
- **Neutral surroundings.** The UI is neutral grey with one violet accent, so it does not tint the picture being graded. Status
  colours (good / warning / serious / critical) are the fixed status palette and always come with an icon and words.
- **One source per fact.** Step names, step count, caption, clocks, live fill and the strip over the video are derived once
  (`lib/useRunModel.ts`) from the backend state and timing markers.
- **A bad record never blanks the editor.** Every section sits in `PanelBoundary`; a section that cannot be drawn says so, keeps
  the rest working and tries again when new data arrives.

## Layout

A sticky top bar (connection pills for Backend / Muse / Grading, the video's estimated cost, Sessions, Import video), then two
columns. Left: the video (Original / Graded / Side by side, version, Import render, Download) and numbered cards:
1 Your direction, 2 Creative brief, 3 Choose a look (optional), 4 Generate the grade, then Feedback and the advanced
Subject masks tool. Right (sticky): **Run** (status, clocks, steps) and **Cost of this video**, and an **Activity log** tab.
While work runs, a status strip over the video shows the same step, measured progress and a Cancel button.

## Cost per video

The Cost card, the cost chip in the top bar and the Sessions list show an **estimate in US dollars** of what a video has cost,
for Muse and SAM together, from the usage Meta reported for each request (`lib/cost.ts`, over the rows of `lib/tokenUsage.ts`).

| Item | Rate (Meta rate sheet, read 2026-11-03) | Counted as |
|---|---|---|
| `muse-spark-1.3` input | $1.25 / 1M tokens | input tokens minus cached tokens |
| `muse-spark-1.3` cached input | $0.15 / 1M tokens | cached tokens (a subset of input) |
| `muse-spark-1.3` output | $4.25 / 1M tokens | output tokens (reasoning tokens are part of output) |
| `sam-3.1` video | $0.20 / 1,000 frames | frames processed, at least 50 per request |

Source: <https://dev.meta.ai/docs/pricing-rate-limits>. The rates live in `takeone/config.py` (`Settings.pricing()`), are published
by `GET /api/health` as `pricing`, and can be overridden with `TAKEONE_PRICE_MUSE_INPUT_PER_M`,
`TAKEONE_PRICE_MUSE_CACHED_INPUT_PER_M`, `TAKEONE_PRICE_MUSE_OUTPUT_PER_M`, `TAKEONE_PRICE_SAM_PER_1K_FRAMES` and
`TAKEONE_SAM_MIN_BILLED_FRAMES` (invalid or negative values fall back to the published rate).

What it is not: a bill. A request that never reported usage (stopped early, failed, cancelled) is **not guessed**: it is left
out and the card says how many were left out. Anything else Meta may charge is not included. If the backend publishes no
rates the card shows no dollar figure instead of a wrong one. Amounts under a cent show four decimals so a small run never
displays as free. Checked against an independent calculation on every saved project: they agree.

## Safeguards and limits, as a picture

The brief's `default_protections` and `technical_envelope` are sentences. `lib/briefVisual.ts` sorts them (by sentence shape
and a few keywords, never by video) into guarantee cards, footage facts (from the structured source when present), the controls
that can be adjusted, limits, how the footage is handled, and things that are never done. Every original sentence is still shown
in full; anything unrecognised becomes a plain row.

## Robustness fixes in this change

- **Crash on "grade all matches".** The backend writes `{kind: "user_selected_all_matches", concept, object_count}` as the recipe's
  execution adjustment, with no `reason` or `changes`. The grade panel assumed both and threw, which blanked the whole editor on
  every reload of that session. `lib/adjustment.ts` describes every shape defensively, and the panel boundaries contain any future
  one. The full-page error screen now says plainly that sessions are safe and offers "Start a new session" (records are kept).
- **First visit showed "active session could not be found"** in development: React StrictMode ran the restore twice and the second
  run saw a session id whose record was not saved yet. Restore is now shared and a new session is saved before it is active.
- **The page loaded scrolled down** to the questions button, because focus was handed back to it on first render.
