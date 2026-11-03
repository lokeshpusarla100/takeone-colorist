import type { BackendState, Health } from '../lib/api';
import { formatUsd, parsePricing, projectCost, STAGE_LABEL } from '../lib/cost';
import { tokenUsage } from '../lib/tokenUsage';
import type { Stage } from '../lib/tokenUsage';
import Icon from './Icon';
import Chip from './Chip';

/** One fixed colour per stage (validated categorical slots 1-5), so a stage keeps its colour in every view. */
export const STAGE_COLOR: Record<Stage, string> = {
  analysis: 'var(--series-1)', rubric: 'var(--series-2)', colorist: 'var(--series-3)', sam: 'var(--series-4)', critic: 'var(--series-5)',
};
const n = (v: number | null) => v === null ? '—' : v.toLocaleString();

/** What this video has cost so far, in dollars, for Muse and SAM together, with where it went. */
export default function CostCard({ state, health }: { state?: BackendState; health: Health | null }) {
  const pricing = parsePricing(health?.pricing);
  const active = state && ['analyzing', 'rendering', 'segmenting', 'reviewing'].includes(state.status) ? state.stage : undefined;
  const cost = projectCost(state?.events ?? [], pricing, active);
  const usage = tokenUsage(state?.events ?? [], active);
  const requests = usage.rows.length;
  const summary = cost.stages.map(s => `${s.label} ${formatUsd(s.usd)} (${Math.round(s.share * 100)}%)`).join(', ');
  return <section className="card cost-card" aria-labelledby="cost-heading">
    <header className="card-head">
      <Icon name="dollar" size={20} /><h2 id="cost-heading">Cost of this video</h2>
      <Chip tone="muted" title="Calculated from the usage Meta reports, at its published prices. It is not a bill.">Estimate</Chip>
    </header>
    <div className="card-body">
      {!requests ? <p className="help">Nothing has been billed yet. Muse and SAM usage appears here as it happens.</p>
        : !cost.priced ? <>
          <p className="help" role="status">Prices are unavailable, so no dollar figure is shown. Restart the backend from this checkout, then refresh.</p>
          <p className="help">Tokens so far: {n(usage.input)} in · {n(usage.output)} out.</p></>
          : <>
            <div className="cost-total" aria-live="polite" aria-atomic="true">
              <strong>{formatUsd(cost.total)}</strong>
              <span>{cost.pending ? 'so far · a request is still running' : 'so far'}</span>
            </div>
            <p className="cost-split"><span>Muse <b>{formatUsd(cost.muse)}</b></span><span>SAM <b>{formatUsd(cost.sam)}</b></span></p>
            {cost.stages.length > 0 && <>
              <ul className="cost-bar" role="img" aria-label={'Where the cost went: ' + summary}>
                {cost.stages.map(s => <li key={s.stage} style={{ flexGrow: Math.max(s.usd, 1e-9), background: STAGE_COLOR[s.stage] }}
                  title={`${s.label}: ${formatUsd(s.usd)} · ${Math.round(s.share * 100)}%`} />)}
              </ul>
              <ul className="cost-rows">{cost.stages.map(s => <li key={s.stage}>
                <i style={{ background: STAGE_COLOR[s.stage] }} aria-hidden="true" /><span>{STAGE_LABEL[s.stage]}</span>
                <b>{formatUsd(s.usd)}</b><small>{s.share >= 0.01 ? Math.round(s.share * 100) + '%' : '<1%'}</small></li>)}</ul></>}
            {cost.unpriced > 0 && <p className="note warn" role="status"><Icon name="alert" size={16} />
              {cost.unpriced} request{cost.unpriced > 1 ? 's' : ''} never reported usage (stopped early or failed), so {cost.unpriced > 1 ? 'they are' : 'it is'} not counted. The real total can be a little higher.</p>}
          </>}
      {requests > 0 && <details className="cost-details"><summary>Every request ({requests})</summary>
        <div className="table-scroll"><table className="plain">
          <thead><tr><th scope="col">Request</th><th scope="col">Used</th><th scope="col">Cost</th></tr></thead>
          <tbody>{(cost.lines.length ? cost.lines : []).map((line, i) => <tr key={line.id}>
            <th scope="row"><span className="swatch" style={{ background: STAGE_COLOR[line.stage] }} aria-hidden="true" />{i + 1}. {line.label}</th>
            <td>{line.detail}</td><td>{formatUsd(line.usd)}</td></tr>)}</tbody>
        </table></div>
        <p className="note">Tokens: {n(usage.input)} in · {n(usage.output)} out{usage.cached ? ` · ${n(usage.cached)} cached` : ''}. Reasoning tokens are part of the output count.</p>
      </details>}
      {pricing && <div className="rates">
        <p><span className="nowrap">Muse</span> <span className="nowrap">{formatUsd(pricing.muse.input_per_million)} in</span> · <span className="nowrap">{formatUsd(pricing.muse.output_per_million)} out</span> per 1M tokens</p>
        <p><span className="nowrap">SAM</span> {formatUsd(pricing.sam.per_thousand_frames)} per 1,000 video frames</p>
        <p className="note">Meta’s published prices, checked {pricing.checked}. An estimate from reported usage, not a bill; other charges Meta may apply aren’t included.</p>
      </div>}
    </div>
  </section>;
}
