import test from 'node:test';
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import {join} from 'node:path';
const load=name=>import(pathToFileURL(join(process.env.TAKEONE_TEST_BUILD,name+'.mjs')));
const {mediaFailure,asAppError}=await load('errors');
const {inspectVideo}=await load('media');
const {validateProject}=await load('storage');
const {makeProject}=await load('types');
const {parseBackend,request,waitForSource,command,health,upload}=await load('api');
const {preparedSource}=await load('preparedSource');
const {tokenUsage}=await load('tokenUsage');
const {mutationLocked,runProgress}=await load('workflow');
const {processingTimings,formatDuration}=await load('progressTiming');
const {validateRenderImport}=await load('renderImport');
test('proportional playback previews can be compared without claiming master resolution',()=>{
  const source={width:3840,height:2160,duration:24.695222};
  assert.deepEqual(validateRenderImport(source,{width:1280,height:720,duration:24.797}),{previewOnly:true});
  assert.deepEqual(validateRenderImport(source,{width:3840,height:2160,duration:24.7}),{previewOnly:false});
  assert.deepEqual(validateRenderImport({width:1920,height:1080,duration:10},{width:640,height:360,duration:10}),{previewOnly:true});
});
test('preview import still rejects changed framing, upscales and duration mismatches',()=>{
  const source={width:3840,height:2160,duration:24.695222};
  for(const render of [
    {width:1280,height:800,duration:24.797},{width:720,height:1280,duration:24.7},
    {width:7680,height:4320,duration:24.7},{width:1280,height:720,duration:25.5},
    {width:NaN,height:720,duration:24.7},{width:0,height:0,duration:24.7},
  ])assert.throws(()=>validateRenderImport(source,render),{code:'RENDER_MISMATCH'});
});
test('imported preview label survives persistence and has no candidate provenance',()=>{
  const p=makeProject();const asset={id:crypto.randomUUID(),name:'preview.webm',size:1,type:'video/webm',width:1280,height:720,duration:24.797,thumbnail:''};
  p.source={...asset,id:crypto.randomUUID(),name:'source.mp4',width:3840,height:2160,duration:24.695222};
  p.assets=[p.source,asset];p.versions=[{id:crypto.randomUUID(),name:'Imported preview',kind:'rendered',createdAt:new Date().toISOString(),assetId:asset.id,recipe:null,previewOnly:true}];
  p.selectedVersionId=p.versions[0].id;
  validateProject(JSON.parse(JSON.stringify(p)));assert.equal(p.versions[0].candidateId,undefined);
  assert.throws(()=>validateProject({...p,versions:[{...p.versions[0],previewOnly:'yes'}]}),{code:'STORAGE_CORRUPT'});
});

const timingEvent=(id,second,stage,active=true,extra={})=>({id,timestamp:new Date(Date.UTC(2026,10,2,10)+second*1000).toISOString(),
  details:{progress_timing:true,progress_stage:stage,progress_active:active,...extra}});
const timingNow=second=>Date.UTC(2026,10,2,10)+second*1000;
test('oversized source upload is rejected before sending any file bytes',async t=>{
  const original=globalThis.fetch;t.after(()=>{globalThis.fetch=original;});let calls=0;
  globalThis.fetch=async path=>{calls++;assert.equal(path,'/api/health');return Response.json({max_upload_bytes:256*1048576});};
  await assert.rejects(upload({size:477646151},new AbortController().signal),e=>e.code==='UPLOAD_LIMIT'
    &&e.details.bytes===477646151&&e.details.max_upload_bytes===256*1048576);
  assert.equal(calls,1);
});
test('within-limit uploads still reach the source endpoint',async t=>{
  const original=globalThis.fetch;t.after(()=>{globalThis.fetch=original;});const calls=[];
  globalThis.fetch=async(path,options)=>{
    calls.push(path);if(path==='/api/health')return Response.json({max_upload_bytes:512*1048576});
    assert.equal(options.body.get('file').name,'small.mp4');
    return Response.json({id:crypto.randomUUID(),revision:1,status:'ingesting',stage:'inspect_source',filename:'small.mp4',
      request:'',answered_count:0,max_questions:5,model_calls:0,source:null,preview:null,question:null,brief:null,events:[],error:null});
  };
  assert.equal((await upload(new File(['test'],'small.mp4'),new AbortController().signal)).status,'ingesting');
  assert.deepEqual(calls,['/api/health','/api/projects']);
});
test('plain proxy and upload errors retain meaningful status instead of invalid JSON diagnosis',async t=>{
  const original=globalThis.fetch;t.after(()=>{globalThis.fetch=original;});
  for(const status of [413,500,502,503,504]){
    globalThis.fetch=async()=>new Response('upstream error',{status});
    await assert.rejects(request('/api/projects'),e=>e.code===(status===413?'UPLOAD_LIMIT':'BACKEND_UNAVAILABLE')&&e.details.http_status===status);
  }
  globalThis.fetch=async()=>new Response('<html/>',{status:200});
  await assert.rejects(request('/api/projects'),{code:'API_RESPONSE_INVALID'});
});
test('missing look routes report a backend update and preserve structured project errors',async t=>{
  const original=globalThis.fetch;t.after(()=>{globalThis.fetch=original;});
  globalThis.fetch=async()=>new Response(JSON.stringify({detail:'Not Found'}),{status:404});
  for(const kind of ['looks','looks/select'])await assert.rejects(command(crypto.randomUUID(),kind,{}),
    e=>e.code==='BACKEND_UPDATE_REQUIRED'&&e.action.includes('Restart'));
  globalThis.fetch=async()=>new Response(JSON.stringify({error:{code:'PROJECT_NOT_FOUND',message:'Missing project',action:'Import again',details:{}}}),{status:404});
  await assert.rejects(command(crypto.randomUUID(),'looks',{}),e=>e.code==='PROJECT_NOT_FOUND');
});
test('health distinguishes an older backend from connected look support',async t=>{
  const original=globalThis.fetch;t.after(()=>{globalThis.fetch=original;});
  const base={status:'ok',model:'mock',muse_configured:true,max_upload_bytes:1,max_questions:5};
  globalThis.fetch=async()=>Response.json(base);assert.equal((await health()).look_previews_connected,undefined);
  globalThis.fetch=async()=>Response.json({...base,look_previews_connected:true});assert.equal((await health()).look_previews_connected,true);
  globalThis.fetch=async()=>Response.json({...base,look_previews_connected:'yes'});await assert.rejects(health(),{code:'API_RESPONSE_INVALID'});
});
test('processing timers persist across refreshes and exclude answer and idle waits',()=>{
  const events=[timingEvent('a',0,'inspect_source'),timingEvent('b',3,'prepare_preview'),timingEvent('c',8,'ready',false),
    timingEvent('d',100,'analyze_scene'),timingEvent('e',110,'select_question'),timingEvent('f',115,'awaiting_answer',false)];
  const state={status:'awaiting_answer',events};
  const measured=processingTimings(state,timingNow(200));
  assert.equal(measured.total,23000);
  assert.deepEqual(measured.byStep,{prepare:8000,inspect:10000,clarify:5000});
  assert.deepEqual(processingTimings(JSON.parse(JSON.stringify(state)),timingNow(500)),measured);
  const running=processingTimings({status:'analyzing',events:[...events,events[0],timingEvent('g',205,'select_question')]},timingNow(210));
  assert.equal(running.total,28000);assert.equal(running.running,true);
});
test('render, masks and review timers count each interval once across phases',()=>{
  const events=[timingEvent('a',0,'plan_grade'),timingEvent('b',2,'segment_subject'),timingEvent('c',8,'validate_local_masks'),
    timingEvent('d',10,'render_local_grade'),timingEvent('e',15,'check_output_quality'),timingEvent('f',17,'creative_review'),timingEvent('g',20,'render_ready',false)];
  const result=processingTimings({status:'render_ready',events},timingNow(50));
  assert.equal(result.total,20000);
  assert.deepEqual(result.byStep,{grade:9000,masks:8000,review:3000});
  assert.deepEqual(result.parts,{plan:2000,track:6000,validate_masks:2000,render:5000,qc:2000});
});
test('timers freeze on disconnect, cancellation and interrupted recovery',()=>{
  const events=[timingEvent('a',0,'plan_grade'),timingEvent('b',5,'render_grade')];
  const state={status:'rendering',events,updated_at:new Date(timingNow(7)).toISOString()};
  assert.equal(processingTimings(state,timingNow(100),false).total,7000);
  const stopped=processingTimings({...state,status:'cancelled',events:[...events,timingEvent('c',8,'cancelling'),timingEvent('d',10,'cancelling',false)]},timingNow(100));
  assert.equal(stopped.total,10000);assert.equal(stopped.running,false);
  const recovered=processingTimings({...state,status:'failed',events:[...events,timingEvent('d',7,'render_grade',false,{progress_interrupted:true})]},timingNow(100));
  assert.equal(recovered.total,7000);assert.equal(recovered.interrupted,true);
});
test('untimed legacy sessions stay unknown and durations support long jobs',()=>{
  assert.equal(processingTimings({events:[],status:'ready'}).total,null);
  assert.equal(formatDuration(undefined),'—');assert.equal(formatDuration(0),'00:00');
  assert.equal(formatDuration(250),'<1s');assert.equal(formatDuration(62000),'01:02');
  assert.equal(formatDuration(3661000),'01:01:01');
});
test('render retry resets render parts and attempt total while retaining completed source time',()=>{
  const events=[timingEvent('a',0,'inspect_source',true,{progress_job:'source'}),timingEvent('b',10,'ready',false),
    timingEvent('c',20,'plan_grade',true,{progress_job:'first'}),timingEvent('d',25,'render_grade',true,{progress_job:'first'}),
    timingEvent('e',40,'check_output_quality',false),timingEvent('f',100,'plan_grade',true,{progress_job:'retry',progress_retry:true})];
  const result=processingTimings({status:'rendering',events},timingNow(102));
  assert.equal(result.byStep.prepare,10000);assert.equal(result.byStep.grade,2000);
  assert.equal(result.parts.plan,2000);assert.equal(result.parts.render,undefined);assert.equal(result.total,2000);
  assert.equal(result.lifetimeTotal,32000);
  assert.deepEqual(processingTimings(JSON.parse(JSON.stringify({status:'rendering',events})),timingNow(102)),result);
});
test('answer continuation accumulates clarification but explicit analysis retry resets it',()=>{
  const events=[timingEvent('a',0,'select_question',true,{progress_job:'q1'}),timingEvent('b',5,'awaiting_answer',false),
    timingEvent('c',10,'select_question',true,{progress_job:'q2'})];
  assert.equal(processingTimings({status:'analyzing',events},timingNow(12)).byStep.clarify,7000);
  events.push(timingEvent('d',13,'select_question',false),timingEvent('e',20,'select_question',true,{progress_job:'retry',progress_retry:true}));
  const result=processingTimings({status:'analyzing',events},timingNow(21));
  assert.equal(result.byStep.clarify,1000);assert.equal(result.total,1000);
});

test('mutating workspace actions lock during local, remote and command processing',()=>{
  for(const status of ['ingesting','analyzing','rendering','segmenting','reviewing'])assert.equal(mutationLocked({status}),true);
  for(const status of ['ready','awaiting_answer','brief_ready','render_ready','failed','cancelled'])assert.equal(mutationLocked({status}),false);
  assert.equal(mutationLocked(undefined,true),true);
  assert.equal(mutationLocked({status:'ready'},false,true),true);
  assert.equal(mutationLocked({status:'ready'},false,false,{status:'ingesting'}),true);
});
test('progress distinguishes waiting for a single answer from model processing and completion',()=>{
  const state={status:'awaiting_answer',stage:'awaiting_answer',source:{},preview:{},analysis:{},
    brief:null,answered_count:1,max_questions:5};
  const waiting=runProgress(state);
  assert.equal(waiting.working,false);
  assert.equal(waiting.steps[2].phase,'waiting');
  assert.match(waiting.steps[2].detail,/answer 2/);
  const processing=runProgress({...state,status:'analyzing',stage:'select_question'});
  assert.equal(processing.working,true);
  assert.equal(processing.steps[2].phase,'active');
  assert.equal(processing.steps[3].phase,'pending');
  const complete=runProgress({...state,status:'brief_ready',stage:'brief_ready',brief:{}});
  assert.equal(complete.working,false);
  assert.equal(complete.steps[2].phase,'complete');
  assert.equal(complete.steps[4].phase,'pending');
  const saving=runProgress({...state,status:'analyzing',stage:'save_brief'});
  assert.equal(saving.steps[2].phase,'complete');assert.equal(saving.steps[3].phase,'active');
  const changedBrief=runProgress({...state,status:'brief_ready',stage:'brief_ready',brief:{},candidates:[{status:'ready'}]});
  assert.equal(changedBrief.steps[4].phase,'pending');
  assert.match(changedBrief.steps[4].detail,/earlier version/);
  const tracking=runProgress({...state,status:'segmenting',stage:'segment_subject',brief:{},segmentations:[{status:'streaming',frame_count:12,frames_with_masks:4}]});
  assert.equal(tracking.working,true);
  assert.equal(tracking.steps[5].phase,'active');
  const gap=runProgress({...state,status:'failed',stage:'decode_segmentation_masks',error:{code:'LOCAL_MASK_GAP'},
    segmentations:[{status:'ready',frame_count:680,frames_with_masks:496}]});
  assert.equal(gap.steps[5].phase,'failed');
  const validating=runProgress({...state,status:'rendering',stage:'validate_local_masks',brief:{},
    candidates:[{status:'rendering',recipe:{proposal:{mode:'localized'}}}],segmentations:[{status:'ready',frame_count:12,frames_with_masks:12}]});
  assert.equal(validating.steps[5].phase,'active');
  const review=runProgress({...state,status:'reviewing',stage:'creative_review',brief:{},candidates:[{status:'ready'}]});
  assert.equal(review.working,true);assert.equal(review.steps[6].phase,'active');
});
test('progress reports persisted failures and source preparation without fake completion',()=>{
  const pending=runProgress(undefined,true,'Uploading source');
  assert.equal(pending.heading,'Uploading source');
  assert.equal(pending.steps[0].phase,'active');
  const failed=runProgress({status:'failed',stage:'analyze_scene',source:{},preview:{},analysis:null,error:{action:'Retry explicitly.'}});
  assert.equal(failed.working,false);
  assert.equal(failed.steps[1].phase,'failed');
  assert.equal(failed.note,'Retry explicitly.');
});

const usageEvent=(id,title,details={})=>({id,title,details,actor:'muse',kind:'activity',timestamp:'2026-11-01T10:00:00Z'});
test('token usage deduplicates event receipts and keeps cached/reasoning counts within totals',()=>{
  const start=usageEvent('start','Inspecting video with Muse',{request_id:'req1',model:'muse-spark-1.3'});
  const receipt=usageEvent('receipt','Scene analysis usage',{request_id:'req1',input_tokens:120,output_tokens:30,
    total_tokens:150,cached_tokens:40,reasoning_tokens:20});
  const usage=tokenUsage([start,start,receipt,receipt]);
  assert.equal(usage.rows.length,1);
  assert.deepEqual([usage.input,usage.output,usage.total,usage.cached,usage.reasoning],[120,30,150,40,20]);
  assert.equal(usage.incomplete,0);
});
test('token usage remains unknown during generation and marks failed/missing receipts as partial',()=>{
  const events=[usageEvent('start','Selecting a useful clarification',{request_id:'req1'})];
  const pending=tokenUsage(events,'select_question');
  assert.equal(pending.running,true);
  assert.equal(pending.total,null);
  assert.equal(pending.incomplete,0);
  const failed=tokenUsage(events);
  assert.equal(failed.running,false);
  assert.equal(failed.incomplete,1);
  assert.equal(failed.total,null);
});
test('legacy usage survives reset call numbers without counting scene-save events twice',()=>{
  const events=[
    usageEvent('a','Inspecting video with Muse',{call:1,model:'muse'}),
    usageEvent('b','Scene analysis saved',{input_tokens:10,output_tokens:2}),
    usageEvent('c','Selecting a useful clarification',{call:2,model:'muse'}),
    usageEvent('d','Muse request usage',{call:2,input_tokens:20,output_tokens:3}),
    usageEvent('e','Selecting a useful clarification',{call:1,model:'muse'}),
    usageEvent('f','Muse request usage',{call:1,input_tokens:30,output_tokens:4}),
    usageEvent('g','Scene analysis saved',{}),
  ];
  const usage=tokenUsage(events);
  assert.equal(usage.rows.length,3);
  assert.deepEqual([usage.input,usage.output,usage.total],[60,9,69]);
});
test('unknown or invalid token fields never become fake zero counts',()=>{
  const usage=tokenUsage([
    usageEvent('a','Colorist request usage',{input_tokens:0,output_tokens:12,total_tokens:12}),
    usageEvent('b','Muse request usage',{input_tokens:-10,output_tokens:null,total_tokens:'100'}),
  ]);
  assert.deepEqual([usage.input,usage.output,usage.total],[0,12,12]);
  assert.equal(usage.incomplete,1);
  assert.equal(usage.rows[1].total,null);
});
test('partial receipts preserve reported provider totals without estimating missing components',()=>{
  const usage=tokenUsage([usageEvent('a','Muse request usage',{input_tokens:15,total_tokens:40})]);
  assert.equal(usage.input,15);assert.equal(usage.output,null);assert.equal(usage.total,40);
  assert.equal(usage.incomplete,1);
});

test('native media errors preserve different diagnoses and original details',()=>{
  for(const [code,expected] of [[1,'MEDIA_ABORTED'],[2,'MEDIA_READ_FAILED'],[3,'MEDIA_DECODE_FAILED'],[4,'MEDIA_UNSUPPORTED']]){
    const error=mediaFailure({code,message:'native diagnostic'},'clip.mp4','media.inspect');
    assert.equal(error.code,expected);
    assert.equal(error.details.browserMessage,'native diagnostic');
    assert.equal(error.details.browserCode,code);
  }
  assert.match(mediaFailure({code:4,message:''},'clip.mp4','media.inspect').action,/exact codec has not been identified/);
});
test('quota failures retain context and a recovery action',()=>{
  const error=asAppError(new DOMException('disk full','QuotaExceededError'),'storage.save');
  assert.equal(error.code,'STORAGE_QUOTA');
  assert.equal(error.details.nativeMessage,'disk full');
  assert.match(error.action,/Export/);
});
test('a disconnected Chromium decoder is not diagnosed as source corruption',()=>{
  const message='PipelineStatus::PIPELINE_ERROR_DISCONNECTED: video decode error!';
  const error=mediaFailure({code:3,message},'test_video.mp4','media.first_frame');
  assert.equal(error.code,'MEDIA_DECODER_DISCONNECTED');
  assert.equal(error.details.browserMessage,message);
  assert.match(error.action,/does not establish that the source file is damaged/);
  assert.equal(mediaFailure({code:3,message:'other decode error'},'other.mp4','media.first_frame').code,'MEDIA_DECODE_FAILED');
});
test('invalid saved records and broken references are rejected without mutation',()=>{
  const project=makeProject();
  validateProject(project);
  project.selectedVersionId='missing';
  const original=JSON.stringify(project);
  assert.throws(()=>validateProject(project),{code:'STORAGE_CORRUPT'});
  assert.equal(JSON.stringify(project),original);
  assert.throws(()=>validateProject({...makeProject(),events:[{title:'bad record'}]}),{code:'STORAGE_CORRUPT'});
});
const file=()=>new File(['video bytes'],'clip.mp4',{type:'video/mp4'});
function environment(t,behavior,{canvasFailure=false}={}){
  const oldDocument=globalThis.document;
  const originalRevoke=URL.revokeObjectURL;
  let revoked=0;
  const video={src:'',duration:3,videoWidth:640,videoHeight:360,readyState:0,networkState:1,error:null,
    removeAttribute(){this.src='';},
    load(){if(this.src)queueMicrotask(()=>behavior(this));else this.error=null;},
  };
  globalThis.document={createElement(type){
    if(type==='video')return video;
    return {width:0,height:0,getContext(){return{drawImage(){if(canvasFailure)throw new Error('thumbnail failed');}};},toDataURL(){return'data:image/jpeg;base64,AA';}};
  }};
  URL.revokeObjectURL=url=>{revoked++;originalRevoke(url);};
  t.after(()=>{globalThis.document=oldDocument;URL.revokeObjectURL=originalRevoke;});
  return {video,revoked:()=>revoked};
}
test('empty and non-video files report input errors',async()=>{
  await assert.rejects(inspectVideo(new File([],'clip.mp4')),{code:'MEDIA_EMPTY'});
  await assert.rejects(inspectVideo(new File(['x'],'note.txt',{type:'text/plain'})),{code:'MEDIA_FILE_TYPE'});
});
test('a decoder failure is captured before cleanup resets MediaError',async t=>{
  const env=environment(t,v=>{v.error={code:3,message:'decoder pipeline failed'};v.onerror();});
  await assert.rejects(inspectVideo(file()),e=>e.code==='MEDIA_DECODE_FAILED'&&e.details.browserMessage==='decoder pipeline failed');
  assert.equal(env.video.error,null);
  assert.equal(env.revoked(),1);
});
test('a thumbnail failure does not reject playable media',async t=>{
  const env=environment(t,v=>{v.onloadedmetadata();v.onloadeddata();},{canvasFailure:true});
  const asset=await inspectVideo(file());
  assert.equal(asset.width,640);
  assert.equal(asset.thumbnail,'');
  assert.equal(asset.thumbnailError,'thumbnail failed');
  assert.equal(env.revoked(),1);
});
test('timeouts settle and release resources rather than hanging an import',async t=>{
  const env=environment(t,()=>{});
  await assert.rejects(inspectVideo(file(),{timeoutMs:5}),{code:'MEDIA_TIMEOUT'});
  assert.equal(env.revoked(),1);
});
test('cancellation settles immediately and releases resources',async t=>{
  const env=environment(t,()=>{});
  const controller=new AbortController();
  const pending=inspectVideo(file(),{signal:controller.signal});
  controller.abort();
  await assert.rejects(pending,{code:'MEDIA_ABORTED'});
  assert.equal(env.revoked(),1);
});
test('structured backend errors keep their code, recovery action and request ID',async t=>{
  const original=globalThis.fetch;t.after(()=>{globalThis.fetch=original;});
  globalThis.fetch=async()=>new Response(JSON.stringify({error:{
    code:'MUSE_NOT_CONFIGURED',message:'Key missing',action:'Configure .env',details:{request_id:'test-request',timing:{max_error_ms:4.8}},
  }}),{status:503,headers:{'Content-Type':'application/json'}});
  await assert.rejects(request('/api/health'),e=>e.code==='MUSE_NOT_CONFIGURED'
    &&e.action==='Configure .env'&&e.details.request_id==='test-request'&&JSON.parse(e.details.timing).max_error_ms===4.8);
});
test('malformed or inconsistent backend state is rejected before it reaches storage',()=>{
  const state={id:crypto.randomUUID(),revision:1,status:'ingesting',stage:'inspect_source',filename:'test.mp4',
    request:'',answered_count:0,max_questions:3,model_calls:0,source:null,preview:null,question:null,brief:null,events:[],error:null};
  assert.equal(parseBackend(state).status,'ingesting');
  const nested=parseBackend({...state,error:{code:'RENDER_TIMING_CHANGED',message:'Timing error',action:'Retry',details:{timing:{maximum:4.8}}}});
  assert.deepEqual(JSON.parse(nested.error.details.timing),{maximum:4.8});
  assert.equal(parseBackend({...state,rubric_history:[{question_id:'q1',question:'Contrast?',answer:'Soft',response_type:'option'}]}).rubric_history[0].answer,'Soft');
  assert.throws(()=>parseBackend({...state,rubric_history:[{question_id:'q1'}]}),{code:'API_RESPONSE_INVALID'});
  assert.throws(()=>parseBackend({...state,status:'awaiting_answer'}),{code:'API_RESPONSE_INVALID'});
  assert.throws(()=>parseBackend({...state,events:[{id:'bad'}]}),{code:'API_RESPONSE_INVALID'});
  assert.throws(()=>parseBackend({...state,preview:{url:'https://untrusted.invalid/video',width:2,height:2,duration:1,bytes:1}}),{code:'API_RESPONSE_INVALID'});
  const consultation={reason:'no_candidates',message:'No additional questions proposed.',model_explanation:'Direction is specific.',proposed_count:0,rejected_count:0};
  assert.equal(parseBackend({...state,consultation}).consultation.reason,'no_candidates');
  assert.throws(()=>parseBackend({...state,consultation:{...consultation,reason:'invented'}}),{code:'API_RESPONSE_INVALID'});
  assert.throws(()=>parseBackend({...state,consultation:{...consultation,proposed_count:-1}}),{code:'API_RESPONSE_INVALID'});
  const proposal={request_id:crypto.randomUUID(),selection_policy:'muse_order',proposal:{candidates:[]},selected_id:null,error:null};
  assert.equal(parseBackend({...state,question_proposals:[proposal]}).question_proposals.length,1);
  assert.throws(()=>parseBackend({...state,question_proposals:[{...proposal,request_id:'bad'}]}),{code:'API_RESPONSE_INVALID'});
});
test('cancellation during JSON decoding is reported as cancellation',async t=>{
  const original=globalThis.fetch;t.after(()=>{globalThis.fetch=original;});
  globalThis.fetch=async(_url,options)=>({ok:true,status:200,json:()=>new Promise((_resolve,reject)=>{
    options.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true});
  })});
  const controller=new AbortController();
  const pending=request('/api/projects',{signal:controller.signal});
  setTimeout(()=>controller.abort(),1);
  await assert.rejects(pending,{code:'MEDIA_ABORTED'});
});
function readySource() {
  const id=crypto.randomUUID();
  return {id,revision:3,status:'ready',stage:'ready',filename:'test.mp4',request:'',
    answered_count:0,max_questions:3,model_calls:0,question:null,brief:null,analysis:null,events:[],error:null,
    source:{name:'test.mp4',bytes:100,width:720,height:1280,duration:40,sha256:'0'.repeat(64),
      video_codec:'h264',audio_codec:null,technical_notes:[]},
    preview:{url:'/api/projects/'+id+'/preview',width:720,height:1280,duration:40,bytes:80}};
}
test('ready localized candidates require a valid layer plan and mask references',()=>{
  const state=readySource();const id=crypto.randomUUID();const prefix='/api/projects/'+state.id+'/candidates/'+id;
  const values={contrast:1,saturation:1,gamma:1,brightness_gain:1,cooling:0};
  const local={concept:'subject',foreground:values,background:{...values,cooling:.1},intervals:[{start:0,end:1}],preserve_subject_color:true};
  const candidate={id,name:'V1',created_at:new Date().toISOString(),status:'ready',recipe:{proposal:{mode:'localized',parameters:values,local,
    summary:'Fixture',decisions:[],limitations:[]}},mask_run_ids:[crypto.randomUUID()],preview_url:prefix+'/preview',download_url:prefix+'/download',
    width:720,height:1280,duration:40,validation:{warnings:[],limitations:[]}};
  const ready={...state,status:'render_ready',stage:'render_ready',selected_candidate_id:id,candidates:[candidate]};
  assert.equal(parseBackend(ready).candidates[0].recipe.proposal.mode,'localized');
  assert.throws(()=>parseBackend({...ready,candidates:[{...candidate,mask_run_ids:[]}]}),{code:'API_RESPONSE_INVALID'});
  assert.throws(()=>parseBackend({...ready,candidates:[{...candidate,recipe:{proposal:{...candidate.recipe.proposal,local:null}}}]}),{code:'API_RESPONSE_INVALID'});
  assert.throws(()=>parseBackend({...ready,candidates:[{...candidate,creative_review:{decision:'sufficient',summary:3,issues:[]}}]}),{code:'API_RESPONSE_INVALID'});
});
test('a prepared source is accepted without creating a browser decoder or fetching the preview',t=>{
  const previousDocument=globalThis.document;
  const previousFetch=globalThis.fetch;
  t.after(()=>{globalThis.document=previousDocument;globalThis.fetch=previousFetch;});
  globalThis.document={createElement(){throw new Error('The browser decoder is unavailable');}};
  globalThis.fetch=()=>{throw new Error('Preview playback is blocked');};
  const state=readySource();
  const source=preparedSource(state);
  assert.equal(source.width,720);
  assert.equal(source.playbackUrl,state.preview.url);
  const project=makeProject();
  project.source=source;project.assets=[source];project.backend=state;
  validateProject(project);
  assert.equal(project.backend.status,'ready');
});
test('source resumption uses existing prepared media without waiting for model work',async()=>{
  const state={...readySource(),status:'analyzing',stage:'select_question'};
  assert.equal(await waitForSource(state,()=>{},new AbortController().signal),state);
});
test('unprepared or invalid remote source metadata is rejected',()=>{
  assert.throws(()=>preparedSource({...readySource(),preview:null}),{code:'API_RESPONSE_INVALID'});
  const state=readySource();
  assert.throws(()=>preparedSource({...state,preview:{...state.preview,url:'https://untrusted.invalid/video'}}),{code:'API_RESPONSE_INVALID'});
});

const {createComparisonSync,isPlaybackAbort}=await load('playbackSync');
function fakeVideo({time=0,paused=true,readyState=4,duration=10,playImpl}={}) {
  const el={currentTime:time,duration,playbackRate:1,paused,readyState,playCalls:0,pauseCalls:0,
    play(){el.playCalls++;return playImpl?playImpl(el):Promise.resolve().then(()=>{el.paused=false;});},
    pause(){el.pauseCalls++;el.paused=true;}};
  return el;
}
test('comparison play treats an AbortError from a superseded play as expected cancellation',async()=>{
  const main=fakeVideo({paused:false});const before=fakeVideo();
  let failures=0;
  before.play=()=>{before.playCalls++;return Promise.reject(new DOMException('play interrupted','AbortError'));};
  const sync=createComparisonSync(()=>main,()=>before,()=>{failures++;});
  assert.equal(await sync.play(),'aborted');
  assert.equal(failures,0);
  assert.equal(isPlaybackAbort(new DOMException('x','AbortError')),true);
  assert.equal(isPlaybackAbort(new Error('AbortError')),false);
});
test('comparison play is single-flight and a pause invalidates its pending request',async()=>{
  const main=fakeVideo({paused:false});let rejectPlay;
  const before=fakeVideo();
  before.play=()=>{before.playCalls++;return new Promise((_resolve,reject)=>{rejectPlay=reject;});};
  let failures=0;const sync=createComparisonSync(()=>main,()=>before,()=>{failures++;});
  const first=sync.play();const second=sync.play();
  assert.equal(first,second);assert.equal(before.playCalls,1);
  sync.pause();assert.equal(before.paused,true);
  rejectPlay(new DOMException('interrupted by pause','AbortError'));
  assert.equal(await first,'aborted');assert.equal(failures,0);
});
test('comparison play reports a genuine failure exactly once',async()=>{
  const main=fakeVideo({paused:false});const before=fakeVideo();
  before.play=()=>Promise.reject(new DOMException('autoplay blocked','NotAllowedError'));
  const seen=[];const sync=createComparisonSync(()=>main,()=>before,cause=>seen.push(cause));
  assert.equal(await sync.play(),'failed');
  assert.equal(seen.length,1);assert.equal(seen[0].name,'NotAllowedError');
});
test('comparison sync only seeks past the drift threshold and copies playback rate',()=>{
  const main=fakeVideo({time:5,paused:false});main.playbackRate=1.5;
  const before=fakeVideo({time:4.95});
  const sync=createComparisonSync(()=>main,()=>before,()=>{throw new Error('no failure expected');});
  assert.equal(sync.sync(),false);assert.equal(before.currentTime,4.95);assert.equal(before.playbackRate,1.5);
  main.currentTime=6;
  assert.equal(sync.sync(),true);assert.equal(before.currentTime,6);
  before.readyState=0;main.currentTime=8;
  assert.equal(sync.sync(true),false);assert.equal(before.currentTime,6);
});
test('look previews require a valid image reference and a selectable stored look',()=>{
  const state=readySource();const lookId=crypto.randomUUID();
  const params={contrast:1,saturation:1,gamma:1,brightness_gain:1,cooling:0};
  const look={id:lookId,name:'Brief look',description:'As planned.',created_at:new Date().toISOString(),schema_version:1,
    brief_sha256:'a'.repeat(64),frame_index:3,frame_time:.25,uses_local:false,
    image_url:'/api/projects/'+state.id+'/looks/'+lookId+'/image',
    proposal:{parameters:params,mode:'global',local:null,summary:'Fixture',decisions:[],limitations:[]}};
  const withLooks={...state,status:'brief_ready',stage:'looks_ready',brief:{initial_request:'x',directives:[],explicit_constraints:[],default_protections:[],technical_envelope:[],answers:[],unresolved:[]},
    looks:[look],selected_look_id:lookId,looks_brief_sha256:look.brief_sha256,looks_frame_index:3,looks_frame_time:.25,looks_limitation:null};
  assert.equal(parseBackend(withLooks).selected_look_id,lookId);
  assert.equal(parseBackend(withLooks).looks[0].name,'Brief look');
  assert.throws(()=>parseBackend({...withLooks,looks:[{...look,image_url:'https://untrusted.invalid/look.jpg'}]}),{code:'API_RESPONSE_INVALID'});
  assert.throws(()=>parseBackend({...withLooks,selected_look_id:crypto.randomUUID()}),{code:'API_RESPONSE_INVALID'});
  assert.throws(()=>parseBackend({...withLooks,looks:[{...look,proposal:{...look.proposal,parameters:{contrast:'x'}}}]}),{code:'API_RESPONSE_INVALID'});
});
