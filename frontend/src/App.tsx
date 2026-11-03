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
import RenderPanel from './components/RenderPanel';
import LooksPanel from './components/LooksPanel';
import MemoryView from './components/MemoryView';
import SamPanel from './components/SamPanel';
import { mutationLocked } from './lib/workflow';
import { loadAutoRender, saveAutoRender } from './lib/autoRender';

export default function App() {
  const w=useWorkspace();
  const {project,busy,error,reportError}=w;
  const [autoRender,setAutoRender]=useState(loadAutoRender);
  const [direction,setDirection]=useState('');
  const consultation=useConsultation(project,w.update,reportError,{enabled:autoRender,direction});
  const [sessions,setSessions]=useState(false);
  const [memoryOpen,setMemoryOpen]=useState(false);
  const [feedback,setFeedback]=useState('');
  const sourceInput=useRef<HTMLInputElement>(null);
  const renderInput=useRef<HTMLInputElement>(null);
  useEffect(()=>setDirection(project?.direction??''),[project?.id,project?.direction]);
  useEffect(()=>setFeedback(''),[project?.id]);
  const viewerError=useCallback((failure:AppError)=>reportError(failure,'media.playback'),[reportError]);
  const [notice,setNotice]=useState('');
  useEffect(()=>{if(!notice)return;const t=setTimeout(()=>setNotice(''),4000);return()=>clearTimeout(t);},[notice]);
  if(!project) return <main className="startup"><h1>Take One</h1>
    {error?<><ErrorNotice error={error}/><div className="actions"><button onClick={w.retryRestore}>Retry opening session</button><button disabled={busy} onClick={()=>void w.newSession()}>New session</button></div></>:<p role="status">Opening session…</p>}
  </main>;
  const sending=consultation.sending||consultation.selecting||consultation.cancelling;
  const locked=mutationLocked(project.backend,busy,sending,project.pendingBackend);
  const selected=project.versions.find(v=>v.id===project.selectedVersionId);
  const asset=project.assets.find(a=>a.id===selected?.assetId);
  const viewerSource=project.source&&project.backend?.preview
    ? {...project.source,playbackUrl:project.source.playbackUrl??project.backend.preview.url}
    : project.source;
  const fileChange=(e:ChangeEvent<HTMLInputElement>,kind:'source'|'rendered')=>{
    const file=e.target.files?.[0];e.target.value='';if(file&&!locked)void w.importVideo(file,kind);
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
    if(locked)return;
    try {await saveProject(project);setSessions(true);}catch(cause){reportError(cause,'storage.sessions');}
  };
  const downloadVideo=async()=>{
    if(selected?.kind!=='rendered'||!asset)return;
    try {
      if(selected.downloadUrl){
        const link=document.createElement('a');link.href=selected.downloadUrl;link.download=selected.name+'-graded.mp4';link.click();return;
      }
      const blob=await getMedia(asset.id);
      if(!blob)throw new AppError('MEDIA_MISSING','The rendered file is missing.','Import the rendered video again.',{assetId:asset.id});
      downloadBlob(blob,asset.name);
    }catch(cause){reportError(cause,'media.download');}
  };
  return <div className="app-shell">
    <input className="visually-hidden" ref={sourceInput} type="file" accept="video/*,.mov,.mkv,.m4v" aria-label="Import source video" disabled={locked} onChange={e=>fileChange(e,'source')}/>
    <input className="visually-hidden" ref={renderInput} type="file" accept="video/*,.mov,.mkv,.m4v" aria-label="Import rendered video" disabled={locked} onChange={e=>fileChange(e,'rendered')}/>
    <header className="app-header"><h1>Take One</h1><label className="visually-hidden" htmlFor="project-name">Session name</label>
      <input id="project-name" maxLength={80} value={project.name} disabled={locked} onChange={e=>w.update(p=>({...p,name:e.target.value}))}
        onBlur={()=>{if(!project.name.trim())w.update(p=>({...p,name:'Untitled session'}));}}/>
      <span className={'save-state '+w.saveState} role="status">{w.saveState==='saved'?'Saved locally':w.saveState==='saving'?'Saving…':'Save failed'}</span>
      <div className="actions"><button disabled={locked} onClick={()=>setMemoryOpen(true)}>Preferences</button><button disabled={locked} onClick={()=>void openSessions()}>Sessions</button>
        <button disabled={locked} onClick={()=>sourceInput.current?.click()}>Import video</button><button onClick={exportLog}>Export log</button></div>
    </header>
    <div className="connection-note">{consultation.online
      ? 'Backend connected · '+(consultation.health?.muse_configured?'Muse configured':'Meta API key missing')
        +' · '+(consultation.health?.grading_connected?'Grading connected':'Grading unavailable')
      : 'Backend unavailable · Start the Python server to prepare video and run Muse'}</div>
    {error&&<ErrorNotice error={error} onDismiss={w.clearError} onRetry={error.code.startsWith('STORAGE')?()=>void w.retrySave():undefined}/>}
    {busy&&<div className="operation-status" role="status"><span>{w.progress??'Saving session…'}</span>{w.canCancel&&<button onClick={w.cancelImport}>Cancel import</button>}</div>}
    {!busy&&project.pendingBackend&&<div className="operation-status">
      <span>Source preparation is saved on the backend.</span><button disabled={sending||!!project.backend&&['analyzing','rendering','segmenting'].includes(project.backend.status)} onClick={w.retryImport}>Resume source preparation</button>
    </div>}
    <div className="workspace">
      <main className="editor">
        {project.source&&<div className="media-info">{project.source.name} · {project.source.width} × {project.source.height} · {formatTime(project.source.duration)}</div>}
        <Viewer source={viewerSource} selectedVersion={selected} selectedAsset={asset} busy={locked}
          onChooseVideo={()=>sourceInput.current?.click()} onDropVideo={file=>void w.importVideo(file,'source')} onError={viewerError}
          onPreviewReady={()=>{if(error?.details.stage==='media.playback')w.clearError();}}/>
        {project.source&&<div className="version-controls">
          <label>Version <select aria-label="Selected version" value={project.selectedVersionId??''} disabled={locked}
            onChange={e=>{
              const v=project.versions.find(item=>item.id===e.target.value);
              if(!v||locked)return;
              w.selectVersion(v);
              w.appendEvent({actor:'user',kind:'activity',title:'Preview selection',details:{version_id:v.id,name:v.name}});
              void consultation.captureSelection({kind:'version_selected',
                version_id:v.kind==='source'?'source':v.candidateId??'client:'+v.id,original_wording:''});
            }}>
            {project.versions.map(v=><option key={v.id} value={v.id}>{v.name}{v.kind==='draft'?' (unrendered)':v.kind==='rendered'?v.previewOnly?' (imported preview)':v.candidateId?' (graded)':' (imported)':''}</option>)}
          </select></label>
          <button disabled={locked} onClick={()=>renderInput.current?.click()}>Import render</button>
          {selected?.kind==='rendered'&&<button onClick={()=>void downloadVideo()}>{selected.previewOnly?'Download imported preview':'Download video'}</button>}
        </div>}
        <RubricPanel key={'rubric:'+project.id} state={project.backend} health={consultation.health} online={consultation.online} busy={busy||!!project.pendingBackend} error={error}
          sending={sending} hasSource={!!project.source} direction={direction} onDirection={setDirection}
          autoRender={autoRender} onAutoRender={on=>{setAutoRender(on);saveAutoRender(on);}}
          onSaveDirection={()=>w.saveDirection(direction)}
          onStart={()=>{w.saveDirection(direction);w.clearError();void consultation.start(direction);}}
          onAnswer={answers=>{w.clearError();void consultation.answer(answers);}}
          onRetry={()=>{w.clearError();void consultation.retry();}} onCancel={()=>void consultation.cancel()}
          onPrepare={()=>void w.prepareExisting()} onDownload={()=>{
            try{downloadBlob(new Blob([JSON.stringify(project.backend?.brief,null,2)],{type:'application/json'}),'creative-brief.json');}
            catch(cause){reportError(cause,'brief.export');}
          }}/>
        <LooksPanel state={project.backend} health={consultation.health} online={consultation.online}
          sending={sending} locked={locked}
          onGenerate={()=>{w.clearError();void consultation.generateLooks();}}
          onSelect={lookId=>{w.clearError();void consultation.selectLook(lookId);}}/>
        <RenderPanel state={project.backend} health={consultation.health} online={consultation.online}
          sending={sending||busy||!!project.pendingBackend} direction={direction}
          autoStarted={!!project.backend?.brief_sha256&&consultation.autoStarted===project.backend.id+':'+project.backend.brief_sha256}
          onGenerate={()=>{w.clearError();void consultation.generate();}}
          onRetry={()=>{w.clearError();void consultation.retry();}} onCancel={()=>void consultation.cancel()}
          viewCandidateId={selected?.candidateId} onReview={id=>{w.clearError();void consultation.review(id);}}
          onRenderSupported={id=>{w.clearError();void consultation.renderSupported(id);}}
          onUseAllMatches={id=>{w.clearError();void consultation.useAllMatches(id);}}
          onRenderFull={id=>{w.clearError();void consultation.renderFull(id);}}/>
        <SamPanel key={project.id} state={project.backend} health={consultation.health} online={consultation.online}
          locked={locked} sending={sending} onTrack={(concept,durationSeconds)=>{w.clearError();void consultation.segment(concept,durationSeconds);}}
          onCancel={()=>void consultation.cancel()} onRetry={()=>{w.clearError();void consultation.retry();}}/>
        {project.backend?.brief&&<section className="project-feedback">
          <label htmlFor="video-feedback">Feedback on this video</label>
          <textarea id="video-feedback" value={feedback} disabled={locked} maxLength={4000} rows={2} onChange={e=>setFeedback(e.target.value)}
            placeholder="Describe the change you want. This updates this video's brief even while personalization is off."/>
          <div className="actions">
            <button disabled={locked||!consultation.online||!feedback.trim()}
              onClick={()=>{void consultation.feedback({kind:'feedback',original_wording:feedback,dimension:'appearance',preference_key:'appearance'})
                .then(saved=>{if(saved)setFeedback('');});}}>Save feedback</button>
            {selected?.kind==='rendered'&&<button disabled={locked||!consultation.online} onClick={()=>void consultation.captureSelection({
              kind:'version_rejected',version_id:selected.candidateId??'client:'+selected.id,original_wording:'',
            })}>Reject selected version</button>}
          </div>
          <p className="inline-help">Feedback revises this video's brief. It does not automatically enable personalization or generate a render.</p>
        </section>}
        {project.backend?.memory_capture_error&&<p className="inline-help" role="status">Preference capture needs attention: {project.backend.memory_capture_error.message}. Current-video feedback is retained.</p>}
      </main>
      <SessionPanel key={project.id} events={project.events} state={project.pendingBackend??project.backend} onExport={exportLog}
        localBusy={busy} localProgress={w.progress} sending={sending} online={consultation.online}/>
    </div>
    {notice&&<p className="notice" role="status">{notice}</p>}
    {sessions&&<WorkspaceDialog mode="sessions" activeId={project.id} onClose={()=>setSessions(false)} onSelect={w.openSession} onNew={w.newSession}/>}
    {memoryOpen&&<MemoryView projectId={project.backend?.id} projectProfile={project.backend?.profile_id??'default'} onClose={()=>setMemoryOpen(false)}/>}
  </div>;
}
