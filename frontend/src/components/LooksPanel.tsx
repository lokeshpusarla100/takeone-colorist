import type { BackendState, Health } from '../lib/api';
import { activeJob, stageLabel } from '../lib/api';

export default function LooksPanel({state,health,sending,online,locked,onGenerate,onSelect}:{
  state?:BackendState;health:Health|null;sending:boolean;online:boolean;locked:boolean;
  onGenerate:()=>void;onSelect:(lookId:string)=>void;
}) {
  if(!state?.brief)return null;
  const looks=state.looks??[];
  const selected=looks.find(l=>l.id===state.selected_look_id);
  const busy=activeJob(state)||sending||locked;
  const generating=state.stage==='select_looks';
  return <section className="looks-panel" aria-labelledby="looks-heading">
    <div className="section-heading"><h2 id="looks-heading">Choose a look</h2>
      <button disabled={busy||!online||!health?.muse_configured||health?.look_previews_connected!==true||!['brief_ready','render_ready'].includes(state.status)}
        onClick={onGenerate}>{looks.length?'Regenerate look previews':'Generate look previews'}</button>
    </div>
    {state.error?.code==='GRADE_REQUEST_UNFULFILLED'&&<div className="error-notice" role="alert">
      <strong>Some requested treatments cannot be previewed.</strong>
      <p>{String(state.error.details.unfulfilled_instructions??'This older attempt saved only the rejection count; its instruction text is unavailable.')}</p>
      <p className="inline-help">The request was stopped rather than presenting a global grade as subject protection. Your video and brief are kept.</p>
    </div>}
    {online&&health&&health.look_previews_connected!==true&&<p className="inline-help" role="status">The running backend needs an update for look previews. Restart it from this checkout, then refresh this page. Saved projects are kept.</p>}
    <p className="inline-help">Eight previews graded from your Creative Brief on one representative frame, using the actual grading pipeline. No video is rendered yet. Select one to approve it as the recipe for the full render; you can change it before rendering.</p>
    {generating&&<p className="consultation-status" role="status"><span>{stageLabel(state.stage)}</span></p>}
    {state.looks_limitation&&<p className="inline-help">{state.looks_limitation}</p>}
    {!!looks.length&&<>
      <p className="inline-help">Representative frame {state.looks_frame_index} at {(state.looks_frame_time??0).toFixed(2)}s. Still-frame previews only; motion, masks over time and encoding are checked during the full render.</p>
      <div className="looks-grid">
        {looks.map(look=><button key={look.id} type="button" className="look-card" aria-pressed={look.id===state.selected_look_id}
          disabled={busy||!online||health?.look_previews_connected!==true||look.preview_only===true} onClick={()=>onSelect(look.id)}>
          <img src={look.image_url} alt={look.name+' graded preview'} loading="lazy"/>
          <strong>{look.name}{look.id===state.selected_look_id?' · Selected':''}</strong>
          <span>{look.description}</span>
          <small>{look.preview_only?'Palette reference only · SAM mask required':look.uses_local?'Localized (SAM mask)':'Global'} · contrast {look.proposal.parameters.contrast.toFixed(2)} · saturation {look.proposal.parameters.saturation.toFixed(2)}</small>
        </button>)}
      </div>
      <p className="inline-help" role="status">{selected
        ? 'Selected look: '+selected.name+'. The next render will use this exact recipe.'
        : 'No look selected yet. Rendering without a selection keeps the current planner flow.'}</p>
    </>}
  </section>;
}
