import { useEffect, useRef, useState } from 'react';
import type { SessionEvent } from '../lib/types';
import type { BackendState } from '../lib/api';
import TokenUsage from './TokenUsage';
import ProgressPanel from './ProgressPanel';
export default function SessionPanel({events,onExport,state,localBusy=false,localProgress,sending=false,online=true}:{
  events:SessionEvent[];onExport:()=>void;state?:BackendState;localBusy?:boolean;localProgress?:string|null;sending?:boolean;online?:boolean;
}) {
  const [tab,setTab]=useState<'progress'|'log'>('progress');
  const [filter,setFilter]=useState('all');
  const [query,setQuery]=useState('');
  const list=useRef<HTMLDivElement>(null);
  const follow=useRef(true);
  useEffect(()=>{
    if(list.current&&follow.current)list.current.scrollTop=list.current.scrollHeight;
  },[events.length,tab]);
  const labels={app:'App',user:'You',muse:'Muse',renderer:'Renderer'};
  const filtered=events.filter(e=>!e.details?.progress_timing&&(filter==='all'||e.kind===filter)
    && (e.title+' '+(e.reason??'')+' '+JSON.stringify(e.details??{})).toLowerCase().includes(query.toLowerCase()));
  return <section className="session-panel" aria-labelledby="session-heading">
    <div className="section-heading"><h2 id="session-heading">Run dashboard</h2><button onClick={onExport}>Export JSON</button></div>
    <div className="dashboard-tabs" role="tablist" aria-label="Run dashboard views">
      {(['progress','log'] as const).map((name,i)=><button key={name} id={'tab-'+name} role="tab"
        aria-selected={tab===name} aria-controls={'panel-'+name} tabIndex={tab===name?0:-1}
        onClick={()=>setTab(name)} onKeyDown={e=>{
          if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
          e.preventDefault();const next=e.key==='Home'?'progress':e.key==='End'?'log':i===0?'log':'progress';
          setTab(next);document.getElementById('tab-'+next)?.focus();
        }}>{name==='progress'?'Progress':'Session log'}</button>)}
    </div>
    <div role="tabpanel" id="panel-progress" aria-labelledby="tab-progress" hidden={tab!=='progress'}>
      <ProgressPanel state={state} localBusy={localBusy} localProgress={localProgress} sending={sending} online={online}/>
      <TokenUsage state={state}/>
    </div>
    <div role="tabpanel" id="panel-log" aria-labelledby="tab-log" hidden={tab!=='log'}>
    <div className="log-tools"><input type="search" aria-label="Search session log" placeholder="Search log" value={query} onChange={e=>setQuery(e.target.value)}/>
      <select aria-label="Filter session log" value={filter} onChange={e=>setFilter(e.target.value)}>
        <option value="all">All activity</option><option value="decision">Decisions</option><option value="error">Errors</option>
      </select></div>
    <div className="event-list" ref={list} role="log" aria-label="Session activity" aria-live="polite" aria-relevant="additions"
      onScroll={e=>{const el=e.currentTarget;follow.current=el.scrollHeight-el.clientHeight-el.scrollTop<40;}}>
      {filtered.length===0 && <p className="muted">{filter==='decision'?'No Muse decisions recorded.':'No matching events.'}</p>}
      {filtered.map(event=><article className={'log-event '+event.kind} key={event.id}>
        <div className="event-meta"><time dateTime={event.timestamp}>{new Date(event.timestamp).toLocaleTimeString([],{hour12:false})}</time><span>{labels[event.actor]}</span><span>{event.kind}</span></div>
        <h3>{event.title}</h3>
        {event.reason && <p>{event.kind==='decision'&&<strong>Why: </strong>}{event.reason}</p>}
        {event.details && <details><summary>Details</summary><pre>{JSON.stringify(event.details,null,2)}</pre></details>}
      </article>)}
    </div>
    </div>
  </section>;
}
