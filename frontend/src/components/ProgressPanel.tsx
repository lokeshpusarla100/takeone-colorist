import { useEffect, useState } from 'react';
import type { BackendState } from '../lib/api';
import { runProgress } from '../lib/workflow';
import { formatDuration, lastTime, processingTimings } from '../lib/progressTiming';
import { runPipeline } from '../lib/progressModel';
import { useLiveProgress } from '../lib/liveProgress';

export default function ProgressPanel({state,localBusy,localProgress,sending,online}:{
  state?:BackendState;localBusy:boolean;localProgress?:string|null;sending:boolean;online:boolean;
}){
  const progress=runProgress(state,localBusy,localProgress,sending);
  const [now,setNow]=useState(Date.now);
  useEffect(()=>{setNow(Date.now());if(!progress.working||!online)return;const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[progress.working,online,state?.id,state?.revision]);
  const timing=processingTimings(state,now,online);
  const live=useLiveProgress(state,online);
  const pipeline=runPipeline(state,live);
  const stepIds=['prepare','inspect','clarify','brief','grade','masks','review'] as const;
  const names={pending:'Not started',active:'In progress',complete:'Complete',waiting:'Your answer needed',failed:'Stopped',skipped:'Not needed'};
  const complete=state?.status==='render_ready'&&!progress.working;
  const chip=progress.working?'Processing':state?.status==='awaiting_answer'?'Waiting for you':state?.status==='failed'||state?.status==='cancelled'?'Stopped':complete?'Complete':'Status';
  const chipClass=progress.working?'working':state?.status==='awaiting_answer'?'waiting':chip==='Stopped'?'failed':complete?'complete':'idle';
  // The step that is running now, with its own clock and how long the same step took last time.
  const current=pipeline?.segments.find(s=>s.phase==='active'||s.phase==='waiting');
  const visit=timing.running&&current?timing.visits.at(-1):undefined;
  const stepNow=visit&&visit.segment===current?.id?visit.ms:null;
  const previous=current?lastTime(timing.visits,current.id,timing.running&&stepNow!==null):null;
  // Waiting for the first backend marker of a request just sent: the clock holds its value instead of restarting or lagging.
  const activeTime=timing.run??timing.total;
  return <div className="run-progress">
    <div className="progress-card" role="status" aria-live="polite">
      <div className="progress-head"><span className={'progress-chip chip-'+chipClass}>{chip}</span><h3>{progress.heading}</h3></div>
      {!online&&<p className="inline-help">Backend unavailable. Status reflects the last saved update.</p>}
      {pipeline?<>
        <ol className="pipeline" role="progressbar" aria-label="Run progress" aria-valuemin={0} aria-valuemax={pipeline.total}
          aria-valuenow={Math.round(pipeline.value*100)/100} aria-valuetext={pipeline.text}>
          {pipeline.segments.map(segment=><li key={segment.id} className={'pipe-'+segment.phase} title={segment.label+': '+({done:'complete',active:'running',waiting:'waiting for you',failed:'stopped',pending:'not started',skipped:'not needed'})[segment.phase]}>
            <span className={'pipe-bar'+(segment.phase==='active'&&segment.fraction===undefined?' pipe-indeterminate':'')}>
              <span className="pipe-fill" style={segment.fraction!==undefined?{width:`${Math.round(segment.fraction*100)}%`}:undefined}/></span>
            <span className="pipe-label">{segment.label}</span></li>)}
        </ol>
        {pipeline.measured&&current?.fraction!==undefined&&<div className="stage-meter" aria-hidden="true"><span style={{width:`${Math.round(current.fraction*100)}%`}}/></div>}
        <p className="pipeline-caption">{pipeline.text}{pipeline.detail?<><br/><strong>{pipeline.detail}</strong></>:null}</p>
      </>:progress.working&&<progress aria-label="Processing in progress"/>}
      <div className="progress-times">
        <div><span className="progress-square-label">Active time</span><strong className="progress-time" aria-label="Total active processing time">{formatDuration(activeTime)}</strong>
          <small>this direction · waiting for you is not counted</small></div>
        {current&&<div><span className="progress-square-label">This step · {current.label}</span>
          <strong className="progress-time" aria-label="Current step time">{stepNow!==null?formatDuration(stepNow):current.phase==='waiting'?'—':formatDuration(0)}</strong>
          <small>{previous!==null?`last time ${formatDuration(previous)}`:'first time here'}</small></div>}
      </div>
      <p className="inline-help progress-note">{progress.note}</p>
    </div>
    <p className="inline-help">{!timing.recorded?'Timers start with new backend work; earlier steps were not timed.':!online?'Timer paused at the last saved update.':timing.interrupted?'Partial timing: interrupted work ends at its last saved update.':'A retried step starts its own clock again; waiting for you is never counted.'}</p>
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
