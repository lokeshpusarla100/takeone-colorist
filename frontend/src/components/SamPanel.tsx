import { useState } from 'react';
import type { BackendState,Health } from '../lib/api';
import { stageLabel } from '../lib/api';
import Icon from './Icon';

export default function SamPanel({state,health,online,locked,sending,onTrack,onCancel,onRetry}:{
  state?:BackendState;health:Health|null;online:boolean;locked:boolean;sending:boolean;
  onTrack:(concept:string,durationSeconds:number|null)=>void;onCancel:()=>void;onRetry:()=>void;
}){
  const [concept,setConcept]=useState('');
  const [view,setView]=useState('');
  const [duration,setDuration]=useState('5');
  if(!state?.brief)return null;
  const runs=state.segmentations??[];
  const selected=runs.find(r=>r.id===view)??runs.at(-1);
  const active=runs.at(-1);
  const working=state.status==='segmenting';
  const failed=state.error?.details.stage==='segment'&&['failed','cancelled'].includes(state.status);
  return <details className="card advanced sam-panel" open={working||failed||undefined}>
    <summary aria-labelledby="sam-heading"><Icon name="mask" size={20}/><span><b id="sam-heading">Subject masks (SAM)</b><small>Advanced · inspect what SAM can follow in this clip</small></span><Icon name="chevron" size={18} className="fold-caret"/></summary>
    <div className="advanced-body">
    <p className="help">Track one subject in the silent proxy with your Meta key and look at the masks. This uses SAM and costs money per frame. Grading asks SAM for masks by itself; this is only for inspecting them.</p>
    {health?.native_mask_timing_connected&&<p className="note">Original frame timing is kept. Irregular footage uses a separate tracking proxy, and masks map back to the original frame numbers and timestamps.</p>}
    <label htmlFor="sam-concept" className="field-label">What should SAM follow?</label>
    <div className="actions grow"><input id="sam-concept" value={concept} maxLength={120} disabled={locked}
      placeholder="e.g. motorcycle" onChange={e=>setConcept(e.target.value)}/>
      <select aria-label="Tracking scope" disabled={locked} value={duration} onChange={e=>setDuration(e.target.value)}>
        <option value="5">First 5 seconds</option><option value="full">Full clip</option>
      </select>
      <button className="primary" disabled={locked||!online||!health?.muse_configured||!health.segmentation_connected||!concept.trim()
        ||!(['brief_ready','render_ready'].includes(state.status)||failed)} onClick={()=>onTrack(concept,duration==='full'?null:Number(duration))}>Track subject</button></div>
    {working&&<div className="consultation-status" role="status"><span className="status-text"><span className="spinner"/>{stageLabel(state.stage)}
      {active&&` · ${active.received_frames??0} frames returned · ${active.frames_with_masks}/${active.frame_count||'…'} with masks`}</span>
      <button disabled={sending||!online||state.stage==='cancelling'} onClick={onCancel}>Cancel tracking</button></div>}
    {failed&&<div className="consultation-actions"><span>{state.error?.message}</span>
      <button disabled={locked||!online} onClick={onRetry}>Retry tracking step</button></div>}
    {runs.length>1&&<label className="field-inline">Saved mask run <select aria-label="Saved mask run" value={selected?.id??''} onChange={e=>setView(e.target.value)}>
      {runs.map((r,i)=><option key={r.id} value={r.id}>{i+1}. {r.concept} · {r.status}</option>)}
    </select></label>}
    {selected&&['failed','cancelled'].includes(selected.status)&&selected.error&&
      <p className="help">Saved run {selected.status}: {selected.error.message}</p>}
    {selected&&(selected.status==='ready'||selected.status==='empty')&&<div className="sam-result">
        <p>{selected.duration_seconds!=null?(selected.start_seconds?`${selected.start_seconds.toFixed(2)}–${(selected.start_seconds+selected.duration_seconds).toFixed(2)} seconds. `:`First ${selected.duration_seconds} seconds (or the available clip). `):'Full clip. '}
          {selected.status==='empty'?'No matching subjects found.':`${selected.tracks.length} object ID(s), ${selected.mask_count} masks across ${selected.frames_with_masks}/${selected.frame_count} frames.`}</p>
      {selected.preview_url&&<video controls playsInline preload="metadata" src={selected.preview_url} aria-label="SAM mask overlay"/>}
      <p className="note">The IDs shown are what SAM matched; looking at them here doesn’t choose what gets graded. Grading uses its own recorded selection rule and stops when it’s ambiguous. A gap can mean the subject is absent or was missed, and masks are never carried across gaps. Check cuts, motion and anything passing in front.</p>
      {selected.manifest_url&&<a href={selected.manifest_url} download className="link-button"><Icon name="download" size={16}/>Download mask manifest</a>}
      <details><summary>Where each object is visible</summary><pre>{JSON.stringify(selected.tracks,null,2)}</pre></details>
    </div>}
    </div>
  </details>;
}
