import type { BackendState } from '../lib/api';
import { runProgress } from '../lib/workflow';
import { formatDuration, processingTimings } from '../lib/progressTiming';

export default function ProgressPanel({state,localBusy,localProgress,sending,online}:{
  state?:BackendState;localBusy:boolean;localProgress?:string|null;sending:boolean;online:boolean;
}){
  const progress=runProgress(state,localBusy,localProgress,sending);
  const [now,setNow]=useState(Date.now);
  useEffect(()=>{setNow(Date.now());if(!progress.working||!online)return;const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[progress.working,online,state?.id,state?.revision]);
  const timing=processingTimings(state,now,online);
  const stepIds=['prepare','inspect','clarify','brief','grade','masks','review'] as const;
  const names={pending:'Not started',active:'In progress',complete:'Complete',waiting:'Your answer needed',failed:'Stopped'};
  return <div className="run-progress">
    <div className="progress-square" role="status" aria-live="polite"><span className="progress-square-label">{progress.working?'Processing':state?.status==='awaiting_answer'?'Waiting for answer':'Current status'}</span><h3>{progress.heading}</h3>
      {!online&&<p className="inline-help">Backend unavailable. Status reflects the last saved update.</p>}
      {progress.working&&<progress aria-label="Processing in progress"/>}
      <div><span className="progress-square-label">Current attempt</span><strong className="progress-square-time" aria-label="Total processing time">{formatDuration(timing.total)}</strong></div>
      <p className="inline-help">{progress.note}</p>
    </div>
    <p className="inline-help">{!timing.recorded?'Timers start with new backend work; earlier steps were not timed.':!online?'Timer paused at the last saved update.':timing.interrupted?'Partial timing: interrupted work ends at its last saved update.':'Retried steps reset. Earlier completed steps keep their last time. Waiting is excluded.'}</p>
    {timing.recorded&&<details><summary>All attempts: {formatDuration(timing.lifetimeTotal)}</summary><p className="inline-help">Historical timings remain in the session log. Source preparation runs on import or an explicit source retry, not on render retry.</p></details>}
    <ol className="progress-steps">{progress.steps.map((step,index)=><li key={step.name} className={'step-'+step.phase}
      aria-current={step.phase==='active'||step.phase==='waiting'?'step':undefined}>
      <div><strong>{step.name}</strong><span>{names[step.phase]}<time aria-label={step.name+' time'}>{formatDuration(timing.byStep[stepIds[index]])}</time></span></div><p>{step.detail}</p>
      {stepIds[index]==='grade'&&<p className="step-timing-parts">Plan {formatDuration(timing.parts.plan)} · Render {formatDuration(timing.parts.render)} · Check {formatDuration(timing.parts.qc)}</p>}
      {stepIds[index]==='masks'&&timing.byStep.masks!==undefined&&<p className="step-timing-parts">Track {formatDuration(timing.parts.track)} · Validate {formatDuration(timing.parts.validate_masks)}</p>}
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
import { useEffect, useState } from 'react';
