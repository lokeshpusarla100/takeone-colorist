import { useEffect, useRef, useState } from 'react';
import type { BackendState, Health } from '../lib/api';
import { activeJob, stageLabel } from '../lib/api';

export default function RubricPanel({state,health,online,busy,sending,hasSource,direction,onDirection,onSaveDirection,
  onStart,onAnswer,onRetry,onCancel,onPrepare,onDownload}:{
  state?:BackendState;health:Health|null;online:boolean;busy:boolean;sending:boolean;hasSource:boolean;
  direction:string;onDirection:(text:string)=>void;onSaveDirection:()=>void;onStart:()=>void;
  onAnswer:(id:string,type:string,response:string)=>void;onRetry:()=>void;onCancel:()=>void;
  onPrepare:()=>void;onDownload:()=>void;
}) {
  const [choice,setChoice]=useState('');
  const [other,setOther]=useState('');
  const questionRef=useRef<HTMLDivElement>(null);
  useEffect(()=>{setChoice('');setOther('');},[state?.question?.id,state?.request,state?.id]);
  useEffect(()=>{
    if(questionRef.current){
      questionRef.current.scrollIntoView({block:'nearest'});
      questionRef.current.querySelector('legend')?.focus({preventScroll:true});
    }
  },[state?.question?.id,state?.request,state?.id]);
  const working=!!state&&activeJob(state);
  const changed=!!state&&direction.trim()!==state.request;
  const disabled=busy||sending||working;
  const question=state?.status==='awaiting_answer'?state.question:null;
  const brief=state?.brief;
  return <section className="direction rubric-panel" aria-label="Creative direction consultation">
    <label htmlFor="direction">Creative direction</label>
    <textarea id="direction" value={direction} disabled={disabled} maxLength={4000} rows={3}
      placeholder="Describe the changes you want and anything to preserve. Leave blank to delegate the direction."
      onChange={e=>onDirection(e.target.value)} onBlur={onSaveDirection}/>
    {!online&&<p className="inline-help">Backend unavailable. Start the Python server to prepare footage and run the consultation.</p>}
    {online&&!health?.muse_configured&&<p className="inline-help">Set MODEL_API_KEY in the root .env, then refresh this page to enable Muse.</p>}
    {hasSource&&!state&&<div className="consultation-actions"><span>This saved video needs backend preparation.</span>
      <button disabled={busy||!online} onClick={onPrepare}>Prepare source</button></div>}
    {!working&&!(question&&!changed)&&!(brief&&!changed)&&<div className="consultation-actions"><span className="muted">
      {hasSource?'Start sends the silent analysis video and your request to Meta.':'Import a video before starting.'}</span>
      <button disabled={disabled||!state?.preview||!online||!health?.muse_configured
        ||(!changed&&!!state&&['awaiting_answer','brief_ready','render_ready','failed','cancelled'].includes(state.status))}
        onClick={onStart}>{changed&&state?.request?'Start with new direction':'Start analysis'}</button></div>}
    {working&&!['rendering','segmenting'].includes(state.status)&&<div className="consultation-status" role="status"><span>{stageLabel(state.stage)}</span><button disabled={sending||!online||state.stage==='cancelling'} onClick={onCancel}>Cancel</button></div>}
    {state&&(state.status==='failed'||state.status==='cancelled')&&state.error?.details.stage!=='segment'&&<div className="consultation-actions">
      <span>{state.status==='cancelled'?'The operation was cancelled. Saved answers are kept.':'The last step failed. Saved evidence and answers are kept.'}</span>
      <button disabled={disabled||!online} onClick={onRetry}>Retry failed step</button>
    </div>}
    {question&&changed&&<p className="inline-help">The request has changed. Start with the new direction to update the questions.</p>}
    {question&&!changed&&<div className="rubric-question" key={question.id} ref={questionRef}>
      <p className="question-count">{state?.answered_count} answered · up to {state?.max_questions} questions, one at a time</p>
      <fieldset disabled={disabled||!online}>
        <legend tabIndex={-1}>{question.question}</legend>
        {question.options.map(option=><label className="question-option" key={option.id}>
          <input type="radio" name={'question-'+question.id} checked={choice===option.id} onChange={()=>setChoice(option.id)}/>
          <span>{option.label}</span>
        </label>)}
        <label className="question-option"><input type="radio" name={'question-'+question.id} checked={choice==='__other'} onChange={()=>setChoice('__other')}/>My own answer</label>
        {choice==='__other'&&<textarea rows={2} maxLength={4000} aria-label="Your answer" value={other} onChange={e=>setOther(e.target.value)}/>}
      </fieldset>
      <p className="question-reason"><strong>Why this matters: </strong>{question.decision_fork}</p>
      <div className="actions">
        <button disabled={disabled||!online||!choice||(choice==='__other'&&!other.trim())}
          onClick={()=>onAnswer(question.id,choice==='__other'?'free_text':'option',choice==='__other'?other.trim():choice)}>
          {sending?'Saving answer…':'Continue'}</button>
        <button disabled={disabled||!online} onClick={()=>onAnswer(question.id,'delegate','')}>You decide</button>
        <button disabled={disabled||!online} onClick={()=>onAnswer(question.id,'skip','')}>Skip</button>
      </div>
    </div>}
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
