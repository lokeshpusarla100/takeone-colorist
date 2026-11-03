/**
 * The backend records an "execution adjustment" on a recipe whenever the run itself changed what was planned.
 * Its shape depends on why: input-level clipping recovery carries `reason` and `changes`; choosing "use all matches"
 * for an ambiguous mask carries `concept` and `object_count` and neither of those; a record may also hold both.
 * Nothing about it is guaranteed, so this reads every field defensively and never throws.
 */
export type AdjustmentChange = { key: string; from: number; to: number };
export type AdjustmentView = { title: string; detail: string; changes: AdjustmentChange[] };

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const words = (key: string) => key.replace(/[_.:]+/g, ' ').trim();

export function describeAdjustment(raw: unknown): AdjustmentView | null {
  if (!isRecord(raw)) return null;
  const kind = typeof raw.kind === 'string' ? raw.kind : '';
  const concept = typeof raw.concept === 'string' && raw.concept.trim() ? raw.concept.trim() : '';
  const count = finite(raw.object_count) && raw.object_count > 0 ? Math.round(raw.object_count) : 0;
  const reason = typeof raw.reason === 'string' ? raw.reason.trim() : '';
  const changes: AdjustmentChange[] = [];
  if (isRecord(raw.changes)) {
    for (const [key, change] of Object.entries(raw.changes)) {
      if (isRecord(change) && finite(change.from) && finite(change.to)) changes.push({ key: words(key), from: change.from, to: change.to });
    }
  }
  let title = 'Adjusted while rendering';
  let detail = reason;
  if (kind === 'input_level_clipping_recovery') {
    title = 'Highlights and shadows protected';
    detail = detail || 'Black and white points were eased so the grade stays inside the clipping limits.';
  } else if (kind === 'user_selected_all_matches') {
    title = 'Every match graded';
    const target = concept ? `“${concept}”` : 'the ambiguous area';
    detail = `You chose to grade ${count > 1 ? `all ${count} matches` : 'every match'} of ${target} as one area.${reason ? ' ' + reason : ''}`;
  } else if (kind) {
    title = words(kind).replace(/^./, c => c.toUpperCase());
  }
  if (!detail && !changes.length && !kind) return null;
  return { title, detail, changes };
}

/** `black point: 0.0500 → 0.0300`, tolerant of any magnitude. */
export const formatChange = (c: AdjustmentChange) => `${c.key}: ${c.from.toFixed(4)} → ${c.to.toFixed(4)}`;
