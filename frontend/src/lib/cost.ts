import type { SessionEvent } from './types';
import { tokenUsage } from './tokenUsage';
import type { Stage, UsageRow } from './tokenUsage';

/** Rates the backend publishes in /api/health (Settings.pricing). Everything here is an estimate, never a bill. */
export type Pricing = {
  currency: 'USD'; basis: string; source: string; checked: string;
  muse: { model: string; input_per_million: number; cached_input_per_million: number; output_per_million: number };
  sam: { model: string; per_thousand_frames: number; min_billed_frames: number };
};
export type CostLine = { id: string; label: string; stage: Stage; usd: number | null; detail: string };
export type StageCost = { stage: Stage; label: string; usd: number; share: number };
export type ProjectCost = {
  total: number; muse: number; sam: number;
  stages: StageCost[]; lines: CostLine[];
  /** Requests whose usage was never reported (still running, failed or cancelled early): not in the total. */
  unpriced: number; pending: boolean; priced: boolean;
};

export const STAGE_LABEL: Record<Stage, string> = {
  analysis: 'Watching the video', rubric: 'Questions', colorist: 'Planning the grade', sam: 'Subject masks (SAM)', critic: 'Creative review',
};
const rate = (v: unknown) => typeof v === 'number' && Number.isFinite(v) && v >= 0;

/** Accepts only a complete, sane price list; anything else means "rates unavailable", never a wrong number. */
export function parsePricing(value: unknown): Pricing | null {
  const o = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null;
  if (!o(value) || value.currency !== 'USD' || !o(value.muse) || !o(value.sam)) return null;
  const { muse, sam } = value;
  if (![muse.input_per_million, muse.cached_input_per_million, muse.output_per_million, sam.per_thousand_frames, sam.min_billed_frames].every(rate)) return null;
  return value as unknown as Pricing;
}

/** Dollars for one reported request, or null when the provider did not report enough to price it. */
export function priceRow(row: UsageRow, pricing: Pricing): number | null {
  if (row.stage === 'sam') {
    const frames = row.processedFrames;
    if (frames === null || frames === undefined) return null;
    if (frames === 0) return 0;                                   // nothing was processed, so nothing is billed
    return Math.max(frames, pricing.sam.min_billed_frames) * pricing.sam.per_thousand_frames / 1000;
  }
  if (row.input === null || row.output === null) return null;
  const cached = Math.min(row.cached ?? 0, row.input);           // cached tokens are a subset of the input count
  return ((row.input - cached) * pricing.muse.input_per_million + cached * pricing.muse.cached_input_per_million
    + row.output * pricing.muse.output_per_million) / 1e6;       // reasoning tokens are part of the output count
}

export function projectCost(events: SessionEvent[], pricing: Pricing | null, activeStage?: string): ProjectCost {
  const usage = tokenUsage(events, activeStage);
  const empty: ProjectCost = { total: 0, muse: 0, sam: 0, stages: [], lines: [], unpriced: 0, pending: usage.running, priced: false };
  if (!pricing) return { ...empty, unpriced: usage.rows.length };
  const lines: CostLine[] = [];
  const byStage = new Map<Stage, number>();
  let unpriced = 0;
  for (const row of usage.rows) {
    const usd = row.reported ? priceRow(row, pricing) : null;
    if (usd === null) { if (!row.running) unpriced++; }
    else byStage.set(row.stage, (byStage.get(row.stage) ?? 0) + usd);
    lines.push({
      id: row.id, label: STAGE_LABEL[row.stage], stage: row.stage, usd,
      detail: row.stage === 'sam'
        ? (row.processedFrames != null ? `${row.processedFrames.toLocaleString()} video frames` : 'frames not reported')
        : (row.input !== null && row.output !== null ? `${row.input.toLocaleString()} in · ${row.output.toLocaleString()} out` : 'tokens not reported'),
    });
  }
  const total = [...byStage.values()].reduce((a, b) => a + b, 0);
  const order: Stage[] = ['analysis', 'rubric', 'colorist', 'sam', 'critic'];
  const stages = order.filter(s => byStage.has(s)).map(stage => ({
    stage, label: STAGE_LABEL[stage], usd: byStage.get(stage)!, share: total > 0 ? byStage.get(stage)! / total : 0,
  }));
  const sam = byStage.get('sam') ?? 0;
  return { total, muse: total - sam, sam, stages, lines, unpriced, pending: usage.running, priced: true };
}

/** `$0.80`, and four decimals below a cent so small runs never display as free. Unknown stays unknown. */
export function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  if (value === 0) return '$0.00';
  if (value < 0.01) return '$' + value.toFixed(4);
  return '$' + value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
