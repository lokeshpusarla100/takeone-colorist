# Take One preference memory

This is application-owned editing memory. It does not train weights or integrate with ChatGPT/Codex memory.

## Current mode

Every profile starts empty, with independent capture/application settings:

    capture_enabled: true
    application_enabled: false

Normal startup also sets memory_application_available=false. The API rejects application enablement and new-project opt-in while that capability is unavailable. There is no environment flag that can silently activate it. Confirming entries, adding projects, or repeating feedback never enables personalization.

## Working integrations

- Submitted direction, rubric answers and current-video feedback enter the project capture outbox.
- Explicit version choices/rejections use the project feedback endpoint. Automatic render selection is not preference evidence.
- Choices/skip/delegation and version events preserve the original action. Selection alone never creates an aesthetic entry. Skips/delegation create no specific preference.
- Rejection with an explicit textual reason can create a tentative project-scoped interpretation of that text.
- Automatic entries stay tentative and project-scoped. A repeated statement in one project remains one supporting project.
- Explicit reusable instructions can be added with reviewed interpretation, dimension and scope.
- Edit supports tentative/confirmed/contradicted status, topic, scope and applicability changes, with revision checks and history.
- Delete creates a tombstone and clears interpreted content/history. Old events and fingerprint receipts cannot recreate it. Raw evidence and project audit history are retained separately.
- Current-video feedback creates a new brief revision and retains the previous brief, even with capture paused and application off. It makes no model call and does not automatically render.
- Capture failures preserve the accepted project action and queue delivery for explicit retry/startup recovery.

The editor has a Preferences view, a recording toggle, profile/context defaults for new uploads, evidence/history, and add/edit/delete controls. It intentionally has no personalization switch.

## Editor workflow

1. Open Preferences. Review the interpretation, original wording, scope, status and supporting events before confirming an automatic entry.
2. Use Add instruction for an explicit reusable preference. Choose project scope, matching category/scene/purpose conditions, or explicitly general scope. Automatic capture does not promote project feedback to a reusable default.
3. Use Edit to correct an interpretation or mark it contradicted. Delete removes it from the active collection and prevents old processed evidence from restoring it.
4. Pause Record new project feedback to stop automatic capture. Explicit add/edit/delete commands remain available.
5. Choose a profile and context before uploading another video. Changing these defaults does not move the current video between profiles.

Feedback on this video is a separate action below the brief. Save feedback revises the current brief; Generate graded video must be requested separately to produce a new render. Neither action enables personalization. Uploading, typing, managing memory and saving feedback do not themselves make model calls; Start analysis and explicit generation can.

## Records and storage

Default location: .takeone-memory/, separate from workspace/<project-id>/ and ignored by Git.

owner.json assigns a stable local owner ID. profiles/<profile-id>.json contains settings, immutable feedback events, preference entries, processed-event receipts and fingerprint tombstones. File locks serialize read/modify/write. Writes use a temporary file, fsync and atomic replacement.

Preference entries contain owner/profile ID, original wording, interpretation, dimension, topic, project/category/general scope, supporting references, applicability conditions, explicit/inferred basis, status, timestamps, revision and history. No numerical confidence is invented.

Only editing labels category, scene and purpose are accepted as context. Automatic capture does not copy model scene descriptions, personal profile fields, media tags or model critiques into memory.

An owner/profile mismatch rejects the record. Supporting project references are checked by the API. Profile changes apply to new uploads; existing videos retain their profile. This is local namespacing, not multi-user authentication.

## API

All routes use the existing /api proxy and structured Problem responses.

| Method/path | Purpose |
| --- | --- |
| GET /api/preferences/profiles | List owner-local profiles |
| POST /api/preferences/profiles | Create an empty profile using name |
| GET /api/preferences/profiles/{profile} | Entries, settings and raw evidence |
| PATCH /api/preferences/profiles/{profile}/settings | Toggle automatic capture; application=true is rejected in this release |
| POST /api/preferences/profiles/{profile}/events | Record evidence from a profile-owned project |
| POST /api/preferences/profiles/{profile}/entries | Explicit reusable instruction plus interpretation/scope; command_id deduplicates retries |
| PATCH /api/preferences/profiles/{profile}/entries/{id} | Edit using the entry revision |
| DELETE /api/preferences/profiles/{profile}/entries/{id}?revision=N | Tombstone an entry |
| POST /api/projects/{id}/feedback | Apply current feedback or record an explicit selection/rejection/manual action |
| POST /api/projects/{id}/preference-capture/retry | Retry the durable capture outbox |

A project feedback request contains command_id, revision, and evidence. Evidence fields are kind, original_wording, dimension, preference_key, optional version_id, editing context and allowlisted action details.

For example, POST /api/projects/{id}/feedback with the latest project revision and a fresh command ID:

```json
{
  "command_id": "85360166-ec9c-4b49-9d8e-8666ab27c0e7",
  "revision": 12,
  "evidence": {
    "kind": "feedback",
    "original_wording": "Less orange; preserve the shadows.",
    "dimension": "appearance",
    "preference_key": "color_balance"
  }
}
```

This is a request example, not seeded user data. Replace the revision and command ID for a new action; retain the same ID and body when retrying that action. A stale revision is rejected rather than overwriting newer work.

Text feedback requires an existing brief and no active job. Metadata-only selections can be recorded while rendering without changing the frozen render contract. Version references are source, a known candidate UUID, or a client:<UUID> reference for local versions. Client references are recorded as client-reported evidence; they do not establish rendered provenance.

Manual adjustment/lock evidence supports control, before/after/value, locked, scope and applied details. The endpoint records evidence only: it does not implement an adjustment or lock in the renderer. Actual manual controls and their execution/locking flow remain an integration point.

Memory settings govern automatic project capture. Explicit add/edit/delete commands in Preferences are intentional management actions and remain available while automatic capture is paused.

## Capture recovery

Accepted project actions are saved independently of preference delivery. Bound evidence remains in the project's private memory_outbox when a settings read or delivery write fails. A failed item keeps memory_capture_error visible even if another item saves successfully. Retry after correcting storage using POST /api/projects/{id}/preference-capture/retry; startup also drains persisted outboxes. Delivery receipts make retries idempotent.

Do not delete owner/profile files to resolve capture errors: they preserve isolation and deletion tombstones. Existing project events are not automatically backfilled into a fresh memory collection. A project without an established owner cannot be safely replayed into an arbitrary owner's collection when storage is unavailable; inspect the reported error instead.

## Future application interface

PromptMemoryGate is enforced at the actual rubric and colorist context construction. Factual video analysis never receives preference data. Disabled projection removes reusable defaults, memory-derived directives/reasons and reserved memory metadata from model views, while preserving current request, current-video answers/feedback and executed numeric parameters. Frozen artifacts are not modified.

Critic, look-selection and preview-planning stage names are supported by the gate for future builders; those model pipelines are not implemented here.

A future rollout must explicitly:

1. Configure Settings(memory_application_available=True) in the deployment/application factory after validating that mode. This is deliberately not an env switch.
2. Enable application for the desired profile via its settings.
3. Upload a new project using profile_id, preference_context and use_preferences=true.

The current deployment rejects steps 2/3. There is no API to turn an existing project's binding on. Profile settings alone never retrofit projects.

New-project bindings freeze matching confirmed entry values/revisions. Context matching is exact and conservative. Unknown context does not supply a default. Conflicting entries with the same topic are preserved and labeled for clarification rather than selected by recency. Explicit overridden topics are excluded.

Prepared rubric fields distinguish memory_refs on influenced questions, memory_conflict on clarification questions, and memory_used_ids on actual defaults. Defaults are optional directives with memory provenance; current project answers override them. Memory cannot add explicit constraints or grant permission. Colorist decisions may cite only supplied references. Entry/revision/stage/target references are kept on the brief/project/recipe.

Edits do not rewrite existing bindings or frozen briefs. Deleted/contradicted entries stop being supplied to future prompts, while current-video user approvals remain current-video evidence. A live personalization-quality evaluation, opt-in UI, and richer uncertain-context routing remain future rollout work; capture-only tests do not establish model behavior when enabled.

## Code map

| File | Responsibility |
| --- | --- |
| takeone/preferences.py | Evidence/entry schemas, atomic profile storage, deduplication, tombstones, scoped retrieval and PromptMemoryGate |
| takeone/preference_api.py | Profile/settings/evidence/entry APIs and project feedback routes |
| takeone/service.py | Accepted-action capture, durable outbox, current-video brief revisions and influence provenance |
| takeone/provider.py and takeone_rubric_v1/muse_adapter.py | Actual model context construction through the central gate |
| takeone_rubric_v1/rubric.py | Current answers/feedback, prepared default/conflict contracts and brief provenance |
| frontend/src/components/MemoryView.tsx | Profile/context selection, evidence, add/edit/delete and capture control |
| tests/test_preferences.py | Local storage, API, gate, precedence and recovery fixtures |

## Verification

Run from the project root:

    .venv/bin/python -m pytest -q tests takeone_rubric_v1
    npm --prefix frontend test
    npm --prefix frontend run build

All automated tests use temporary files, fixture providers or mocked SDK requests. They cover persistence, deduplication, deletion replay, one-project evidence counting, owner/profile isolation, scope matching, frozen bindings, current instruction precedence, actual prompt isolation, and ordinary feedback while recording is paused. No live model calls are needed.

The 1 November feature handoff recorded 62 Python tests, 15 frontend tests, a passing production build and 12 offline browser checks covering memory management, profile isolation, paused-capture feedback and mobile layout. The normal API was checked with an empty collection, capture on and application unavailable/off. Existing project JSON artifacts were unchanged. These are recorded results from feature verification, not a claim of a fresh test run for subsequent documentation edits.
