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

Text feedback requires an existing brief and no active job. Metadata-only selections can be recorded while rendering without changing the frozen render contract. Version references are source, a known candidate UUID, or a client:<UUID> reference for local versions. Client references are recorded as client-reported evidence; they do not establish rendered provenance.

Manual adjustment/lock evidence supports control, before/after/value, locked, scope and applied details. The endpoint records evidence only: it does not implement an adjustment or lock in the renderer. Actual manual controls and their execution/locking flow remain an integration point.

Memory settings govern automatic project capture. Explicit add/edit/delete commands in Preferences are intentional management actions and remain available while automatic capture is paused.

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

## Verification

Run from the project root:

    .venv/bin/python -m pytest -q tests/test_preferences.py takeone_rubric_v1
    npm --prefix frontend test
    npm --prefix frontend run build

All automated tests use temporary files, fixture providers or mocked SDK requests. They cover persistence, deduplication, deletion replay, one-project evidence counting, owner/profile isolation, scope matching, frozen bindings, current instruction precedence, actual prompt isolation, and ordinary feedback while recording is paused. No live model calls are needed.
