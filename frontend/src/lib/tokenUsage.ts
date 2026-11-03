import type { SessionEvent } from './types';

export type Stage='analysis'|'rubric'|'colorist'|'sam'|'critic';
export type UsageRow={
  id:string;timestamp:string;label:string;stage:Stage;model:string|null;
  input:number|null;output:number|null;total:number|null;cached:number|null;reasoning:number|null;
  reported:boolean;running:boolean;
  processedFrames?:number|null;
};
const starts:Record<string,Stage>={
  'Inspecting video with Muse':'analysis','Selecting a useful clarification':'rubric',
  'Muse is preparing a global grading recipe':'colorist',
  'Muse is planning supported adjustments':'colorist',
  'Tracking subject with SAM':'sam',
  'Muse is reviewing rendered evidence':'critic',
};
const usageTitles:Record<string,Stage>={
  'Scene analysis usage':'analysis','Scene analysis saved':'analysis',
  'Muse request usage':'rubric','Colorist request usage':'colorist',
  'SAM request usage':'sam',
  'Critic request usage':'critic',
};
const labels:Record<Stage,string>={analysis:'Video analysis',rubric:'Rubric',colorist:'Colorist',sam:'SAM masks',critic:'Creative review'};
const count=(value:unknown):number|null=>typeof value==='number'&&Number.isSafeInteger(value)&&value>=0?value:null;
const text=(value:unknown):string|null=>typeof value==='string'&&value?value:null;

/** Project event IDs are authoritative. Old call numbers can repeat after new directions. */
export function tokenUsage(events:SessionEvent[],activeStage?:string){
  const rows:UsageRow[]=[];
  const requests=new Map<string,UsageRow>();
  const legacy:Partial<Record<Stage,UsageRow>>={};
  const seen=new Set<string>();
  let latestRequest:UsageRow|undefined;
  for(const event of events){
    if(seen.has(event.id)||event.actor!=='muse')continue;
    seen.add(event.id);
    const details=event.details??{};
    const start=starts[event.title];
    const requestId=text(details.request_id);
    if(start){
      const id=requestId??event.id;
      if(requests.has(id))continue;
      const row:UsageRow={id,timestamp:event.timestamp,label:labels[start],stage:start,model:text(details.model),
        input:null,output:null,total:null,cached:null,reasoning:null,reported:false,running:false};
      rows.push(row);requests.set(id,row);legacy[start]=row;latestRequest=row;
      continue;
    }
    const stage=usageTitles[event.title];
    if(!stage)continue;
    // "Scene analysis saved" in older projects doubles as the usage receipt.
    if(event.title==='Scene analysis saved'&&!('input_tokens' in details||'output_tokens' in details))continue;
    let row=requestId?requests.get(requestId):legacy[stage];
    if(!row){
      const id=requestId??event.id;
      row={id,timestamp:event.timestamp,label:labels[stage],stage,model:text(details.model),
        input:null,output:null,total:null,cached:null,reasoning:null,reported:false,running:false};
      rows.push(row);requests.set(id,row);
    }
    if(row.reported)continue;
    row.reported=true;
    row.input=count(details.input_tokens)??count(details.prompt_tokens);
    row.output=count(details.output_tokens)??count(details.completion_tokens);
    row.total=count(details.total_tokens)??(row.input!==null&&row.output!==null?row.input+row.output:null);
    row.cached=count(details.cached_tokens);row.reasoning=count(details.reasoning_tokens);
    row.processedFrames=count(details.video_frames_processed);
    row.model=text(details.model)??row.model;
  }
  // Only the latest outstanding model request may be running. Old failures stay unknown.
  if(latestRequest&&!latestRequest.reported&&['analyze_scene','select_question','plan_grade','segment_subject','creative_review','cancelling'].includes(activeStage??'')){
    latestRequest.running=true;
  }
  const sum=(key:'input'|'output'|'total'|'cached'|'reasoning')=>{
    const values=rows.map(r=>r[key]).filter((n):n is number=>n!==null);
    return values.length?values.reduce((a,b)=>a+b,0):null;
  };
  return {rows,input:sum('input'),output:sum('output'),total:sum('total'),cached:sum('cached'),reasoning:sum('reasoning'),
    incomplete:rows.filter(r=>!r.running&&(r.input===null||r.output===null||r.total===null)).length,
    running:rows.some(r=>r.running)};
}
