import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import Icon from './Icon';
import type { MediaAsset, Version } from '../lib/types';
import { useAssetUrl } from '../lib/useWorkspace';
import { AppError, asAppError, mediaFailure } from '../lib/errors';
import { createComparisonSync, isPlaybackAbort } from '../lib/playbackSync';

export default function Viewer({source,selectedVersion,selectedAsset,busy,onChooseVideo,onDropVideo,onError,onPreviewReady,controls,hint}:{
  source:MediaAsset|null; selectedVersion?:Version; selectedAsset?:MediaAsset; busy:boolean;controls?:ReactNode;hint?:string;
  onChooseVideo:()=>void; onDropVideo:(file:File)=>void; onError:(error:AppError)=>void;onPreviewReady?:()=>void;
}) {
  const [original,setOriginal] = useState(false);
  const [compare,setCompare] = useState(false);
  const [failed,setFailed] = useState(false);
  const [attempt,setAttempt] = useState(0);
  const main = useRef<HTMLVideoElement>(null);
  const before = useRef<HTMLVideoElement>(null);
  const position = useRef(0);
  const seeking = useRef(false);
  const rendered = selectedVersion?.kind === 'rendered' && selectedAsset;
  const asset = rendered && !original ? selectedAsset : source;
  const {url,error} = useAssetUrl(asset??undefined);
  const {url:sourceUrl,error:sourceError} = useAssetUrl(compare ? source??undefined : undefined);

  // One coordinator owns every play/pause/seek interaction between the two
  // players. Expected AbortError cancellations (a play interrupted by a
  // pause, seek or source change) are swallowed inside it; a genuine failure
  // is reported once and only then is comparison turned off.
  const comparison = useMemo(() => createComparisonSync(
    () => main.current,
    () => before.current,
    (cause) => {
      if (isPlaybackAbort(cause)) return;
      setCompare(false);
      onError(asAppError(cause, 'media.compare'));
    },
  ), [onError]);

  useEffect(()=>{setOriginal(false);setCompare(false);comparison.invalidate();},[selectedVersion?.id,comparison]);
  useEffect(()=>{position.current=0;comparison.invalidate();},[source?.id,comparison]);
  useEffect(()=>{if(!compare) comparison.pause();},[compare,comparison]);
  useEffect(()=>()=>comparison.invalidate(),[comparison]);
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

  const resumeComparison = () => { if (compare && main.current && !main.current.paused) void comparison.play(); };

  return <section className="viewer" aria-label="Video viewer"
    onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();if(!busy&&e.dataTransfer.files[0])onDropVideo(e.dataTransfer.files[0]);}}>
    {source&&<div className="viewer-bar">
      {rendered&&<div className="segmented" role="group" aria-label="What to play">
        <button aria-pressed={original} onClick={()=>{comparison.pause();setOriginal(true);setCompare(false);}}>Original</button>
        <button aria-pressed={!original} onClick={()=>{comparison.invalidate();setOriginal(false);}}>Graded</button>
      </div>}
      {rendered&&<button className="toggle" aria-pressed={compare} onClick={()=>{setOriginal(false);setCompare(!compare);if(compare)comparison.pause();}}><Icon name="split" size={16}/>Side by side</button>}
      {!rendered&&<span className="viewer-label"><Icon name="film" size={16}/>Original</span>}
      <div className="viewer-controls">{controls}</div>
    </div>}
    {!source ? <div className="empty-viewer dropzone">
      <span className="drop-icon"><Icon name="upload" size={30}/></span>
      <h2>Drop a video to begin</h2>
      <p className="help">Describe the look you want, answer a few short questions, and Take One grades the whole clip. Your original is never changed.</p>
      <button className="primary large" onClick={onChooseVideo} disabled={busy}><Icon name="upload" size={18}/>Choose a video</button>
      <span className="help">{hint??'Or drop a file anywhere on this panel.'}</span>
      <ol className="how"><li><b>1</b>Import</li><li><b>2</b>Describe the look</li><li><b>3</b>Answer a few questions</li><li><b>4</b>Compare and download</li></ol>
    </div>
    : error || failed ? <div className="empty-viewer">
      <p>{source.playbackUrl?'Preview playback failed. The source is still prepared for analysis.':'Preview unavailable. See the error details.'}</p>
      <div className="actions"><button onClick={()=>{setFailed(false);setAttempt(n=>n+1);}} disabled={busy}>Retry playback</button>
      {!source.playbackUrl&&<button onClick={onChooseVideo} disabled={busy}>Choose another video</button>}</div>
    </div>
    : !url ? <div className="empty-viewer" role="status"><span className="spinner large"/>Loading saved video…</div>
    : <div className={'video-container '+(compare?'comparison':'')}>
      {compare && sourceUrl && <div><span className="video-label">Original</span><video ref={before} src={sourceUrl} muted playsInline preload="auto"
        aria-label="Original comparison video"
        onLoadedData={()=>{comparison.sync(true);resumeComparison();}}
        onError={e=>{setCompare(false);onError(mediaFailure(e.currentTarget.error,source.name,'media.compare'));}}/></div>}
      <div>{compare && <span className="video-label graded">Graded</span>}<video ref={main} src={url} controls playsInline preload="metadata"
        aria-label={rendered&&!original?'Selected rendered video':'Original video'}
        onLoadedMetadata={e=>{e.currentTarget.currentTime=Math.min(position.current,e.currentTarget.duration||position.current);}}
        onCanPlay={onPreviewReady}
        onTimeUpdate={e=>{position.current=e.currentTarget.currentTime;if(compare&&!seeking.current&&comparison.sync())resumeComparison();}}
        onSeeking={()=>{if(compare){seeking.current=true;comparison.pause();}}}
        onSeeked={()=>{seeking.current=false;if(compare){comparison.sync(true);resumeComparison();}}}
        onRateChange={()=>{if(compare)comparison.sync();}}
        onPlay={resumeComparison} onPause={()=>comparison.pause()} onEnded={()=>comparison.pause()}
        onError={e=>{setFailed(true);onError(mediaFailure(e.currentTarget.error,asset?.name??'unknown','media.playback'));}}/></div>
    </div>}
    {selectedVersion?.previewOnly&&<p className="inline-note"><Icon name="info" size={16}/>Imported preview · {selectedAsset?.width} × {selectedAsset?.height}. This file is for comparison; it is not the original-resolution master. Grading provenance is unverified.</p>}
    {selectedVersion?.kind==='draft' && <p className="inline-note"><Icon name="info" size={16}/>Unrendered recipe. Showing original footage.</p>}
  </section>;
}
