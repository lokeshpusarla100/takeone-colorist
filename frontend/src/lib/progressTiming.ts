import { activeJob } from './api';
import type { BackendState } from './api';

export type StepId='prepare'|'inspect'|'clarify'|'brief'|'grade'|'masks'|'review';
type Part='plan'|'render'|'qc'|'track'|'validate_masks';
const stages:Record<string,[StepId,Part?]>={
  inspect_source:['prepare'],prepare_preview:['prepare'],analyze_scene:['inspect'],select_question:['clarify'],save_brief:['brief'],
  plan_grade:['grade','plan'],plan_revision:['grade','plan'],preflight_grade:['grade','plan'],render_grade:['grade','render'],render_local_grade:['grade','render'],render_final:['grade','render'],
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
  let attemptTotal=0;let lifetimeTotal=0;const jobs=new Set<string>();
  const add=(end:number)=>{
    if(!previous?.details?.progress_active||!group)return;
    const elapsed=Math.max(0,end-Date.parse(previous.timestamp));
    attemptTotal+=elapsed;lifetimeTotal+=elapsed;
    byStep[group[0]]=(byStep[group[0]]??0)+elapsed;
    if(group[1])parts[group[1]]=(parts[group[1]]??0)+elapsed;
  };
  for(const marker of markers){
    add(Date.parse(marker.timestamp));
    const stage=String(marker.details?.progress_stage??'');
    const job=marker.details?.progress_job;
    if(marker.details?.progress_active&&typeof job==='string'&&!jobs.has(job)){
      jobs.add(job);attemptTotal=0;interrupted=false;
      const reset:StepId[]=stage==='plan_grade'?['grade','masks','review']:stage==='inspect_source'?['prepare']:
        stage==='analyze_scene'?['inspect','clarify','brief']:stage==='creative_review'?['review']:
        stage==='verify_segmentation_input'?['masks']:marker.details?.progress_retry?['clarify','brief']:[];
      for(const id of reset)delete byStep[id];
      if(reset.includes('grade'))for(const id of ['plan','render','qc'] as const)delete parts[id];
      if(reset.includes('masks'))for(const id of ['track','validate_masks'] as const)delete parts[id];
    }
    group=stages[stage]??(stage==='cancelling'?group:undefined);
    previous=marker;interrupted||=marker.details?.progress_interrupted===true;
  }
  const running=!!previous?.details?.progress_active&&!!state&&activeJob(state);
  const lastSaved=Date.parse(state?.updated_at??previous?.timestamp??'');
  add(running&&online?now:Math.min(now,Number.isFinite(lastSaved)?lastSaved:now));
  return {byStep,parts,total:markers.length?attemptTotal:null,lifetimeTotal:markers.length?lifetimeTotal:null,
    running:running&&online,interrupted,recorded:markers.length>0};
}

export function formatDuration(milliseconds:number|null|undefined){
  if(milliseconds==null||!Number.isFinite(milliseconds))return '—';
  if(milliseconds>0&&milliseconds<1000)return '<1s';
  const seconds=Math.max(0,Math.floor(milliseconds/1000));
  const values=seconds>=3600?[Math.floor(seconds/3600),Math.floor(seconds/60)%60,seconds%60]:[Math.floor(seconds/60),seconds%60];
  return values.map(v=>String(v).padStart(2,'0')).join(':');
}
