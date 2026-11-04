# Source color: phone footage in, Rec.709 SDR out

## What was wrong

Grading, matte QC and the output contract are defined for **display-referred Rec.709 SDR**. The pipeline used to accept
exactly one kind of source: 8-bit 4:2:0, every tag BT.709, limited range. Everything else failed with
`GRADE_DOMAIN_UNSUPPORTED` ("Use known 8-bit SDR Rec.709 footage"), and HDR was refused at upload.

That excludes ordinary phone video. The recording that triggered this change was an **Android 16** clip: HEVC Main 10,
**full range**, tagged BT.601/PAL (`smpte170m` matrix and transfer, `bt470bg` primaries). That is plain SDR, just not in the
one exact format. Many Android phones record 10-bit and/or full range; iPhones record **HLG HDR** by default; many files
carry no color tags at all.

Simply dropping the check would be wrong. Measured on the Android clip's format, the correct conversion is within 1.4 of 255
levels, while ignoring the matrix is off by up to 25, ignoring full range by up to 20 (mean 8.7), and ignoring the BT.470BG primaries
by up to 40 on saturated colors. An untagged HD file decoded with ffmpeg's BT.601 default is also visibly off (more than 8 levels).
So the check became a *conversion*.

## What is supported now

| Footage | Treatment |
|---|---|
| Rec.709, limited range, any bit depth (8/10/12) or chroma (4:2:0/4:2:2/4:4:4) | **Native**: no conversion; read at full precision by the existing 16-bit path |
| Full range, BT.601 / BT.470BG / SMPTE 170M matrix or primaries, BT.2020 SDR, sRGB or 240M transfer | **Converted** to Rec.709 limited range, in 16-bit |
| Missing tags (matrix, primaries, transfer, range) | **Converted**, assuming the usual convention, and reported as assumed |
| **HLG** and **PQ / HDR10** (BT.2020), including Dolby Vision profile 8 | **Tone-mapped to SDR**, an approximation that the interface says so about |
| Log or linear curves, Dolby Vision profile 5, RGB/gray/alpha video, odd frame sizes, wide-gamut with no transfer tag, BT.2020 constant-luminance | **Refused**, with the specific reason and what to do |

The **output is always 8-bit Rec.709 SDR**. The 10-bit or HDR original is kept untouched; grading runs in 16-bit.

### Conventions for tags a file does not state

* **Matrix**: BT.601 only for 480/576-line formats (long side up to 1024 and short side up to 576), BT.709 for everything else.
  A portrait 720x1280 phone clip is HD.
* **Range**: full for `yuvj*` pixel formats, limited otherwise.
* **Primaries**: those of the matrix. **Transfer**: Rec.709 (but wide-gamut BT.2020 with no transfer tag is refused, because
  SDR and HDR look completely different and nothing says which it is).

## How it works

`takeone/color/domain.py` (pure) resolves a probed stream into a `SourceDomain` once, at ingest, and records it with the source
(`Source.domain`). Every conversion parameter is then passed to ffmpeg's `zscale` **explicitly**, so nothing depends on the
file having been tagged correctly or on a decoder's default. (An early experiment showed why: a file whose transfer tag was
missing could not be converted at all until the input was stated.)

* SDR: `zscale=rangein=..:matrixin=..:primariesin=..:transferin=..:range=limited:matrix=bt709:primaries=bt709:transfer=bt709`,
  then `format=yuv444p16le`.
* HDR: to linear light, gamut to BT.709, `tonemap`, back to Rec.709. **HLG uses Hable, PQ uses Mobius**, chosen from measurements:
  Hable follows HLG's built-in SDR-compatible look (input 0.50 gives 124/255 against 128 for a plain reinterpretation) while Mobius
  lifts HLG midtones to 185; Mobius keeps PQ reference white (0.58, 203 nits) near SDR white (234/255) where Hable renders it dark (192).

A native source gets an **empty** conversion, so its filter graphs are byte-identical to what they were (tested for the preview
graph and the render graph). The same conversion is used wherever the original's pixels are read, through `takeone/color/files.py`:

| Reader | Where the conversion sits |
|---|---|
| Render (`rendering.filter_graph`) | Full resolution, before the grade |
| Browser preview and analysis proxy (`media.make_previews`) | After shrinking: the previews are small |
| SAM tracking proxy (`sam.mapping`) | After shrinking; the conversion is part of its cache identity |
| Evidence frames (`evidence.extract`, `decoded_evidence`) | After the frame selection and shrink |
| Matte QC (`local_grading`) | Both original and output are read through ffmpeg at the check's size (OpenCV ignores tags, so mixing it with a converted original would compare unlike pictures); native footage keeps OpenCV |

On the real 4K Android file the conversion adds **0.7 s** to preview preparation (26.3 s to 27.0 s). Full-resolution SDR conversion
runs at about 21 fps against 26 fps for plain decode; HDR tone mapping at about 14 fps at 3840x1644.

## Sessions saved before this

Their source has no recorded domain. Before any job reads the source it is resolved from the file and recorded; if it needs
converting, its preview and analysis proxy are rebuilt in the converted domain once ("Converting the source to Rec.709" in the
activity log), cached pixel evidence made before that is discarded, and nothing is rebuilt for native sources. This is how the
failing Android session now grades.

## Verified

* `tests/color/test_domain.py` encodes known colors in each phone-style format, probes the file as ingest does, converts, and compares
  with an independent numpy reference (display-referred, BT.1886-like gamma): maximum 3/255 and mean 1.5/255 over eight formats. A
  separate test shows those bounds are tight enough to fail on a wrong matrix, range or primaries. HDR ramps are checked for
  monotonic mapping, black staying black, the peak reaching white, neutrals staying neutral and saturated colors keeping their hue.
* `tests/color/test_phone_footage.py` runs phone footage through the real service: previews in Rec.709 with the conversion recorded, a
  global grade equal to the same footage recorded natively, a localized grade passing the matte checks, refusals before any billed
  call, HDR graded as disclosed SDR, old sessions upgraded once, native sources untouched.
* Real file: the Android clip that failed (3840x1644 HEVC 10-bit, 30 s) now renders: 910 of 910 frames, audio copied, fully decoded.

Two regressions the existing suite caught while building this are fixed and now covered: the render pass's evidence decoder must
convert exactly as the standalone extractor does, and the converted-source QC reader must open its file in-process so it survives
the render renaming its partial output.

## Log and flat footage: the input profile

Camera log (Apple Log, S-Log3, V-Log, O-Log, Canon Log, LogC, ...) is ordinary video as far as the file is concerned: the
container says Rec.709 or nothing, and the picture is simply flat. No reader can know that from metadata, which is why
DaVinci Resolve and Premiere also ask the person for an input color space or LUT. Take One does the same, in two ways
(`takeone/color/profile.py`, applied by the same chain as the automatic conversion, so every reader of the original agrees):

| Choice | What it does | Accuracy |
|---|---|---|
| **Camera LUT** (`.cube`, 3D or 1D) | The camera maker's own transform, applied to the file's RGB with no other conversion before it | Exact: the camera's own picture |
| **Fix the contrast automatically** | Stretches the clip's own measured black and white (0.5th / 99.5th percentile of luma) to the full range | Approximate, and labelled so everywhere |

* **Flat footage is only ever suggested.** At import the analysis proxy is measured (`color/levels.py`). Blacks lifted to 7% or more,
  a span of 75% or less and whites below 90% mean "looks flat" (ordinary phone clips measure 0 to 3% and 93 to 100%; the OnePlus 12R
  clip measured 14.6% and 77.2%). A dark or foggy clip can be flat on purpose, so nothing is applied until the person chooses, after
  seeing a before/after still of their own footage. A LUT that gives an almost black, almost white or still-flat result is flagged.
* **LUT files are untrusted input.** They are parsed completely before ffmpeg sees them: size limits, row count, finite numbers,
  domain, unknown keywords. Every refusal has its own code and message. Paths are escaped for both levels of ffmpeg's filter
  syntax (tested with spaces, commas, colons, quotes, brackets, semicolons, backslashes and non-ASCII).
* **Where it is saved.** `source.bin.color.json` beside the original (plus `input-lut-<sha16>.cube`, content-addressed). Every decoder
  is handed that path, so no reader can forget the profile. The state keeps a display copy (`Source.profile`), the measured levels
  (`Source.levels`), the last failure (`Source.profile_error`) and the signature the proxies were built under (`Preview.conform_sig`).
* **Safe to change.** The profile is saved first, the preview and analysis proxy are rebuilt, and the old profile is restored if that
  fails. If the process stops half way, the next job sees the proxies do not match the saved profile and rebuilds them.
* **Locked after analysis.** Once Muse has seen the footage (or a brief, look or grade exists) the colors can not change
  (`409 INPUT_PROFILE_LOCKED`): import the video again to try another profile.
* **Not for HDR.** HDR is already converted from its own tags, so a profile would be applied twice (`INPUT_PROFILE_NOT_APPLICABLE`).
* **Endpoints.** `GET /api/projects/{id}/input-profile`, `POST .../lut` (upload and validate), `POST .../preview` (two stills, saves
  nothing), `PUT .../input-profile` (apply or `kind: none`). The card lives in `frontend/src/features/color/`.

Verified against real camera transforms: V-Log, S-Log3, Canon Log 3, RED Log3G10 and ARRI LogC3 to Rec.709 from OpenColorIO's
built-in library, exported as 65-point `.cube` files and run through the real chain, in limited and full range
(`tests/color/test_input_profile.py`): mean error 0.2 to 1.2 of 255 against OpenColorIO itself, and the same tolerance fails a range
misread. A flat clip graded under either profile equals the same footage already corrected at the source.

## Not covered

* **No built-in camera library yet.** A camera's log is recognised only if the person supplies its LUT (or accepts the approximate
  contrast fix). Shipping verified per-camera transforms (for example from OpenColorIO's library) is the next layer.
* The automatic contrast fix is a linear stretch, not the camera's log curve: shadows and highlights are not shaped like the maker's
  transform.
* **HDR is delivered as SDR.** There is no HDR output, and the tone mapping is an approximation of an HDR display, not an exact match.
* RGB/gray/alpha video, odd frame sizes and non-square pixels are refused (the last was already).
* Needs an ffmpeg build with `zscale` (libzimg) and, for HDR, `tonemap`; `GET /api/health` reports `source_conversion`, and a missing
  converter is a clear refusal, not a failed render.
* Setting: `TAKEONE_HDR_TO_SDR=0` refuses HDR instead of converting it.
