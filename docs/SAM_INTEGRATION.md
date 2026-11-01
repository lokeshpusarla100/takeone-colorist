# Hosted SAM integration

The app uses hosted sam-3.1 with the same MODEL_API_KEY/base URL as Muse. Installing meta-sam-parser did not itself connect segmentation; the implementation now uses its public Responses stream parser and mask decoder. No local model weights or additional framework were added.

## Scope

After a brief exists, explicitly request a short subject phrase. The UI defaults to the first five seconds; Full clip is an explicit alternative. There is no automatic segmentation on upload, analysis, answer submission or global grading.

Results are diagnostic masks and a colour-coded proxy overlay. All returned object IDs remain distinct, with no automatic selection for editing. Regional grading/compositing, mask refinement and subject locks are not implemented by this addition. The existing global renderer continues independently.

## Mapping and artifacts

Before a remote call, verify the original checksum, upright geometry, constant frame rate, source/proxy frame counts and relative timestamps. Reject unsupported mapping rather than guessing frame positions. Excerpts preserve the first source frame indices and are verified after encoding. This version rejects VFR, rotation metadata and non-matching source/proxy timing.

Each project stores segmentations/<run-id>/ with:

- frame-map.json: source/proxy hashes, dimensions, frame rate, actual timestamps, explicit index mapping and excerpt scope.
- input.mp4 when needed: the verified bounded excerpt, separate from the original.
- raw.txt and events.ndjson: the returned segmentation grammar and normalized Responses events. Failed previous attempts remain archived.
- stream-complete.json: a hash-bound receipt written only after the network stream completes.
- parsed.json: parser outcome, diagnostics and records.
- masks/: box-local binary PNGs, keyed by frame and object ID.
- manifest.json: records, original/proxy mapping, visibility intervals, unmatched frame count, supplied confidence or null, and limitations.
- overlay.webm: a fully decoded, duration/geometry-checked diagnostic preview.

Writes to JSON artifacts use temporary files, fsync and replacement. Project state records the command receipt, run status/error and progress. Existing source and brief artifacts are retained unchanged.

The parser uses explicit frame markers and object IDs, never line order. Mask bounds use the parser's half-open coordinates; crop rasters are resized into those bounds using nearest-neighbour interpolation. Gaps are not filled or smoothed across cuts. An unmatched frame may represent absence or missed segmentation; it does not establish either. IDs are not assumed stable through full occlusion, re-entry or cuts.

## Network, decoding and recovery

Save the network event stream before CPU-intensive mask decoding. This prevents local decoding from controlling network consumption and allows completed responses to be replayed without inference. The official Python decoder can be slow on many masks; network completion and usable-mask completion are separate stages.

No automatic billable retry is made. An incomplete stream is retained as failed; retrying it explicitly can make another request. After stream completion, decode/overlay retries use the cached result and need no API key. Cache hashes and media mapping must match. Restart marks interrupted jobs failed for explicit recovery. Cancellation interrupts a waiting request or local processing; it cannot guarantee that remote usage stopped.

Zero matches is a valid empty result. Incomplete/malformed streams, bad geometry and unmapped frames are not published as ready results. Overlay creation verifies frame count while decoding, duration, dimensions and full output decode. These checks do not prove boundary quality or temporal stability.

## API

POST /api/projects/{project}/segment uses the existing revision/command contract:

```json
{
  "command_id": "7b71f68e-aea7-4ea7-9e61-391cc4e4c868",
  "revision": 12,
  "concept": "motorcycle",
  "duration_seconds": 5
}
```

Use the current revision and a fresh command ID for a new action; retain the same body/ID on retry. duration_seconds=null means full clip. The API accepts one phrase and requires a prepared source and completed brief. A new explicit run can also follow a failed/cancelled segmentation. It does not modify the brief or authorize protected edits.

- GET /api/projects/{project}: public run status, received/validated frames, masks and object visibility intervals.
- GET /api/projects/{project}/segmentations/{run}/preview: ready diagnostic WebM, including range requests.
- GET /api/projects/{project}/segmentations/{run}/manifest: ready/empty result manifest.
- POST /api/projects/{project}/retry: explicit retry of the saved failed step.
- POST /api/projects/{project}/cancel: cancellation request.

New private upload/cache references are excluded from public state. Raw model results are not preference-memory evidence. SAM prompts contain the explicitly supplied subject and media, with no cross-project remembered preferences.

## Accounting

SAM video is accounted per processed frame. The request's returned video_frames_processed is recorded when available. A zero token total does not imply a free SAM request. Missing accounting stays unknown; no monetary estimate or account balance is invented. Failed requests can still incur usage.

## Validation and limits

Offline checks cover real parser decoding, sparse/out-of-order frames and separate IDs, empty/incomplete results, mapping rejection, cancellation, cached-stream replay, idempotent commands and overlay-only retry without a key. Browser fixtures check scope disclosure, locking, masks/manifest, saved results, cancellation and mobile layout. They make no model calls.

The 1 November live check used the supplied 39.9-second motorcycle clip. The initial full-clip attempt ended with a stream-source error after partial output and was not accepted. After separating network capture from decoding, a second request on the first five seconds completed: 150 processed frames, one object ID and masks on all 150 frames. The original checksum and brief remained unchanged. The overlay passed media checks, played through all five seconds in Chrome, and was inspected at six sampled timestamps. Coverage is not an accuracy score: boundaries are coarse in places and regional grading has not been approved. This result does not validate the remaining footage or full-clip transport reliability.

Final checks for this addition passed 81 Python tests, 23 frontend tests, 10 offline SAM browser checks and the production build. The two live attempts are separate from that offline suite.

Run offline checks from the repository root:

    .venv/bin/python -m pytest -q tests takeone_rubric_v1
    npm --prefix frontend test
    npm --prefix frontend run build

Live tracking is only initiated explicitly through the editor/API and may incur usage. Documentation: [Meta SAM API basics](https://dev.meta.ai/docs/cookbook/sam-api-basics), [segmentation output and identity semantics](https://dev.meta.ai/docs/sam/reading-segmentation).
