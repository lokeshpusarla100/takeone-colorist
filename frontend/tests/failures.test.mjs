import test from 'node:test';
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import {join} from 'node:path';
const load=name=>import(pathToFileURL(join(process.env.TAKEONE_TEST_BUILD,name+'.mjs')));
const {mediaFailure,asAppError}=await load('errors');
const {inspectVideo}=await load('media');
const {validateProject}=await load('storage');
const {makeProject}=await load('types');
const {parseBackend,request}=await load('api');

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
