"""What Muse is shown about rendered versions: every decision-relevant fact once, nothing duplicated.

Measured on real sessions, the stored validation record sent verbatim made up 80-84% of planner input
(previous_candidates: per-frame histograms of the same source repeated per version, encoder timing
manifests, render timings) and ~30% of critic input (statistics already sent as tonal_levels and
paired_diagnostics). These helpers keep warnings, protected-area results, the mask summary, errors and a
compact measured change, and round measurement blocks to 4 decimals, far finer than the 0.02 "visible"
threshold any decision uses. Plans, briefs, times and identifiers are never rounded or altered.
"""
from . import convergence

# Validation fields with no creative-decision value, or already sent to the model in another form.
NOT_FOR_MODEL = {'source_samples', 'output_samples', 'diagnostics', 'timing', 'performance', 'ffmpeg_version',
                 'fully_decoded', 'source_frames', 'output_frames', 'bytes', 'preview_width', 'preview_height',
                 'audio_mode', 'execution_capabilities'}
MEASURED = ['shadows', 'midtones', 'highlights', 'contrast', 'warmth', 'saturation']


def rounded(value, places=4):
    """Round every float in a measurement block; structure, ints and strings are unchanged."""
    if isinstance(value, float):
        return round(value, places)
    if isinstance(value, dict):
        return {k: rounded(v, places) for k, v in value.items()}
    if isinstance(value, list):
        return [rounded(v, places) for v in value]
    return value


def measured_change(validation):
    """Mean change versus the source on the version's sampled frames, plus the worst new clipping."""
    output, source = validation.get('output_samples') or [], validation.get('source_samples') or []
    change = {}
    for name in MEASURED:
        value = convergence._mean_change(output, source, name)
        if value is not None:
            change[name] = value
    pairs = (validation.get('diagnostics') or {}).get('paired_measurements') or []
    if pairs:
        change['max_new_black_fraction'] = max(p.get('new_black_fraction', 0) for p in pairs)
        change['max_new_white_fraction'] = max(p.get('new_white_fraction', 0) for p in pairs)
    return rounded(change)


def technical_result(validation):
    """A version's validation as the model should see it."""
    if not validation:
        return validation
    result = {k: v for k, v in validation.items() if k not in NOT_FOR_MODEL}
    result['measured_change_vs_source'] = measured_change(validation)
    return rounded(result)


def version_record(candidate, rendered_plans=()):
    """One earlier version for the planner. A critic revision plan that was then rendered (it is the next
    version's plan, already listed) is replaced by a pointer instead of being sent twice."""
    opinion = candidate.creative_review.model_dump(mode='json') if candidate.creative_review else None
    if opinion and opinion.get('revision_plan') is not None and candidate.id in rendered_plans:
        opinion = {**opinion, 'revision_plan': None, 'revision_plan_rendered_as': rendered_plans[candidate.id]}
    error = candidate.error or None
    return {'id': candidate.id, 'name': candidate.name, 'brief_sha256': candidate.brief_sha256, 'tier': candidate.tier,
            'plan': candidate.recipe.proposal.model_dump(mode='json') if candidate.recipe else None,
            'technical_result': technical_result(candidate.validation), 'creative_opinion': opinion,
            'revision_stop_reason': candidate.revision_stop_reason,
            'execution_error': {'code': error.get('code'), 'message': error.get('message'),
                                'observed_clipping': rounded(error.get('details', {}).get('observed_clipping'))} if error else None}


def previous_versions(candidates, limit=3):
    """The last `limit` ready or failed versions, compact (see version_record)."""
    shown = [c for c in candidates[-limit:] if c.status in {'ready', 'failed'}]
    rendered = {c.parent_id: c.name for c in candidates if c.parent_id}
    return [version_record(c, rendered) for c in shown]


def columns(records):
    """A list of per-frame records as {'columns': [...], 'rows': [[...], ...]}: field names written once.
    Fields missing from a record become null. as_rows() inverts it exactly for records without nulls."""
    if not records:
        return records
    keys = []
    for record in records:
        keys += [k for k in record if k not in keys]
    return {'columns': keys, 'rows': [[record.get(k) for k in keys] for record in records]}


def as_rows(table):
    return [{k: v for k, v in zip(table['columns'], row) if v is not None} for row in table['rows']]


def as_columns(context):
    """The critic context with its per-frame tables in column layout (same facts, fewer tokens)."""
    result = dict(context)
    levels = context.get('tonal_levels')
    if levels:
        result['tonal_levels'] = {role: columns(records) for role, records in levels.items()}
    if context.get('detail_retention'):
        result['detail_retention'] = columns(context['detail_retention'])
    paired = context.get('paired_diagnostics')
    if paired:
        result['paired_diagnostics'] = {**paired, **{k: columns(paired[k]) for k in ['paired_measurements', 'sampled_temporal_changes'] if paired.get(k)}}
    return result
