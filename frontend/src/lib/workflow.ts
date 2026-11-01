import { activeJob, stageLabel } from './api';
import type { BackendState } from './api';

export function mutationLocked(state?:BackendState,localBusy=false,requestPending=false,pendingSource?:BackendState){
  return localBusy||requestPending||!!(state&&activeJob(state))||!!(pendingSource&&activeJob(pendingSource));
}

type Phase='pending'|'active'|'complete'|'waiting'|'failed';
export type ProgressStep={name:string;phase:Phase;detail:string};
export function runProgress(state?:BackendState,localBusy=false,localProgress?:string|null,sending=false){
  const working=localBusy||sending||!!(state&&activeJob(state));
  const failed=state?.status==='failed'||state?.status==='cancelled';
  const source=!!state?.source&&!!state.preview;
  const analysis=!!state?.analysis;
  const brief=!!state?.brief;
  const latest=state?.candidates?.at(-1);
  const masks=state?.segmentations?.at(-1);
  const waiting=state?.status==='awaiting_answer';
  const step=(name:string,complete:boolean,active:boolean,detail:string):ProgressStep=>({
    name,phase:complete?'complete':active?(failed?'failed':'active'):'pending',detail,
  });
  const steps:ProgressStep[]=[
    step('Prepare source',source,localBusy||!!state&&!source,'Upload and prepare a browser preview.'),
    step('Inspect video',analysis,source&&!analysis&&!!state&&state.status!=='ready','Inspect the clip once; reuse cached observations.'),
    {...step('Clarify direction',brief,analysis&&!brief,'Process each answer before selecting another useful question.'),
      ...(waiting?{phase:'waiting' as Phase,detail:`Waiting for answer ${state.answered_count+1}. One question at a time.`}:{})},
    step('Save creative brief',brief,false,'Save current intent, boundaries and limitations.'),
    {...step('Generate grade · optional',['render_ready','reviewing'].includes(state?.status??'')&&latest?.status==='ready',state?.status==='rendering',
      latest?.status==='ready'?(['render_ready','reviewing'].includes(state?.status??'')?'A rendered version is available. Review the moving result.'
        :'An earlier version is available. Generate again to apply the current brief.'):'Starts only when you request a graded video.'),
      ...(latest&&['failed','cancelled'].includes(latest.status)&&!working?{phase:'failed' as Phase}:{})},
    {...step('Subject masks · when needed',masks?.status==='ready'||masks?.status==='empty',state?.status==='segmenting'||state?.status==='rendering'&&latest?.recipe?.proposal.mode==='localized'&&['verify_segmentation_input','segment_subject','decode_segmentation_masks'].includes(state.stage),
      masks?`${masks.frames_with_masks}/${masks.frame_count} frames with masks. Review the diagnostic overlay.`:'Starts automatically for localized plans; manual tracking is also available.'),
      ...(masks&&['failed','cancelled'].includes(masks.status)&&!working?{phase:'failed' as Phase}:{})},
    step('Creative review',!!latest?.creative_review,state?.status==='reviewing','Muse inspects actual rendered/source evidence. Its opinion does not approve the candidate for you.'),
  ];
  let heading=localBusy?(localProgress||'Preparing source'):sending?'Sending request…':!state?'Import a video to begin':stageLabel(state.stage);
  if(failed&&!localBusy)heading=state.status==='cancelled'?'Processing cancelled':'Processing failed';
  let note=waiting?'Answer the displayed question, skip it, or delegate. Muse then reevaluates what remains.':
    state?.stage==='select_question'?'Reviewing the request and saved answers. A follow-up is asked only if it changes a supported edit.':
    brief?'Review the brief. Generating a grade is a separate action.':'Progress follows the backend stages; no completion percentage is estimated.';
  if(failed)note=state.error?.action??'Saved source and answers remain available for an explicit retry.';
  if(state?.status==='segmenting')note='Manual subject tracking on the verified proxy. Missing masks remain explicit gaps.';
  if(state?.status==='rendering'&&latest?.recipe?.proposal.mode==='localized')note='Automatic localized pipeline: required masks must pass alignment, completeness and stability checks before compositing.';
  if(state?.status==='reviewing')note='Muse compares the rendered candidate with the original and the current brief. Final creative approval remains yours.';
  return {working,heading,note,steps};
}
