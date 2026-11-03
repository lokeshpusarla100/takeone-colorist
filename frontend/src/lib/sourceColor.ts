import type { IconName } from '../components/Icon';

/**
 * How the backend reads a source's colors (takeone/color_domain.py), turned into words for the person using the app.
 * Everything here tolerates a missing or odd record: sessions saved before color domains existed have none, and the
 * interface then simply says nothing about color.
 */
export type SourceDomain = {
  kind: 'native' | 'convert' | 'tonemap' | 'unsupported';
  matrix?: string | null; primaries?: string | null; transfer?: string | null; range?: string | null;
  bit_depth?: number | null; chroma?: string | null; hdr?: string | null;
  assumed?: string[]; steps?: string[]; reason?: string | null; message?: string | null; action?: string | null;
};
export type ColorNotice = { tone: 'info' | 'warn' | 'bad'; icon: IconName; title: string; lead: string; chips: string[]; steps: string[]; action?: string };

const NAMES: Record<string, string> = {
  bt709: 'Rec.709', bt470bg: 'BT.470BG', smpte170m: 'BT.601', smpte240m: 'SMPTE 240M', bt2020nc: 'BT.2020', bt2020: 'BT.2020', smpte431: 'DCI-P3', smpte432: 'Display P3',
};
const name = (value?: string | null) => (value && NAMES[value]) || value || '';
const texts = (v: unknown): string[] => Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && !!x.trim()) : [];

export function parseDomain(value: unknown): SourceDomain | null {
  if (typeof value !== 'object' || value === null) return null;
  const v = value as Record<string, unknown>;
  if (!['native', 'convert', 'tonemap', 'unsupported'].includes(v.kind as string)) return null;
  const text = (x: unknown) => typeof x === 'string' ? x : null;
  return {
    kind: v.kind as SourceDomain['kind'], matrix: text(v.matrix), primaries: text(v.primaries), transfer: text(v.transfer), range: text(v.range),
    bit_depth: typeof v.bit_depth === 'number' ? v.bit_depth : null, chroma: text(v.chroma), hdr: text(v.hdr),
    assumed: texts(v.assumed), steps: texts(v.steps), reason: text(v.reason), message: text(v.message), action: text(v.action),
  };
}

/** What is different about this footage, in a few words each. */
export function differences(d: SourceDomain): string[] {
  const out: string[] = [];
  if (d.hdr) out.push(`${d.hdr} HDR`);
  if ((d.bit_depth ?? 8) > 8) out.push(`${d.bit_depth}-bit`);
  if (d.range === 'full') out.push('Full range');
  if (d.matrix && d.matrix !== 'bt709' && !d.hdr) out.push(`${name(d.matrix)} matrix`);
  if (d.primaries && d.primaries !== 'bt709' && !d.hdr) out.push(`${name(d.primaries)} colors`);
  if (d.assumed?.some(a => a === 'matrix' || a === 'primaries' || a === 'transfer')) out.push('Color tags missing');
  return out;
}

export function colorNotice(domain: SourceDomain | null): ColorNotice | null {
  if (!domain || domain.kind === 'native') return null;
  const chips = differences(domain);
  const steps = domain.steps ?? [];
  if (domain.kind === 'unsupported') {
    return { tone: 'bad', icon: 'alert', title: 'This video can’t be graded yet', lead: domain.message || 'Its color format isn’t supported.', chips, steps: [], action: domain.action || undefined };
  }
  if (domain.kind === 'tonemap') {
    return { tone: 'warn', icon: 'info', title: `${domain.hdr ?? 'HDR'} video: converted to standard SDR`, chips, steps,
      lead: 'Take One grades in standard dynamic range. Your HDR video is converted to SDR first, which is an approximation of how an HDR screen shows it, and the graded video is delivered as SDR. Your original file is never changed.' };
  }
  return { tone: 'info', icon: 'info', title: 'Converted to Rec.709 before grading', chips, steps,
    lead: 'This video isn’t recorded as standard Rec.709, which is common for phones. Take One converts it first so colors stay true to what you shot. Your original file is never changed.' };
}

/** The “Color” tile for the footage facts. */
export function colorTile(domain: SourceDomain | null): { value: string; note?: string } | null {
  if (!domain) return null;
  if (domain.kind === 'native') return { value: 'Rec.709' };
  if (domain.kind === 'tonemap') return { value: `${domain.hdr ?? 'HDR'} → SDR`, note: 'tone-mapped' };
  if (domain.kind === 'unsupported') return { value: 'Not supported' };
  return { value: `${name(domain.matrix) || 'Source'} → Rec.709`, note: domain.assumed?.length ? 'assumed from the file' : 'converted' };
}

export const canGrade = (domain: SourceDomain | null) => !domain || domain.kind !== 'unsupported';
