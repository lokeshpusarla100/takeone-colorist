import { useEffect, useRef, useState } from 'react';
import type { AnswerInput, BackendState, Health } from '../lib/api';
import { activeJob, stageLabel } from '../lib/api';
import RubricDialog from './RubricDialog';
import type { AppError } from '../lib/errors';
import Card from './Card';
import Icon from './Icon';
import { canGrade, parseDomain } from '../features/color/sourceColor';

export default function RubricPanel({state,health,online,busy,sending,hasSource,direction,onDirection,onSaveDirection,autoRender,onAutoRender,
  onStart,onAnswer,onRetry,onCancel,onPrepare,error}:{
  state?:BackendState;health:Health|null;online:boolean;busy:boolean;sending:boolean;hasSource:boolean;
  direction:string;onDirection:(text:string)=>void;onSaveDirection:()=>void;onStart:()=>void;
  autoRender:boolean;onAutoRender:(on:boolean)=>void;
  onAnswer:(answers:AnswerInput[])=>void;onRetry:()=>void;onCancel:()=>void;
  onPrepare:()=>void;
  error?:AppError|null;
}) {
  const [open,setOpen]=useState(false);
  const trigger=useRef<HTMLButtonElement>(null);
  const working=!!state&&activeJob(state);
  const changed=!!state&&direction.trim()!==state.request;
  const disabled=busy||sending||working;
  const question=state?.status==='awaiting_answer'?state.question:null;
  const round=question?Math.max(1,state?.pending_questions?.length??1):0;
  const brief=state?.brief;
  useEffect(()=>{if(question&&!changed)setOpen(true);},[state?.id,state?.request,question?.id,changed]);
  // Hand focus back to the button only after the dialog has been open. Doing it on first render scrolled the page
  // down to this card every time the editor loaded.
  const wasOpen=useRef(false);
  useEffect(()=>{if(open)wasOpen.current=true;else if(wasOpen.current){wasOpen.current=false;trigger.current?.focus();}},[open]);
  // The questions are done: with automatic grading the dialog gets out of the way so the progress is visible.
  const asked=useRef(false);
  useEffect(()=>{
    if(question)asked.current=true;
    else if(asked.current&&brief&&!changed){asked.current=false;if(autoRender)setOpen(false);}
  },[question,brief,changed,autoRender]);
  const close=()=>setOpen(false);
  const status=!state?undefined:working?{tone:'accent' as const,label:'Working'}:question&&!changed?{tone:'warn' as const,label:'Needs your answers',icon:'chat' as const}
    :state.status==='failed'||state.status==='cancelled'?{tone:'bad' as const,label:'Stopped'}:brief&&!changed?{tone:'ok' as const,label:'Brief saved',icon:'check' as const}:undefined;
  const tooLong=direction.length>16000;
  const gradable=canGrade(parseDomain(state?.source?.domain));
  return <Card n={1} title="Your direction" className="direction rubric-panel" label="Creative direction consultation" status={status}>
    <label htmlFor="direction" className="field-label">Describe the look you want, and anything that must stay as it is</label>
    <textarea id="direction" value={direction} disabled={disabled} rows={4}
      placeholder="For example: a calm, cool evening look. Keep skin tones natural and the red jacket exactly as it is. Leave this blank to let Muse decide."
      onChange={e=>onDirection(e.target.value)} onBlur={onSaveDirection}/>
    <p className={'field-foot'+(tooLong?' bad':'')} role={tooLong?'alert':undefined}>{direction.length.toLocaleString()} / 16,000 characters{tooLong?' · Too long to submit. Your text is kept; shorten it to continue.':''}</p>
    <label className="auto-render switch-row"><input type="checkbox" className="switch" checked={autoRender} onChange={e=>onAutoRender(e.target.checked)}/>
      <span><strong>Start grading automatically when the questions are done</strong>
        <small>Uses Muse and SAM, which cost money. The running total is under “Cost of this video”.</small></span></label>
    {!online&&<p className="note warn"><Icon name="alert" size={16}/>Backend unavailable. Start the Python server to prepare footage and run the consultation.</p>}
    {online&&!health?.muse_configured&&<p className="note warn"><Icon name="alert" size={16}/>Set MODEL_API_KEY in the root .env, then refresh this page to enable Muse.</p>}
    {hasSource&&!state&&<div className="consultation-actions"><span>This saved video needs backend preparation.</span>
      <button disabled={busy||!online} onClick={onPrepare}>Prepare source</button></div>}
    {!working&&!(question&&!changed)&&!(brief&&!changed)&&<div className="consultation-actions"><span className="muted">
      {!gradable?'This video can’t be graded yet. See the notice above the player.':hasSource?'Start sends the silent analysis video and your request to Meta.':'Import a video before starting.'}</span>
      <button className="primary" disabled={disabled||tooLong||!gradable||!state?.preview||!online||!health?.muse_configured
        ||(!changed&&!!state&&['awaiting_answer','brief_ready','render_ready','failed','cancelled'].includes(state.status))}
        onClick={onStart}><Icon name="spark" size={18}/>{changed&&state?.request?'Start with new direction':'Start analysis'}</button></div>}
    {working&&!['rendering','segmenting','reviewing'].includes(state.status)&&<div className="consultation-status" role="status"><span className="status-text"><span className="spinner"/>{stageLabel(state.stage)}</span><button disabled={sending||!online||state.stage==='cancelling'} onClick={onCancel}>Cancel</button></div>}
    {state&&(state.status==='failed'||state.status==='cancelled')&&state.error?.details.stage!=='segment'&&<div className="consultation-actions">
      <span>{state.status==='cancelled'?'The operation was cancelled. Saved answers are kept.':'The last step failed. Saved evidence and answers are kept.'}</span>
      <button disabled={disabled||!online} onClick={onRetry}>Retry failed step</button>
    </div>}
    {question&&changed&&<p className="note"><Icon name="info" size={16}/>The request has changed. Start with the new direction to update the questions.</p>}
    {state&&(state.analysis||question||brief)&&<div className="consultation-actions">
      <span>{question&&!changed?(round>1?`Muse has ${round} questions for you.`:'Muse has a question for you.'):`${state.answered_count} ${state.answered_count===1?'answer':'answers'} saved.`}</span>
      <button ref={trigger} className={question&&!changed?'primary':''} onClick={()=>setOpen(true)}>{question&&!changed?(round>1?'Answer questions':'Answer question'):'Review Q&A'}</button></div>}
    <RubricDialog open={open} onClose={close} state={state} disabled={disabled} online={online} changed={changed}
      onAnswer={onAnswer} onRetry={onRetry} onCancel={onCancel} error={error}/>
  </Card>;
}
