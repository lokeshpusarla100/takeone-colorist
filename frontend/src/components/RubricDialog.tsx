import { useEffect, useRef, useState } from 'react';
import type { BackendState } from '../lib/api';
import type { AppError } from '../lib/errors';
import { activeJob, stageLabel } from '../lib/api';
import ErrorNotice from './ErrorNotice';

export default function RubricDialog({open,onClose,state,disabled,online,changed,onAnswer,onRetry,onCancel,error}:{
  open:boolean;onClose:()=>void;state?:BackendState;disabled:boolean;online:boolean;changed:boolean;error?:AppError|null;
  onAnswer:(id:string,type:string,response:string)=>void;onRetry:()=>void;onCancel:()=>void;
}){
  const dialog=useRef<HTMLDialogElement>(null);
  const [choice,setChoice]=useState('');const [other,setOther]=useState('');
  const question=state?.status==='awaiting_answer'&&!changed?state.question:null;
  const working=!!state&&activeJob(state);
  useEffect(()=>{setChoice('');setOther('');},[state?.id,state?.request,question?.id]);
  useEffect(()=>{
    const element=dialog.current;
    if(open&&!element?.open)element?.showModal();else if(!open&&element?.open)element.close();
    if(open)element?.querySelector<HTMLElement>('legend')?.focus();
  },[open,question?.id]);
  useEffect(()=>()=>dialog.current?.close(),[]);
  return <dialog ref={dialog} className="rubric-dialog" aria-labelledby="rubric-dialog-title"
    onCancel={e=>{e.preventDefault();onClose();}}>
    <div className="section-heading"><h2 id="rubric-dialog-title">Creative questions</h2><button onClick={onClose}>Close</button></div>
    <p className="question-count">{state?.answered_count??0} answered · up to {state?.max_questions??5} questions, one at a time</p>
    {error&&<ErrorNotice error={error}/>}
    {working&&<div className="consultation-status" role="status"><span>{stageLabel(state.stage)}</span>
      <button disabled={!online||state.stage==='cancelling'} onClick={onCancel}>Cancel processing</button></div>}
    {state&&['failed','cancelled'].includes(state.status)&&<div className="consultation-actions">
      <span>{state.error?.message??'Processing stopped. Saved answers are kept.'}</span>
      <button disabled={disabled||!online} onClick={onRetry}>Retry failed step</button></div>}
    {question&&<div className="rubric-question" key={question.id}>
      <fieldset disabled={disabled||!online}>
        <legend tabIndex={-1}>{question.question}</legend>
        {question.options.map(option=><label className="question-option" key={option.id}>
          <input type="radio" name={'question-'+question.id} checked={choice===option.id} onChange={()=>setChoice(option.id)}/>
          <span>{option.label}</span></label>)}
        <label className="question-option"><input type="radio" name={'question-'+question.id} checked={choice==='__other'} onChange={()=>setChoice('__other')}/>My own answer</label>
        {choice==='__other'&&<textarea rows={2} maxLength={4000} aria-label="Your answer" value={other} onChange={e=>setOther(e.target.value)}/>}
      </fieldset>
      <p className="question-reason"><strong>Why this matters: </strong>{question.decision_fork}</p>
      <div className="actions">
        <button disabled={disabled||!online||!choice||(choice==='__other'&&!other.trim())}
          onClick={()=>onAnswer(question.id,choice==='__other'?'free_text':'option',choice==='__other'?other.trim():choice)}>Continue</button>
        <button disabled={disabled||!online} onClick={()=>onAnswer(question.id,'delegate','')}>You decide</button>
        <button disabled={disabled||!online} onClick={()=>onAnswer(question.id,'skip','')}>Skip</button>
      </div>
    </div>}
    {!question&&!working&&<p className="inline-help">{changed?'Start the new direction to update the questions.':state?.brief?'Questions complete. The creative brief is saved in the editor.':'No question is waiting for an answer.'}</p>}
    {!!state?.rubric_history?.length&&<section className="rubric-history" aria-label="Previous questions and answers">
      <h3>Previous answers</h3><ol>{state.rubric_history.map((item,index)=><li key={item.question_id+':'+index}>
        <p><strong>{item.question}</strong></p><p>{item.answer}</p></li>)}</ol>
    </section>}
  </dialog>;
}
