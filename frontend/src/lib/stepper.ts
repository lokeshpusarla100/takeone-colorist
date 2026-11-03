import type { BackendState } from './api';
import { activeJob } from './api';
import { SEGMENT_ORDER } from './progressModel';
import type { Pipeline, SegmentId, SegmentPhase } from './progressModel';
import type { Visit } from './progressTiming';
import { lastTime } from './progressTiming';

/**
 * One list that says what the run is doing: the same segments and phases the progress model derives from the backend
 * stage, plus source preparation in front, each with its own time. The old panel showed two lists with different step
 * names; this is the only one.
 */
export type StepId = 'prepare' | SegmentId;
export type StepRow = {
  id: StepId; name: string; phase: SegmentPhase; ms: number | null;
  fraction?: number; detail?: string | null; last?: number | null;
};
export const STEP_NAME: Record<StepId, string> = {
  prepare: 'Prepare source', inspect: 'Watch the video', clarify: 'Questions', brief: 'Creative brief', plan: 'Plan the grade',
  masks: 'Subject masks (SAM)', render: 'Render', check: 'Quality check', review: 'Creative review',
};
export const STEP_BLURB: Record<StepId, string> = {
  prepare: 'Uploading and making a preview you can play.', inspect: 'Muse watches the whole clip once; the notes are reused later.',
  clarify: 'Muse asks only what would change the edit.', brief: 'Saving your direction, answers and limits.',
  plan: 'Choosing the controls from measured frames and your brief.', masks: 'SAM follows the subject and areas through every frame.',
  render: 'Encoding the graded video.', check: 'Checking timing, protected areas and clipping on the file.', review: 'Muse compares the result with the original.',
};

type Timing = { byStep: Partial<Record<string, number>>; parts: Partial<Record<string, number>>; visits: Visit[]; running: boolean };

export function runSteps(state: BackendState | undefined, pipeline: Pipeline | null, timing: Timing): StepRow[] {
  const sourceReady = !!state?.source && !!state.preview;
  const preparing = !!state && !sourceReady && (state.status === 'ingesting' || activeJob(state));
  const prepare: StepRow = {
    id: 'prepare', name: STEP_NAME.prepare, phase: sourceReady ? 'done' : preparing ? 'active' : state?.status === 'failed' ? 'failed' : 'pending',
    ms: timing.byStep.prepare ?? null,
  };
  const spent: Record<SegmentId, number | undefined> = {
    inspect: timing.byStep.inspect, clarify: timing.byStep.clarify, brief: timing.byStep.brief, plan: timing.parts.plan,
    masks: timing.byStep.masks, render: timing.parts.render, check: timing.parts.qc, review: timing.byStep.review,
  };
  const segments = pipeline?.segments ?? SEGMENT_ORDER.map(id => ({ id, label: '', phase: 'pending' as SegmentPhase, fraction: undefined as number | undefined }));
  const rows = segments.map(s => {
    const running = timing.running && (s.phase === 'active');
    const visit = running ? timing.visits.at(-1) : undefined;
    // The running step shows the clock of the visit in progress, the same number the "This step" tile shows.
    const ms = visit && visit.segment === s.id ? visit.ms : spent[s.id] ?? null;
    const row: StepRow = { id: s.id, name: STEP_NAME[s.id], phase: s.phase, ms, fraction: s.fraction };
    if (s.phase === 'active' || s.phase === 'waiting') row.last = lastTime(timing.visits, s.id, running);
    return row;
  });
  if (pipeline?.detail) { const active = rows.find(r => r.phase === 'active'); if (active) active.detail = pipeline.detail; }
  return [prepare, ...rows];
}

export type StepProgress = {
  /** Finished steps plus the measured fraction of the running one; steps that are not needed are not counted. */
  value: number; total: number;
  /** "Step 6 of 9: Subject masks (SAM)" - the same rows, names and count the list shows. */
  text: string; focus: StepRow | null;
};
export function stepProgress(rows: StepRow[]): StepProgress {
  const counted = rows.filter(r => r.phase !== 'skipped');
  const done = counted.filter(r => r.phase === 'done').length;
  const at = counted.findIndex(r => r.phase === 'active' || r.phase === 'waiting' || r.phase === 'failed');
  const focus = at >= 0 ? counted[at] : null;
  const running = counted.find(r => r.phase === 'active');
  const value = done + (running?.fraction ?? 0);
  const text = focus
    ? `Step ${at + 1} of ${counted.length}: ${focus.name}${focus.phase === 'waiting' ? ' (waiting for you)' : focus.phase === 'failed' ? ' (stopped)' : ''}`
    : counted.length > 1 && done === counted.length ? 'All steps complete' : `${done} of ${counted.length} steps complete`;
  return { value, total: counted.length, text, focus };
}
