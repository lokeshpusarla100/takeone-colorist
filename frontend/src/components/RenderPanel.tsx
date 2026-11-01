import type { BackendState, Health } from '../lib/api';
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
  return <section className="render-panel" aria-labelledby="render-heading">
    <div className="section-heading"><h2 id="render-heading">Global grade</h2>
      <button disabled={busy||!online||!health?.muse_configured||!health.grading_connected||changed
        ||!['brief_ready','render_ready'].includes(state.status)} onClick={onGenerate}>
        {selected?'Generate another version':'Generate graded video'}</button>
    </div>
    <p className="inline-help">Generate sends four selected source frames and the saved brief to Meta. The render uses the original footage. One global treatment applies across the clip.</p>
    {changed&&<p className="inline-help">The direction has changed. Complete its updated brief before generating a grade.</p>}
    {running&&<div className="consultation-status" role="status"><span>{stageLabel(state.stage)}</span>
      <button disabled={sending||state.stage==='cancelling'} onClick={onCancel}>Cancel render</button></div>}
    {latest&&(latest.status==='failed'||latest.status==='cancelled')&&state.status!=='render_ready'&&
      <div className="consultation-actions"><span>{latest.name}: {latest.error?.message??latest.status}</span>
        <button disabled={busy||!online} onClick={onRetry}>Retry render</button></div>}
    {selected?.status==='ready'&&<div className="render-result">
      <p><strong>{selected.name} is rendered.</strong> Select it in Version, compare with Original, and review it before export.</p>
      {selected.recipe&&<>
        <p>{selected.recipe.proposal.summary}</p>
        <dl className="recipe-values">{Object.entries(selected.recipe.proposal.parameters).map(([name,value])=>
          <div key={name}><dt>{name}</dt><dd>{value.toFixed(3)}×</dd></div>)}</dl>
      </>}
      {!!selected.validation?.warnings.length&&<ul className="render-warnings">{selected.validation.warnings.map((w,i)=><li key={i}>{w}</li>)}</ul>}
      <details><summary>Recipe and checks</summary><pre>{JSON.stringify({recipe:selected.recipe,validation:selected.validation},null,2)}</pre></details>
      <p className="inline-help">Media checks passed. Aesthetic quality and preservation still require your moving-video review. SAM and automatic critique are not connected.</p>
    </div>}
  </section>;
}
