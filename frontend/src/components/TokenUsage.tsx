import type { BackendState } from '../lib/api';
import { tokenUsage } from '../lib/tokenUsage';

const format=(n:number|null)=>n===null?'—':n.toLocaleString();
export default function TokenUsage({state}:{state?:BackendState}){
  const active=state&&['analyzing','rendering','segmenting'].includes(state.status)?state.stage:undefined;
  const usage=tokenUsage(state?.events??[],active);
  return <section className="token-usage" aria-labelledby="token-usage-heading">
    <h3 id="token-usage-heading">Token usage · this project</h3>
    <dl className="token-totals" aria-live="polite" aria-atomic="true">
      <div><dt>Input</dt><dd>{format(usage.input)}</dd></div>
      <div><dt>Output</dt><dd>{format(usage.output)}</dd></div>
      <div><dt>Total reported</dt><dd>{format(usage.total)}</dd></div>
    </dl>
    {!usage.rows.length?<p className="inline-help">No model requests recorded.</p>:<>
      <p className="inline-help" role="status">{usage.running?'Request running — awaiting Meta usage.':'Updates automatically after each model response.'}
        {usage.incomplete>0&&` Counts missing for ${usage.incomplete} request(s); totals are partial.`}</p>
      {(usage.cached!==null||usage.reasoning!==null)&&<p className="inline-help">
        Cached input: {format(usage.cached)} · Reasoning output: {format(usage.reasoning)}. Included in totals.
      </p>}
      <details><summary>Per-request usage ({usage.rows.length})</summary>
        <div className="token-table"><table>
          <thead><tr><th scope="col">Request</th><th scope="col">Input</th><th scope="col">Output</th><th scope="col">Total</th></tr></thead>
          <tbody>{usage.rows.map((row,i)=><tr key={row.id}>
            <th scope="row">{i+1}. {row.label}<small>{new Date(row.timestamp).toLocaleTimeString()}
              {row.model&&` · ${row.model}`}
              {row.running?' · Running':row.input===null||row.output===null||row.total===null?' · Not fully reported':''}</small></th>
            <td>{format(row.input)}</td><td>{format(row.output)}</td><td>{format(row.total)}
              {row.processedFrames!=null&&<small>{format(row.processedFrames)} video frames reported</small>}</td>
          </tr>)}</tbody>
        </table></div>
      </details>
    </>}
    <p className="inline-help">Provider-reported counts, not a billing balance or context-window meter.</p>
    {!!state?.sam_calls&&<p className="inline-help">SAM video usage is per frame; missing token counts do not imply a free request.</p>}
  </section>;
}
