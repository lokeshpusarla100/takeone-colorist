import { useEffect, useRef, useState } from 'react';
import type { MediaAsset, Version } from '../lib/types';
import { useAssetUrl } from '../lib/useWorkspace';
import { AppError, asAppError, mediaFailure } from '../lib/errors';

export default function Viewer({source,selectedVersion,selectedAsset,busy,onChooseVideo,onDropVideo,onError,onPreviewReady}:{
  source:MediaAsset|null; selectedVersion?:Version; selectedAsset?:MediaAsset; busy:boolean;
  onChooseVideo:()=>void; onDropVideo:(file:File)=>void; onError:(error:AppError)=>void;onPreviewReady?:()=>void;
}) {
  const [original,setOriginal] = useState(false);
  const [compare,setCompare] = useState(false);
  const [failed,setFailed] = useState(false);
  const [attempt,setAttempt] = useState(0);
  const main = useRef<HTMLVideoElement>(null);
  const before = useRef<HTMLVideoElement>(null);
  const position = useRef(0);
  const rendered = selectedVersion?.kind === 'rendered' && selectedAsset;
  const asset = rendered && !original ? selectedAsset : source;
  const {url,error} = useAssetUrl(asset??undefined);
  const {url:sourceUrl,error:sourceError} = useAssetUrl(compare ? source??undefined : undefined);
  useEffect(()=>{setOriginal(false);setCompare(false);},[selectedVersion?.id]);
  useEffect(()=>{position.current=0;},[source?.id]);
  useEffect(()=>{
    setFailed(false);
    if(!url)return;
    const timer=setTimeout(()=>{
      const video=main.current;
      if(!video||video.readyState>0)return;
      setFailed(true);
      onError(new AppError('PREVIEW_PLAYBACK_TIMEOUT','The browser preview did not finish loading.',
        'Retry playback. A prepared source can still be used for analysis; preview playback does not block the rubric.',
        {stage:'media.playback',file:asset?.name??'unknown',readyState:video.readyState,
          networkState:video.networkState,timeoutMs:20000}));
    },20000);
    return()=>clearTimeout(timer);
  },[url,attempt,onError,asset?.name]);
  useEffect(()=>{if(error) onError(error);},[error,onError]);
  useEffect(()=>{if(sourceError) onError(sourceError);},[sourceError,onError]);
  const sync = () => {
    if(main.current && before.current) {
      if(Math.abs(main.current.currentTime-before.current.currentTime)>.1) before.current.currentTime=main.current.currentTime;
      before.current.playbackRate=main.current.playbackRate;
    }
  };
  const playBefore = () => {
    sync();
    void before.current?.play().catch(cause=>{setCompare(false);onError(asAppError(cause,'media.compare'));});
  };
  return <section className="viewer" aria-label="Video viewer"
    onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();if(!busy&&e.dataTransfer.files[0])onDropVideo(e.dataTransfer.files[0]);}}>
    {rendered && <div className="viewer-options">
      <button aria-pressed={original} onClick={()=>{setOriginal(true);setCompare(false);}}>Original</button>
      <button aria-pressed={!original} onClick={()=>setOriginal(false)}>Render</button>
      <label><input type="checkbox" checked={compare} onChange={e=>{setOriginal(false);setCompare(e.target.checked);}}/>Compare</label>
    </div>}
    {!source ? <div className="empty-viewer"><p>No video loaded.</p><button onClick={onChooseVideo} disabled={busy}>Import video</button><span>Or drop a file here.</span></div>
    : error || failed ? <div className="empty-viewer">
      <p>{source.playbackUrl?'Preview playback failed. The source is still prepared for analysis.':'Preview unavailable. See the error details.'}</p>
      <button onClick={()=>{setFailed(false);setAttempt(n=>n+1);}} disabled={busy}>Retry playback</button>
      {!source.playbackUrl&&<button onClick={onChooseVideo} disabled={busy}>Choose another video</button>}
    </div>
    : !url ? <div className="empty-viewer" role="status">Loading saved video…</div>
    : <div className={'video-container '+(compare?'comparison':'')}>
      {compare && sourceUrl && <div><span className="video-label">Original</span><video ref={before} src={sourceUrl} muted playsInline preload="metadata"
        aria-label="Original comparison video" onLoadedData={()=>{sync();if(main.current&&!main.current.paused)playBefore();}}
        onError={e=>{setCompare(false);onError(mediaFailure(e.currentTarget.error,source.name,'media.compare'));}}/></div>}
      <div>{compare && <span className="video-label">Render</span>}<video ref={main} src={url} controls playsInline preload="metadata"
        aria-label={rendered&&!original?'Selected rendered video':'Original video'}
        onLoadedMetadata={e=>{e.currentTarget.currentTime=Math.min(position.current,e.currentTarget.duration);}}
        onCanPlay={onPreviewReady}
        onTimeUpdate={e=>{position.current=e.currentTarget.currentTime;if(compare)sync();}}
        onSeeking={()=>{if(compare)sync();}} onRateChange={()=>{if(compare)sync();}}
        onPlay={()=>{if(compare)playBefore();}} onPause={()=>before.current?.pause()} onEnded={()=>before.current?.pause()}
        onError={e=>{setFailed(true);onError(mediaFailure(e.currentTarget.error,asset?.name??'unknown','media.playback'));}}/></div>
    </div>}
    {selectedVersion?.kind==='draft' && <p className="inline-note">Unrendered recipe. Showing original footage.</p>}
  </section>;
}
