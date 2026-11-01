import { activeJob } from './api';
import type { BackendState } from './api';

export type StepId='prepare'|'inspect'|'clarify'|'brief'|'grade'|'masks'|'review';
type Part='plan'|'render'|'qc'|'track'|'validate_masks';
const stages:Record<string,[StepId,Part?]>={
  inspect_source:['prepare'],prepare_preview:['prepare'],analyze_scene:['inspect'],select_question:['clarify'],save_brief:['brief'],
  plan_grade:['grade','plan'],plan_revision:['grade','plan'],render_grade:['grade','render'],render_local_grade:['grade','render'],
  check_output_quality:['grade','qc'],verify_segmentation_input:['masks','track'],segment_subject:['masks','track'],
  decode_segmentation_masks:['masks','track'],render_mask_overlay:['masks','track'],validate_local_masks:['masks','validate_masks'],
  creative_review:['review'],
};
export function processingTimings(state:BackendState|undefined,now=Date.now(),online=true){
  const byStep:Partial<Record<StepId,number>>={};const parts:Partial<Record<Part,number>>={};
  const seen=new Set<string>();
  const markers=(state?.events??[]).filter(e=>{
    if(e.details?.progress_timing!==true||typeof e.details.progress_active!=='boolean'||seen.has(e.id))return false;
    seen.add(e.id);return Number.isFinite(Date.parse(e.timestamp));
  }).sort((a,b)=>Date.parse(a.timestamp)-Date.parse(b.timestamp));
  let previous:typeof markers[number]|undefined;let group:[StepId,Part?]|undefined;let interrupted=false;
  const add=(end:number)=>{
    if(!previous?.details?.progress_active||!group)return;
    const elapsed=Math.max(0,end-Date.parse(previous.timestamp));
    byStep[group[0]]=(byStep[group[0]]??0)+elapsed;
    if(group[1])parts[group[1]]=(parts[group[1]]??0)+elapsed;
  };
  for(const marker of markers){
    add(Date.parse(marker.timestamp));
    const stage=String(marker.details?.progress_stage??'');
    group=stages[stage]??(stage==='cancelling'?group:undefined);
    previous=marker;interrupted||=marker.details?.progress_interrupted===true;
  }
  const running=!!previous?.details?.progress_active&&!!state&&activeJob(state);
  const lastSaved=Date.parse(state?.updated_at??previous?.timestamp??'');
  add(running&&online?now:Math.min(now,Number.isFinite(lastSaved)?lastSaved:now));
  return {byStep,parts,total:markers.length?Object.values(byStep).reduce((a,b)=>a+(b??0),0):null,
    running:running&&online,interrupted,recorded:markers.length>0};
}

export function formatDuration(milliseconds:number|null|undefined){
  if(milliseconds==null||!Number.isFinite(milliseconds))return '—';
  if(milliseconds>0&&milliseconds<1000)return '<1s';
  const seconds=Math.max(0,Math.floor(milliseconds/1000));
  const values=seconds>=3600?[Math.floor(seconds/3600),Math.floor(seconds/60)%60,seconds%60]:[Math.floor(seconds/60),seconds%60];
  return values.map(v=>String(v).padStart(2,'0')).join(':');
}
