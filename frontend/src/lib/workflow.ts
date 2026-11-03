import { activeJob, stageLabel } from './api';
import type { BackendState } from './api';
import { canGrade, parseDomain } from './sourceColor';

export function mutationLocked(state?:BackendState,localBusy=false,requestPending=false,pendingSource?:BackendState){
  return localBusy||requestPending||!!(state&&activeJob(state))||!!(pendingSource&&activeJob(pendingSource));
}

type Phase='pending'|'active'|'complete'|'waiting'|'failed'|'skipped';
export type ProgressStep={name:string;phase:Phase;detail:string};
export function runProgress(state?:BackendState,localBusy=false,localProgress?:string|null,sending=false){
  const working=localBusy||sending||!!(state&&activeJob(state));
  const failed=state?.status==='failed'||state?.status==='cancelled';
  const source=!!state?.source&&!!state.preview;
  const analysis=!!state?.analysis;
  const brief=!!state?.brief;
  const latest=state?.candidates?.at(-1);
  const masks=state?.segmentations?.at(-1);
  const masking=state?.status==='segmenting'||state?.status==='rendering'&&latest?.recipe?.proposal.mode==='localized'
    &&['verify_segmentation_input','segment_subject','decode_segmentation_masks','validate_local_masks'].includes(state.stage);
  const waiting=state?.status==='awaiting_answer';
  const roundSize=Math.max(1,state?.pending_questions?.length??1);
  const step=(name:string,complete:boolean,active:boolean,detail:string):ProgressStep=>({
    name,phase:complete?'complete':active?(failed?'failed':'active'):'pending',detail,
  });
  const steps:ProgressStep[]=[
    step('Prepare source',source,localBusy||!!state&&!source,'Upload and prepare a browser preview.'),
    step('Inspect video',analysis,source&&!analysis&&!!state&&state.status!=='ready','Inspect the clip once; reuse cached observations.'),
    {...step('Clarify direction',brief||state?.stage==='save_brief',analysis&&!brief&&state?.stage!=='save_brief','Muse asks only what would change the edit; independent questions come together, then it reconsiders once.'),
      ...(waiting?{phase:'waiting' as Phase,detail:roundSize>1?`Waiting for your answers to ${roundSize} questions. Answer them together.`:`Waiting for answer ${state.answered_count+1}.`}:{})},
    step('Save creative brief',brief,state?.stage==='save_brief','Save current intent, boundaries and limitations.'),
    {...step('Generate grade',['render_ready','reviewing'].includes(state?.status??'')&&latest?.status==='ready',state?.status==='rendering',
      latest?.status==='ready'?(['render_ready','reviewing'].includes(state?.status??'')?'A rendered version is available. Review the moving result.'
        :'An earlier version is available. Generate again to apply the current brief.'):'Plan, render and check the graded video once the brief is ready.'),
      ...(latest&&['failed','cancelled'].includes(latest.status)&&!working?{phase:'failed' as Phase}:{})},
    {...step('Subject masks · when needed',!masking&&(masks?.status==='ready'||masks?.status==='empty'),masking,
      masks?`${masks.frames_with_masks}/${masks.frame_count} frames with masks. Review the diagnostic overlay.`:'Starts automatically for localized plans; manual tracking is also available.'),
      ...((masks&&['failed','cancelled'].includes(masks.status)||state?.error?.code==='LOCAL_MASK_GAP')&&!working?{phase:'failed' as Phase}:{}),
      ...(!masks&&!masking&&latest?.recipe&&latest.recipe.proposal.mode!=='localized'?{phase:'skipped' as Phase,detail:'Not needed: this grade applies to the whole frame.'}:{})},
    step('Creative review',!!latest?.creative_review,state?.status==='reviewing','Muse inspects actual rendered/source evidence. Its opinion does not approve the candidate for you.'),
  ];
  let heading=localBusy?(localProgress||'Preparing source'):sending?'Sending request…':!state?'Import a video to begin':stageLabel(state.stage);
  if(failed&&!localBusy)heading=state.status==='cancelled'?'Processing cancelled':'Processing failed';
  const stage=state?.stage??'';
  let note=waiting?(roundSize>1?'Answer every question, choose "You decide" or skip. Muse then reconsiders once with all your answers.':'Answer the displayed question, skip it, or delegate. Muse then reevaluates what remains.'):
    stage==='select_question'?'Reviewing the request and saved answers. A follow-up is asked only if it changes a supported edit.':
    stage==='analyze_scene'?'Muse watches the whole clip once; the observations are reused for every later step.':
    stage==='save_brief'?'Saving the request, your answers, boundaries and limitations as the creative brief.':
    stage==='plan_grade'||stage==='plan_revision'||stage==='preflight_grade'?'Muse plans the grade from measured frame evidence and the brief, then the plan is checked against clipping limits before any frame is rendered.':
    ['render_grade','render_local_grade','render_final'].includes(stage)?'Encoding the graded video. The percentage is measured from the encoder, not estimated.':
    stage==='check_output_quality'?'Checking frame timing, protected areas and clipping on the finished file.':
    state?.status==='render_ready'?'The graded video is ready. Compare it with the original, review it, or generate another version.':
    state?.status==='brief_ready'?'The brief is saved. Generate a graded video to continue.':
    !state?'Your video stays on this computer until you press Start analysis.':
    state?.status==='ready'?'Your video is ready. Describe the look you want, then start the analysis.':
    'Percentages shown are measured (encoding, tracking). Other stages show their elapsed time.';
  if(failed)note=state.error?.action??'Saved source and answers remain available for an explicit retry.';
  // A source that cannot be graded must not be described as ready to start: the notice above the player says why.
  if(state?.source&&!working&&!canGrade(parseDomain(state.source.domain))){heading='This video can’t be graded yet';note='See the notice above the player for the reason and what to do.';}
  if(state?.status==='segmenting')note='Manual subject tracking on the verified proxy. Missing masks remain explicit gaps.';
  if(state?.status==='rendering'&&latest?.recipe?.proposal.mode==='localized'&&masking)note='Automatic localized pipeline: required masks must pass alignment, completeness and stability checks before compositing.';
  if(state?.status==='reviewing')note='Muse compares the rendered candidate with the original and the current brief. Final creative approval remains yours.';
  return {working,heading,note,steps};
}
