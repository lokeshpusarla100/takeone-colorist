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
const {processingTimings,formatDuration,lastTime}=await load('progressTiming');
const {runPipeline,parseLive,formatEta,segmentOfStage}=await load('progressModel');
const {shouldAutoRender,consultationRunning,loadAutoRender,saveAutoRender,MAX_AUTO_ATTEMPTS}=await load('autoRender');
const {validateRenderImport}=await load('renderImport');
const {describeAdjustment,formatChange}=await load('adjustment');
const {projectCost,parsePricing,formatUsd,priceRow}=await load('cost');
const {safeguards,parseEnvelope,headline,codecName,sizeName,seconds,megabytes}=await load('briefVisual');
const {parseMarkdown,inline}=await load('markdown');
const {CONTROLS,cellFor,layersOf,activeControls}=await load('recipeView');
const {runSteps,stepProgress}=await load('stepper');
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
  assert.match(runProgress({...state,pending_questions:[{},{},{}]}).steps[2].detail,/answers to 3 questions/);
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

// ---- Pipeline progress, continuous run time, automatic start ----------------------------------------------------
const piece=(state,live)=>runPipeline({source:{},preview:{},analysis:{},brief:null,answered_count:0,max_questions:5,...state},live);
const phases=pipeline=>pipeline.segments.map(s=>s.id+':'+s.phase).join(' ');
test('the pipeline places the run by its backend stage and only fills what is measured',()=>{
  const asking=piece({status:'analyzing',stage:'select_question'});
  assert.equal(phases(asking),'inspect:done clarify:active brief:pending plan:pending masks:pending render:pending check:pending review:pending');
  assert.equal(asking.text,'Step 2 of 8: Q&A');assert.equal(asking.measured,false);assert.equal(asking.value,1);
  const waiting=piece({status:'awaiting_answer',stage:'awaiting_answer'});
  assert.equal(waiting.segments[1].phase,'waiting');assert.match(waiting.text,/waiting for you/);
  const planning=piece({status:'rendering',stage:'plan_grade',brief:{},candidates:[{status:'planning'}]});
  assert.equal(phases(planning),'inspect:done clarify:done brief:done plan:active masks:pending render:pending check:pending review:pending');
  const encoding=piece({status:'rendering',stage:'render_grade',brief:{},candidates:[{status:'rendering',recipe:{proposal:{mode:'global'}}}]},
    {stage:'render_grade',kind:'encode',fraction:.42,speed:1.5,eta_seconds:90,encoded_seconds:8.4,total_seconds:20});
  assert.equal(encoding.segments.find(s=>s.id==='masks').phase,'skipped');  // a whole-frame grade needs no masks
  assert.equal(encoding.total,7);assert.equal(encoding.text,'Step 5 of 7: Render, 42%');assert.equal(encoding.measured,true);
  assert.equal(encoding.value,4.42);assert.match(encoding.detail,/42% encoded · 1\.5× real time · about 2 min left/);
});
test('a measurement from another stage never fills the bar',()=>{
  const state={status:'rendering',stage:'render_grade',brief:{},candidates:[{status:'rendering',recipe:{proposal:{mode:'global'}}}]};
  assert.equal(piece(state,{stage:'render_local_grade',kind:'encode',fraction:.9,speed:1,eta_seconds:1,encoded_seconds:1,total_seconds:1}).measured,false);
  assert.equal(piece({...state,stage:'check_output_quality'},{stage:'render_grade',kind:'encode',fraction:.9,speed:1,eta_seconds:1,encoded_seconds:1,total_seconds:1}).measured,false);
  assert.equal(piece(state,null).measured,false);
});
test('tracked frames fill the masks segment and a revision pass restarts the later steps',()=>{
  const tracking=piece({status:'rendering',stage:'segment_subject',brief:{},candidates:[{status:'rendering',recipe:{proposal:{mode:'localized'}}}],
    segmentations:[{status:'streaming',frame_count:200,received_frames:50,frames_with_masks:0}]});
  assert.equal(tracking.segments.find(s=>s.id==='masks').fraction,.25);assert.match(tracking.detail,/50 frames of 200 tracked/);
  const revising=piece({status:'rendering',stage:'plan_revision',brief:{},candidates:[{status:'ready',creative_review:{},recipe:{proposal:{mode:'localized'}}},{status:'planning'}]});
  assert.equal(phases(revising),'inspect:done clarify:done brief:done plan:active masks:pending render:pending check:pending review:pending');
});
test('finished, failed and unreviewed runs are described without inventing progress',()=>{
  const done=piece({status:'render_ready',stage:'render_ready',brief:{},candidates:[{status:'ready',creative_review:{},recipe:{proposal:{mode:'global'}}}]});
  assert.equal(done.text,'All steps complete');assert.equal(done.segments.find(s=>s.id==='review').phase,'done');
  const unreviewed=piece({status:'render_ready',stage:'render_ready',brief:{},candidates:[{status:'ready',recipe:{proposal:{mode:'global'}}}]});
  assert.equal(unreviewed.segments.find(s=>s.id==='review').phase,'skipped');
  const failed=piece({status:'failed',stage:'validate_local_masks',brief:{},candidates:[{status:'failed',recipe:{proposal:{mode:'localized'}}}]});
  assert.equal(failed.segments.find(s=>s.id==='masks').phase,'failed');assert.match(failed.text,/stopped/);
  assert.equal(runPipeline(undefined),null);assert.equal(runPipeline({status:'ingesting',stage:'inspect_source',source:null,preview:null}),null);
});
test('live measurements are validated and clamped; unknown stages map to no segment',()=>{
  assert.equal(parseLive(null),null);assert.equal(parseLive({kind:'encode',stage:'x',fraction:'1'}),null);
  assert.equal(parseLive({kind:'encode',stage:'render_grade',fraction:1.7,encoded_seconds:1,total_seconds:2}).fraction,1);
  assert.equal(parseLive({kind:'encode',stage:'render_grade',fraction:.5,encoded_seconds:1,total_seconds:2,speed:null,eta_seconds:null}).speed,null);
  assert.equal(segmentOfStage('select_looks'),null);assert.equal(segmentOfStage('render_final'),'render');
  assert.equal(formatEta(20),'under a minute left');assert.equal(formatEta(200),'about 3 min left');assert.equal(formatEta(NaN),'');
});
test('active time runs across the consultation and the render but not across waits or an earlier direction',()=>{
  const events=[timingEvent('a',0,'inspect_source',true,{progress_job:'src'}),timingEvent('b',5,'ready',false),
    timingEvent('c',100,'select_question',true,{progress_job:'old'}),timingEvent('d',110,'brief_ready',false),   // an earlier direction
    timingEvent('e',200,'analyze_scene',true,{progress_job:'q'}),timingEvent('f',210,'awaiting_answer',false),
    timingEvent('g',400,'select_question',true,{progress_job:'q2'}),timingEvent('h',405,'plan_grade',true,{progress_job:'q2'}),
    timingEvent('i',425,'render_grade',true,{progress_job:'q2'}),timingEvent('j',445,'check_output_quality',true,{progress_job:'q2'}),timingEvent('k',450,'render_ready',false)];
  const state={status:'render_ready',request:'x',request_started_at:new Date(timingNow(150)).toISOString(),events};
  const result=processingTimings(state,timingNow(900));
  assert.equal(result.run,60000);                  // 10 + 5 + 20 + 20 + 5: the earlier direction and the answer wait are excluded
  assert.equal(result.total,50000);                // the legacy per-attempt total is unchanged
  assert.equal(processingTimings({...state,request_started_at:null,request:''},timingNow(900)).run,null);   // nothing submitted yet
  assert.equal(processingTimings({...state,request_started_at:null},timingNow(900)).run,result.lifetimeTotal);  // legacy session: all timed work
  assert.equal(processingTimings({status:'ready',events:[]}).run,null);
});
test('last time is the previous finished visit to a step, including an earlier revision pass',()=>{
  const events=[timingEvent('a',0,'plan_grade',true,{progress_job:'j'}),timingEvent('b',10,'preflight_grade',true,{progress_job:'j'}),
    timingEvent('c',15,'render_grade',true,{progress_job:'j'}),timingEvent('d',75,'check_output_quality',true,{progress_job:'j'}),
    timingEvent('e',85,'creative_review',true,{progress_job:'j'}),timingEvent('f',95,'plan_revision',true,{progress_job:'j'}),
    timingEvent('g',100,'render_grade',true,{progress_job:'j'})];
  const running={status:'rendering',request_started_at:new Date(timingNow(-5)).toISOString(),events};
  const t=processingTimings(running,timingNow(110));
  assert.deepEqual(t.visits.map(v=>[v.segment,v.ms]),[['plan',15000],['render',60000],['check',10000],['review',10000],['plan',5000],['render',10000]]);
  assert.equal(lastTime(t.visits,'render',t.running),60000);   // pass 1; the visit running now is not "last time"
  assert.equal(lastTime(t.visits,'plan',t.running),5000);
  assert.equal(lastTime(t.visits,'masks',t.running),null);
  const answered=processingTimings({status:'analyzing',events:[timingEvent('a',0,'select_question',true,{progress_job:'1'}),timingEvent('b',4,'awaiting_answer',false),
    timingEvent('c',50,'select_question',true,{progress_job:'2'}),timingEvent('d',59,'save_brief',true,{progress_job:'2'})]},timingNow(60));
  assert.deepEqual(answered.visits.map(v=>[v.segment,v.ms]),[['clarify',4000],['clarify',9000],['brief',1000]]);  // a wait splits visits
});
const auto=(changes={},state={})=>({state:{id:'p',status:'brief_ready',request:'make it moody',brief:{},brief_sha256:'abc',candidates:[],...state},armed:true,enabled:true,online:true,
  health:{muse_configured:true,grading_connected:true},direction:' make it moody ',attempts:0,busy:false,...changes});
test('grading starts by itself only for a brief this tab watched being made, once, when Generate would be usable',()=>{
  assert.equal(shouldAutoRender(auto()),true);
  assert.equal(shouldAutoRender(auto({armed:false})),false);                       // a session merely opened at "brief ready"
  assert.equal(shouldAutoRender(auto({enabled:false})),false);
  assert.equal(shouldAutoRender(auto({online:false})),false);
  assert.equal(shouldAutoRender(auto({busy:true})),false);
  assert.equal(shouldAutoRender(auto({health:null})),false);
  assert.equal(shouldAutoRender(auto({health:{muse_configured:false,grading_connected:true}})),false);
  assert.equal(shouldAutoRender(auto({health:{muse_configured:true,grading_connected:false}})),false);
  assert.equal(shouldAutoRender(auto({direction:'something else'})),false);          // direction edited after the brief
  assert.equal(shouldAutoRender(auto({attempts:MAX_AUTO_ATTEMPTS})),false);          // no retry loop
  assert.equal(shouldAutoRender(auto({attempts:MAX_AUTO_ATTEMPTS-1})),true);
  for(const status of ['analyzing','awaiting_answer','rendering','render_ready','failed','cancelled','reviewing'])assert.equal(shouldAutoRender(auto({},{status})),false,status);
  assert.equal(shouldAutoRender(auto({},{brief:null})),false);
  assert.equal(shouldAutoRender(auto({},{brief_sha256:null})),false);
  assert.equal(shouldAutoRender(auto({},{candidates:[{brief_sha256:'abc',status:'failed'}]})),false);   // this brief already has a version
  assert.equal(shouldAutoRender(auto({},{candidates:[{brief_sha256:'older',status:'ready'}]})),true);
  assert.equal(shouldAutoRender({...auto(),state:undefined}),false);
});
test('only a running or answer-waiting consultation arms the automatic start',()=>{
  for(const status of ['analyzing','awaiting_answer'])assert.equal(consultationRunning({status}),true);
  for(const status of ['ready','brief_ready','rendering','render_ready','failed','reviewing','segmenting'])assert.equal(consultationRunning({status}),false);
  assert.equal(consultationRunning(undefined),false);
});
test('the automatic-start choice is remembered, defaults on, and survives unavailable storage',t=>{
  const original=globalThis.localStorage;t.after(()=>{Object.defineProperty(globalThis,'localStorage',{value:original,configurable:true});});
  const memory=new Map();
  Object.defineProperty(globalThis,'localStorage',{value:{getItem:k=>memory.get(k)??null,setItem:(k,v)=>memory.set(k,v)},configurable:true});
  assert.equal(loadAutoRender(),true);saveAutoRender(false);assert.equal(loadAutoRender(),false);saveAutoRender(true);assert.equal(loadAutoRender(),true);
  Object.defineProperty(globalThis,'localStorage',{get(){throw new Error('blocked');},configurable:true});
  assert.equal(loadAutoRender(),true);assert.doesNotThrow(()=>saveAutoRender(false));
});
test('step copy matches how the consultation and render actually run',()=>{
  const base={source:{},preview:{},analysis:{},brief:{},answered_count:2,max_questions:5};
  assert.match(runProgress({...base,status:'rendering',stage:'render_grade',candidates:[{status:'rendering'}]}).note,/Encoding/);
  assert.doesNotMatch(runProgress({...base,status:'rendering',stage:'render_grade',candidates:[{status:'rendering'}]}).note,/separate action/);
  assert.doesNotMatch(runProgress({...base,status:'render_ready',stage:'render_ready',candidates:[{status:'ready'}]}).note,/separate action/);
  assert.doesNotMatch(runProgress({...base,status:'brief_ready',stage:'brief_ready'}).steps[4].name,/optional/);
  const global=runProgress({...base,status:'render_ready',stage:'render_ready',candidates:[{status:'ready',recipe:{proposal:{mode:'global'}}}]});
  assert.equal(global.steps[5].phase,'skipped');
});
test('a cancelled or cancelling run keeps its place from the last timing marker',()=>{
  const events=[timingEvent('a',0,'plan_grade',true,{progress_job:'j'}),timingEvent('b',5,'render_grade',true,{progress_job:'j'}),
    timingEvent('c',8,'cancelling',true,{progress_job:'j'}),timingEvent('d',9,'cancelling',false)];
  const recipe={proposal:{mode:'global'}};
  const stopped=piece({status:'cancelled',stage:'cancelling',brief:{},candidates:[{status:'cancelled',recipe}],events});
  assert.equal(stopped.segments.find(s=>s.id==='render').phase,'failed');
  assert.equal(stopped.segments.find(s=>s.id==='plan').phase,'done');assert.match(stopped.text,/Render \(stopped\)/);
  const stopping=piece({status:'rendering',stage:'cancelling',brief:{},candidates:[{status:'rendering',recipe}],events:events.slice(0,3)});
  assert.equal(stopping.segments.find(s=>s.id==='render').phase,'active');   // still the render until it has actually stopped
  assert.equal(piece({status:'cancelled',stage:'cancelling',brief:{},candidates:[],events:[]}).segments.every(s=>s.phase!=='failed'),true);  // unknown place: nothing is blamed
});

test('every shape of execution adjustment the backend records can be described without throwing',()=>{
  // The record written when the user chooses "grade all matches": no reason, no changes. This crashed the whole editor.
  const all=describeAdjustment({kind:'user_selected_all_matches',concept:'passenger boat',object_count:4});
  assert.equal(all.title,'Every match graded');assert.match(all.detail,/all 4 matches of “passenger boat”/);assert.deepEqual(all.changes,[]);
  // Input-level clipping recovery: reason plus per-layer changes.
  const clip=describeAdjustment({kind:'input_level_clipping_recovery',reason:'Cutoffs were reduced.',changes:{'background.black_point':{from:.05,to:.03}}});
  assert.equal(clip.detail,'Cutoffs were reduced.');assert.equal(formatChange(clip.changes[0]),'background black point: 0.0500 → 0.0300');
  // Both written onto one record (the "all matches" record is merged over an earlier correction).
  const both=describeAdjustment({kind:'user_selected_all_matches',concept:'sky',object_count:2,reason:'Also protected.',changes:{black_point:{from:.1,to:.08}}});
  assert.match(both.detail,/all 2 matches of “sky”.*Also protected\./);assert.equal(both.changes.length,1);
  // Unknown kinds, partial or malformed records, and non-records never throw.
  assert.equal(describeAdjustment({kind:'some_future_kind'}).title,'Some future kind');
  assert.equal(describeAdjustment({kind:'x',changes:{a:{from:'1',to:null},b:7,c:{from:1,to:2}}}).changes.length,1);
  assert.equal(describeAdjustment({kind:'user_selected_all_matches'}).detail.startsWith('You chose to grade every match of the ambiguous area'),true);
  for(const junk of [null,undefined,0,'text',[],[1],{}])assert.equal(describeAdjustment(junk),null);
  assert.doesNotThrow(()=>describeAdjustment({changes:'nope',object_count:'4',concept:7,reason:3}));
});

const RATES={currency:'USD',basis:'estimate_from_reported_usage',source:'x',checked:'2026-11-03',
  muse:{model:'muse-spark-1.3',input_per_million:1.25,cached_input_per_million:.15,output_per_million:4.25},
  sam:{model:'sam-3.1',per_thousand_frames:.2,min_billed_frames:50}};
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-12,`${a} !== ${b}`);
test('cost is priced from reported usage at the published rates',()=>{
  const muse=(id,title,input,output,cached=0)=>usageEvent(id,title,{token_usage:true,request_id:id,usage_stage:'x',input_tokens:input,output_tokens:output,total_tokens:input+output,cached_tokens:cached,reasoning_tokens:0});
  const sam=(id,frames)=>usageEvent(id,'SAM request usage',{token_usage:true,request_id:id,model:'sam-3.1',video_frames_processed:frames,input_tokens:0,output_tokens:0,total_tokens:0,cached_tokens:0});
  const events=[muse('a','Scene analysis usage',6584,1165),muse('b','Critic request usage',10000,100,4000),sam('c',150),sam('d',20)];
  const cost=projectCost(events,RATES);
  near(cost.lines[0].usd,(6584*1.25+1165*4.25)/1e6);                       // plain input + output
  near(cost.lines[1].usd,(6000*1.25+4000*.15+100*4.25)/1e6);                // cached input has its own rate
  near(cost.lines[2].usd,150*.2/1000);                                      // SAM per frame
  near(cost.lines[3].usd,50*.2/1000);                                       // a short SAM request is billed for the 50-frame minimum
  near(cost.total,cost.lines.reduce((a,l)=>a+l.usd,0));near(cost.sam,.03+.01);near(cost.muse,cost.total-cost.sam);
  assert.deepEqual(cost.stages.map(s=>s.stage),['analysis','sam','critic']);   // pipeline order, only stages that cost something
  near(cost.stages.reduce((a,s)=>a+s.share,0),1);
  assert.equal(cost.unpriced,0);assert.equal(cost.priced,true);
});
test('usage that was not reported is never turned into a made-up price',()=>{
  const started=usageEvent('s','Tracking subject with SAM',{request_id:'r'});             // SAM request began, no usage arrived
  const partial=usageEvent('m','Muse request usage',{request_id:'m1',input_tokens:100,total_tokens:100});  // output missing
  const cost=projectCost([started,partial],RATES);
  assert.equal(cost.total,0);assert.equal(cost.unpriced,2);assert.ok(cost.lines.every(l=>l.usd===null));
  assert.equal(projectCost([started],RATES,'segment_subject').pending,true);                // a running request is pending, not unpriced
  assert.equal(projectCost([started],RATES,'segment_subject').unpriced,0);
  const none=projectCost([usageEvent('a','Scene analysis usage',{token_usage:true,request_id:'a',input_tokens:1,output_tokens:1,total_tokens:2})],null);
  assert.equal(none.priced,false);assert.equal(none.total,0);assert.equal(none.unpriced,1);   // no rates published: unknown, not free
  assert.equal(priceRow({stage:'sam',processedFrames:0},RATES),0);
  near(priceRow({stage:'analysis',input:10,output:0,cached:99},RATES),10*.15/1e6);            // cached can never exceed input
});
test('only a complete, sane price list is accepted, and money never rounds to free',()=>{
  assert.equal(parsePricing(RATES),RATES);
  for(const bad of [null,{},{...RATES,currency:'EUR'},{...RATES,muse:{...RATES.muse,output_per_million:-1}},{...RATES,sam:{...RATES.sam,per_thousand_frames:'0.2'}},{...RATES,muse:null}])assert.equal(parsePricing(bad),null);
  assert.equal(formatUsd(null),'—');assert.equal(formatUsd(NaN),'—');assert.equal(formatUsd(0),'$0.00');
  assert.equal(formatUsd(.0043),'$0.0043');assert.equal(formatUsd(.8),'$0.80');assert.equal(formatUsd(1234.5),'$1,234.50');
});

const PROTECTIONS=['Keep original media unchanged; grades must be reversible.','Do not alter identity, invent scene content or claim reconstruction of missing detail.',
  'Do not recolor meaningful products or markings without authorization.','Do not sacrifice subject legibility or introduce temporal artifacts without approval.'];
const ENVELOPE=['Source: 3840x2160, 24.695s; h264; yuv420p.','Analysis used the complete silent proxy; source/proxy timing is recorded.','Silent clip only; no audio information available.',
  'Assessment is visual only without camera or exposure metadata.','No numerical exposure or color values measured; descriptions are qualitative.','Fine detail limited by distance, compression, and water reflections.',
  'SDR Rec.709 luminance tone, black/white points, bounded brightness and OCIO CDL saturation/RGB balance are supported. These are not calibrated scene-exposure or Kelvin estimates.',
  'Rotation metadata in quarter turns (portrait phone video) is applied before analysis, masks and rendering. Localized grading requires a verified frame-index/native-PTS mapping and complete unambiguous SAM masks. Irregular source timing uses a separate tracking proxy; source timing is preserved. No physical relighting, reconstruction or shot matching.'];
test('the four default protections become icon cards that keep their full sentence',()=>{
  const cards=safeguards(PROTECTIONS);
  assert.deepEqual(cards.map(c=>c.icon),['undo','user','tag','eye']);
  assert.deepEqual(cards.map(c=>c.text),PROTECTIONS);                       // nothing reworded or dropped
  assert.equal(safeguards(['Never touch the watermark.'])[0].icon,'shield');  // unknown rules still get a card
  assert.equal(safeguards(['Never touch the watermark.'])[0].title,'Never touch the watermark');
  assert.deepEqual(safeguards(['','  ']),[]);
});
test('the technical envelope is sorted into facts, supported controls, limits and what will not be done',()=>{
  const e=parseEnvelope(ENVELOPE);
  assert.deepEqual(e.facts.map(f=>[f.label,f.value]),[['Resolution','3840 × 2160'],['Duration','24.7 s'],['Codec','H.264'],['Pixel format','yuv420p']]);
  assert.equal(e.facts[0].note,'4K UHD');
  assert.deepEqual(e.supported,['SDR Rec.709 luminance tone','Black/white points','Bounded brightness','OCIO CDL saturation/RGB balance']);
  assert.equal(e.supportedNote,'These are not calibrated scene-exposure or Kelvin estimates.');
  assert.deepEqual(e.wont,['physical relighting','reconstruction','shot matching']);
  assert.equal(e.handling.length,3);assert.deepEqual(e.handling.map(h=>h.icon),['rotate','clock','clock']);
  assert.equal(e.limits.length,5);
  assert.deepEqual(e.limits.map(l=>l.icon),['mute','mute','eye','ruler','layers']);
  assert.deepEqual([e.limits[1].lead,e.limits[1].rest],['Silent clip only','No audio information available.']);   // split at the semicolon, never mid-word
  assert.deepEqual([e.limits[0].lead,e.limits[0].rest],['Analysis used the complete silent proxy','Source/proxy timing is recorded.']);
  assert.deepEqual([e.limits[2].lead,e.limits[2].rest],['','Assessment is visual only without camera or exposure metadata.']);   // no semicolon: the whole sentence, untruncated
  // Every original sentence is still present somewhere (nothing is lost by sorting).
  const shown=[...e.limits.map(l=>l.lead+' '+l.rest),...e.handling.map(h=>h.text)].join(' ');
  for(const needle of ['complete silent proxy','visual only','qualitative','water reflections','quarter turns','native-PTS','tracking proxy','exposure metadata'])assert.match(shown,new RegExp(needle));
  assert.equal(e.notes.length,0);
});
test('an envelope in another shape degrades to plain rows instead of breaking',()=>{
  const e=parseEnvelope(['Source: weird','Some single note.','HDR is unsupported.']);
  assert.equal(e.facts.length,0);assert.equal(e.limits.length,3);assert.equal(e.limits[0].rest,'Source: weird.');
  assert.deepEqual(parseEnvelope([]),{facts:[],supported:[],supportedNote:'',limits:[],handling:[],wont:[],notes:[]});
  assert.deepEqual(parseEnvelope(['Source: 1080x1920, 5s; hevc; yuv420p10le.']).facts.map(f=>f.value),['1080 × 1920','5.0 s','HEVC (H.265)','yuv420p10le']);
  assert.equal(parseEnvelope(['Source: 1080x1920, 5s; hevc; yuv420p10le.']).facts[0].note,'Full HD portrait');
  assert.equal(headline('A very long first clause that keeps going well past the limit; second'),'A very long first clause that keeps going wel…');
  assert.equal(codecName('prores'),'ProRes');assert.equal(codecName('weird'),'WEIRD');assert.equal(sizeName(7,7),'');
  assert.equal(seconds(24.695),'24.7 s');assert.equal(seconds(125),'2 min 5 s');assert.equal(megabytes(477646151),'455.5 MB');
});
test('markdown becomes data: headings, bold, bullet lists and paragraphs, never markup',()=>{
  const blocks=parseMarkdown('## Look\n\nDeep **steel-blue** shadows,\nsoft light.\n\n- one\n- **two**\n\n### Target\n**Final**');
  assert.deepEqual(blocks.map(b=>b.kind),['heading','paragraph','list','heading','paragraph']);
  assert.equal(blocks[0].level,2);assert.equal(blocks[3].level,3);
  assert.deepEqual(blocks[1].inline,[{text:'Deep ',bold:false},{text:'steel-blue',bold:true},{text:' shadows, soft light.',bold:false}]);
  assert.equal(blocks[2].items.length,2);assert.equal(blocks[2].items[1][0].bold,true);
  assert.deepEqual(inline('<img src=x onerror=alert(1)>'),[{text:'<img src=x onerror=alert(1)>',bold:false}]);   // stays text
  assert.deepEqual(parseMarkdown(''),[]);assert.deepEqual(parseMarkdown('  \n \n'),[]);
  assert.equal(parseMarkdown('# a\r\ntext\r\n')[1].kind,'paragraph');
});
test('grade controls are measured against the backend bounds and neutral is marked, not painted',()=>{
  const contrast=CONTROLS.find(c=>c.key==='contrast'),black=CONTROLS.find(c=>c.key==='black_point'),white=CONTROLS.find(c=>c.key==='white_point');
  const c=cellFor(contrast,1.1);assert.ok(Math.abs(c.neutralAt-.5)<1e-9);assert.ok(Math.abs(c.at-.7)<1e-9);assert.equal(c.description,'+10% from neutral');assert.equal(c.text,'1.10');
  assert.equal(cellFor(contrast,1).changed,false);assert.equal(cellFor(contrast,1).description,'unchanged');
  assert.equal(cellFor(CONTROLS.find(k=>k.key==='saturation'),.7).description,'−30% from neutral');
  assert.equal(cellFor(black,0).neutralAt,0);assert.equal(cellFor(white,1).neutralAt,1);        // neutral at an edge: the fill grows one way only
  assert.equal(cellFor(black,.01).description,'+0.010 from neutral');
  assert.equal(cellFor(contrast,9).at,1);assert.equal(cellFor(contrast,-9).at,0);                // out-of-bounds values never leave the track
  assert.equal(cellFor(contrast,undefined),null);assert.equal(cellFor(contrast,NaN),null);
});
test('a recipe is split into the areas it grades separately, and rows nobody changes are left out',()=>{
  const v=(extra={})=>({contrast:1,saturation:1,gamma:1,...extra});
  const local={proposal:{mode:'localized',parameters:v(),local:{concept:'passenger boat',foreground:v({contrast:1.08}),background:v({saturation:.85}),
    regions:[{concept:'sky',treatment:v({saturation:.7})},{concept:'water',treatment:v()}]}}};
  const layers=layersOf(local);
  assert.deepEqual(layers.map(l=>l.name),['Passenger boat','Sky','Water','Everything else']);
  assert.deepEqual(layers.map(l=>l.role),['subject','region','region','background']);
  assert.deepEqual(activeControls(layers).map(c=>c.key),['contrast','saturation']);
  assert.deepEqual(layersOf({proposal:{parameters:v({gamma:.9})}}).map(l=>l.name),['Whole frame']);
  for(const none of [null,undefined,{},{proposal:{}},{proposal:{local:{}}}])assert.deepEqual(layersOf(none),[]);
});
test('one stepper carries every step with the same phases and times the progress model derives',()=>{
  const timing={byStep:{prepare:18000,inspect:13000},parts:{plan:2000,render:7000},visits:[{segment:'render',ms:3000}],running:true};
  const state={status:'rendering',stage:'render_local_grade',source:{},preview:{},analysis:{},brief:{},events:[],candidates:[],id:'x'};
  const pipeline={segments:[{id:'inspect',phase:'done'},{id:'clarify',phase:'done'},{id:'brief',phase:'done'},{id:'plan',phase:'done'},
    {id:'masks',phase:'skipped'},{id:'render',phase:'active',fraction:.4},{id:'check',phase:'pending'},{id:'review',phase:'skipped'}],detail:'40% encoded'};
  const rows=runSteps(state,pipeline,timing);
  assert.deepEqual(rows.map(r=>r.id),['prepare','inspect','clarify','brief','plan','masks','render','check','review']);
  assert.equal(rows[0].phase,'done');assert.equal(rows[0].ms,18000);
  const render=rows.find(r=>r.id==='render');
  assert.equal(render.phase,'active');assert.equal(render.ms,3000);assert.equal(render.fraction,.4);assert.equal(render.detail,'40% encoded');   // the running step shows its live visit clock
  assert.equal(rows.find(r=>r.id==='plan').ms,2000);assert.equal(rows.find(r=>r.id==='masks').phase,'skipped');
  assert.equal(rows.find(r=>r.id==='check').ms,null);
  // Before anything has run, the whole path is still listed, so the person sees what is ahead.
  const fresh=runSteps({status:'ready',stage:'ready',source:{},preview:{},id:'x',events:[]},null,{byStep:{},parts:{},visits:[],running:false});
  assert.equal(fresh.length,9);assert.equal(fresh[0].phase,'done');assert.ok(fresh.slice(1).every(r=>r.phase==='pending'));
  assert.equal(runSteps({status:'ingesting',stage:'prepare_preview',id:'x',events:[]},null,{byStep:{},parts:{},visits:[],running:true})[0].phase,'active');
});

test('the caption and overall bar count the same rows the list shows',()=>{
  const row=(id,phase,extra={})=>({id,name:id,phase,ms:null,...extra});
  const rows=[row('prepare','done'),row('inspect','done'),row('clarify','done'),row('brief','done'),row('plan','done'),row('masks','active',{fraction:.5,name:'Subject masks (SAM)'}),
    row('render','pending'),row('check','pending'),row('review','skipped')];
  const p=stepProgress(rows);
  assert.equal(p.text,'Step 6 of 8: Subject masks (SAM)');assert.equal(p.total,8);assert.equal(p.value,5.5);   // prepare counts, the skipped review does not
  assert.equal(stepProgress(rows.map(r=>r.phase==='skipped'?r:{...r,phase:'done',fraction:undefined})).text,'All steps complete');
  assert.equal(stepProgress([row('prepare','done'),row('inspect','pending')]).text,'1 of 2 steps complete');
  assert.equal(stepProgress([row('prepare','done'),row('clarify','waiting',{name:'Questions'})]).text,'Step 2 of 2: Questions (waiting for you)');
  assert.equal(stepProgress([row('prepare','done'),row('render','failed',{name:'Render'})]).text,'Step 2 of 2: Render (stopped)');
  assert.equal(stepProgress([row('prepare','active',{name:'Prepare source'})]).text,'Step 1 of 1: Prepare source');
});
