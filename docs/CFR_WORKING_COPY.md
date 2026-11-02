# Authorized CFR working copy

For project `83983d34-1f08-41fb-a5e2-9e99b5415cf7`, the user explicitly chose a separate CFR copy
instead of changing the original or weakening SAM timing validation.

Created `/home/lokesh/Downloads/V2-graded-cfr.mp4`. This clip's packet PTS can be mapped uniquely to
the nearest 30 FPS frame index, with every index present exactly once. FFmpeg's
[setts bitstream filter](https://ffmpeg.org/ffmpeg-bitstream-filters.html#setts) remuxed video PTS/DTS
to that verified grid using codec copy. Audio packets were copied; neither stream was re-encoded.
This is a validated conversion for this near-CFR clip, not a generic arbitrary-VFR normalization.

Verification: all 741 decoded frame hashes matched in order, decoded PCM audio hashes matched, the
video start remained 76.8 ms, and the original source checksum was unchanged. Output video duration
is 24.700000 seconds. Maximum individual timestamp adjustment is 4.8 ms. The source project and its
brief were not replaced. Local mapping and validation reports are ignored artifacts in
`outputs/cfr-working-copy/`.

Import the CFR file as a new source in the editor and reuse the intended creative direction. The
old project's Retry action still targets the original file and does not switch sources. Full SAM
tracking, localized rendering and creative quality on the working copy have not been run here.

Look planning now retains rejected proposals in `looks-plan.json` and reports the actual unfulfilled
instructions plus the localized-tool blocker. The older run retained only count=2, so its exact two
model messages cannot be reconstructed. Optional weather generation that the user explicitly allows
approximating should be a limitation, not a required unsupported instruction. Required boat protection
cannot be replaced silently by a global grade. Where a local plan lacks cached masks, global palette
references are explicitly preview-only and cannot be selected as the protected-subject render recipe.

Seven offline looks tests, 40 frontend tests, production build and compilation/diff checks passed.
The working-copy verification made no Meta API calls.
