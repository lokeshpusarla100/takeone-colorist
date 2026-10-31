import { useCallback, useEffect, useRef, useState } from 'react';
import type { ChangeEvent } from 'react';
import Viewer from './components/Viewer';
import SessionPanel from './components/SessionPanel';
import WorkspaceDialog from './components/WorkspaceDialog';
import ErrorNotice from './components/ErrorNotice';
import { AppError } from './lib/errors';
import { downloadBlob, formatTime } from './lib/types';
import { getMedia, saveProject } from './lib/storage';
import { useWorkspace } from './lib/useWorkspace';
import { useConsultation } from './lib/useConsultation';
import RubricPanel from './components/RubricPanel';

export default function App() {
  const w=useWorkspace();
  const {project,busy,error,reportError}=w;
  const consultation=useConsultation(project,w.update,reportError);
  const [sessions,setSessions]=useState(false);
  const [direction,setDirection]=useState('');
  const sourceInput=useRef<HTMLInputElement>(null);
  const renderInput=useRef<HTMLInputElement>(null);
  useEffect(()=>setDirection(project?.direction??''),[project?.id,project?.direction]);
  const viewerError=useCallback((failure:AppError)=>reportError(failure,'media.playback'),[reportError]);
  const [notice,setNotice]=useState('');
  useEffect(()=>{if(!notice)return;const t=setTimeout(()=>setNotice(''),4000);return()=>clearTimeout(t);},[notice]);
  if(!project) return <main className="startup"><h1>Take One</h1>
    {error?<><ErrorNotice error={error}/><div className="actions"><button onClick={w.retryRestore}>Retry opening session</button><button disabled={busy} onClick={()=>void w.newSession()}>New session</button></div></>:<p role="status">Opening session…</p>}
  </main>;
  const selected=project.versions.find(v=>v.id===project.selectedVersionId);
  const asset=project.assets.find(a=>a.id===selected?.assetId);
  const viewerSource=project.source&&project.backend?.preview
    ? {...project.source,playbackUrl:project.source.playbackUrl??project.backend.preview.url}
    : project.source;
  const fileChange=(e:ChangeEvent<HTMLInputElement>,kind:'source'|'rendered')=>{
    const file=e.target.files?.[0];e.target.value='';if(file)void w.importVideo(file,kind);
  };
  const exportLog=()=>{
    try {
      const result={...project,format:'take-one-session',exportedAt:new Date().toISOString(),
        source:project.source?{...project.source,thumbnail:undefined}:null,
        assets:project.assets.map(a=>({...a,thumbnail:undefined}))};
      downloadBlob(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}),
        (project.name.replace(/[^a-z0-9_-]+/gi,'-')||'session')+'-session.json');
      setNotice('Session log exported.');
    } catch(cause){reportError(cause,'session.export');}
  };
  const openSessions=async()=>{
    try {await saveProject(project);setSessions(true);}catch(cause){reportError(cause,'storage.sessions');}
  };
  const downloadVideo=async()=>{
    if(selected?.kind!=='rendered'||!asset)return;
    try {
      const blob=await getMedia(asset.id);
      if(!blob)throw new AppError('MEDIA_MISSING','The rendered file is missing.','Import the rendered video again.',{assetId:asset.id});
      downloadBlob(blob,asset.name);
    }catch(cause){reportError(cause,'media.download');}
  };
  return <div className="app-shell">
    <input className="visually-hidden" ref={sourceInput} type="file" accept="video/*,.mov,.mkv,.m4v" aria-label="Import source video" disabled={busy} onChange={e=>fileChange(e,'source')}/>
    <input className="visually-hidden" ref={renderInput} type="file" accept="video/*,.mov,.mkv,.m4v" aria-label="Import rendered video" disabled={busy} onChange={e=>fileChange(e,'rendered')}/>
    <header className="app-header"><h1>Take One</h1><label className="visually-hidden" htmlFor="project-name">Session name</label>
      <input id="project-name" maxLength={80} value={project.name} disabled={busy} onChange={e=>w.update(p=>({...p,name:e.target.value}))}
        onBlur={()=>{if(!project.name.trim())w.update(p=>({...p,name:'Untitled session'}));}}/>
      <span className={'save-state '+w.saveState} role="status">{w.saveState==='saved'?'Saved locally':w.saveState==='saving'?'Saving…':'Save failed'}</span>
      <div className="actions"><button disabled={busy} onClick={()=>void openSessions()}>Sessions</button>
        <button disabled={busy} onClick={()=>sourceInput.current?.click()}>Import video</button><button onClick={exportLog}>Export log</button></div>
    </header>
    <div className="connection-note">{consultation.online
      ? 'Backend connected · '+(consultation.health?.muse_configured?'Muse configured':'Meta API key missing')+' · Automatic grading not connected'
      : 'Backend unavailable · Start the Python server to prepare video and run Muse'}</div>
    {error&&<ErrorNotice error={error} onDismiss={w.clearError} onRetry={error.code.startsWith('STORAGE')?()=>void w.retrySave():undefined}/>}
    {busy&&<div className="operation-status" role="status"><span>{w.progress??'Saving session…'}</span>{w.canCancel&&<button onClick={w.cancelImport}>Cancel import</button>}</div>}
    {!busy&&project.pendingBackend&&<div className="operation-status">
      <span>Source preparation is saved on the backend.</span><button onClick={w.retryImport}>Resume source preparation</button>
    </div>}
    <div className="workspace">
      <main className="editor">
        {project.source&&<div className="media-info">{project.source.name} · {project.source.width} × {project.source.height} · {formatTime(project.source.duration)}</div>}
        <Viewer source={viewerSource} selectedVersion={selected} selectedAsset={asset} busy={busy}
          onChooseVideo={()=>sourceInput.current?.click()} onDropVideo={file=>void w.importVideo(file,'source')} onError={viewerError}
          onPreviewReady={()=>{if(error?.details.stage==='media.playback')w.clearError();}}/>
        {project.source&&<div className="version-controls">
          <label>Version <select aria-label="Selected version" value={project.selectedVersionId??''} disabled={busy}
            onChange={e=>{const v=project.versions.find(item=>item.id===e.target.value);if(v)w.selectVersion(v);}}>
            {project.versions.map(v=><option key={v.id} value={v.id}>{v.name}{v.kind==='draft'?' (unrendered)':v.kind==='rendered'?' (imported)':''}</option>)}
          </select></label>
          <button disabled={busy} onClick={()=>renderInput.current?.click()}>Import render</button>
          {selected?.kind==='rendered'&&<button onClick={()=>void downloadVideo()}>Download video</button>}
        </div>}
        <RubricPanel state={project.backend} health={consultation.health} online={consultation.online} busy={busy}
          sending={consultation.sending} hasSource={!!project.source} direction={direction} onDirection={setDirection}
          onSaveDirection={()=>w.saveDirection(direction)}
          onStart={()=>{w.saveDirection(direction);w.clearError();void consultation.start(direction);}}
          onAnswer={(id,type,response)=>{w.clearError();void consultation.answer(id,type,response);}}
          onRetry={()=>{w.clearError();void consultation.retry();}} onCancel={()=>void consultation.cancel()}
          onPrepare={()=>void w.prepareExisting()} onDownload={()=>{
            try{downloadBlob(new Blob([JSON.stringify(project.backend?.brief,null,2)],{type:'application/json'}),'creative-brief.json');}
            catch(cause){reportError(cause,'brief.export');}
          }}/>
      </main>
      <SessionPanel key={project.id} events={project.events} onExport={exportLog}/>
    </div>
    {notice&&<p className="notice" role="status">{notice}</p>}
    {sessions&&<WorkspaceDialog mode="sessions" activeId={project.id} onClose={()=>setSessions(false)} onSelect={w.openSession} onNew={w.newSession}/>}
  </div>;
}
