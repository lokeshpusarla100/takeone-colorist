import type { BackendState } from '../lib/api';
import { runProgress } from '../lib/workflow';

export default function ProgressPanel({state,localBusy,localProgress,sending,online}:{
  state?:BackendState;localBusy:boolean;localProgress?:string|null;sending:boolean;online:boolean;
}){
  const progress=runProgress(state,localBusy,localProgress,sending);
  const names={pending:'Not started',active:'In progress',complete:'Complete',waiting:'Your answer needed',failed:'Stopped'};
  return <div className="run-progress">
    <div role="status" aria-live="polite"><h3>{progress.heading}</h3>
      {!online&&<p className="inline-help">Backend unavailable. Status reflects the last saved update.</p>}
      {progress.working&&<progress aria-label="Processing in progress"/>}
      <p className="inline-help">{progress.note}</p>
    </div>
    {state&&<p className="question-count">{state.answered_count} answered · up to {state.max_questions} questions, selected one at a time</p>}
    <ol className="progress-steps">{progress.steps.map(step=><li key={step.name} className={'step-'+step.phase}
      aria-current={step.phase==='active'||step.phase==='waiting'?'step':undefined}>
      <div><strong>{step.name}</strong><span>{names[step.phase]}</span></div><p>{step.detail}</p>
    </li>)}</ol>
    {state?.brief&&<section className="consultation-summary" aria-label="Consultation stopping reason">
      <h3>Why questioning ended</h3>
      <p>{state.consultation?.message??'The brief is ready. This older session did not save a specific stopping explanation.'}</p>
      {state.consultation?.model_explanation&&<p><strong>Muse explanation: </strong>{state.consultation.model_explanation}</p>}
      {state.consultation?.reason==='no_candidates'&&!state.consultation.model_explanation&&
        <p className="inline-help">Muse did not supply a specific explanation. Zero questions does not prove every creative choice was assessed.</p>}
      {!!state.consultation?.rejected_count&&<p className="inline-help">{state.consultation.rejected_count} validation rejection(s) recorded.</p>}
    </section>}
  </div>;
}
