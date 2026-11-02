#!/usr/bin/env python3
"""Experiment: a sky-only localized grade through the unchanged production path.

The sky is the SAM concept (foreground layer) and everything else is neutral, so the
result isolates two questions: does SAM track the sky on this footage, and does
highlight_ceiling turn a pale sky into the requested dark one?

Default (offline, free): build the recipe for a saved project and run the real
clipping/highlight preflight on its cached source samples, then print what the sky
treatment does to each sampled frame's brightest region. No SAM, render or model call.

--execute: render it as a new candidate with Service._render_one. That runs SAM on the
concept (uploads the analysis proxy to Meta and is billed) unless masks for it are
already cached, then a full encode. Stop the backend first: the workspace lock is
exclusive, so the script refuses to run alongside it.

    python scripts/sky_only_experiment.py <project-id> [--ceiling 0.62] [--execute]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from takeone import color_processing, grade_guard, local_grading, rendering, tonal  # noqa: E402
from takeone.config import Settings  # noqa: E402
from takeone.contracts import GradeParameters, GradeProposal, GradeRecipe, RenderCandidate, State  # noqa: E402
from takeone.errors import Problem  # noqa: E402
from takeone.render_preflight import prepare  # noqa: E402

NEUTRAL = dict(contrast=1., saturation=1., gamma=1.)


def sky_treatment(ceiling=.62, cooling=.12, gain=.9, saturation=.9):
    return GradeParameters(contrast=1.05, saturation=saturation, gamma=.95, brightness_gain=gain, cooling=cooling, highlight_ceiling=ceiling)


def sky_only_proposal(duration, concept='sky', treatment=None):
    """Sky as the localized layer, everything else (and outside the interval) untouched."""
    return GradeProposal(
        mode='localized', parameters=GradeParameters(**NEUTRAL),
        local={'concept': concept, 'selection': 'all_matches', 'intervals': [{'start': 0, 'end': duration}],
               'foreground': treatment or sky_treatment(), 'background': GradeParameters(**NEUTRAL),
               'preserve_subject_color': False, 'feather_px': 1.5},
        summary=f'Experiment: grade only the {concept}; all other pixels unchanged.', limitations=['Isolation experiment, not a full grade.'],
        decisions=[{'parameter': 'local_treatment', 'reason': f'Isolate {concept} to test SAM tracking and highlight_ceiling.', 'source_ids': ['user_request']}],
        intent={'description': f'Darker, cooler {concept} only.', 'highlight_policy': 'subdue', 'shadow_policy': 'preserve_detail'},
        review_required=False)


def recipe_for(state, proposal, template, timeline=None):
    return GradeRecipe(schema_version='takeone-grade-v3', prompt_version='colorist-v3', tone_operator='luminance_ocio_v3',
                       source_sha256=state.source.sha256, brief_sha256=rendering.fingerprint(state.brief.model_dump(mode='json')),
                       model='experiment:sky-only', proposal=proposal, source_samples=template.recipe.source_samples,
                       evidence_selection=template.recipe.evidence_selection, timeline=timeline,
                       operation_order=['layer_treatment', 'mask_merge', 'interval_fade', 'output_rec709'])


def predicted(pixels, treatment):
    rows = []
    for index, frame in pixels.items():
        rgb = frame.astype(np.float32) / 255
        luma = rgb @ tonal.WEIGHTS
        high = tonal.highlight_mask(luma)
        if high is None:
            continue
        after = color_processing.apply_rgb(rgb[high][None], treatment)[0]
        rows.append({'frame_index': index, 'brightest_fraction': round(float(high.mean()), 3),
                     'luma': [round(float(luma[high].mean()), 3), round(float((after @ tonal.WEIGHTS).mean()), 3)],
                     'rgb_after': [round(float(v), 3) for v in after.mean(axis=0)]})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('project_id')
    parser.add_argument('--concept', default='sky')
    parser.add_argument('--ceiling', type=float, default=.62)
    parser.add_argument('--cooling', type=float, default=.12)
    parser.add_argument('--execute', action='store_true', help='Run SAM (billed upload) and a full render as a new candidate.')
    args = parser.parse_args()
    settings = Settings.from_env()
    directory = settings.workspace / args.project_id
    state = State.model_validate(json.loads((directory / 'state.json').read_text()))
    template = next((c for c in reversed(state.candidates) if c.status == 'ready' and c.recipe and c.recipe.evidence_selection), None)
    if not state.brief or not state.source or template is None:
        sys.exit('The project needs a brief and one rendered candidate whose sampled evidence can be reused.')
    treatment = sky_treatment(args.ceiling, args.cooling)
    proposal = sky_only_proposal(state.source.duration, args.concept, treatment)
    recipe = recipe_for(state, proposal, template)
    cancel = threading.Event()
    _, pixels, _ = grade_guard.source_pixels(directory / 'source.bin', recipe, state.source, settings, cancel, directory)
    report = prepare(recipe, pixels)['report']
    print(json.dumps({'preflight': report['status'], 'blocked_by': report.get('blocked_by'),
                      'layers': {k: {m: v.get(m) for m in ['mid_grey_change', 'highlight_change', 'darkening']}
                                 for k, v in report.get('highlights', {}).get('layers', {}).items()},
                      'brightest_region_if_it_were_sky': predicted(pixels, treatment)}, indent=1))
    if not args.execute:
        print('Dry run only: no SAM request or render. Re-run with --execute to render (billed SAM upload).')
        return
    from takeone.service import Service
    service = Service(settings)
    try:
        recipe.timeline = local_grading.timeline(directory, state.source, settings, cancel)
        candidate = RenderCandidate(id=str(uuid4()), name='V' + str(len(state.candidates) + 1), brief_sha256=recipe.brief_sha256,
                                    source_sha256=state.source.sha256, recipe=recipe, status='rendering', generation_id=str(uuid4()))

        def add(s):
            s.candidates.append(candidate)
            s.render_target_id = candidate.id
            service.event(s, 'Sky-only experiment queued', 'Isolation experiment from scripts/sky_only_experiment.py.', candidate=candidate.name)
        service.mutate(args.project_id, add)
        service._render_one(args.project_id, cancel)
        result = next(c for c in service.store.get(args.project_id).candidates if c.id == candidate.id)
        print(json.dumps({'candidate': result.name, 'status': result.status, 'warnings': result.validation.get('warnings'),
                          'mask_validation': result.validation.get('mask_validation'),
                          'output': str(directory / 'candidates' / result.id / 'grade.mp4')}, indent=1, default=str))
    except Problem as error:
        sys.exit(f'{error.code}: {error.message} {error.action}')
    finally:
        service.close()


if __name__ == '__main__':
    main()
