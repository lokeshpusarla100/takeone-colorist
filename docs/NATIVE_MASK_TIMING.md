# Native source timing for localized grading

The original source is authoritative and never replaced or retimed. Analysis proxies preserve native
relative PTS. CFR footage retains its existing segmentation path. For irregular native PTS, SAM uses
a separately generated silent `tracking.mp4`: exactly one scaled frame per source frame, with a
regular clock for tracking only. No frames are dropped, duplicated or interpolated.

Version3 frame maps bind both hashes, integer native PTS, proxy PTS and frame indices. The source
frame inspection and tracking artifacts are cached by source identity. A changed source/proxy,
count mismatch, missing timestamp, unsupported geometry or missing/ambiguous mask remains an error.

The matte is encoded in proxy order, checked against that clock, then scheduled on the native source
time base inside the renderer. A balanced integer-PTS lookup places mask i before video frame i and
after frame i-1. Video PTS are never changed. Large lookup expressions use an application-generated
filter file rather than exceeding command argument limits. Interval selection and cut mapping use
frame identity; final video/audio/PTS validation remains enabled.

Existing files can be reimported unchanged; no manual CFR conversion is required. Old analysis
proxies with a changed frame count are rejected with a reimport instruction. Historical SAM manifests
are reused only when their complete mapping matches. The previous standalone CFR copy remains a
separate optional artifact; it is not substituted for any original.

Synthetic tests exercise genuine irregular intervals, near-CFR jitter, nonzero video/audio offsets,
moving subject masks, first/last frames, a long timing gap and mask-cache reuse. Exact source/output
timestamps and localized pixels are checked. Live SAM inference and semantic mask accuracy on all
formats are not established by these tests; supported format, duration, frame-count, rotation and
colour-space limits still apply.

Verification: 57 existing SAM/local-grading/timing/looks checks passed; focused native mapping,
moving-edge and large-filter tests passed after final changes. Forty frontend tests and build passed.
The original 4K upload was also mapped offline: all 741 native frame IDs/PTS were retained, with its
source checksum unchanged. Its tracking-only clock differs by at most 4.8 ms; this is never copied
onto the original or the output video. Local evidence is in `outputs/native-timing-verification/`.
The validated tracking artifacts for that source were cached for reuse. No live Meta requests or full
4K localized render were performed during these checks.
