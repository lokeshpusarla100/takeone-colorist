# Take One creative-direction rubric v1 (standalone)

Implementation of only the **creative-intent question rubric**, not the video editor.

- `DESIGN.md`: purpose, architecture position, coverage, contracts, example, limitations.
- `rubric.py`: Pydantic data/tool contracts, one-question session state, literal answer/brief assembly and provenance. Muse owns creative selection; Python does not rank questions or infer duplicate meanings.
- `muse_adapter.py`: **Meta Muse Spark 1.3** structured-output adapter. Requires `MODEL_API_KEY`; offline checks use mocked requests and do not establish live question quality.
- `test_rubric.py`: offline unit tests; no API key needed.
- `proposal_schema.json` and `creative_brief_schema.json`: generated JSON Schemas for integrations.

## Local tests

The local application integrates this module through takeone/service.py and takeone/provider.py.
RubricSession.snapshot()/restore() persist pending questions, answers, directives and the budget.
Proposal.explicit_constraints accepts literal user-request quotes; invented constraints are rejected.

```bash
# From the repository root:
.venv/bin/python -m pytest -q takeone_rubric_v1
```

## Integration boundary

Scene analysis and video I/O are **external**. Supply cached evidence + verified editing tool capabilities; query Muse for candidate creative questions; ask at most one per cycle; record answer; finish with a traceable CreativeBrief. No RAG required. Do not pass API credentials to browsers.

Muse is instructed to return zero or one question. Legacy multi-candidate lists use the first item in model order. Same-family, same-topic and overlapping wording no longer cause a Python rejection. Invalid IDs, unavailable tool references and malformed data raise a contract error; the application saves the full proposal and fails visibly for retry instead of treating the consultation as complete. The question ceiling, exact answer/option matching, literal-quote provenance and capture-only memory gate remain. See [the inventory](../docs/RULE_BASED_LOGIC.md).

Phase 2 retains those freedoms and adds strict repetition provenance: scope-specific model decision_id values persist across paraphrases, while a finer clarification needs followup_to and a new-information reason with different alternatives. Verbatim same-scope question/option repeats are rejected. Separate regional scopes remain independent. The model receives current-epoch contract errors so an explicit retry can correct the proposal rather than silently finishing.

The application currently records preferences with cross-project application disabled. Optional remembered defaults, question references/conflicts and brief provenance fields prepare a future opt-in interface; the central application prompt gate supplies none in normal startup. Literal current-video feedback remains part of the brief. See [the memory contract](../docs/PREFERENCE_MEMORY.md).
