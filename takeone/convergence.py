"""Measured revision progress and metric-based final-candidate choice.

The critic labels each issue with an optional measurable ``target`` (contracts.IssueTarget).
Progress is measured on the renderer's own sampled output frames, which every candidate of a
brief shares (revisions reuse the parent's evidence selection), so candidates are compared on
identical frames. "Visible" is tonal.VISIBLE_CHANGE; nothing here is fitted to one video.

* ``revision_progress``: did a revision move each targeted metric the asked-for way, versus its
  parent? If no targeted issue moved visibly the right way, the revision was ineffective: the
  chosen operation does not fix the issue, so escalating it again is pointless.
* ``rank_candidates``: when the loop stops with unresolved targeted issues and the critic did not
  name a preferred candidate, choose the candidate that does the least harm against the source
  (fewest visible wrong-way moves, then the smallest worst one, then the most progress), instead
  of defaulting to the last render.
"""
from .tonal import VISIBLE_CHANGE

# target -> (metric, sign). sign +1 means the issue asks for the metric to go up.
TARGETS = {
    'darker_highlights': ('highlights', -1), 'brighter_highlights': ('highlights', 1),
    'darker_midtones': ('midtones', -1), 'brighter_midtones': ('midtones', 1),
    'deeper_shadows': ('shadows', -1), 'lifted_shadows': ('shadows', 1),
    'cooler': ('warmth', -1), 'warmer': ('warmth', 1),
    'more_contrast': ('contrast', 1), 'less_contrast': ('contrast', -1),
    'more_saturation': ('saturation', 1), 'less_saturation': ('saturation', -1),
}


def metric(record, name):
    """One whole-frame metric from an evidence.measure record, or None if the record predates it."""
    if name == 'highlights':
        return record.get('p95_encoded_luma')
    if name == 'midtones':
        return record.get('median_encoded_luma')
    if name == 'shadows':
        return record.get('p05_encoded_luma')
    if name == 'contrast':
        high, low = record.get('p95_encoded_luma'), record.get('p05_encoded_luma')
        return None if high is None or low is None else high - low
    if name == 'warmth':
        rgb = record.get('median_rgb')
        return None if rgb is None else rgb[0] - rgb[2]
    if name == 'saturation':
        return record.get('mean_chroma')
    raise ValueError(name)


def _mean_change(after, before, name):
    """Mean per-frame change of a metric on the frames both record sets share, or None."""
    a = {r['frame_index']: r for r in after}
    b = {r['frame_index']: r for r in before}
    changes = []
    for index in sorted(a.keys() & b.keys()):
        x, y = metric(a[index], name), metric(b[index], name)
        if x is not None and y is not None:
            changes.append(x - y)
    return sum(changes) / len(changes) if changes else None


def targeted(issues):
    return [i for i in issues if i.correctable and i.target in TARGETS]


def revision_progress(child_records, parent_records, issues):
    """Per targeted issue: signed progress of the child over its parent (positive = the asked-for way)."""
    rows = []
    for issue in targeted(issues):
        name, sign = TARGETS[issue.target]
        change = _mean_change(child_records, parent_records, name)
        if change is not None:
            rows.append({'issue': issue.id, 'target': issue.target, 'metric': name, 'progress': sign * change,
                         'visible_progress': sign * change >= VISIBLE_CHANGE})
    return {'rows': rows, 'visible_change': VISIBLE_CHANGE,
            'ineffective': bool(rows) and not any(r['visible_progress'] for r in rows)}


def rank_candidates(candidates, issues):
    """Rank (candidate, output_records, source_records) tuples by measured fit to the targeted issues.

    Returns [(candidate, score)] best first; empty when there is nothing measurable to rank on.
    """
    goals = targeted(issues)
    if not goals:
        return []
    ranked = []
    for candidate, output_records, source_records in candidates:
        regressions, progress, measured = [], 0., 0
        for issue in goals:
            name, sign = TARGETS[issue.target]
            change = _mean_change(output_records, source_records, name)
            if change is None:
                continue
            measured += 1
            moved = sign * change
            progress += moved
            if moved <= -VISIBLE_CHANGE:
                regressions.append(-moved)
        if measured:
            score = {'regressions': len(regressions), 'worst_regression': max(regressions, default=0.),
                     'progress': progress, 'measured_issues': measured}
            ranked.append((candidate, score))
    ranked.sort(key=lambda item: (item[1]['regressions'], item[1]['worst_regression'], -item[1]['progress']))
    return ranked
