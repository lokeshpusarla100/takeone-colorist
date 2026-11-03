import type { BackendState, GradeValues, Health } from '../lib/api';
import { activeJob, stageLabel } from '../lib/api';

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
  const values=(p:GradeValues)=><dl className="recipe-values">{Object.entries(p).filter(([,v])=>typeof v==='number').map(([name,value])=>
    <div key={name}><dt>{name.replaceAll('_',' ')}</dt><dd>{value!.toFixed(3)}{name==='cooling'?' shift':'×'}</dd></div>)}</dl>;
  return <section className="render-panel" aria-labelledby="render-heading">
    <div className="section-heading"><h2 id="render-heading">Generate grade</h2>
      <button disabled={busy||!online||!health?.muse_configured||!health.grading_connected||changed
        ||!['brief_ready','render_ready'].includes(state.status)} onClick={onGenerate}>
        {selected?'Generate another version':'Generate graded video'}</button>
    </div>
    <p className="inline-help">Muse plans from bounded adaptive visual evidence, measurements and the current brief. Localized plans obtain SAM masks automatically. Substantive candidates can receive a rendered-evidence critique and up to three render iterations. Model review is an opinion; final approval remains yours. Muse and SAM calls may incur usage.</p>
    {autoStarted&&running&&<p className="inline-help">Grading started automatically when the questions finished. Cancel it below, or turn this off next to the direction.</p>}
    {changed&&<p className="inline-help">The direction has changed. Complete its updated brief before generating a grade.</p>}
    {running&&<div className="consultation-status" role="status"><span>{stageLabel(state.stage)}</span>
      <button disabled={sending||!online||state.stage==='cancelling'} onClick={onCancel}>Cancel render</button></div>}
    {running&&latest?.recipe?.proposal.mode==='localized'&&<p className="inline-help">
      Localized plan: {latest.recipe.proposal.local?.concept}. Required mask failures stop this candidate; there is no silent global fallback.
    </p>}
    {latest?.status==='failed'&&latest.error?.code==='GRADE_REQUEST_UNFULFILLED'&&latest.error.details?.supported_plan_available?
      <div className="error-notice" role="alert">
        <strong>Muse can grade everything except:</strong>
        <ul>{String(latest.error.details.unfulfilled_instructions??'').split('; ').filter(Boolean).map((item,i)=><li key={i}>{item}</li>)}</ul>
        <p className="inline-help">Render the supported part now (the items above are recorded on the version as not done), or revise the direction to rephrase them.</p>
        <div className="actions"><button disabled={busy||!online||!onRenderSupported} onClick={()=>onRenderSupported?.(latest.id)}>Render the supported part</button></div>
      </div>
    :latest?.status==='failed'&&latest.error?.code==='LOCAL_SUBJECT_AMBIGUOUS'&&latest.error.details?.all_matches_available?
      <div className="error-notice" role="alert">
        <strong>SAM found {String(latest.error.details.object_count??'several')} separate “{String(latest.error.details.concept??'')}” objects.</strong>
        <p className="inline-help">Grade all of them as this area, or revise the direction to name the single one you mean. No object was guessed.</p>
        <div className="actions"><button disabled={busy||!online||!onUseAllMatches} onClick={()=>onUseAllMatches?.(latest.id)}>Grade all matches</button></div>
      </div>
    :latest&&(latest.status==='failed'||latest.status==='cancelled')&&state.status!=='render_ready'&&
      <div className="consultation-actions"><span>{latest.name}: {latest.error?.message??latest.status}</span>
        <button disabled={busy||!online} onClick={onRetry}>Retry render</button></div>}
    {selected?.tier==='draft'&&selected.status==='ready'&&<div className="consultation-actions">
      <span>{selected.name} is a fast {selected.width}×{selected.height} draft used for review. The version Take One delivers is rendered at full resolution.</span>
      {!state.candidates?.some(c=>c.promoted_from===selected.id&&c.status==='ready')&&
        <button disabled={busy||!online||!onRenderFull} onClick={()=>onRenderFull?.(selected.id)}>Render this version at full resolution</button>}
    </div>}
    {selected?.promoted_from&&<p className="inline-help">{selected.name} is the full-resolution render of draft {state.candidates?.find(c=>c.id===selected.promoted_from)?.name}, using the same plan.</p>}
    {selected?.approved_omissions?.length?<p className="inline-help">{selected.name} was rendered without: {selected.approved_omissions.join('; ')}.</p>:null}
    {latest&&selected&&latest.id!==selected.id&&['failed','cancelled'].includes(latest.status)&&
      <p className="inline-help">The latest attempt did not produce an accepted candidate. The displayed render is an earlier version.</p>}
    {latest&&latest.id!==selected?.id&&latest.review_error&&<p className="inline-help">
      Creative review for {latest.name} needs attention: {latest.review_error.message} Its render remains available in Version.
    </p>}
    {latest&&selected&&latest.id!==selected.id&&latest.creative_review&&<p className="inline-help">
      Latest Muse opinion for {latest.name}: {latest.creative_review.summary} The selected version remains available for comparison.
    </p>}
    {selected?.status==='ready'&&<div className="render-result">
      <p><strong>{selected.name} is rendered.</strong> Select it in Version, compare with Original, and review it before export.</p>
      {!!selected.iteration&&<p className="inline-help">Render iteration {selected.iteration}. Earlier versions remain available for comparison.</p>}
      <button disabled={busy||!online||!health?.muse_configured||(!!selected.creative_review||(selected.review_attempts??0)>=2)&&selected.review_brief_sha256===state.brief_sha256}
        onClick={()=>onReview(selected.id)}>{selected.review_error?'Retry creative review':'Review this candidate'}</button>
      {selected.review_error&&<p className="inline-help">Creative review unavailable: {selected.review_error.message} {selected.review_error.action} The rendered video is retained.</p>}
      {selected.creative_review&&<section className="creative-opinion"><h3>Muse creative opinion · {selected.name}</h3>
        <p>{selected.creative_review.summary}</p>
        <ul>{selected.creative_review.issues.map(i=><li key={i.id}>{i.description} <small>Evidence: {i.evidence_ids.join(', ')}</small></li>)}</ul>
        {!!selected.review_evidence?.length&&<details><summary>Rendered evidence used</summary><ul>
          {selected.review_evidence.map(f=><li key={f.frame_index}>Frame {f.frame_index} · {f.time.toFixed(3)}s · {f.roles.join(', ')}</li>)}
        </ul></details>}
        <p className="inline-help">{[selected.creative_review.decision.replaceAll('_',' '),selected.revision_stop_reason?.replaceAll('_',' ')].filter(Boolean).join(' · ')}. This is not objective quality or user approval.</p>
      </section>}
      {selected.recipe&&<>
        <p>{selected.recipe.proposal.summary}</p>
        {selected.recipe.execution_adjustment&&<p className="inline-help"><strong>Execution correction: </strong>{selected.recipe.execution_adjustment.reason}
          {' '}{Object.entries(selected.recipe.execution_adjustment.changes).map(([key,change])=>`${key}: ${change.from.toFixed(4)} → ${change.to.toFixed(4)}`).join(' · ')}</p>}
        <p>Treatment: {selected.recipe.proposal.mode??'global'}</p>
        {selected.recipe.proposal.local?<>
          <h3>Subject</h3>{values(selected.recipe.proposal.local.foreground)}
          {selected.recipe.proposal.local.regions?.map((region,i)=><div key={i}><h3>Region: {region.concept}</h3>{values(region.treatment)}</div>)}
          <h3>Background</h3>{values(selected.recipe.proposal.local.background)}
          <p className="inline-help">{selected.recipe.proposal.local.concept} · {selected.recipe.proposal.local.intervals.map(i=>`${i.start.toFixed(2)}–${i.end.toFixed(2)}s`).join(', ')} · {selected.mask_run_ids?.length??0} mask run(s)</p>
        </>:values(selected.recipe.proposal.parameters)}
      </>}
      {!!selected.validation?.warnings.length&&<ul className="render-warnings">{selected.validation.warnings.map((w,i)=><li key={i}>{w}</li>)}</ul>}
      <details><summary>Recipe and checks</summary><pre>{JSON.stringify({recipe:selected.recipe,validation:selected.validation},null,2)}</pre></details>
      <p className="inline-help">Technical checks passed; this is ready for your review, not an aesthetic approval. Inspect the moving video for paint/light fidelity, dark detail, mask edges and flicker. Physical relighting and automatic creative approval are unavailable.</p>
    </div>}
  </section>;
}
