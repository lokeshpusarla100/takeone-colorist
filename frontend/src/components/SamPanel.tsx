import { useState } from 'react';
import type { BackendState,Health } from '../lib/api';
import { stageLabel } from '../lib/api';

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
  return <section className="sam-panel" aria-labelledby="sam-heading">
    <h2 id="sam-heading">Subject masks · SAM</h2>
    <p className="inline-help">Manual diagnostics: track one subject in the silent proxy using the same Meta key. This may incur per-frame usage. Ordinary localized grading requests SAM automatically; this panel remains available for inspecting masks.</p>
    <label htmlFor="sam-concept">Subject phrase</label>
    <div className="actions"><input id="sam-concept" value={concept} maxLength={120} disabled={locked}
      placeholder="e.g. motorcycle" onChange={e=>setConcept(e.target.value)}/>
      <select aria-label="Tracking scope" disabled={locked} value={duration} onChange={e=>setDuration(e.target.value)}>
        <option value="5">First 5 seconds</option><option value="full">Full clip</option>
      </select>
      <button disabled={locked||!online||!health?.muse_configured||!health.segmentation_connected||!concept.trim()
        ||!(['brief_ready','render_ready'].includes(state.status)||failed)} onClick={()=>onTrack(concept,duration==='full'?null:Number(duration))}>Track subject</button></div>
    {working&&<div className="consultation-status" role="status"><span>{stageLabel(state.stage)}
      {active&&` · ${active.received_frames??0} returned frames · ${active.frames_with_masks}/${active.frame_count||'…'} validated frames with masks`}</span>
      <button disabled={sending||!online||state.stage==='cancelling'} onClick={onCancel}>Cancel tracking</button></div>}
    {failed&&<div className="actions"><p className="inline-help">{state.error?.message}</p>
      <button disabled={locked||!online} onClick={onRetry}>Retry tracking step</button></div>}
    {runs.length>1&&<label>Saved mask run <select aria-label="Saved mask run" value={selected?.id??''} onChange={e=>setView(e.target.value)}>
      {runs.map((r,i)=><option key={r.id} value={r.id}>{i+1}. {r.concept} · {r.status}</option>)}
    </select></label>}
    {selected&&['failed','cancelled'].includes(selected.status)&&selected.error&&
      <p className="inline-help">Saved run {selected.status}: {selected.error.message}</p>}
    {selected&&(selected.status==='ready'||selected.status==='empty')&&<div className="sam-result">
        <p>{selected.duration_seconds!=null?(selected.start_seconds?`${selected.start_seconds.toFixed(2)}–${(selected.start_seconds+selected.duration_seconds).toFixed(2)} seconds. `:`First ${selected.duration_seconds} seconds (or the available clip). `):'Full clip. '}
          {selected.status==='empty'?'No matching subjects found.':`${selected.tracks.length} object ID(s), ${selected.mask_count} masks across ${selected.frames_with_masks}/${selected.frame_count} frames.`}</p>
      {selected.preview_url&&<video controls playsInline preload="metadata" src={selected.preview_url} aria-label="SAM mask overlay"/>}
      <p className="inline-help">Diagnostics show matched IDs. Reviewing them here does not change an editing target. Automatic grading uses its recorded selection policy and stops on ambiguity. Gaps may mean absence or missed segmentation; masks are not carried across them. Inspect cuts, motion and occlusion.</p>
      {selected.manifest_url&&<a href={selected.manifest_url} download>Download mask manifest</a>}
      <details><summary>Object visibility and gaps</summary><pre>{JSON.stringify(selected.tracks,null,2)}</pre></details>
    </div>}
  </section>;
}
