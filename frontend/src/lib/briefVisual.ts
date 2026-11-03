import type { IconName } from '../components/Icon';

/**
 * The backend writes a brief's safeguards and technical envelope as plain sentences. These helpers sort those sentences
 * into things a person can scan (guarantees, facts about the footage, what can be adjusted, limits, what will not be
 * done). Nothing is dropped or reworded: every sentence is still shown in full beside its icon, and anything that is
 * not recognised lands in `notes`. They depend on sentence shape and a few keywords, never on a particular video.
 */
export type Safeguard = { icon: IconName; title: string; text: string };
export type Fact = { label: string; value: string; note?: string };
/** `lead` is the clause before a semicolon (shown in bold) and `rest` what follows; with no semicolon the whole sentence is `rest`. */
export type Limit = { icon: IconName; lead: string; rest: string };
export type Handling = { icon: IconName; text: string };
export type Envelope = {
  facts: Fact[]; supported: string[]; supportedNote: string; limits: Limit[]; handling: Handling[]; wont: string[]; notes: string[];
};

const clean = (s: string) => s.replace(/\s+/g, ' ').trim();
const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const sentences = (s: string) => clean(s).split(/(?<=[.!?])\s+(?=[A-Z0-9])/).map(x => x.trim()).filter(Boolean);

/** The first clause: up to a semicolon or full stop, shortened so it fits a heading. */
export function headline(text: string, max = 46): string {
  const first = clean(text).split(/;|\.(?:\s|$)/)[0].trim();
  return first.length > max ? first.slice(0, max - 1).trimEnd() + '…' : first;
}

const SAFEGUARDS: [RegExp, IconName, string][] = [
  [/reversib|original media|unchanged/i, 'undo', 'Original stays untouched'],
  [/identity|invent|reconstruct/i, 'user', 'Nothing is invented'],
  [/recolou?r|product|marking/i, 'tag', 'Products keep their color'],
  [/legib|temporal|artifact/i, 'eye', 'Subject stays clear'],
];
export function safeguards(items: string[]): Safeguard[] {
  return items.map(clean).filter(Boolean).map(text => {
    const hit = SAFEGUARDS.find(([re]) => re.test(text));
    return hit ? { icon: hit[1], title: hit[2], text } : { icon: 'shield', title: headline(text), text };
  });
}

const CODECS: Record<string, string> = { h264: 'H.264', avc1: 'H.264', hevc: 'HEVC (H.265)', h265: 'HEVC (H.265)', vp9: 'VP9', av1: 'AV1', prores: 'ProRes', mpeg4: 'MPEG-4' };
export const codecName = (codec: string) => CODECS[codec.toLowerCase()] ?? codec.toUpperCase();
const SIZES: Record<string, string> = { '3840x2160': '4K UHD', '4096x2160': 'DCI 4K', '2560x1440': 'QHD', '1920x1080': 'Full HD', '1280x720': 'HD', '720x1280': 'HD portrait', '1080x1920': 'Full HD portrait', '2160x3840': '4K portrait' };
export const sizeName = (w: number, h: number) => SIZES[`${w}x${h}`] ?? '';
export const colorName = (space?: string | null) => !space ? '' : /709/.test(space) ? 'Rec.709' : /2020/.test(space) ? 'Rec.2020' : /601|170m|470/.test(space) ? 'Rec.601' : space;

export function seconds(value: number): string {
  if (!Number.isFinite(value)) return '—';
  return value >= 60 ? `${Math.floor(value / 60)} min ${(value % 60).toFixed(0)} s` : `${value.toFixed(1)} s`;
}
export function megabytes(bytes: number): string {
  return bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} GB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

const SOURCE = /^Source:\s*(\d+)\s*[x×]\s*(\d+)\s*,\s*([\d.]+)\s*s\s*;\s*([^;]+?)\s*;\s*([^.;]+?)\s*\.?$/i;
const SUPPORTED = /\b(?:is|are)\s+supported\b/i;

function limitIcon(text: string): IconName {
  if (/audio|silent|sound/i.test(text)) return 'mute';
  if (/visual|camera|metadata/i.test(text)) return 'eye';
  if (/numer|measur|qualitative|estimate/i.test(text)) return 'ruler';
  if (/detail|distance|compress|resolution/i.test(text)) return 'layers';
  return 'info';
}
function handlingIcon(text: string): IconName {
  if (/rotat/i.test(text)) return 'rotate';
  if (/timing|frame|pts|timestamp/i.test(text)) return 'clock';
  if (/mask|sam\b/i.test(text)) return 'mask';
  return 'check';
}
const sentenceCase = (s: string) => /[.!?]$/.test(s) ? capital(s) : capital(s) + '.';
export function splitLead(sentence: string): { lead: string; rest: string } {
  const text = clean(sentence).replace(/\.$/, '');
  const at = text.indexOf(';');
  if (at <= 0 || at === text.length - 1) return { lead: '', rest: sentenceCase(text) };
  return { lead: text.slice(0, at).trim(), rest: sentenceCase(text.slice(at + 1).trim()) };
}
/** "No physical relighting, reconstruction or shot matching." -> three things that will not be done. */
function refusals(sentence: string): string[] {
  const body = sentence.replace(/^no\s+/i, '').replace(/\.$/, '');
  return body.split(/,\s*|\s+or\s+|\s+and\s+/).map(clean).filter(Boolean);
}

export function parseEnvelope(items: string[]): Envelope {
  const out: Envelope = { facts: [], supported: [], supportedNote: '', limits: [], handling: [], wont: [], notes: [] };
  for (const raw of items.map(clean).filter(Boolean)) {
    const source = SOURCE.exec(raw);
    if (source) {
      const [, w, h, dur, codec, pixel] = source;
      out.facts.push({ label: 'Resolution', value: `${w} × ${h}`, note: sizeName(+w, +h) },
        { label: 'Duration', value: seconds(+dur) }, { label: 'Codec', value: codecName(codec) }, { label: 'Pixel format', value: pixel });
      continue;
    }
    const parts = sentences(raw);
    const supportedAt = parts.findIndex(p => SUPPORTED.test(p));
    if (supportedAt >= 0) {
      const list = parts[supportedAt].replace(/\s+(?:is|are)\s+supported\.?$/i, '');
      out.supported.push(...list.split(/,\s*|\s+and\s+/).map(clean).filter(Boolean).map(capital));
      out.supportedNote = [out.supportedNote, ...parts.filter((_, i) => i !== supportedAt)].filter(Boolean).join(' ');
      continue;
    }
    if (parts.length >= 3) {                          // a paragraph about how the footage is handled
      for (const sentence of parts) {
        if (/^no\s/i.test(sentence)) out.wont.push(...refusals(sentence));
        else out.handling.push({ icon: handlingIcon(sentence), text: sentence });
      }
      continue;
    }
    if (parts.length) out.limits.push(...parts.map(text => ({ icon: limitIcon(text), ...splitLead(text) })));
    else out.notes.push(raw);
  }
  return out;
}
