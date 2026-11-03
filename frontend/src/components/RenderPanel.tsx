import type { BackendState, Health } from '../lib/api';
import { activeJob, stageLabel } from '../lib/api';
import { describeAdjustment, formatChange } from '../lib/adjustment';
import Callout from './Callout';
import Card from './Card';
import Chip from './Chip';
import type { Tone } from './Chip';
import Icon from './Icon';
import RecipeMatrix from './RecipeMatrix';

const DECISION: Record<string, { tone: Tone; label: string; icon: 'check' | 'alert' | 'info' }> = {
  sufficient: { tone: 'ok', label: 'Good enough', icon: 'check' },
  revise: { tone: 'warn', label: 'Needs another pass', icon: 'alert' },
  needs_user: { tone: 'info', label: 'Needs your decision', icon: 'info' },
  not_evaluable: { tone: 'muted', label: 'Could not be judged', icon: 'info' },
};

export default function RenderPanel({state,health,sending,online,direction,autoStarted=false,onGenerate,onRetry,onCancel,onReview,onRenderSupported,onUseAllMatches,onRenderFull,viewCandidateId}:{
  state?:BackendState;health:Health|null;sending:boolean;online:boolean;direction:string;autoStarted?:boolean;
  onGenerate:()=>void;onRetry:()=>void;onCancel:()=>void;
  onReview:(id:string)=>void;onRenderSupported?:(id:string)=>void;onUseAllMatches?:(id:string)=>void;onRenderFull?:(id:string)=>void;viewCandidateId?:string;
}) {
  if(!state?.brief)return null;
  const running=state.status==='rendering'||state.status==='reviewing';
  const busy=activeJob(state)||sending;
  const latest=state.candidates?.at(-1);
  const selected=state.candidates?.find(c=>c.id===(viewCandidateId??state.selected_candidate_id));
  const changed=direction.trim()!==state.request;
  const adjustment=describeAdjustment(selected?.recipe?.execution_adjustment);
  const local=selected?.recipe?.proposal.local;
  const reviewed=!!selected&&(!!selected.creative_review||(selected.review_attempts??0)>=2)&&selected.review_brief_sha256===state.brief_sha256;
  const decision=selected?.creative_review?DECISION[selected.creative_review.decision]??DECISION.not_evaluable:null;
  const status=running?{tone:'accent' as const,label:'Working'}:selected?.status==='ready'?{tone:'ok' as const,label:'Rendered',icon:'check' as const}
    :latest?.status==='failed'?{tone:'bad' as const,label:'Failed'}:undefined;
  return <Card n={4} title="Generate the grade" className="render-panel" label="Generate grade" status={status}
    actions={<button className="primary" disabled={busy||!online||!health?.muse_configured||!health.grading_connected||changed
        ||!['brief_ready','render_ready'].includes(state.status)} onClick={onGenerate}>
        <Icon name="wand" size={18}/>{selected?'Generate another version':'Generate graded video'}</button>}>
    <p className="help">Muse plans the grade from measured frames and your brief. SAM masks any areas it needs, and Muse can review the result up to three times. That review is an opinion; the decision is yours. Muse and SAM calls cost money, and the total is under “Cost of this video”.</p>
    {autoStarted&&running&&<Callout tone="info">Grading started automatically when the questions finished. Cancel it below, or turn this off next to your direction.</Callout>}
    {changed&&<Callout tone="warn">Your direction has changed. Complete its updated brief before generating a grade.</Callout>}
    {running&&<div className="consultation-status" role="status"><span className="status-text"><span className="spinner"/>{stageLabel(state.stage)}</span>
      <button disabled={sending||!online||state.stage==='cancelling'} onClick={onCancel}>Cancel render</button></div>}
    {running&&latest?.recipe?.proposal.mode==='localized'&&<p className="help">
      Separate areas: {latest.recipe.proposal.local?.concept}. If a required mask fails, this version stops; it never quietly falls back to a whole-frame grade.
    </p>}
    {latest?.status==='failed'&&latest.error?.code==='GRADE_REQUEST_UNFULFILLED'&&latest.error.details?.supported_plan_available?
      <Callout tone="warn" title="Muse can grade everything except:" actions={<button disabled={busy||!online||!onRenderSupported} onClick={()=>onRenderSupported?.(latest.id)}>Render the supported part</button>}>
        <ul>{String(latest.error.details.unfulfilled_instructions??'').split('; ').filter(Boolean).map((item,i)=><li key={i}>{item}</li>)}</ul>
        <p className="help">Render the supported part now (the items above are recorded on the version as not done), or revise your direction to rephrase them.</p>
      </Callout>
    :latest?.status==='failed'&&latest.error?.code==='LOCAL_SUBJECT_AMBIGUOUS'&&latest.error.details?.all_matches_available?
      <Callout tone="warn" title={`SAM found ${String(latest.error.details.object_count??'several')} separate “${String(latest.error.details.concept??'')}” objects.`}
        actions={<button disabled={busy||!online||!onUseAllMatches} onClick={()=>onUseAllMatches?.(latest.id)}>Grade all matches</button>}>
        <p className="help">Grade all of them as this area, or revise your direction to name the single one you mean. Nothing was guessed.</p>
      </Callout>
    :latest&&(latest.status==='failed'||latest.status==='cancelled')&&state.status!=='render_ready'&&
      <div className="consultation-actions"><span>{latest.name}: {latest.error?.message??latest.status}</span>
        <button disabled={busy||!online} onClick={onRetry}>Retry render</button></div>}
    {selected?.tier==='draft'&&selected.status==='ready'&&<div className="consultation-actions">
      <span>{selected.name} is a fast {selected.width}×{selected.height} draft used for review. The version Take One delivers is rendered at full resolution.</span>
      {!state.candidates?.some(c=>c.promoted_from===selected.id&&c.status==='ready')&&
        <button disabled={busy||!online||!onRenderFull} onClick={()=>onRenderFull?.(selected.id)}>Render this version at full resolution</button>}
    </div>}
    {selected?.promoted_from&&<p className="help">{selected.name} is the full-resolution render of draft {state.candidates?.find(c=>c.id===selected.promoted_from)?.name}, using the same plan.</p>}
    {selected?.approved_omissions?.length?<p className="help">{selected.name} was rendered without: {selected.approved_omissions.join('; ')}.</p>:null}
    {latest&&selected&&latest.id!==selected.id&&['failed','cancelled'].includes(latest.status)&&
      <p className="help">The latest attempt did not produce an accepted candidate. The displayed render is an earlier version.</p>}
    {latest&&latest.id!==selected?.id&&latest.review_error&&<p className="help">
      Creative review for {latest.name} needs attention: {latest.review_error.message} Its render remains available in Version.
    </p>}
    {latest&&selected&&latest.id!==selected.id&&latest.creative_review&&<p className="help">
      Latest Muse opinion for {latest.name}: {latest.creative_review.summary} The selected version remains available for comparison.
    </p>}
    {selected?.status==='ready'&&<div className="render-result">
      <div className="result-head">
        <div className="grow"><h3>{selected.name} is rendered</h3>
          <p className="help">Choose it under Version, compare it with the original, and review it before you export.{!!selected.iteration&&` Render pass ${selected.iteration}; earlier versions stay available.`}</p></div>
        {!(reviewed&&selected.creative_review)&&<button disabled={busy||!online||!health?.muse_configured||reviewed}
          onClick={()=>onReview(selected.id)}><Icon name="eye" size={18}/>{selected.review_error?'Retry creative review':'Review this candidate'}</button>}
      </div>
      {selected.review_error&&<Callout tone="warn" title="Creative review unavailable">{selected.review_error.message} {selected.review_error.action} The rendered video is kept.</Callout>}
      {selected.creative_review&&decision&&<section className="creative-opinion" aria-label={'Muse creative opinion, '+selected.name}>
        <div className="opinion-head"><h4>Muse’s opinion</h4><Chip tone={decision.tone} icon={decision.icon}>{decision.label}</Chip>
          {selected.revision_stop_reason&&selected.revision_stop_reason!==selected.creative_review.decision&&<span className="help">{selected.revision_stop_reason.replaceAll('_',' ')}</span>}</div>
        <p>{selected.creative_review.summary}</p>
        {!!selected.creative_review.issues.length&&<ul className="issue-list">{selected.creative_review.issues.map(i=><li key={i.id}><Icon name="alert" size={18}/>
          <span>{i.description} <small>Evidence: {i.evidence_ids.join(', ')}</small></span></li>)}</ul>}
        {!!selected.review_evidence?.length&&<details><summary>Frames Muse looked at ({selected.review_evidence.length})</summary><ul className="plain-list">
          {selected.review_evidence.map(f=><li key={f.frame_index}>Frame {f.frame_index} · {f.time.toFixed(3)}s · {f.roles.join(', ')}</li>)}
        </ul></details>}
        <p className="note">An opinion from the model, not a measure of quality and not your approval.</p>
      </section>}
      {selected.recipe&&<section aria-labelledby="changed-heading">
        <h4 id="changed-heading" className="sub-head">What the grade changed</h4>
        <p className="summary">{selected.recipe.proposal.summary}</p>
        <p className="treatment">{local
          ?<><Chip tone="info" icon="layers">Separate areas</Chip> <span>{local.concept}{local.intervals?.length?` · ${local.intervals.map(i=>`${i.start.toFixed(2)}–${i.end.toFixed(2)} s`).join(', ')}`:''} · {selected.mask_run_ids?.length??0} mask run(s)</span></>
          :<><Chip tone="muted" icon="film">Whole frame</Chip> <span>One grade applied to every pixel</span></>}</p>
        <RecipeMatrix recipe={selected.recipe}/>
        {adjustment&&<Callout tone="info" title={adjustment.title}>{adjustment.detail&&<p>{adjustment.detail}</p>}
          {adjustment.changes.length>0&&<p className="mono-line">{adjustment.changes.map(formatChange).join(' · ')}</p>}</Callout>}
      </section>}
      {!!selected.validation?.warnings.length&&<Callout tone="warn" title="Worth a look"><ul>{selected.validation.warnings.map((w,i)=><li key={i}>{w}</li>)}</ul></Callout>}
      <details><summary>Recipe and checks (technical)</summary><pre>{JSON.stringify({recipe:selected.recipe,validation:selected.validation},null,2)}</pre></details>
      <p className="note">Technical checks passed. That means it is ready for your review, not that it looks right. Watch the moving video for color fidelity, dark detail, mask edges and flicker. Physical relighting and automatic creative approval aren’t available.</p>
    </div>}
  </Card>;
}
