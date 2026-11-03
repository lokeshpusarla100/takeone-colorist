import { activeJob } from './api';
import type { BackendState } from './api';

/** One measured fact about the stage that is running now (encode position from FFmpeg). */
export type Live={stage:string;kind:'encode';fraction:number;speed:number|null;eta_seconds:number|null;encoded_seconds:number;total_seconds:number};

export type SegmentId='inspect'|'clarify'|'brief'|'plan'|'masks'|'render'|'check'|'review';
export type SegmentPhase='done'|'active'|'waiting'|'failed'|'pending'|'skipped';
export type Segment={id:SegmentId;label:string;phase:SegmentPhase;fraction?:number};

const ORDER:SegmentId[]=['inspect','clarify','brief','plan','masks','render','check','review'];
const LABEL:Record<SegmentId,string>={inspect:'Inspect',clarify:'Q&A',brief:'Brief',plan:'Plan',masks:'Masks',render:'Render',check:'Check',review:'Review'};
const STAGE:Record<string,SegmentId>={
  analyze_scene:'inspect',select_question:'clarify',awaiting_answer:'clarify',save_brief:'brief',
  plan_grade:'plan',preflight_grade:'plan',plan_revision:'plan',
  verify_segmentation_input:'masks',segment_subject:'masks',decode_segmentation_masks:'masks',render_mask_overlay:'masks',validate_local_masks:'masks',
  render_grade:'render',render_local_grade:'render',render_final:'render',check_output_quality:'check',creative_review:'review',
};
export const segmentOfStage=(stage:string):SegmentId|null=>STAGE[stage]??null;
const ENCODING=new Set(['render_grade','render_local_grade','render_final']);
export const isEncoding=(stage?:string)=>!!stage&&ENCODING.has(stage);

/** Server records are validated before use; anything unexpected is simply "no live measurement". */
export function parseLive(value:unknown):Live|null{
  const v=value as Partial<Live>|null;
  if(!v||typeof v!=='object'||v.kind!=='encode'||typeof v.stage!=='string')return null;
  const finite=(n:unknown):n is number=>typeof n==='number'&&Number.isFinite(n);
  if(!finite(v.fraction)||!finite(v.encoded_seconds)||!finite(v.total_seconds))return null;
  return {stage:v.stage,kind:'encode',fraction:Math.min(1,Math.max(0,v.fraction)),encoded_seconds:v.encoded_seconds,total_seconds:v.total_seconds,
    speed:finite(v.speed)?v.speed:null,eta_seconds:finite(v.eta_seconds)?v.eta_seconds:null};
}

function lastKnownStage(state:BackendState){
  const events=state.events??[];
  for(let i=events.length-1;i>=0;i--){
    const stage=events[i].details?.progress_stage;
    if(events[i].details?.progress_timing===true&&typeof stage==='string'&&segmentOfStage(stage))return stage;
  }
  return state.stage;
}

export type Pipeline={
  segments:Segment[];activeId:SegmentId|null;
  /** Finished segments plus the measured fraction of the active one; segments that are not needed are not counted. */
  total:number;value:number;measured:boolean;
  /** "Step 6 of 8: Render, 42%": the same text for screen readers and the caption. */
  text:string;detail:string|null;
};

const pluralFrames=(n:number)=>n===1?'1 frame':`${n.toLocaleString()} frames`;

export function formatEta(seconds:number){
  if(!Number.isFinite(seconds)||seconds<0)return '';
  if(seconds<45)return 'under a minute left';
  const minutes=Math.round(seconds/60);
  return minutes>=60?`about ${Math.floor(minutes/60)} h ${minutes%60} min left`:`about ${minutes} min left`;
}

/**
 * The whole run as segments: what is finished, what is running, what is still ahead. The position comes from the
 * backend stage; a fill inside the running segment appears only where something is measured (encode position,
 * tracked frames). Nothing is guessed: other stages show an indeterminate stripe and their elapsed time.
 */
export function runPipeline(state?:BackendState,live?:Live|null):Pipeline|null{
  if(!state||!state.source||!state.preview)return null;
  const working=activeJob(state);
  const waiting=state.status==='awaiting_answer';
  const failed=state.status==='failed'||state.status==='cancelled';
  const latest=state.candidates?.at(-1);
  // "cancelling" (and a stopped job's stage) replaces the stage that was running; its last timing marker still knows it.
  const stage=segmentOfStage(state.stage)?state.stage:lastKnownStage(state);
  const current=working||waiting||failed?segmentOfStage(stage):null;
  if(!state.analysis&&!current&&!working)return null;
  const index=current?ORDER.indexOf(current):-1;
  const localized=latest?.recipe?.proposal.mode==='localized';
  const globalOnly=!!latest?.recipe&&!localized;
  const idleDone:Record<SegmentId,boolean>={
    inspect:!!state.analysis,clarify:!!state.brief,brief:!!state.brief,
    plan:latest?.status==='ready',render:latest?.status==='ready',check:latest?.status==='ready',
    masks:latest?.status==='ready'&&localized,review:!!latest?.creative_review,
  };
  const segments:Segment[]=ORDER.map((id,i):Segment=>{
    const label=LABEL[id];
    if(id==='masks'&&globalOnly&&current!=='masks')return {id,label,phase:'skipped'};
    if(index>=0){
      if(i<index)return {id,label,phase:'done'};
      if(i===index)return {id,label,phase:failed?'failed':waiting?'waiting':'active'};
      return {id,label,phase:'pending'};
    }
    if(idleDone[id])return {id,label,phase:'done'};
    // A finished render that was never reviewed has nothing left to do for review.
    if(id==='review'&&latest?.status==='ready'&&state.status==='render_ready')return {id,label,phase:'skipped'};
    return {id,label,phase:'pending'};
  });
  const active=segments.find(s=>s.phase==='active');
  let detail:string|null=null;
  if(active&&current==='render'&&live&&live.stage===state.stage){
    active.fraction=live.fraction;
    detail=`${Math.round(live.fraction*100)}% encoded${live.speed?` · ${live.speed.toFixed(1)}× real time`:''}${live.eta_seconds!=null?` · ${formatEta(live.eta_seconds)}`:''}`;
  }else if(active&&current==='masks'){
    const run=state.segmentations?.at(-1);
    if(run&&run.frame_count>0&&run.status==='streaming'&&typeof run.received_frames==='number'){
      active.fraction=Math.min(1,run.received_frames/run.frame_count);
      detail=`${pluralFrames(run.received_frames)} of ${run.frame_count.toLocaleString()} tracked`;
    }
  }
  const counted=segments.filter(s=>s.phase!=='skipped');
  const done=counted.filter(s=>s.phase==='done').length;
  const value=done+(active?.fraction??0);
  const position=counted.findIndex(s=>s.phase==='active'||s.phase==='waiting'||s.phase==='failed');
  const focus=position>=0?counted[position]:null;
  const text=focus?`Step ${position+1} of ${counted.length}: ${focus.label}${focus.phase==='waiting'?' (waiting for you)':focus.phase==='failed'?' (stopped)':
    active?.fraction!==undefined?`, ${Math.round(active.fraction*100)}%`:''}`
    :done===counted.length?'All steps complete':`${done} of ${counted.length} steps complete`;
  return {segments,activeId:active?.id??null,total:counted.length,value,measured:active?.fraction!==undefined,text,detail};
}
