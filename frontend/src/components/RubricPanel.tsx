import { useEffect, useRef, useState } from 'react';
import type { BackendState, Health } from '../lib/api';
import { activeJob, stageLabel } from '../lib/api';
import RubricDialog from './RubricDialog';
import type { AppError } from '../lib/errors';

export default function RubricPanel({state,health,online,busy,sending,hasSource,direction,onDirection,onSaveDirection,
  onStart,onAnswer,onRetry,onCancel,onPrepare,onDownload,error}:{
  state?:BackendState;health:Health|null;online:boolean;busy:boolean;sending:boolean;hasSource:boolean;
  direction:string;onDirection:(text:string)=>void;onSaveDirection:()=>void;onStart:()=>void;
  onAnswer:(id:string,type:string,response:string)=>void;onRetry:()=>void;onCancel:()=>void;
  onPrepare:()=>void;onDownload:()=>void;
  error?:AppError|null;
}) {
  const [open,setOpen]=useState(false);
  const trigger=useRef<HTMLButtonElement>(null);
  const working=!!state&&activeJob(state);
  const changed=!!state&&direction.trim()!==state.request;
  const disabled=busy||sending||working;
  const question=state?.status==='awaiting_answer'?state.question:null;
  const brief=state?.brief;
  useEffect(()=>{if(question&&!changed)setOpen(true);},[state?.id,state?.request,question?.id,changed]);
  useEffect(()=>{if(!open)trigger.current?.focus();},[open]);
  const close=()=>setOpen(false);
  return <section className="direction rubric-panel" aria-label="Creative direction consultation">
    <label htmlFor="direction">Creative direction</label>
    <textarea id="direction" value={direction} disabled={disabled} rows={3}
      placeholder="Describe the changes you want and anything to preserve. Leave blank to delegate the direction."
      onChange={e=>onDirection(e.target.value)} onBlur={onSaveDirection}/>
    <p className="inline-help" role={direction.length>16000?'alert':undefined}>{direction.length.toLocaleString()} / 16,000 characters{direction.length>16000?' · Request is too long to submit. Your text is kept; shorten it to continue.':''}</p>
    {!online&&<p className="inline-help">Backend unavailable. Start the Python server to prepare footage and run the consultation.</p>}
    {online&&!health?.muse_configured&&<p className="inline-help">Set MODEL_API_KEY in the root .env, then refresh this page to enable Muse.</p>}
    {hasSource&&!state&&<div className="consultation-actions"><span>This saved video needs backend preparation.</span>
      <button disabled={busy||!online} onClick={onPrepare}>Prepare source</button></div>}
    {!working&&!(question&&!changed)&&!(brief&&!changed)&&<div className="consultation-actions"><span className="muted">
      {hasSource?'Start sends the silent analysis video and your request to Meta.':'Import a video before starting.'}</span>
      <button disabled={disabled||direction.trim().length>16000||!state?.preview||!online||!health?.muse_configured
        ||(!changed&&!!state&&['awaiting_answer','brief_ready','render_ready','failed','cancelled'].includes(state.status))}
        onClick={onStart}>{changed&&state?.request?'Start with new direction':'Start analysis'}</button></div>}
    {working&&!['rendering','segmenting','reviewing'].includes(state.status)&&<div className="consultation-status" role="status"><span>{stageLabel(state.stage)}</span><button disabled={sending||!online||state.stage==='cancelling'} onClick={onCancel}>Cancel</button></div>}
    {state&&(state.status==='failed'||state.status==='cancelled')&&state.error?.details.stage!=='segment'&&<div className="consultation-actions">
      <span>{state.status==='cancelled'?'The operation was cancelled. Saved answers are kept.':'The last step failed. Saved evidence and answers are kept.'}</span>
      <button disabled={disabled||!online} onClick={onRetry}>Retry failed step</button>
    </div>}
    {question&&changed&&<p className="inline-help">The request has changed. Start with the new direction to update the questions.</p>}
    {state&&(state.analysis||question||brief)&&<div className="consultation-actions">
      <span>{question&&!changed?'Muse has a question for you.':`${state.answered_count} answers saved.`}</span>
      <button ref={trigger} onClick={()=>setOpen(true)}>{question&&!changed?'Answer question':'Review Q&A'}</button></div>}
    <RubricDialog open={open} onClose={close} state={state} disabled={disabled} online={online} changed={changed}
      onAnswer={onAnswer} onRetry={onRetry} onCancel={onCancel} error={error}/>
    {brief&&<section className="creative-brief" aria-labelledby="brief-heading">
      <div className="section-heading"><h2 id="brief-heading">Creative brief</h2><button onClick={onDownload}>Download brief</button></div>
      <p><strong>Request: </strong>{brief.initial_request||'Conservative direction delegated to Muse.'}</p>
      {!!brief.directives.length&&<ul>{brief.directives.map((d,i)=><li key={i}>{d.outcome}</li>)}</ul>}
      {!!brief.explicit_constraints.length&&<><h3>Preserve</h3><ul>{brief.explicit_constraints.map((v,i)=><li key={i}>{v}</li>)}</ul></>}
      {!!brief.unresolved.length&&<><h3>Unresolved or delegated</h3><ul>{brief.unresolved.map((v,i)=><li key={i}>{v}</li>)}</ul></>}
      <details><summary>Protections and technical limits</summary><ul>{[...brief.default_protections,...brief.technical_envelope].map((v,i)=><li key={i}>{v}</li>)}</ul></details>
      <p className="inline-help">Brief saved. Generate a grade below; Muse chooses global correction or supported localized treatment with automatic SAM masks.</p>
    </section>}
  </section>;
}
