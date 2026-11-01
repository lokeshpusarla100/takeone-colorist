import test from 'node:test';
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import {join} from 'node:path';
const load=name=>import(pathToFileURL(join(process.env.TAKEONE_TEST_BUILD,name+'.mjs')));
const {mediaFailure,asAppError}=await load('errors');
const {inspectVideo}=await load('media');
const {validateProject}=await load('storage');
const {makeProject}=await load('types');
const {parseBackend,request,waitForSource}=await load('api');
const {preparedSource}=await load('preparedSource');
const {tokenUsage}=await load('tokenUsage');
const {mutationLocked,runProgress}=await load('workflow');

test('mutating workspace actions lock during local, remote and command processing',()=>{
  for(const status of ['ingesting','analyzing','rendering','segmenting'])assert.equal(mutationLocked({status}),true);
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
  const changedBrief=runProgress({...state,status:'brief_ready',stage:'brief_ready',brief:{},candidates:[{status:'ready'}]});
  assert.equal(changedBrief.steps[4].phase,'pending');
  assert.match(changedBrief.steps[4].detail,/earlier version/);
  const tracking=runProgress({...state,status:'segmenting',stage:'segment_subject',brief:{},segmentations:[{status:'streaming',frame_count:12,frames_with_masks:4}]});
  assert.equal(tracking.working,true);
  assert.equal(tracking.steps[5].phase,'active');
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
    code:'MUSE_NOT_CONFIGURED',message:'Key missing',action:'Configure .env',details:{request_id:'test-request'},
  }}),{status:503,headers:{'Content-Type':'application/json'}});
  await assert.rejects(request('/api/health'),e=>e.code==='MUSE_NOT_CONFIGURED'
    &&e.action==='Configure .env'&&e.details.request_id==='test-request');
});
test('malformed or inconsistent backend state is rejected before it reaches storage',()=>{
  const state={id:crypto.randomUUID(),revision:1,status:'ingesting',stage:'inspect_source',filename:'test.mp4',
    request:'',answered_count:0,max_questions:3,model_calls:0,source:null,preview:null,question:null,brief:null,events:[],error:null};
  assert.equal(parseBackend(state).status,'ingesting');
  assert.throws(()=>parseBackend({...state,status:'awaiting_answer'}),{code:'API_RESPONSE_INVALID'});
  assert.throws(()=>parseBackend({...state,events:[{id:'bad'}]}),{code:'API_RESPONSE_INVALID'});
  assert.throws(()=>parseBackend({...state,preview:{url:'https://untrusted.invalid/video',width:2,height:2,duration:1,bytes:1}}),{code:'API_RESPONSE_INVALID'});
  const consultation={reason:'no_candidates',message:'No additional questions proposed.',model_explanation:'Direction is specific.',proposed_count:0,rejected_count:0};
  assert.equal(parseBackend({...state,consultation}).consultation.reason,'no_candidates');
  assert.throws(()=>parseBackend({...state,consultation:{...consultation,reason:'invented'}}),{code:'API_RESPONSE_INVALID'});
  assert.throws(()=>parseBackend({...state,consultation:{...consultation,proposed_count:-1}}),{code:'API_RESPONSE_INVALID'});
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
