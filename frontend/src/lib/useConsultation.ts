import { useCallback, useEffect, useRef, useState } from 'react';
import type { Project } from './types';
import { AppError } from './errors';
import * as api from './api';
import { withRenderedVersions } from './renderedVersions';

export function useConsultation(project:Project|null,update:(fn:(p:Project)=>Project)=>void,
  reportError:(cause:unknown,stage:string)=>void) {
  const [health,setHealth]=useState<api.Health|null>(null);
  const [online,setOnline]=useState(false);
  const [sending,setSending]=useState(false);
  const [refresh,setRefresh]=useState(0);
  const inFlight=useRef(false);
  const reported=useRef('');
  const current=useRef(project);current.current=project;
  useEffect(()=>{
    let cancelled=false;
    const check=()=>void api.health().then(value=>{if(!cancelled){setHealth(value);setOnline(true);}})
      .catch(()=>{if(!cancelled){setOnline(false);setHealth(null);}});
    check();const timer=setInterval(check,15000);
    return()=>{cancelled=true;clearInterval(timer);};
  },[]);
  const apply=useCallback((state:api.BackendState)=>{
    if(current.current?.backend?.id!==state.id)return;
    setOnline(true);
    update(p=>{
      if(p.backend?.id!==state.id||p.backend.revision>state.revision)return p;
      if(p.backend.revision===state.revision)return withRenderedVersions(p,state);
      const ids=new Set(p.events.map(e=>e.id));
      return {...withRenderedVersions(p,state),backend:state,events:[...p.events,...state.events.filter(e=>!ids.has(e.id))]
        .sort((a,b)=>Date.parse(a.timestamp)-Date.parse(b.timestamp))};
    });
    if(state.status==='failed'||state.status==='cancelled'){
      const key=state.id+':'+state.revision;
      if(reported.current!==key){reported.current=key;reportError(api.backendError(state),'backend.job');}
    }
  },[update,reportError]);
  useEffect(()=>{
    const id=project?.backend?.id;
    if(!id||!online)return;
    const aborter=new AbortController();
    let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{
      try{
        const state=await api.getState(id,aborter.signal);
        if(aborter.signal.aborted)return;
        apply(state);
        if(api.activeJob(state))timer=setTimeout(()=>void poll(),1000);
      }catch(cause){
        if(!aborter.signal.aborted){setOnline(false);reportError(cause,'backend.poll');}
      }
    };
    void poll();
    return()=>{aborter.abort();clearTimeout(timer);};
  },[project?.backend?.id,refresh,apply,online]);
  const send=async(kind:'analyze'|'answers'|'retry'|'render',body:Record<string,unknown>)=>{
    const source=current.current;
    if(!source?.backend||inFlight.current)return;
    inFlight.current=true;setSending(true);
    const id=source.backend.id;
    try{
      const state=await api.command(id,kind,{revision:source.backend.revision,...body});
      if(current.current?.backend?.id===id)apply(state);
    }catch(cause){
      if(current.current?.backend?.id===id)reportError(cause,'backend.'+kind);
    }finally{inFlight.current=false;setSending(false);setRefresh(n=>n+1);}
  };
  const stop=async()=>{
    if(!project?.backend)return;
    try{apply(await api.cancel(project.backend.id));setRefresh(n=>n+1);}
    catch(cause){reportError(cause,'backend.cancel');}
  };
  return {health,online,sending,
    start:(request:string)=>send('analyze',{request}),
    answer:(questionId:string,responseType:string,response:string)=>send('answers',{
      answer:{question_id:questionId,response_type:responseType,response},
    }),
    retry:()=>send('retry',{}),cancel:stop,
    generate:()=>send('render',{}),
    refresh:()=>setRefresh(n=>n+1),
    unavailable:()=>reportError(new AppError('SOURCE_NOT_PREPARED','Prepare the source before analysis.',
      'Upload the original video to the local backend first.'),'backend.source'),
  };
}
