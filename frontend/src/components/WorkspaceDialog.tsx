import { useEffect, useRef, useState } from 'react';
import type { Project } from '../lib/types';
import { AppError, asAppError } from '../lib/errors';
import { getProjects } from '../lib/storage';
import { health } from '../lib/api';
import { formatUsd, parsePricing, projectCost } from '../lib/cost';
import type { Pricing } from '../lib/cost';
import ErrorNotice from './ErrorNotice';
import Icon from './Icon';

export default function WorkspaceDialog({activeId,onClose,onSelect,onNew}:{
  mode:'sessions';activeId:string;onClose:()=>void;
  onSelect:(id:string)=>Promise<boolean>;onNew:()=>Promise<boolean>;
}) {
  const dialog=useRef<HTMLDialogElement>(null);
  const [projects,setProjects]=useState<Project[]>([]);
  const [pricing,setPricing]=useState<Pricing|null>(null);
  const [error,setError]=useState<AppError|null>(null);
  const [recordErrors,setRecordErrors]=useState<AppError[]>([]);
  const [busy,setBusy]=useState(false);
  useEffect(()=>{
    let cancelled=false;
    const element=dialog.current;element?.showModal();
    void getProjects().then(result=>{if(!cancelled){setProjects(result.projects);setRecordErrors(result.errors);}}).catch(cause=>{if(!cancelled)setError(asAppError(cause,'storage.list'));});
    void health().then(h=>{if(!cancelled)setPricing(parsePricing(h.pricing));}).catch(()=>{/* costs simply stay unknown */});
    return()=>{cancelled=true;element?.close();};
  },[]);
  const run=async(operation:()=>Promise<boolean>)=>{
    setBusy(true);
    // Workspace failures are already recorded and displayed by the parent.
    try{await operation();onClose();}
    catch(cause){setError(asAppError(cause,'storage.session'));}
    finally{setBusy(false);}
  };
  const costs=new Map(projects.map(p=>[p.id,p.backend&&pricing?projectCost(p.backend.events,pricing):null]));
  const total=[...costs.values()].reduce((sum,c)=>sum+(c?.total??0),0);
  const priced=[...costs.values()].filter(c=>c?.priced).length;
  return <dialog ref={dialog} className="sessions-dialog" aria-labelledby="dialog-heading" onCancel={e=>{e.preventDefault();if(!busy)onClose();}}>
    <div className="dialog-head"><h2 id="dialog-heading">Saved sessions</h2><button className="icon-button" aria-label="Close" disabled={busy} onClick={onClose}><Icon name="x" size={20}/></button></div>
    {error&&<ErrorNotice error={error}/>}
    {recordErrors.map((failure,index)=><ErrorNotice key={index} error={failure}/>)}
    <div className="saved-session-list">{projects.map(p=>{const cost=costs.get(p.id);
      return <button disabled={busy} className="saved-session" key={p.id} aria-pressed={p.id===activeId} onClick={()=>void run(()=>onSelect(p.id))}>
        <span className="session-info"><strong>{p.name}</strong><span>{p.events.length} events · {new Date(p.updatedAt).toLocaleDateString()}{p.id===activeId?' · open now':''}</span></span>
        <span className="session-cost" title="Estimated cost of this video">{cost?.priced?formatUsd(cost.total):'—'}</span>
      </button>;})}</div>
    {priced>0&&<p className="note">Estimated cost across {priced} video{priced>1?'s':''} shown: <b>{formatUsd(total)}</b>. From the usage Meta reported, at its published prices.</p>}
    <div className="actions"><button className="primary" disabled={busy} onClick={()=>void run(onNew)}><Icon name="plus" size={18}/>New session</button></div>
  </dialog>;
}
