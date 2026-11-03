#!/usr/bin/env python3
"""A/B: does the critic decide the same with per-frame tables in column layout?

Collects one candidate's real review evidence once, then asks the critic N times with the default
row layout and N times with the column layout (same facts, field names written once), and compares
what matters: decision, issue targets and correctability, mask verdicts, preferred candidate, the
revision plan's controls, and the token usage the API reports.

Default: dry run, no API call (prints the JSON size of both layouts). --yes makes 2*N billed critic calls.
Works on a hard-linked copy of the project, so the live workspace and backend are untouched.

    python scripts/critic_layout_ab.py <project-id> [--calls 3] [--yes]
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from takeone import creative_review  # noqa: E402
from takeone.config import Settings  # noqa: E402
from takeone.provider import MuseProvider  # noqa: E402
from takeone.service import Service  # noqa: E402


def outcome(review, usage):
    plan = review.revision_plan
    controls = None
    if plan is not None:
        layers = {'parameters': plan.parameters.model_dump()}
        if plan.local:
            layers.update(foreground=plan.local.foreground.model_dump(), background=plan.local.background.model_dump())
        controls = {name: {k: round(v, 2) for k, v in values.items()} for name, values in layers.items()}
    return {'decision': review.decision,
            'targets': sorted(f'{i.target}:{"fixable" if i.correctable else "not fixable"}' for i in review.issues),
            'masks': sorted(f'{m.concept}={m.verdict}' for m in review.mask_assessments),
            'preferred': review.preferred_candidate_id, 'revision_controls': controls,
            'input_tokens': (usage or {}).get('prompt_tokens') or (usage or {}).get('input_tokens'),
            'output_tokens': (usage or {}).get('completion_tokens') or (usage or {}).get('output_tokens')}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('project_id')
    parser.add_argument('--calls', type=int, default=3)
    parser.add_argument('--yes', action='store_true', help='Make the billed critic calls.')
    args = parser.parse_args()
    base = Settings.from_env()
    source = base.workspace / args.project_id
    if not source.is_dir():
        sys.exit(f'No project {args.project_id} in {base.workspace}')
    work = Path(tempfile.mkdtemp(prefix='takeone-ab-'))
    try:
        subprocess.run(['cp', '-al', str(source), str(work / args.project_id)], check=True)  # hard links: no copy, no writes to originals
        settings = replace(base, workspace=work)
        service = Service(settings)
        try:
            state = service.store.get(args.project_id)
            candidate = next((c for c in reversed(state.candidates) if c.status == 'ready' and c.validation and c.recipe), None)
            if candidate is None:
                sys.exit('The project has no rendered candidate to review.')
            data, previous, _ = creative_review.collect(service, state, candidate, threading.Event())
            captured = {}
            for layout in ['rows', 'columns']:
                provider = MuseProvider(replace(settings, critic_table_layout=layout), service.prompt_gate)
                original = provider.client
                class Capture:  # dry run: build the exact request without sending it
                    def __init__(self):
                        from types import SimpleNamespace
                        def parse(**kwargs):
                            captured[layout] = kwargs['messages'][1]['content'][0]['text']
                            raise KeyboardInterrupt
                        self.beta = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
                    def __enter__(self): return self
                    def __exit__(self, *_): pass
                provider.client = Capture
                try:
                    provider.review(state.brief, candidate, data, previous, [])
                except KeyboardInterrupt:
                    pass
                provider.client = original
            rows, cols = len(captured['rows']), len(captured['columns'])
            print(f'candidate {candidate.name}: critic JSON rows {rows:,} chars, columns {cols:,} chars ({100 * (rows - cols) / rows:.0f}% smaller)')
            if not args.yes:
                print('Dry run only. Re-run with --yes to make', 2 * args.calls, 'billed critic calls and compare decisions.')
                return
            results = {}
            for layout in ['rows', 'columns']:
                provider = MuseProvider(replace(settings, critic_table_layout=layout), service.prompt_gate)
                results[layout] = []
                for number in range(args.calls):
                    review, usage = provider.review(state.brief, candidate, data, previous, [])
                    results[layout].append(outcome(review, usage))
                    print(f'  {layout:7s} call {number + 1}: {json.dumps(results[layout][-1])}', flush=True)
            for key in ['decision', 'targets', 'masks', 'preferred']:
                spread = {layout: Counter(json.dumps(r[key]) for r in results[layout]) for layout in results}
                print(f'{key:10s} rows {dict(spread["rows"])} | columns {dict(spread["columns"])}')
            for layout in results:
                tokens = [r['input_tokens'] for r in results[layout] if r['input_tokens']]
                if tokens:
                    print(f'{layout:7s} mean input tokens (API-reported): {sum(tokens) / len(tokens):,.0f}')
            print('Keep the column layout only if decisions, targets, mask verdicts and preferences match as closely '
                  'across layouts as the critic already varies within one layout.')
        finally:
            service.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
