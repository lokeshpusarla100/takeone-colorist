import type { GradeValues } from './api';

/**
 * One row per grade control. `min`/`max` are the bounds the backend enforces (takeone/contracts.py GradeParameters),
 * so a meter's full width is exactly what the grader is allowed to do; `neutral` is the value that changes nothing.
 */
export type Control = { key: keyof GradeValues; label: string; min: number; max: number; neutral: number; hint: string; additive?: boolean };
export const CONTROLS: Control[] = [
  { key: 'contrast', label: 'Contrast', min: 0.75, max: 1.25, neutral: 1, hint: 'Separation between darks and lights. 1.00 leaves it unchanged.' },
  { key: 'saturation', label: 'Saturation', min: 0, max: 1.5, neutral: 1, hint: 'Color intensity. 0 is black and white, 1.00 leaves it unchanged.' },
  { key: 'gamma', label: 'Gamma', min: 0.8, max: 1.2, neutral: 1, hint: 'Midtone curve. 1.00 leaves it unchanged.' },
  { key: 'brightness_gain', label: 'Brightness', min: 0.65, max: 1.35, neutral: 1, hint: 'Overall light level. 1.00 leaves it unchanged.' },
  { key: 'cooling', label: 'Cooling', min: -0.15, max: 0.15, neutral: 0, additive: true, hint: 'Shift of the color balance toward cool tones. 0 leaves it unchanged.' },
  { key: 'black_point', label: 'Black point', min: 0, max: 0.15, neutral: 0, additive: true, hint: 'Input level treated as pure black. 0 leaves it unchanged.' },
  { key: 'white_point', label: 'White point', min: 0.75, max: 1, neutral: 1, hint: 'Input level treated as pure white. 1.00 leaves it unchanged.' },
  { key: 'highlight_ceiling', label: 'Highlight ceiling', min: 0.6, max: 1, neutral: 1, hint: 'How far the brightest highlights are rolled down. 1.00 leaves them unchanged.' },
];

export type Layer = { id: string; name: string; role: 'whole' | 'subject' | 'region' | 'background'; values: Partial<GradeValues> };
type Recipe = { proposal?: { parameters?: GradeValues; mode?: string; local?: null | { concept?: string; foreground?: GradeValues; background?: GradeValues; regions?: { concept: string; treatment?: GradeValues }[] } } } | null | undefined;

const sentence = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** The areas a recipe grades separately, in reading order: subject, each named region, then everything else. */
export function layersOf(recipe: Recipe): Layer[] {
  const proposal = recipe?.proposal;
  if (!proposal) return [];
  const local = proposal.local;
  if (!local) return proposal.parameters ? [{ id: 'whole', name: 'Whole frame', role: 'whole', values: proposal.parameters }] : [];
  const layers: Layer[] = [];
  if (local.foreground) layers.push({ id: 'subject', name: local.concept ? sentence(local.concept) : 'Subject', role: 'subject', values: local.foreground });
  (local.regions ?? []).forEach((r, i) => { if (r.treatment) layers.push({ id: 'region:' + i, name: sentence(r.concept), role: 'region', values: r.treatment }); });
  if (local.background) layers.push({ id: 'background', name: 'Everything else', role: 'background', values: local.background });
  return layers;
}

export type Cell = {
  /** Where the value sits on the track, 0..1, and where "unchanged" sits. */
  at: number; neutralAt: number; delta: number; changed: boolean; text: string; description: string;
};
const clamp = (n: number) => Math.min(1, Math.max(0, n));

export function cellFor(control: Control, value: number | undefined): Cell | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  const span = control.max - control.min;
  const at = clamp((value - control.min) / span);
  const neutralAt = clamp((control.neutral - control.min) / span);
  const delta = value - control.neutral;
  const changed = Math.abs(delta) > 1e-9;
  const text = control.key === 'cooling' || control.key === 'black_point' ? value.toFixed(3) : value.toFixed(2);
  const description = !changed ? 'unchanged'
    : control.additive ? `${delta > 0 ? '+' : '−'}${Math.abs(delta).toFixed(3)} from neutral`
      : `${delta > 0 ? '+' : '−'}${Math.abs(delta / control.neutral * 100).toFixed(0)}% from neutral`;
  return { at, neutralAt, delta, changed, text, description };
}

/** Controls that any layer actually moves; the table leaves out rows where nothing differs from neutral. */
export function activeControls(layers: Layer[]): Control[] {
  return CONTROLS.filter(c => layers.some(l => { const cell = cellFor(c, l.values[c.key]); return cell?.changed; }));
}
