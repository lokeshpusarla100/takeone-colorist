import { AppError } from './errors';
import type { ErrorDetails } from './errors';
import type { SessionEvent } from './types';
import { nextProfile, nextContext } from './preferenceContext';

export type Question = {
  id:string; question:string; scope:string; decision_fork:string;
  options:{id:string;label:string;intended_outcome:string}[];
};
export type Brief = {
  initial_request:string;
  directives:{dimension:string;scope:string;outcome:string;provenance:string}[];
  explicit_constraints:string[];default_protections:string[];technical_envelope:string[];
  answers:{question_id:string;response_type:string;response:string}[];
  unresolved:string[];
};
export type RenderCandidate = {
  id:string;name:string;created_at:string;brief_sha256:string;source_sha256:string;
  status:'planning'|'rendering'|'ready'|'failed'|'cancelled';
  recipe:null|{proposal:{parameters:{contrast:number;saturation:number;gamma:number};
    summary:string;decisions:{parameter:string;reason:string;source_ids:string[]}[];limitations:string[]}};
  preview_url:string|null;download_url:string|null;width:number|null;height:number|null;duration:number|null;
  validation:null|{warnings:string[];limitations:string[];review_status:string;bytes:number;audio_mode:string};
  error:null|{code:string;message:string;action:string};
};
export type BackendState = {
  id:string;revision:number;
  status:'ingesting'|'ready'|'analyzing'|'awaiting_answer'|'brief_ready'|'rendering'|'render_ready'|'segmenting'|'failed'|'cancelled';
  stage:string;filename:string;request:string;answered_count:number;max_questions:number;model_calls:number;
  source:null|{name:string;bytes:number;sha256:string;width:number;height:number;duration:number;
    video_codec:string;audio_codec:string|null;technical_notes:string[]};
  preview:null|{url:string;width:number;height:number;duration:number;bytes:number};
  analysis:null|{summary:string;caveats:string[]};
  question:Question|null;brief:Brief|null;events:SessionEvent[];
  consultation?:null|{reason:'question_selected'|'question_limit'|'no_candidates'|'no_valid_candidates';
    message:string;model_explanation:string;proposed_count:number;rejected_count:number};
  error:null|{code:string;message:string;action:string;details:ErrorDetails};
  candidates?:RenderCandidate[];
  selected_candidate_id?:string|null;
  owner_id?:string|null;profile_id?:string;preference_context?:Record<string,string>;
  memory_capture_error?:{message:string}|null;
  sam_calls?:number;
  segmentations?:{id:string;concept:string;model:string;status:'queued'|'preparing'|'streaming'|'overlay'|'ready'|'empty'|'failed'|'cancelled';
    duration_seconds?:number|null;received_frames?:number;
    frame_count:number;frames_with_masks:number;mask_count:number;
    tracks:{object_id:string;visible_frames:number;intervals:number[][]}[];
    preview_url:string|null;manifest_url:string|null;error:null|{message:string}}[];
};
export type Health = {
  status:string;model:string;muse_configured:boolean;ffmpeg_available:boolean;ffprobe_available:boolean;
  max_upload_bytes:number;max_duration_seconds:number;max_questions:number;grading_connected:boolean;
  sam_model?:string;segmentation_connected?:boolean;
};
const record=(value:unknown):value is Record<string,unknown>=>typeof value==='object'&&value!==null;
const text=(value:unknown)=>typeof value==='string';
const finite=(value:unknown)=>typeof value==='number'&&Number.isFinite(value);
const strings=(value:unknown)=>Array.isArray(value)&&value.every(text);
const uuid=(value:unknown)=>text(value)&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value as string);
const primitive=(value:unknown)=>value===null||text(value)||finite(value)||typeof value==='boolean';
const details=(value:unknown)=>record(value)&&Object.values(value).every(primitive);
const validEvent=(value:unknown)=>record(value)&&text(value.id)&&text(value.timestamp)
  &&Number.isFinite(Date.parse(value.timestamp as string))&&text(value.title)
  &&['app','user','muse','renderer'].includes(value.actor as string)
  &&['activity','decision','error'].includes(value.kind as string)
  &&(value.reason===undefined||text(value.reason))&&(value.details===undefined||details(value.details));
function invalidResponse():never {
  throw new AppError('API_RESPONSE_INVALID','The backend state did not match the application contract.',
    'Refresh the project. If it repeats, inspect the backend terminal. Invalid state has not been saved.');
}
export function parseBackend(value:unknown):BackendState {
  if(!record(value)||!uuid(value.id)||!Number.isInteger(value.revision)||Number(value.revision)<0
    ||!['ingesting','ready','analyzing','awaiting_answer','brief_ready','rendering','render_ready','segmenting','failed','cancelled'].includes(value.status as string)
    ||!text(value.stage)||!text(value.filename)||!text(value.request)||!finite(value.answered_count)
    ||!finite(value.max_questions)||!finite(value.model_calls)||!Array.isArray(value.events)||!value.events.every(validEvent)){
    return invalidResponse();
  }
  if(value.source!==null&&(!record(value.source)||!text(value.source.name)||!finite(value.source.bytes)
    ||!finite(value.source.width)||Number(value.source.width)<=0||!finite(value.source.height)||Number(value.source.height)<=0
    ||!finite(value.source.duration)||Number(value.source.duration)<=0||!text(value.source.sha256)
    ||!strings(value.source.technical_notes)))return invalidResponse();
  if(value.preview!==null&&(!record(value.preview)||value.preview.url!=='/api/projects/'+value.id+'/preview'
    ||!finite(value.preview.width)||!finite(value.preview.height)||!finite(value.preview.duration)
    ||!finite(value.preview.bytes)))return invalidResponse();
  if(value.question!==null&&(!record(value.question)||!text(value.question.id)||!text(value.question.question)
    ||!text(value.question.decision_fork)||!Array.isArray(value.question.options)
    ||value.question.options.length<2||value.question.options.length>3
    ||!value.question.options.every(o=>record(o)&&text(o.id)&&text(o.label)&&text(o.intended_outcome))))return invalidResponse();
  if(value.brief!==null&&(!record(value.brief)||!text(value.brief.initial_request)||!Array.isArray(value.brief.directives)
    ||!value.brief.directives.every(d=>record(d)&&text(d.outcome)&&text(d.scope)&&text(d.provenance))
    ||!strings(value.brief.explicit_constraints)||!strings(value.brief.default_protections)
    ||!strings(value.brief.technical_envelope)||!strings(value.brief.unresolved)||!Array.isArray(value.brief.answers)))return invalidResponse();
  if(value.error!==null&&(!record(value.error)||!text(value.error.code)||!text(value.error.message)
    ||!text(value.error.action)||!details(value.error.details)))return invalidResponse();
  if(value.status==='awaiting_answer'&&!value.question)return invalidResponse();
  if(value.consultation!==undefined&&value.consultation!==null&&(!record(value.consultation)
    ||!['question_selected','question_limit','no_candidates','no_valid_candidates'].includes(value.consultation.reason as string)
    ||!text(value.consultation.message)||!text(value.consultation.model_explanation)
    ||!Number.isInteger(value.consultation.proposed_count)||Number(value.consultation.proposed_count)<0
    ||!Number.isInteger(value.consultation.rejected_count)||Number(value.consultation.rejected_count)<0))return invalidResponse();
  if(value.status==='brief_ready'&&!value.brief)return invalidResponse();
  if(['ready','awaiting_answer','brief_ready','rendering','render_ready','segmenting'].includes(value.status as string)&&(!value.source||!value.preview))return invalidResponse();
  if(value.segmentations!==undefined&&(!Array.isArray(value.segmentations)||!value.segmentations.every(s=>{
    if(!record(s)||!uuid(s.id)||!text(s.concept)||!text(s.model)
      ||!['queued','preparing','streaming','overlay','ready','empty','failed','cancelled'].includes(s.status as string)
      ||![s.frame_count,s.frames_with_masks,s.mask_count].every(n=>Number.isSafeInteger(n)&&Number(n)>=0)
      ||!Array.isArray(s.tracks)||!s.tracks.every(t=>record(t)&&text(t.object_id)&&finite(t.visible_frames)&&Array.isArray(t.intervals)))return false;
    const prefix='/api/projects/'+value.id+'/segmentations/'+s.id;
    return (s.preview_url===null||s.preview_url===prefix+'/preview')&&(s.manifest_url===null||s.manifest_url===prefix+'/manifest');
  })))return invalidResponse();
  if(value.candidates!==undefined&&(!Array.isArray(value.candidates)||!value.candidates.every(c=>{
    if(!record(c)||!uuid(c.id)||!text(c.name)||!text(c.created_at)
      ||!['planning','rendering','ready','failed','cancelled'].includes(c.status as string))return false;
    if(c.recipe!==null&&(!record(c.recipe)||!record(c.recipe.proposal)||!record(c.recipe.proposal.parameters)
      ||!text(c.recipe.proposal.summary)||!Array.isArray(c.recipe.proposal.decisions)||!strings(c.recipe.proposal.limitations)))return false;
    if(c.status==='ready'){
      const prefix='/api/projects/'+value.id+'/candidates/'+c.id;
      return c.preview_url===prefix+'/preview'&&c.download_url===prefix+'/download'
        &&finite(c.width)&&Number(c.width)>0&&finite(c.height)&&Number(c.height)>0&&finite(c.duration)&&Number(c.duration)>0
        &&record(c.validation)&&strings(c.validation.warnings)&&strings(c.validation.limitations);
    }
    return true;
  })))return invalidResponse();
  if(value.selected_candidate_id!==undefined&&value.selected_candidate_id!==null
    &&(!uuid(value.selected_candidate_id)||!Array.isArray(value.candidates)
      ||!value.candidates.some(c=>record(c)&&c.id===value.selected_candidate_id&&c.status==='ready')))return invalidResponse();
  if(value.status==='render_ready'&&!value.selected_candidate_id)return invalidResponse();
  return value as unknown as BackendState;
}
export const activeJob=(s:BackendState)=>s.status==='ingesting'||s.status==='analyzing'||s.status==='rendering'||s.status==='segmenting';
export function stageLabel(stage:string) {
  const labels:Record<string,string>={
    inspect_source:'Inspecting source metadata',prepare_preview:'Preparing video previews',
    analyze_scene:'Muse is inspecting the video',select_question:'Checking whether clarification is needed',
    awaiting_answer:'Waiting for your answer',brief_ready:'Creative brief ready',ready:'Source ready',
    cancelling:'Cancelling; an in-flight request may still finish',queue:'Waiting for the worker',
    plan_grade:'Preparing the global grading recipe',render_grade:'Rendering and validating the video',render_ready:'Rendered video ready',
    verify_segmentation_input:'Verifying source/proxy frame mapping',segment_subject:'SAM is tracking the subject',
    render_mask_overlay:'Rendering the mask diagnostic overlay',segmentation_ready:'Subject tracking complete',
    decode_segmentation_masks:'Validating and decoding saved SAM masks locally',
  };
  return labels[stage]??stage;
}
function issue(value:unknown,status:number):AppError {
  const body=value as {error?:{code?:string;message?:string;action?:string;details?:ErrorDetails}};
  if(body?.error&&typeof body.error.message==='string') {
    return new AppError(body.error.code??'API_FAILED',body.error.message,
      body.error.action??'Retry the failed operation.',{...body.error.details,http_status:status});
  }
  return new AppError('API_FAILED','The backend request failed.','Check the backend terminal and retry.',{http_status:status});
}
export async function request<T>(path:string,options:RequestInit={},timeout=15000):Promise<T> {
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),timeout);
  const external=options.signal;
  const abort=()=>controller.abort();
  external?.addEventListener('abort',abort,{once:true});
  if(external?.aborted)controller.abort();
  try {
    const response=await fetch(path,{...options,signal:controller.signal,credentials:'same-origin'});
    let body:unknown;
    try{body=await response.json();}catch(cause){
      if(controller.signal.aborted)throw cause;
      throw new AppError('API_RESPONSE_INVALID','The backend returned an unreadable response.',
        'Check that the Python backend is running and the frontend API proxy is configured.',{http_status:response.status});
    }
    if(!response.ok)throw issue(body,response.status);
    return body as T;
  }catch(cause){
    if(cause instanceof AppError)throw cause;
    if(external?.aborted)throw new AppError('MEDIA_ABORTED','Operation cancelled.','The current session has been kept.');
    throw new AppError(controller.signal.aborted?'API_TIMEOUT':'BACKEND_UNAVAILABLE',
      controller.signal.aborted?'The backend request timed out.':'The local backend could not be reached.',
      'Check the backend terminal. Refresh to recover any work already accepted by the server.',
      {nativeMessage:cause instanceof Error?cause.message:String(cause)});
  }finally{clearTimeout(timer);external?.removeEventListener('abort',abort);}
}
export async function health() {
  const value=await request<unknown>('/api/health');
  if(!record(value)||value.status!=='ok'||!text(value.model)||typeof value.muse_configured!=='boolean'
    ||!finite(value.max_upload_bytes)||!finite(value.max_questions))return invalidResponse();
  return value as unknown as Health;
}
export const getState=async(id:string,signal?:AbortSignal)=>parseBackend(await request<unknown>('/api/projects/'+id,{signal}));
export async function upload(file:File,signal:AbortSignal) {
  const form=new FormData();form.append('file',file);
  form.append('profile_id',nextProfile());form.append('preference_context',JSON.stringify(nextContext()));
  return parseBackend(await request<unknown>('/api/projects',{method:'POST',body:form,signal},120000));
}
export async function command(id:string,kind:'analyze'|'answers'|'retry'|'render'|'feedback'|'segment',body:Record<string,unknown>) {
  return parseBackend(await request<unknown>('/api/projects/'+id+'/'+kind,{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({...body,command_id:crypto.randomUUID()}),
  }));
}
export const cancel=async(id:string)=>parseBackend(await request<unknown>('/api/projects/'+id+'/cancel',{method:'POST'}));
export function backendError(state:BackendState) {
  const e=state.error;
  return e?new AppError(e.code,e.message,e.action,e.details):new AppError('BACKEND_JOB_FAILED','The backend operation failed.','Retry the saved step.');
}
export async function waitForSource(initial:BackendState,onProgress:(s:BackendState)=>void,signal:AbortSignal) {
  let state=initial;
  while(!state.preview&&activeJob(state)) {
    onProgress(state);
    await new Promise<void>((resolve,reject)=>{
      const abort=()=>{clearTimeout(timer);reject(new AppError('MEDIA_ABORTED','Import cancelled.','The original remains on the backend.'));};
      const timer=setTimeout(()=>{signal.removeEventListener('abort',abort);resolve();},750);
      if(signal.aborted){abort();return;}
      signal.addEventListener('abort',abort,{once:true});
    });
    state=await getState(state.id,signal);
  }
  onProgress(state);
  if(!state.source||!state.preview)throw backendError(state);
  return state;
}
export async function previewBlob(state:BackendState,signal?:AbortSignal) {
  if(!state.preview)throw new AppError('PREVIEW_NOT_READY','No preview is available.','Retry source preparation.');
  let response:Response;
  try{response=await fetch(state.preview.url,{signal,credentials:'same-origin'});}
  catch(cause){
    if(signal?.aborted)throw new AppError('MEDIA_ABORTED','Import cancelled.','The original remains on the backend.');
    throw new AppError('PREVIEW_FETCH_FAILED','The prepared preview could not be downloaded.','Check the backend and retry.',
      {nativeMessage:cause instanceof Error?cause.message:String(cause)});
  }
  if(!response.ok)throw new AppError('PREVIEW_FETCH_FAILED','The prepared preview could not be downloaded.',
    'Check the backend and retry.',{http_status:response.status});
  if(!response.headers.get('content-type')?.startsWith('video/webm')){
    throw new AppError('PREVIEW_RESPONSE_INVALID','The backend did not return a WebM preview.','Check the backend and retry.');
  }
  try{return await response.blob();}
  catch(cause){
    if(signal?.aborted)throw new AppError('MEDIA_ABORTED','Import cancelled.','The source remains on the backend.');
    throw new AppError('PREVIEW_FETCH_FAILED','The preview download was interrupted.','Retry source preparation.',
      {nativeMessage:cause instanceof Error?cause.message:String(cause)});
  }
}
