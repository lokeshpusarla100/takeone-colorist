# Take One creative-direction rubric v1 (standalone)

Implementation of only the **creative-intent question rubric**, not the video editor.

- `DESIGN.md`: purpose, architecture position, coverage, contracts, example, limitations.
- `rubric.py`: Pydantic data contracts, evidence/feasibility question selection, per-answer state, creative brief, diagnostics.
- `muse_adapter.py`: optional **Meta Muse Spark 1.3** structured-output adapter. Requires `MODEL_API_KEY`; it hasn't been tested against a live account.
- `test_rubric.py`: offline unit tests; no API key needed.
- `proposal_schema.json` and `creative_brief_schema.json`: generated JSON Schemas for integrations.

## Local tests

```bash
pip install pydantic pytest openai
python -m pytest -q
```

## Integration boundary

Scene analysis and video I/O are **external**. Supply cached evidence + verified editing tool capabilities; query Muse for candidate creative questions; ask at most one per cycle; record answer; finish with a traceable CreativeBrief. No RAG required. Do not pass API credentials to browsers.
