import { useEffect, useRef, useState } from 'react';
import ErrorNotice from './ErrorNotice';
import { asAppError } from '../lib/errors';
import type { AppError } from '../lib/errors';
import * as api from '../lib/preferenceApi';
import type { MemoryView as View, Preference, Scope } from '../lib/preferenceApi';

const dimensions=['meaning','appearance','attention','boundaries','continuity'];
type Draft={original_wording:string;interpretation:string;dimension:string;preference_key:string;scope:Scope;status:string};
const empty=(project?:string):Draft=>({original_wording:'',interpretation:'',dimension:'appearance',preference_key:'appearance',
  scope:{kind:project?'project':'general',project_id:project??null,conditions:{}},status:'confirmed'});

export default function MemoryView({projectId,projectProfile,onClose}:{
  projectId?:string;projectProfile?:string;onClose:()=>void;
}){
  const dialog=useRef<HTMLDialogElement>(null);
  const [profiles,setProfiles]=useState<{id:string;name:string}[]>([]);
  const [profile,setProfile]=useState(api.nextProfile());
  const [data,setData]=useState<View|null>(null);
  const [error,setError]=useState<AppError|null>(null);
  const [busy,setBusy]=useState(false);
  const [editing,setEditing]=useState<Preference|null>(null);
  const [showForm,setShowForm]=useState(false);
  const [draft,setDraft]=useState<Draft>(empty(projectId));
  const [newProfile,setNewProfile]=useState('');
  const [uploadContext,setUploadContext]=useState(api.nextContext());
  const command=useRef<string|null>(null);
  const currentProject=profile===(projectProfile??'default')?projectId:undefined;
  useEffect(()=>{const el=dialog.current;el?.showModal();return()=>el?.close();},[]);
  useEffect(()=>{
    let cancelled=false;setData(null);setError(null);setShowForm(false);setEditing(null);
    void (async()=>{
      const list=await api.profiles();
      if(cancelled)return;
      setProfiles(list);
      const value=await api.view(profile);
      if(!cancelled)setData(value);
    })().catch(cause=>{if(!cancelled)setError(asAppError(cause,'memory.view'));});
    return()=>{cancelled=true;};
  },[profile]);
  const refresh=async()=>setData(await api.view(profile));
  const run=async(operation:()=>Promise<unknown>,reload=true)=>{
    if(busy)return;setBusy(true);setError(null);
    try{await operation();if(reload)await refresh();}
    catch(cause){setError(asAppError(cause,'memory.manage'));}
    finally{setBusy(false);}
  };
  const change=(next:Partial<Draft>)=>{setDraft(d=>({...d,...next}));command.current=null;};
  const startEdit=(entry:Preference)=>{
    setEditing(entry);setShowForm(true);command.current=null;
    setDraft({original_wording:entry.original_wording,interpretation:entry.interpretation,dimension:entry.dimension,
      preference_key:entry.preference_key,scope:entry.scope,status:entry.status});
  };
  const save=()=>void run(async()=>{
    const body={...draft};
    if(editing)await api.edit(profile,editing.id,{...body,revision:editing.revision,context:editing.context});
    else{
      command.current??=crypto.randomUUID();
      const {status:_status,...fields}=body;
      await api.add(profile,{...fields,command_id:command.current,project_id:currentProject??null,context:{}});
    }
    setShowForm(false);setEditing(null);command.current=null;
  });
  return <dialog ref={dialog} className="memory-dialog" aria-labelledby="memory-heading"
    onCancel={e=>{e.preventDefault();if(!busy)onClose();}}>
    <div className="section-heading"><h2 id="memory-heading">Editing preferences</h2><button disabled={busy} onClick={onClose}>Close</button></div>
    <p className="memory-mode">{!data?'Loading preference storage…'
      :data.application_available&&data.settings.application_enabled?'Personalization enabled for explicitly opted-in new videos.'
      :data.settings.capture_enabled===false?'Preference recording paused — personalization off.':'Recording preferences — personalization off.'}</p>
    <p className="inline-help">Stored evidence is not used to personalize questions or grading in this release.</p>
    {error&&<ErrorNotice error={error}/>}
    <div className="memory-profile"><label>Profile <select aria-label="Preference profile" disabled={busy} value={profile}
      onChange={e=>{setProfile(e.target.value);api.setNextProfile(e.target.value);}}>
      {!!profiles.length&&!profiles.some(p=>p.id===profile)&&<option value={profile} disabled>Unavailable profile</option>}
      {profiles.length?profiles.map(p=><option key={p.id} value={p.id}>{p.name}</option>):<option value={profile}>{profile}</option>}
    </select></label>
    {data&&<label><input type="checkbox" checked={data.settings.capture_enabled} disabled={busy}
      onChange={e=>void run(()=>api.settings(profile,e.target.checked))}/> Record new project feedback</label>}</div>
    <p className="inline-help">Profile changes apply to new uploads. This video's profile: {profiles.find(p=>p.id===(projectProfile??'default'))?.name??projectProfile??'Default'}.</p>
    <details><summary>New profile and context for the next upload</summary>
      <div className="actions"><input aria-label="New preference profile name" value={newProfile} maxLength={80}
        placeholder="Profile name" onChange={e=>setNewProfile(e.target.value)}/>
        <button disabled={busy||!newProfile.trim()} onClick={()=>void run(async()=>{
          const p=await api.createProfile(newProfile);api.setNextProfile(p.id);setProfile(p.id);setNewProfile('');
        },false)}>Create profile</button></div>
      <div className="memory-context">{['category','scene','purpose'].map(key=><label key={key}>{key}
        <input aria-label={'Next upload '+key} maxLength={120} value={uploadContext[key]??''} onChange={e=>{
          const context={...uploadContext,[key]:e.target.value};setUploadContext(context);api.setNextContext(context);
        }}/></label>)}</div>
    </details>
    <div className="section-heading"><h3>Stored preferences ({data?.preferences.length??0})</h3>
      <button disabled={busy} onClick={()=>{setEditing(null);setDraft(empty(currentProject));command.current=null;setShowForm(true);}}>Add instruction</button></div>
    {showForm&&<form className="memory-form" onSubmit={e=>{e.preventDefault();save();}}>
      <label>Original wording<textarea required maxLength={4000} value={draft.original_wording} onChange={e=>change({original_wording:e.target.value})}/></label>
      <label>Interpretation<textarea required maxLength={2000} value={draft.interpretation} onChange={e=>change({interpretation:e.target.value})}/></label>
      <div className="memory-context">
        <label>Dimension<select value={draft.dimension} onChange={e=>change({dimension:e.target.value})}>{dimensions.map(d=><option key={d}>{d}</option>)}</select></label>
        <label>Topic<input value={draft.preference_key} required maxLength={160} onChange={e=>change({preference_key:e.target.value})}/></label>
        <label>Scope<select value={draft.scope.kind} onChange={e=>{
          const kind=e.target.value as Scope['kind'];change({scope:{kind,project_id:kind==='project'?(editing?.scope.project_id??currentProject??null):null,
            conditions:kind==='category'?draft.scope.conditions:{}}});
        }}><option value="project" disabled={!currentProject&&!editing?.scope.project_id}>This project</option>
          <option value="category">Matching context</option><option value="general">Explicitly general</option></select></label>
      </div>
      {draft.scope.kind==='category'&&<div className="memory-context">{['category','scene','purpose'].map(key=><label key={key}>{key}
        <input maxLength={120} value={draft.scope.conditions[key]??''} onChange={e=>change({scope:{...draft.scope,conditions:{...draft.scope.conditions,[key]:e.target.value}}})}/></label>)}</div>}
      {editing&&<label>Status<select value={draft.status} onChange={e=>change({status:e.target.value})}>
        <option value="tentative">Tentative</option><option value="confirmed">Confirmed</option><option value="contradicted">Contradicted</option>
      </select></label>}
      <div className="actions"><button type="submit" disabled={busy||!draft.interpretation.trim()||!draft.original_wording.trim()}>Save preference</button>
        <button type="button" onClick={()=>setShowForm(false)} disabled={busy}>Cancel</button></div>
    </form>}
    <div className="memory-entries">
      {data?.preferences.length===0&&<p className="muted">No preferences recorded yet.</p>}
      {data?.preferences.map(entry=><article key={entry.id} className="memory-entry">
        <div className="section-heading"><strong>{entry.interpretation}</strong><span>{entry.status}</span></div>
        <p>{entry.original_wording}</p>
        <p className="inline-help">{entry.dimension} · {entry.scope.kind} · {entry.basis.replaceAll('_',' ')} · {entry.independent_projects} supporting project(s)</p>
        {!!Object.keys(entry.scope.conditions).length&&<p className="inline-help">{JSON.stringify(entry.scope.conditions)}</p>}
        <details><summary>Supporting evidence and revisions</summary><pre>{JSON.stringify({
          projects:entry.project_ids,questions:entry.question_ids,versions:entry.version_ids,
          events:data.events.filter(e=>entry.event_ids.includes(String(e.event_id))),history:entry.history,
        },null,2)}</pre></details>
        <div className="actions"><button disabled={busy} onClick={()=>startEdit(entry)}>Edit</button>
          <button disabled={busy} onClick={()=>{if(window.confirm('Delete this preference? A tombstone prevents replay. Supporting feedback remains in the audit history.'))
            void run(()=>api.remove(profile,entry.id,entry.revision));}}>Delete</button></div>
      </article>)}
    </div>
    <details><summary>Feedback evidence ({data?.events.length??0})</summary>
      <p className="inline-help">Selecting a version records only the selection. It does not imply approval of every adjustment.</p>
      <pre>{JSON.stringify(data?.events??[],null,2)}</pre>
    </details>
  </dialog>;
}
