import type { BackendState } from '../lib/api';
import { formatDuration } from '../lib/progressTiming';
import { stepProgress } from '../lib/stepper';
import type { RunModel } from '../lib/useRunModel';
import Icon from './Icon';

/** The run's state where you are already looking: over the video, from the same model as the rail. */
export default function RunStrip({ state, run, online, onCancel, cancelling }: { state?: BackendState; run: RunModel; online: boolean; onCancel: () => void; cancelling: boolean }) {
  const { progress, pipeline, rows } = run;
  const steps = stepProgress(rows);
  // Answers just sent: the request is in flight, so the strip follows that, not the stale "waiting" state.
  const waiting = state?.status === 'awaiting_answer' && !progress.working;
  if (!progress.working && !waiting) return null;
  const row = rows.find(r => r.phase === 'active' || r.phase === 'waiting');
  const fraction = row?.fraction;
  const canCancel = !!state && ['rendering', 'segmenting', 'reviewing', 'analyzing'].includes(state.status) && state.stage !== 'cancelling';
  return <div className={'run-strip' + (waiting ? ' waiting' : '')} role="status" aria-live="polite">
    <div className="run-strip-main">
      <span className="run-strip-icon">{waiting ? <Icon name="chat" size={20} /> : <span className="spinner" />}</span>
      <div className="run-strip-text">
        <strong>{waiting ? 'Muse has questions for you' : progress.heading}</strong>
        <span>{waiting ? <a href="#direction">Answer them under “Your direction”. Waiting for you isn’t counted.</a>
          : <>{steps.text}{pipeline?.detail ? ` · ${pipeline.detail}` : ''}{row?.ms != null && row.phase === 'active' ? ` · step time ${formatDuration(row.ms)}` : ''}</>}</span>
      </div>
      {canCancel && online && <button disabled={cancelling} onClick={onCancel}>Cancel</button>}
    </div>
    {!waiting && <div className={'meter-bar' + (fraction === undefined ? ' indeterminate' : '')} aria-hidden="true"><span style={fraction !== undefined ? { width: Math.round(fraction * 100) + '%' } : undefined} /></div>}
  </div>;
}
