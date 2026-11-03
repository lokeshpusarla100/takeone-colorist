#!/usr/bin/env python3
"""Replay a saved project's FIRST consultation request against the current rubric instructions.

Answers "with today's prompt, how many questions does Muse ask for this brief, and which?" for both question
modes: one_at_a_time (one question per call) and batch (every independent question of a round together).
Rebuilds the session from the saved request, scene analysis and source only (no answers, no remembered
defaults) and never writes to the workspace.

Also reports, for free, how often the recorded closing-call prediction (final_if_options_chosen) agreed with
the closing call actually made. TAKEONE_RUBRIC_SKIP_CLOSING should stay off until that agreement is high.

Default: dry run, no API call (prints request sizes). --yes makes one billed rubric call per project per mode.

    python scripts/rubric_replay.py <project-id> [<project-id> ...] [--mode both|batch|one_at_a_time] [--yes]
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from takeone.config import Settings  # noqa: E402
from takeone.contracts import State  # noqa: E402
from takeone.media import capabilities  # noqa: E402
from takeone.preferences import PromptMemoryGate  # noqa: E402
from takeone.provider import MuseProvider  # noqa: E402
from takeone_rubric_v1 import muse_adapter  # noqa: E402
from takeone_rubric_v1.rubric import CapabilityManifest, Observation, RubricInput, RubricSession  # noqa: E402


def first_session(state: State, batch: bool) -> RubricSession:
    """The session exactly as service.analyze builds it before the first question."""
    return RubricSession(RubricInput(
        user_request=state.request, project_id=state.id,
        observations=[Observation(id=o.id, scope=f"{o.start:.3f}-{o.end:.3f}s", source="muse_visual",
                                  description=o.description, uncertainty=o.uncertainty) for o in state.analysis.observations],
        capabilities=CapabilityManifest(**capabilities(state.source)),
        technical_envelope=[f"Source: {state.source.width}x{state.source.height}, {state.source.duration:.3f}s; "
                            f"{state.source.video_codec}; {state.source.pixel_format}.",
                            "Analysis used the complete silent proxy; source/proxy timing is recorded.", *state.analysis.caveats],
    ), max_decisions=state.max_questions, batch=batch)


def request_size(session) -> int:
    captured = {}
    def parse(**kwargs):
        captured.update(kwargs)
        raise KeyboardInterrupt
    try:
        muse_adapter.propose(session, client=SimpleNamespace(beta=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))),
                             context_filter=PromptMemoryGate.clean)
    except KeyboardInterrupt:
        pass
    return sum(len(m['content']) for m in captured['messages'])


def prediction_agreement(workspace: Path):
    pairs = Counter()
    for path in workspace.glob('*/state.json'):
        try:
            proposals = json.loads(path.read_text()).get('question_proposals') or []
        except (OSError, ValueError):
            continue
        for record in proposals:
            if record.get('closing_prediction'):
                p = record['closing_prediction']
                pairs[(p['predicted_final'], p['actual_final'])] += 1
    total = sum(pairs.values())
    if not total:
        print('Closing-call prediction: no recorded closing calls yet (they are recorded from now on).')
        return
    print(f"Closing-call prediction over {total} closing calls: agreed {pairs[(True, True)] + pairs[(False, False)]}; "
          f"predicted final but Muse asked more {pairs[(True, False)]} (unsafe to skip); "
          f"predicted more but none asked {pairs[(False, True)]} (missed saving).")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('project_ids', nargs='+')
    parser.add_argument('--mode', choices=['both', 'batch', 'one_at_a_time'], default='both')
    parser.add_argument('--yes', action='store_true', help='Make the billed rubric calls.')
    args = parser.parse_args()
    settings = Settings.from_env()
    modes = ['one_at_a_time', 'batch'] if args.mode == 'both' else [args.mode]
    provider = MuseProvider(settings, None)
    for project_id in args.project_ids:
        path = settings.workspace / project_id / 'state.json'
        if not path.is_file():
            sys.exit(f'No project {project_id} in {settings.workspace}')
        state = State.model_validate_json(path.read_text())
        if not (state.analysis and state.source):
            sys.exit(f'{project_id}: no saved scene analysis; nothing to replay.')
        asked = len((state.rubric or {}).get('answers', []))
        print(f'== {project_id[:8]}  request {len(state.request):,} chars; originally asked {asked} question(s); budget {state.max_questions}')
        for mode in modes:
            session = first_session(state, mode == 'batch')
            print(f'   {mode:13s} request ~{request_size(session) // 4:,} tokens')
            if not args.yes:
                continue
            with provider.client() as client:
                proposal = muse_adapter.propose(session, settings.model, client=client, context_filter=PromptMemoryGate.clean)
            usage = session.events[-1].get('usage') or {}
            print(f"     -> {len(proposal.candidates)} question(s); tokens in {usage.get('prompt_tokens')}, out {usage.get('completion_tokens')}")
            for c in proposal.candidates:
                print(f'        [{c.preference_family.value}] {c.question}')
            if not proposal.candidates:
                print(f'        completion: {proposal.completion_reason}')
            elif proposal.final_if_options_chosen:
                print(f'        predicted final after these: {proposal.completion_reason_if_final}')
    if not args.yes:
        print('Dry run only. Re-run with --yes for', len(args.project_ids) * len(modes), 'billed rubric call(s).')
    prediction_agreement(settings.workspace)


if __name__ == '__main__':
    main()
