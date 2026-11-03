import type { BackendState } from '../lib/api';
import { formatDuration } from '../lib/progressTiming';
import { STEP_BLURB, stepProgress } from '../lib/stepper';
import type { StepRow } from '../lib/stepper';
import type { RunModel } from '../lib/useRunModel';
import Chip from './Chip';
import type { Tone } from './Chip';
import Icon from './Icon';

const WORD: Record<StepRow['phase'], string> = { done: '', active: 'Running', waiting: 'Waiting for you', failed: 'Stopped', pending: '', skipped: 'Not needed' };
const PHASE: Record<StepRow['phase'], string> = { done: 'complete', active: 'running', waiting: 'waiting for you', failed: 'stopped', pending: 'not started', skipped: 'not needed' };

function Dot({ phase }: { phase: StepRow['phase'] }) {
  return <span className="step-dot" aria-hidden="true">
    {phase === 'done' ? <Icon name="check" size={14} /> : phase === 'failed' ? <Icon name="x" size={14} /> : phase === 'waiting' ? <Icon name="clock" size={14} />
      : phase === 'active' ? <span className="spinner" /> : phase === 'skipped' ? <span className="dash" /> : null}
  </span>;
}

/**
 * The run as one list. Every row, its state, its time and the measured fill come from the same backend stage and
 * timing markers, so the bar, the step names and the clocks cannot disagree.
 */
export default function ProgressPanel({ state, run, online }: { state?: BackendState; run: RunModel; online: boolean }) {
  const { progress, timing, pipeline, rows } = run;
  const steps = stepProgress(rows);
  const complete = state?.status === 'render_ready' && !progress.working;
  const stopped = state?.status === 'failed' || state?.status === 'cancelled';
  const tone: Tone = progress.working ? 'accent' : state?.status === 'awaiting_answer' ? 'warn' : stopped ? 'bad' : complete ? 'ok' : 'muted';
  const chip = progress.working ? 'Working' : state?.status === 'awaiting_answer' ? 'Waiting for you' : stopped ? 'Stopped' : complete ? 'Done' : 'Ready';
  const current = pipeline?.segments.find(s => s.phase === 'active' || s.phase === 'waiting');
  const activeRow = rows.find(r => r.id === current?.id);
  const activeTime = timing.run ?? timing.total;
  const stepTime = activeRow?.phase === 'active' ? activeRow.ms : null;
  const overall = Math.round(steps.value / steps.total * 100);
  const hasSource = !!state?.source && !!state.preview;
  return <div className="run-progress">
    <section className="card run-card" role="status" aria-live="polite">
      <div className="run-head"><Chip tone={tone} className="progress-chip">{chip}</Chip></div>
      <h3 className="run-title">{progress.heading}</h3>
      <p className="help run-note">{progress.note}</p>
      {!online && <p className="note warn"><Icon name="alert" size={16} />Backend unavailable. This shows the last saved update.</p>}
      {hasSource && <>
        <div className="overall" role="progressbar" aria-label="Run progress" aria-valuemin={0} aria-valuemax={steps.total}
          aria-valuenow={Math.round(steps.value * 100) / 100} aria-valuetext={steps.text}><span style={{ width: overall + '%' }} /></div>
        <p className="overall-text">{steps.text}{activeRow?.fraction !== undefined ? `, ${Math.round(activeRow.fraction * 100)}%` : ''}</p>
      </>}
      {(timing.recorded || progress.working) && <div className="clocks">
        <div><span className="clock-label">Active time</span><strong className="clock" aria-label="Total active processing time">{formatDuration(activeTime)}</strong>
          <small>Waiting for you isn’t counted</small></div>
        {activeRow && <div><span className="clock-label">This step</span><strong className="clock" aria-label="Current step time">{stepTime !== null ? formatDuration(stepTime) : activeRow.phase === 'waiting' ? '—' : formatDuration(0)}</strong>
          <small>{activeRow.last != null ? `Last time ${formatDuration(activeRow.last)}` : 'First time here'}</small></div>}
      </div>}
      {hasSource && <ol className="stepper" aria-label="Steps">
        {rows.map(row => <li key={row.id} className={'step step-' + row.phase} title={STEP_BLURB[row.id]}
          aria-current={row.phase === 'active' || row.phase === 'waiting' ? 'step' : undefined}>
          <Dot phase={row.phase} />
          <div className="step-main">
            <div className="step-line">
              <span className="step-name">{row.name}<span className="visually-hidden">, {PHASE[row.phase]}</span></span>
              {WORD[row.phase] && <span className="step-word">{WORD[row.phase]}</span>}
              {(row.phase === 'done' || row.phase === 'active' || row.phase === 'failed') && row.ms !== null && <time className="step-time" aria-label={row.name + ' time'}>{formatDuration(row.ms)}</time>}
            </div>
            {row.phase === 'active' && (row.fraction !== undefined
              ? <div className="meter-bar" role="progressbar" aria-label={row.name + ' progress'} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(row.fraction * 100)}><span style={{ width: Math.round(row.fraction * 100) + '%' }} /></div>
              : <div className="meter-bar indeterminate" aria-hidden="true"><span /></div>)}
            {row.detail && row.phase === 'active' && <p className="step-detail">{row.detail}</p>}
          </div>
        </li>)}
      </ol>}
      {(timing.recorded || (hasSource && !!state?.analysis)) && <p className="note timing-note">{!timing.recorded ? 'This session was recorded before step times were saved, so earlier steps have no time.' : !online ? 'Timer paused at the last saved update.'
        : timing.interrupted ? 'Partial timing: interrupted work ends at its last saved update.' : 'A retried step starts its own clock again.'}
        {timing.recorded && <> <span className="nowrap">All attempts: {formatDuration(timing.lifetimeTotal)}</span></>}</p>}
    </section>
    {state?.brief && <details className="card why-card">
      <summary>Why the questions ended</summary>
      <p>{state.consultation?.message ?? 'The brief is ready. This older session did not save a specific stopping explanation.'}</p>
      {state.consultation?.model_explanation && <p><strong>Muse said: </strong>{state.consultation.model_explanation}</p>}
      {state.consultation?.reason === 'no_candidates' && !state.consultation.model_explanation &&
        <p className="help">Muse gave no specific explanation. Asking no questions doesn’t prove every creative choice was considered.</p>}
      {!!state.consultation?.rejected_count && <p className="help">{state.consultation.rejected_count} validation rejection(s) recorded.</p>}
    </details>}
  </div>;
}
