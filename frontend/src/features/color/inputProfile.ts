/**
 * The input profile: how a person tells Take One to read footage whose own file tags cannot be trusted (camera log, flat
 * profiles). The backend is takeone/color/profile.py; this is everything the interface decides from its answers, kept free
 * of React so it can be tested.
 *
 * Every parser tolerates a missing or odd value and returns null: an older backend, or a project saved before this existed,
 * must simply show nothing, never break the page.
 */
export type Levels = { black: number; white: number; p1: number; median: number; p99: number; flat: boolean; samples: number };
export type ProfileSummary = { kind: 'normalize' | 'lut'; label: string; steps: string[]; signature: string; lut_name: string | null; black: number | null; white: number | null };
export type Problem = { code: string; message: string; action: string };
export type ProfileInfo = { levels: Levels | null; profile: ProfileSummary | null; error: Problem | null; applicable: boolean; applicableReason: string; locked: string | null; suggest: 'normalize' | null };
export type Choice = { kind: 'none' | 'normalize' | 'lut'; lut_id?: string; lut_name?: string };
export type LutInfo = { lut_id: string; name: string; kind: '3d' | '1d'; size: number; title: string };
export type StillPair = { before: string; after: string; afterLevels: Levels | null; warnings: string[] };

const rec = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const num = (v: unknown): number | null => typeof v === 'number' && Number.isFinite(v) ? v : null;
const str = (v: unknown): string | null => typeof v === 'string' && v.trim() ? v : null;
const strings = (v: unknown): string[] => Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && !!x.trim()) : [];

export function parseLevels(v: unknown): Levels | null {
  if (!rec(v)) return null;
  const [black, white, p1, median, p99, samples] = ['black', 'white', 'p1', 'median', 'p99', 'samples'].map(k => num(v[k]));
  if (black === null || white === null || p1 === null || median === null || p99 === null || samples === null || typeof v.flat !== 'boolean') return null;
  return { black, white, p1, median, p99, flat: v.flat, samples };
}

export function parseSummary(v: unknown): ProfileSummary | null {
  if (!rec(v) || (v.kind !== 'normalize' && v.kind !== 'lut')) return null;
  const label = str(v.label), signature = str(v.signature);
  if (!label || !signature) return null;
  return { kind: v.kind, label, steps: strings(v.steps), signature, lut_name: str(v.lut_name), black: num(v.black), white: num(v.white) };
}

export function parseProblem(v: unknown): Problem | null {
  if (!rec(v)) return null;
  const message = str(v.message);
  return message ? { code: str(v.code) ?? 'UNKNOWN', message, action: str(v.action) ?? '' } : null;
}

export function parseInfo(v: unknown): ProfileInfo | null {
  if (!rec(v) || typeof v.applicable !== 'boolean') return null;
  return { levels: parseLevels(v.levels), profile: parseSummary(v.profile), error: parseProblem(v.error), applicable: v.applicable,
    applicableReason: str(v.applicable_reason) ?? '', locked: str(v.locked), suggest: v.suggest === 'normalize' ? 'normalize' : null };
}

export function parseLut(v: unknown): LutInfo | null {
  if (!rec(v) || !str(v.lut_id) || !str(v.name) || (v.kind !== '3d' && v.kind !== '1d') || num(v.size) === null) return null;
  return { lut_id: v.lut_id as string, name: v.name as string, kind: v.kind, size: v.size as number, title: str(v.title) ?? '' };
}

export function parseStills(v: unknown): StillPair | null {
  if (!rec(v)) return null;
  const image = (x: unknown) => typeof x === 'string' && x.startsWith('data:image/jpeg;base64,') ? x : null;
  const before = image(v.before), after = image(v.after);
  return before && after ? { before, after, afterLevels: parseLevels(v.after_levels), warnings: strings(v.warnings) } : null;
}

export const percent = (x: number) => `${Math.round(Math.min(1, Math.max(0, x)) * 100)}%`;

/** The clip's brightness range as one plain sentence. */
export function levelsSentence(l: Levels): string {
  return `Its darkest areas are about ${percent(l.black)} bright and its brightest about ${percent(l.white)}.`;
}

/** The clip's range as a bar: where the filled part starts and how wide it is, in percent of the full scale. */
export function levelsBar(l: Levels): { left: number; width: number } {
  const left = Math.min(100, Math.max(0, l.black * 100));
  return { left, width: Math.max(1, Math.min(100, l.white * 100) - left) };
}

/** Whether stretching this clip's own range would do anything (the backend's rule, so the button can say why not). */
export function canNormalize(l: Levels | null): { ok: boolean; reason?: string } {
  if (!l) return { ok: false, reason: 'This clip’s brightness could not be measured.' };
  if (l.black < 0.03 && l.white > 0.95) return { ok: false, reason: 'This clip already uses the full range from dark to bright.' };
  if (l.black > 0.45 || l.white < 0.5 || l.white - l.black < 0.2) return { ok: false, reason: 'This clip’s range is too narrow to stretch without adding noise.' };
  return { ok: true };
}

export type Option = { kind: Choice['kind']; label: string; hint: string; badge?: string; disabled?: string };

/** The ways to read this footage, in the order they are offered. */
export function options(info: ProfileInfo): Option[] {
  const stretch = canNormalize(info.levels);
  return [
    { kind: 'none', label: 'Keep it as it is', hint: 'Use the video’s own colors. Right for almost every video.' },
    { kind: 'lut', label: 'Use the camera’s LUT', hint: 'Upload the .cube file your camera maker publishes for this camera. This is the accurate way.', badge: 'Most accurate' },
    { kind: 'normalize', label: 'Fix the contrast automatically', hint: 'Stretches this clip’s own darkest and brightest points to the full range. A quick approximation when you have no LUT.',
      badge: info.suggest ? 'Suggested' : undefined, disabled: stretch.ok ? undefined : stretch.reason },
  ];
}

export type CardView =
  | { mode: 'hidden' }
  | { mode: 'locked'; profile: ProfileSummary }
  | { mode: 'choose'; tone: 'warn' | 'info' | 'ok'; title: string; lead: string; open: boolean; options: Option[]; current: Choice['kind'] };

/** What the card shows for this project right now. Hidden for footage a profile does not apply to, and once it is too late to change. */
export function cardView(info: ProfileInfo | null, status: string): CardView {
  if (!info) return { mode: 'hidden' };
  if (info.locked) return info.profile ? { mode: 'locked', profile: info.profile } : { mode: 'hidden' };
  if (!info.applicable || status !== 'ready') return { mode: 'hidden' };
  const opts = options(info);
  if (info.profile) {
    return { mode: 'choose', tone: 'ok', title: 'Footage color: ' + info.profile.label, open: false, options: opts, current: info.profile.kind,
      lead: info.profile.steps[0] ?? 'This choice is used for the preview, Muse’s view of the video and the grade.' };
  }
  if (info.suggest && info.levels) {
    return { mode: 'choose', tone: 'warn', title: 'This video looks flat', open: true, options: opts, current: 'none',
      lead: `${levelsSentence(info.levels)} That is typical of camera “log” or “flat” profiles, which are meant to be corrected. If you shot it that way, choose how to correct it. If the flat look is what you want, keep it as it is.` };
  }
  return { mode: 'choose', tone: 'info', title: 'Shot in log or a flat profile?', open: false, options: opts, current: 'none',
    lead: 'Most videos need nothing here. If this one looks washed out because it was shot in a camera’s log mode, choose how to correct it.' };
}

export const sameChoice = (a: Choice, b: Choice) => a.kind === b.kind && (a.lut_id ?? '') === (b.lut_id ?? '');

/** Whether the person can apply what they have picked, and if not, what is missing. */
export function readiness(choice: Choice, info: ProfileInfo, stills: StillPair | null): { ok: boolean; reason?: string } {
  if (choice.kind === 'lut' && !choice.lut_id) return { ok: false, reason: 'Choose the LUT file first.' };
  const current: Choice = { kind: info.profile?.kind ?? 'none' };
  if (choice.kind !== 'lut' && sameChoice(choice, current)) return { ok: false, reason: 'This is already how the video is read.' };
  if (choice.kind !== 'none' && !stills) return { ok: false, reason: 'Looking at the result…' };
  return { ok: true };
}
