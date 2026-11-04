import { useCallback, useEffect, useRef, useState } from 'react';
import type { Project } from './types';
import { AppError } from './errors';
import * as api from './api';
import { withRenderedVersions } from './renderedVersions';
import { autoRenderKey, consultationRunning, shouldAutoRender } from './autoRender';
import * as colorApi from '../features/color/api';
import type { Choice } from '../features/color/inputProfile';

export function useConsultation(project:Project|null,update:(fn:(p:Project)=>Project)=>void,
  reportError:(cause:unknown,stage:string)=>void,auto?:{enabled:boolean;direction:string}) {
  const [health,setHealth]=useState<api.Health|null>(null);
  const [online,setOnline]=useState(false);
  const [sending,setSending]=useState(false);
  const [selecting,setSelecting]=useState(false);
  const [cancelling,setCancelling]=useState(false);
  const [refresh,setRefresh]=useState(0);
  const inFlight=useRef(false);
  const selectionInFlight=useRef(false);
  const cancelInFlight=useRef(false);
  const reported=useRef('');
  const armed=useRef<string|null>(null);              // project whose consultation this tab watched
  const attempts=useRef(new Map<string,number>());    // automatic starts per brief
  const [autoStarted,setAutoStarted]=useState<string|null>(null);
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
  const send=async(kind:'analyze'|'answers'|'answers/round'|'retry'|'render'|'feedback'|'segment'|'review'|'looks'|'looks/select'|'render-supported'|'use-all-matches'|'render-full',body:Record<string,unknown>)=>{
    const source=current.current;
    if(!source?.backend||inFlight.current||selectionInFlight.current||cancelInFlight.current||api.activeJob(source.backend))return false;
    if((kind==='looks'||kind==='looks/select')&&health?.look_previews_connected!==true){
      reportError(new AppError('BACKEND_UPDATE_REQUIRED','The running backend has not loaded look previews.',
        'Restart the backend from this checkout, then refresh the page. Your saved project is kept.'),'backend.'+kind);return false;
    }
    inFlight.current=true;setSending(true);
    const id=source.backend.id;
    try{
      const state=await api.command(id,kind,{revision:source.backend.revision,...body});
      if(current.current?.backend?.id===id)apply(state);
      return true;
    }catch(cause){
      if(current.current?.backend?.id===id)reportError(cause,'backend.'+kind);
      return false;
    }finally{inFlight.current=false;setSending(false);setRefresh(n=>n+1);}
  };
  // Changing how the footage's colors are read re-prepares the preview, so it follows the same one-at-a-time rules as every command.
  const changeInputProfile=async(choice:Choice)=>{
    const source=current.current;
    if(!source?.backend||inFlight.current||selectionInFlight.current||cancelInFlight.current||api.activeJob(source.backend))return false;
    inFlight.current=true;setSending(true);
    const id=source.backend.id;
    try{
      const state=await colorApi.setProfile(id,source.backend.revision,choice);
      if(current.current?.backend?.id===id)apply(state);
      return true;
    }catch(cause){
      if(current.current?.backend?.id===id)reportError(cause,'backend.input-profile');
      return false;
    }finally{inFlight.current=false;setSending(false);setRefresh(n=>n+1);}
  };
  // When the consultation hands over its brief, start the first grade by itself (see autoRender.ts for the rules).
  useEffect(()=>{
    const state=project?.backend;
    if(!state)return;
    if(consultationRunning(state))armed.current=state.id;
    // The choice is made when the brief arrives: switched off then, this consultation never renders by itself,
    // even if the switch is turned on afterwards.
    else if(state.status==='brief_ready'&&armed.current===state.id&&!auto?.enabled)armed.current=null;
    const key=state.brief_sha256?autoRenderKey(state):'';
    const input={state,armed:armed.current===state.id,enabled:!!auto?.enabled,online,health,direction:auto?.direction??'',
      attempts:attempts.current.get(key)??0,busy:sending||selecting||cancelling||inFlight.current};
    if(!shouldAutoRender(input))return;
    attempts.current.set(key,input.attempts+1);setAutoStarted(key);
    void send('render',{}).then(started=>{if(started&&armed.current===state.id)armed.current=null;});
  },[project?.backend,auto?.enabled,auto?.direction,online,health,sending,selecting,cancelling]);
  const stop=async()=>{
    const source=current.current;
    if(!source?.backend||!api.activeJob(source.backend)||cancelInFlight.current||source.backend.stage==='cancelling')return;
    const id=source.backend.id;
    cancelInFlight.current=true;setCancelling(true);
    try{const state=await api.cancel(id);if(current.current?.backend?.id===id)apply(state);setRefresh(n=>n+1);}
    catch(cause){if(current.current?.backend?.id===id)reportError(cause,'backend.cancel');}
    finally{cancelInFlight.current=false;setCancelling(false);}
  };
  return {health,online,sending,selecting,cancelling,autoStarted,
    start:(request:string)=>send('analyze',{request}),
    answer:(answers:api.AnswerInput[])=>send('answers/round',{answers}),
    retry:()=>send('retry',{}),cancel:stop,
    generate:()=>send('render',{}),
    generateLooks:()=>send('looks',{}),
    selectLook:(lookId:string)=>send('looks/select',{look_id:lookId}),
    review:(candidateId:string)=>send('review',{candidate_id:candidateId}),
    renderSupported:(candidateId:string)=>send('render-supported',{candidate_id:candidateId}),
    useAllMatches:(candidateId:string)=>send('use-all-matches',{candidate_id:candidateId}),
    renderFull:(candidateId:string)=>send('render-full',{candidate_id:candidateId}),
    segment:(concept:string,durationSeconds:number|null)=>send('segment',{concept,duration_seconds:durationSeconds}),
    feedback:(evidence:Record<string,unknown>)=>send('feedback',{evidence}),
    captureSelection:async(evidence:Record<string,unknown>)=>{
      const source=current.current;
      if(!source?.backend||api.activeJob(source.backend)||selectionInFlight.current||inFlight.current||cancelInFlight.current)return;
      selectionInFlight.current=true;setSelecting(true);
      try{
        const state=await api.command(source.backend.id,'feedback',{revision:source.backend.revision,evidence});
        if(current.current?.backend?.id===state.id)apply(state);
      }catch(cause){if(current.current?.backend?.id===source.backend.id)reportError(cause,'memory.selection');}
      finally{selectionInFlight.current=false;setSelecting(false);}
    },
    changeInputProfile,
    refresh:()=>setRefresh(n=>n+1),
    unavailable:()=>reportError(new AppError('SOURCE_NOT_PREPARED','Prepare the source before analysis.',
      'Upload the original video to the local backend first.'),'backend.source'),
  };
}
