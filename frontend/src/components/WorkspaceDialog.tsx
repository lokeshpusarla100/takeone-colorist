import { useEffect, useRef, useState } from 'react';
import type { Project } from '../lib/types';
import { AppError, asAppError } from '../lib/errors';
import { getProjects } from '../lib/storage';
import ErrorNotice from './ErrorNotice';

export default function WorkspaceDialog({activeId,onClose,onSelect,onNew}:{
  mode:'sessions';activeId:string;onClose:()=>void;
  onSelect:(id:string)=>Promise<boolean>;onNew:()=>Promise<boolean>;
}) {
  const dialog=useRef<HTMLDialogElement>(null);
  const [projects,setProjects]=useState<Project[]>([]);
  const [error,setError]=useState<AppError|null>(null);
  const [recordErrors,setRecordErrors]=useState<AppError[]>([]);
  const [busy,setBusy]=useState(false);
  useEffect(()=>{
    let cancelled=false;
    const element=dialog.current;element?.showModal();
    void getProjects().then(result=>{if(!cancelled){setProjects(result.projects);setRecordErrors(result.errors);}}).catch(cause=>{if(!cancelled)setError(asAppError(cause,'storage.list'));});
    return()=>{cancelled=true;element?.close();};
  },[]);
  const run=async(operation:()=>Promise<boolean>)=>{
    setBusy(true);
    // Workspace failures are already recorded and displayed by the parent.
    try{await operation();onClose();}
    catch(cause){setError(asAppError(cause,'storage.session'));}
    finally{setBusy(false);}
  };
  return <dialog ref={dialog} aria-labelledby="dialog-heading" onCancel={e=>{e.preventDefault();if(!busy)onClose();}}>
    <div className="section-heading"><h2 id="dialog-heading">Saved sessions</h2><button disabled={busy} onClick={onClose}>Close</button></div>
    {error&&<ErrorNotice error={error}/>}
    {recordErrors.map((failure,index)=><ErrorNotice key={index} error={failure}/>)}
    <div className="saved-session-list">{projects.map(p=><button disabled={busy} className="saved-session" key={p.id}
      aria-pressed={p.id===activeId} onClick={()=>void run(()=>onSelect(p.id))}>
      <strong>{p.name}</strong><span>{p.events.length} events · {new Date(p.updatedAt).toLocaleDateString()}</span>
    </button>)}</div>
    <button disabled={busy} onClick={()=>void run(onNew)}>New session</button>
  </dialog>;
}
