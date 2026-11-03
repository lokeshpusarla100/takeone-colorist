import type { BackendState, Health } from '../lib/api';
import { activeJob, stageLabel } from '../lib/api';
import Callout from './Callout';
import Card from './Card';
import Icon from './Icon';

export default function LooksPanel({state,health,sending,online,locked,onGenerate,onSelect}:{
  state?:BackendState;health:Health|null;sending:boolean;online:boolean;locked:boolean;
  onGenerate:()=>void;onSelect:(lookId:string)=>void;
}) {
  if(!state?.brief)return null;
  const looks=state.looks??[];
  const selected=looks.find(l=>l.id===state.selected_look_id);
  const busy=activeJob(state)||sending||locked;
  const generating=state.stage==='select_looks';
  return <Card n={3} title="Choose a look" className="looks-panel" label="Choose a look" status={selected?{tone:'ok',label:'Look chosen',icon:'check'}:{tone:'muted',label:'Optional'}}
    actions={<button disabled={busy||!online||!health?.muse_configured||health?.look_previews_connected!==true||!['brief_ready','render_ready'].includes(state.status)}
        onClick={onGenerate}><Icon name="star" size={18}/>{looks.length?'Regenerate previews':'Generate previews'}</button>}>
    {state.error?.code==='GRADE_REQUEST_UNFULFILLED'&&<Callout tone="warn" title="Some requested treatments can’t be previewed.">
      <p>{String(state.error.details.unfulfilled_instructions??'This older attempt saved only the rejection count; its instruction text is unavailable.')}</p>
      <p className="help">The request was stopped rather than showing a whole-frame grade as if it protected the subject. Your video and brief are kept.</p>
    </Callout>}
    {online&&health&&health.look_previews_connected!==true&&<Callout tone="warn">The running backend needs an update for look previews. Restart it from this checkout, then refresh this page. Saved projects are kept.</Callout>}
    <p className="help">Eight still previews of your brief, graded on one representative frame with the real grading pipeline. Pick one and the full render uses that exact recipe. Skip this step and Muse plans the grade itself.</p>
    {generating&&<div className="consultation-status" role="status"><span className="status-text"><span className="spinner"/>{stageLabel(state.stage)}</span></div>}
    {state.looks_limitation&&<p className="help">{state.looks_limitation}</p>}
    {!!looks.length&&<>
      <p className="note">Frame {state.looks_frame_index} at {(state.looks_frame_time??0).toFixed(2)} s. These are stills: motion, masks over time and encoding are checked in the full render.</p>
      <div className="looks-grid">
        {looks.map(look=><button key={look.id} type="button" className="look-card" aria-pressed={look.id===state.selected_look_id}
          disabled={busy||!online||health?.look_previews_connected!==true||look.preview_only===true} onClick={()=>onSelect(look.id)}>
          <img src={look.image_url} alt={look.name+' graded preview'} loading="lazy"/>
          {look.id===state.selected_look_id&&<span className="look-badge"><Icon name="check" size={14}/>Selected</span>}
          <strong>{look.name}</strong>
          <span>{look.description}</span>
          <small>{look.preview_only?'Palette reference only · needs a SAM mask':look.uses_local?'Separate areas (SAM mask)':'Whole frame'} · contrast {look.proposal.parameters.contrast.toFixed(2)} · saturation {look.proposal.parameters.saturation.toFixed(2)}</small>
        </button>)}
      </div>
      <p className="help" role="status">{selected
        ? 'Chosen: '+selected.name+'. The next render will use this exact recipe.'
        : 'No look chosen yet. Rendering without one lets Muse plan the grade.'}</p>
    </>}
  </Card>;
}
