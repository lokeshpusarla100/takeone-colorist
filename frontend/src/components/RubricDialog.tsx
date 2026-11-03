import { useEffect, useRef, useState } from 'react';
import type { AnswerInput, BackendState, Question } from '../lib/api';
import type { AppError } from '../lib/errors';
import { activeJob, stageLabel } from '../lib/api';
import ErrorNotice from './ErrorNotice';
import Icon from './Icon';

type Choice={choice:string;other:string};
const SPECIAL:{[k:string]:'skip'|'delegate'}={__delegate:'delegate',__skip:'skip'};

/** The answer for one question, or null while it is incomplete. */
export function answerFor(question:Question,value?:Choice):AnswerInput|null{
  if(!value?.choice)return null;
  if(value.choice==='__other')return value.other.trim()?{question_id:question.id,response_type:'free_text',response:value.other.trim()}:null;
  if(SPECIAL[value.choice])return {question_id:question.id,response_type:SPECIAL[value.choice],response:''};
  return question.options.some(o=>o.id===value.choice)?{question_id:question.id,response_type:'option',response:value.choice}:null;
}

export default function RubricDialog({open,onClose,state,disabled,online,changed,onAnswer,onRetry,onCancel,error}:{
  open:boolean;onClose:()=>void;state?:BackendState;disabled:boolean;online:boolean;changed:boolean;error?:AppError|null;
  onAnswer:(answers:AnswerInput[])=>void;onRetry:()=>void;onCancel:()=>void;
}){
  const dialog=useRef<HTMLDialogElement>(null);
  const [choices,setChoices]=useState<Record<string,Choice>>({});
  const waiting=state?.status==='awaiting_answer'&&!changed&&state.question?state.question:null;
  // The whole round (shown together); sessions from before batching have only `question`.
  const round:Question[]=waiting?(state?.pending_questions?.length?state.pending_questions:[waiting]):[];
  const roundKey=round.map(q=>q.id).join('|');
  const working=!!state&&activeJob(state);
  useEffect(()=>{setChoices({});},[state?.id,state?.request,roundKey]);
  useEffect(()=>{
    const element=dialog.current;
    if(open&&!element?.open)element?.showModal();else if(!open&&element?.open)element.close();
    if(open)element?.querySelector<HTMLElement>('legend')?.focus();
  },[open,roundKey]);
  useEffect(()=>()=>dialog.current?.close(),[]);
  const set=(id:string,change:Partial<Choice>)=>setChoices(c=>({...c,[id]:{...(c[id]??{choice:'',other:''}),...change}}));
  const answers=round.map(q=>answerFor(q,choices[q.id]));
  const complete=round.length>0&&answers.every(a=>a!==null);
  const locked=disabled||!online;
  return <dialog ref={dialog} className="rubric-dialog" aria-labelledby="rubric-dialog-title"
    onCancel={e=>{e.preventDefault();onClose();}}>
    <div className="dialog-head"><h2 id="rubric-dialog-title">A few quick questions</h2><button className="icon-button" aria-label="Close" onClick={onClose}><Icon name="x" size={20}/></button></div>
    <p className="question-count">{state?.answered_count??0} answered so far · Muse asks at most {state?.max_questions??5}{round.length>1?' · answer these together':''}</p>
    {error&&<ErrorNotice error={error}/>}
    {working&&<div className="consultation-status" role="status"><span>{stageLabel(state.stage)}</span>
      <button disabled={!online||state.stage==='cancelling'} onClick={onCancel}>Cancel processing</button></div>}
    {state&&['failed','cancelled'].includes(state.status)&&<div className="consultation-actions">
      <span>{state.error?.message??'Processing stopped. Saved answers are kept.'}</span>
      <button disabled={disabled||!online} onClick={onRetry}>Retry failed step</button></div>}
    {round.map((question,index)=>{const value=choices[question.id];const name='question-'+question.id;
      return <div className="rubric-question" key={question.id}>
      <fieldset disabled={locked}>
        <legend tabIndex={-1}>{round.length>1&&<span className="question-number">{index+1}/{round.length} </span>}{question.question}</legend>
        {question.options.map(option=><label className="question-option" key={option.id}>
          <input type="radio" name={name} checked={value?.choice===option.id} onChange={()=>set(question.id,{choice:option.id})}/>
          <span><b>{option.label}</b>{option.intended_outcome&&<small>{option.intended_outcome}</small>}</span></label>)}
        <label className="question-option"><input type="radio" name={name} checked={value?.choice==='__other'} onChange={()=>set(question.id,{choice:'__other'})}/><span><b>My own answer</b></span></label>
        {value?.choice==='__other'&&<textarea rows={2} maxLength={4000} aria-label={'Your answer: '+question.question} value={value.other} onChange={e=>set(question.id,{other:e.target.value})}/>}
        <label className="question-option"><input type="radio" name={name} checked={value?.choice==='__delegate'} onChange={()=>set(question.id,{choice:'__delegate'})}/><span><b>You decide</b><small>Let Muse choose</small></span></label>
        <label className="question-option"><input type="radio" name={name} checked={value?.choice==='__skip'} onChange={()=>set(question.id,{choice:'__skip'})}/><span><b>Skip</b><small>Leave this open</small></span></label>
      </fieldset>
      <p className="question-reason"><strong>Why this matters: </strong>{question.decision_fork}</p>
      {question.unresolved_preference&&<p className="inline-help"><strong>What needs clarification: </strong>{question.unresolved_preference}</p>}
    </div>;})}
    {round.length>0&&<div className="actions">
      <button className="primary" disabled={locked||!complete} onClick={()=>onAnswer(answers.filter((a):a is AnswerInput=>a!==null))}>Continue</button>
      {round.length>1&&!complete&&<span className="muted">Choose an answer for each question ({answers.filter(Boolean).length}/{round.length}).</span>}
    </div>}
    {!round.length&&!working&&<p className="inline-help">{changed?'Start the new direction to update the questions.':state?.brief?'Questions complete. The creative brief is saved in the editor.':'No question is waiting for an answer.'}</p>}
    {!!state?.rubric_history?.length&&<section className="rubric-history" aria-label="Previous questions and answers">
      <h3>Previous answers</h3><ol>{state.rubric_history.map((item,index)=><li key={item.question_id+':'+index}>
        <p><strong>{item.question}</strong></p><p>{item.answer}</p></li>)}</ol>
    </section>}
  </dialog>;
}
