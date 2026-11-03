import { useCallback, useEffect, useRef, useState } from 'react';
import type { MediaAsset, Project, SessionEvent, Version } from './types';
import { makeEvent, makeProject, NEUTRAL_RECIPE } from './types';
import { getActiveId, getMedia, getProject, saveImport, savePreparedSource, saveProject, setActiveId } from './storage';
import { inspectVideo } from './media';
import { AppError, asAppError } from './errors';
import type { ErrorDetails } from './errors';
import * as api from './api';
import { preparedSource } from './preparedSource';
import { validateRenderImport } from './renderImport';

let restoring: Promise<Project> | undefined;
async function restoreOrCreate(): Promise<Project> {
  const id = getActiveId();
  const saved = id ? await getProject(id) : undefined;
  if (id && !saved) throw new AppError('SESSION_MISSING', 'The active session could not be found.',
    'Start a new session. Existing records have not been changed.', { projectId: id });
  const next = saved ?? makeProject();
  if (!saved) await saveProject(next);
  setActiveId(next.id);
  return next;
}

export function useWorkspace() {
  const [project, setProject] = useState<Project | null>(null);
  const [saveState, setSaveState] = useState<'saving'|'saved'|'error'>('saving');
  const [busy, setBusy] = useState(false);
  const [canCancel, setCanCancel] = useState(false);
  const [progress,setProgress]=useState<string|null>(null);
  const [error, setError] = useState<AppError | null>(null);
  const [restoreAttempt, setRestoreAttempt] = useState(0);
  const current = useRef(project);
  const operation = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const lastFailure = useRef('');
  const remoteImport=useRef<string|null>(null);
  const resumed=useRef<string|null>(null);
  current.current = project;
  const update = useCallback((fn: (previous: Project) => Project) => {
    setProject(previous => {
      if(!previous)return previous;
      const next=fn(previous);
      return next===previous?previous:{...next,updatedAt:new Date().toISOString()};
    });
  }, []);
  const appendEvent = useCallback((event: Omit<SessionEvent,'id'|'timestamp'>) => {
    const record = makeEvent(event);
    update(p => ({ ...p, events: [...p.events, record] }));
  }, [update]);
  const reportError = useCallback((cause: unknown, stage: string, details: ErrorDetails = {}) => {
    const failure = asAppError(cause, stage, { ...details, context: stage });
    setError(failure);
    const signature = JSON.stringify([current.current?.id, failure.code, failure.details]);
    // A failed autosave must not create an endless save/error/event loop.
    if (lastFailure.current !== signature) {
      lastFailure.current = signature;
      appendEvent({ actor: 'app', kind: 'error', title: failure.message, reason: failure.action,
        details: { code: failure.code, ...failure.details } });
    }
  }, [appendEvent]);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    // One restore serves every run of this effect (React StrictMode runs it twice in development), so a brand-new
    // session is created once and is on disk before it counts as the active one.
    const task = restoring ?? (restoring = restoreOrCreate());
    void task.then(next => { if (!cancelled) setProject(next); },
      cause => { if (!cancelled) { setSaveState('error'); reportError(cause, 'storage.restore'); } })
      .finally(() => { if (restoring === task) restoring = undefined; });
    return () => { cancelled = true; };
  }, [restoreAttempt, reportError]);

  useEffect(() => {
    if (!project || busy) return;
    let cancelled = false;
    setSaveState('saving');
    const timer = setTimeout(() => {
      if (operation.current) return;
      void saveProject(project).then(() => {
        if (!cancelled) {
          setSaveState('saved');
          setError(e => e?.details.context === 'storage.save' ? null : e);
          lastFailure.current = '';
        }
      }).catch(cause => {
        if (!cancelled) { setSaveState('error'); reportError(cause, 'storage.save'); }
      });
    }, 250);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [project, busy, reportError]);
  useEffect(() => {
    const flush = () => {
      if (!current.current || operation.current || document.visibilityState !== 'hidden') return;
      void saveProject(current.current).catch(cause => {
        setSaveState('error'); reportError(cause, 'storage.save');
      });
    };
    document.addEventListener('visibilitychange', flush);
    return () => document.removeEventListener('visibilitychange', flush);
  }, [reportError]);
  useEffect(() => () => controller.current?.abort(), []);

  const switchSession = async (id?: string): Promise<boolean> => {
    if (operation.current) return false;
    operation.current = true; setBusy(true);
    try {
      if (current.current) await saveProject(current.current);
      const next = id ? await getProject(id) : makeProject();
      if (!next) throw new AppError('SESSION_MISSING', 'The selected session was not found.', 'Choose another session.');
      await saveProject(next);
      setActiveId(next.id); current.current = next; setProject(next);
      setError(null); lastFailure.current = '';
      return true;
    } catch (cause) { reportError(cause, 'storage.session'); return false; }
    finally { operation.current = false; setBusy(false); }
  };

  const trackImport=(state:api.BackendState)=>{
    setProgress(api.stageLabel(state.stage));
    update(p=>({...p,pendingBackend:state}));
  };
  const finishSource=async(state:api.BackendState,attach:boolean,signal:AbortSignal)=>{
    const asset=preparedSource(state);
    setProgress('Saving prepared source');
    if(signal.aborted)throw new AppError('MEDIA_ABORTED','Import cancelled.','The source remains on the backend.');
    const latest=current.current;
    if(!latest)return;
    setCanCancel(false);
    const next=attach||!latest.source?latest:{...makeProject(),direction:latest.direction};
    const version:Version={
      id:crypto.randomUUID(),name:'Original',kind:'source',createdAt:new Date().toISOString(),
      assetId:asset.id,recipe:{...NEUTRAL_RECIPE},
    };
    const versions=attach?next.versions.map(v=>v.kind==='source'?{...v,assetId:asset.id}:v):[version];
    const result:Project={
      ...next,name:next.name==='Untitled session'?asset.name.replace(/\.[^.]+$/,''):next.name,
      updatedAt:new Date().toISOString(),source:asset,
      assets:attach?[...next.assets,asset]:[asset],versions,
      selectedVersionId:attach?(versions.find(v=>v.kind==='source')?.id??version.id):version.id,
      backend:state,pendingBackend:undefined,pendingAttach:undefined,
      events:[...next.events,...state.events.filter(e=>!next.events.some(old=>old.id===e.id))]
        .sort((a,b)=>Date.parse(a.timestamp)-Date.parse(b.timestamp)),
    };
    if(attach&&!versions.some(v=>v.kind==='source'))result.versions.push(version);
    const previous={...latest,pendingBackend:undefined,pendingAttach:undefined};
    await savePreparedSource(result,previous);
    setActiveId(result.id);current.current=result;setProject(result);setSaveState('saved');setError(null);
  };
  const resumeImport=async(pending:api.BackendState,attach:boolean,retry=false)=>{
    if(operation.current)return;
    operation.current=true;setBusy(true);setCanCancel(true);setError(null);
    const aborter=new AbortController();controller.current=aborter;remoteImport.current=pending.id;
    try{
      let state=await api.getState(pending.id,aborter.signal);
      if(retry&&(state.status==='failed'||state.status==='cancelled')){
        state=await api.command(state.id,'retry',{revision:state.revision});
      }
      const ready=await api.waitForSource(state,trackImport,aborter.signal);
      await finishSource(ready,attach,aborter.signal);
    }catch(cause){
      if(cause instanceof AppError&&cause.code==='MEDIA_ABORTED'){
        update(p=>({...p,pendingBackend:p.pendingBackend?{...p.pendingBackend,status:'cancelled'}:undefined}));
        appendEvent({actor:'user',kind:'activity',title:'Import cancelled'});
      }else reportError(cause,'backend.import');
    }finally{controller.current=null;remoteImport.current=null;operation.current=false;setCanCancel(false);setBusy(false);setProgress(null);}
  };
  useEffect(()=>{
    const pending=project?.pendingBackend;
    if(!pending||operation.current||resumed.current===pending.id||!api.activeJob(pending))return;
    resumed.current=pending.id;
    void resumeImport(pending,project?.pendingAttach??false);
  },[project?.pendingBackend?.id,project?.id]);

  const importVideo = async (file: File, kind: 'source'|'rendered', attach=false) => {
    if(operation.current||!current.current)return;
    operation.current=true;setBusy(true);setCanCancel(true);setError(null);
    const aborter=new AbortController();controller.current=aborter;
    try {
      if(kind==='source'){
        if(!file.size)throw new AppError('MEDIA_EMPTY','The selected file is empty.','Choose a video containing data.');
        setProgress('Uploading source to the local backend');
        const state=await api.upload(file,aborter.signal);
        remoteImport.current=state.id;resumed.current=state.id;
        const latest=current.current;
        if(!latest)return;
        const saved={...latest,pendingBackend:state,pendingAttach:attach};
        await saveProject(saved);current.current=saved;setProject(saved);
        const ready=await api.waitForSource(state,trackImport,aborter.signal);
        await finishSource(ready,attach,aborter.signal);
      }else{
        setProgress('Inspecting imported render');
        const asset=await inspectVideo(file,{signal:aborter.signal});
        const latest=current.current;
        if(!latest?.source)throw new AppError('SOURCE_REQUIRED','Import a source first.','Choose the original video.');
        const compatibility=validateRenderImport(latest.source,asset);
        const version:Version={id:crypto.randomUUID(),name:'Render '+(latest.versions.filter(v=>v.kind==='rendered').length+1),
          kind:'rendered',createdAt:new Date().toISOString(),assetId:asset.id,recipe:null,previewOnly:compatibility.previewOnly,
          parentId:latest.versions.find(v=>v.kind==='source')?.id};
        const next={...latest,assets:[...latest.assets,asset],versions:[...latest.versions,version],selectedVersionId:version.id,
          events:[...latest.events,makeEvent({actor:'user',kind:'activity',title:'Render imported',
            reason:compatibility.previewOnly?'Imported lower-resolution preview. Recipe provenance is unknown.':'Imported output. Recipe provenance is unknown.',
            details:{file:asset.name,preview_only:compatibility.previewOnly,width:asset.width,height:asset.height}})]};
        setCanCancel(false);await saveImport(next,asset.id,file);
        current.current=next;setProject(next);setSaveState('saved');
      }
    }catch(cause){
      if(cause instanceof AppError&&cause.code==='MEDIA_ABORTED'){
        update(p=>({...p,pendingBackend:p.pendingBackend?{...p.pendingBackend,status:'cancelled'}:undefined}));
        appendEvent({actor:'user',kind:'activity',title:'Import cancelled',details:{file:file.name}});
      }else reportError(cause,'media.import',{file:file.name,bytes:file.size,mime:file.type||'unknown'});
    }finally{controller.current=null;remoteImport.current=null;operation.current=false;setCanCancel(false);setBusy(false);setProgress(null);}
  };
  const prepareExisting=async()=>{
    const source=current.current?.source;
    if(!source||current.current?.backend||operation.current)return;
    try{
      const blob=await getMedia(source.id);
      if(!blob)throw new AppError('MEDIA_MISSING','The saved original is missing.','Import the original video again.');
      await importVideo(new File([blob],source.name,{type:blob.type||source.type}),'source',true);
    }catch(cause){reportError(cause,'backend.prepare');}
  };
  const saveDirection = (direction: string) => {
    if (operation.current || direction.trim() === current.current?.direction) return;
    const record = makeEvent({ actor:'user', kind:'activity', title:'Direction saved', reason:direction.trim() || 'Direction cleared.' });
    update(p => ({ ...p, direction:direction.trim(), events:[...p.events,record] }));
  };
  const retrySave = async () => {
    if (!current.current || operation.current) return;
    setSaveState('saving');
    try { await saveProject(current.current); setSaveState('saved'); setError(null); lastFailure.current = ''; }
    catch (cause) { setSaveState('error'); reportError(cause, 'storage.save'); }
  };
  return {
    project, busy, canCancel, progress, saveState, error, clearError:()=>setError(null), reportError,
    update, appendEvent, importVideo, saveDirection, retrySave,
    prepareExisting,
    retryImport:()=>{const p=current.current;if(p?.pendingBackend)void resumeImport(p.pendingBackend,p.pendingAttach??false,true);},
    cancelImport:()=>{
      controller.current?.abort();
      if(remoteImport.current)void api.cancel(remoteImport.current).catch(cause=>reportError(cause,'backend.cancel'));
    },
    newSession:()=>switchSession(), openSession:(id:string)=>switchSession(id),
    retryRestore:()=>setRestoreAttempt(n=>n+1),
    selectVersion:(version:Version)=>update(p=>({...p,selectedVersionId:version.id})),
  };
}

export function useAssetUrl(asset?: MediaAsset) {
  const assetId=asset?.id;
  const playbackUrl=asset?.playbackUrl;
  const [result,setResult] = useState<{id:string;url:string}|null>(null);
  const [error,setError] = useState<AppError|null>(null);
  useEffect(() => {
    let cancelled = false;
    let url: string | undefined;
    setError(null);
    if(playbackUrl)return;
    if (!assetId) { setResult(null); return; }
    void getMedia(assetId).then(blob => {
      if (cancelled) return;
      if (!blob) throw new AppError('MEDIA_MISSING', 'The saved video file is missing.',
        'Import the source again. The session log and metadata are still available.', {assetId});
      url = URL.createObjectURL(blob); setResult({id:assetId,url});
    }).catch(cause => { if (!cancelled) setError(asAppError(cause,'storage.media',{assetId})); });
    return () => { cancelled = true; if(url) URL.revokeObjectURL(url); };
  },[assetId,playbackUrl]);
  return {url:playbackUrl??(result?.id === assetId ? result?.url : undefined),error:playbackUrl?null:error};
}
