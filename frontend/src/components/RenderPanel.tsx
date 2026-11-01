import type { BackendState, GradeValues, Health } from '../lib/api';
import { activeJob, stageLabel } from '../lib/api';

export default function RenderPanel({state,health,sending,online,direction,onGenerate,onRetry,onCancel}:{
  state?:BackendState;health:Health|null;sending:boolean;online:boolean;direction:string;
  onGenerate:()=>void;onRetry:()=>void;onCancel:()=>void;
}) {
  if(!state?.brief)return null;
  const running=state.status==='rendering';
  const busy=activeJob(state)||sending;
  const latest=state.candidates?.at(-1);
  const selected=state.candidates?.find(c=>c.id===state.selected_candidate_id);
  const changed=direction.trim()!==state.request;
  const values=(p:GradeValues)=><dl className="recipe-values">{Object.entries(p).filter(([,v])=>typeof v==='number').map(([name,value])=>
    <div key={name}><dt>{name.replaceAll('_',' ')}</dt><dd>{value!.toFixed(3)}{name==='cooling'?' shift':'×'}</dd></div>)}</dl>;
  return <section className="render-panel" aria-labelledby="render-heading">
    <div className="section-heading"><h2 id="render-heading">Generate grade</h2>
      <button disabled={busy||!online||!health?.muse_configured||!health.grading_connected||changed
        ||!['brief_ready','render_ready'].includes(state.status)} onClick={onGenerate}>
        {selected?'Generate another version':'Generate graded video'}</button>
    </div>
    <p className="inline-help">Muse plans from source frames, measurements and the current brief. Global corrections bypass SAM. A localized plan automatically tracks the requested subject, validates masks, and composites separate treatments on the original footage. SAM may incur per-frame usage.</p>
    {changed&&<p className="inline-help">The direction has changed. Complete its updated brief before generating a grade.</p>}
    {running&&<div className="consultation-status" role="status"><span>{stageLabel(state.stage)}</span>
      <button disabled={sending||!online||state.stage==='cancelling'} onClick={onCancel}>Cancel render</button></div>}
    {running&&latest?.recipe?.proposal.mode==='localized'&&<p className="inline-help">
      Localized plan: {latest.recipe.proposal.local?.concept}. Required mask failures stop this candidate; there is no silent global fallback.
    </p>}
    {latest&&(latest.status==='failed'||latest.status==='cancelled')&&state.status!=='render_ready'&&
      <div className="consultation-actions"><span>{latest.name}: {latest.error?.message??latest.status}</span>
        <button disabled={busy||!online} onClick={onRetry}>Retry render</button></div>}
    {latest&&selected&&latest.id!==selected.id&&['failed','cancelled'].includes(latest.status)&&
      <p className="inline-help">The latest attempt did not produce an accepted candidate. The displayed render is an earlier version.</p>}
    {selected?.status==='ready'&&<div className="render-result">
      <p><strong>{selected.name} is rendered.</strong> Select it in Version, compare with Original, and review it before export.</p>
      {selected.recipe&&<>
        <p>{selected.recipe.proposal.summary}</p>
        <p>Treatment: {selected.recipe.proposal.mode??'global'}</p>
        {selected.recipe.proposal.local?<>
          <h3>Subject</h3>{values(selected.recipe.proposal.local.foreground)}
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
