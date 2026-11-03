import type { BackendState } from '../lib/api';
import Card from './Card';
import Chip from './Chip';
import Guardrails from './Guardrails';
import Icon from './Icon';
import RichText from './RichText';

/** The brief Muse will grade from: what you asked for, what must stay, and the safeguards and limits as a scannable summary. */
export default function BriefCard({ state, onDownload }: { state?: BackendState; onDownload: () => void }) {
  const brief = state?.brief;
  if (!state || !brief) return null;
  return <Card n={2} title="Creative brief" className="creative-brief" status={{ tone: 'ok', label: 'Saved', icon: 'check' }}
    actions={<button onClick={onDownload} title="Download the brief as JSON"><Icon name="download" size={16} />Download</button>}>
    <section aria-labelledby="brief-request">
      <h3 id="brief-request" className="sub-head">{brief.initial_request ? 'What you asked for' : 'Direction'}</h3>
      {brief.initial_request ? <RichText source={brief.initial_request} /> : <p className="help">No direction was written, so Muse chose a conservative one.</p>}
    </section>
    {!!brief.directives.length && <section aria-labelledby="brief-directives"><h3 id="brief-directives" className="sub-head">What the grade will aim for</h3>
      <ul className="check-list">{brief.directives.map((d, i) => <li key={i}><Icon name="spark" size={18} /><span>{d.outcome}</span></li>)}</ul></section>}
    {!!brief.explicit_constraints.length && <section aria-labelledby="brief-keep"><h3 id="brief-keep" className="sub-head">Keep as it is</h3>
      <ul className="check-list keep">{brief.explicit_constraints.map((v, i) => <li key={i}><Icon name="check" size={18} /><span>{v}</span></li>)}</ul></section>}
    {!!brief.unresolved.length && <section aria-labelledby="brief-open"><h3 id="brief-open" className="sub-head">Open or left to Muse <Chip tone="info">Delegated</Chip></h3>
      <ul className="check-list open">{brief.unresolved.map((v, i) => <li key={i}><Icon name="info" size={18} /><span>{v}</span></li>)}</ul></section>}
    <details className="fold" open>
      <summary><Icon name="shield" size={18} />Safeguards and technical limits<Icon name="chevron" size={18} className="fold-caret" /></summary>
      <Guardrails protections={brief.default_protections} envelope={brief.technical_envelope} source={state.source} />
    </details>
    <p className="help">Brief saved. Generate a grade below. Muse chooses a whole-frame correction or a supported treatment of separate areas, with SAM masks made for you.</p>
  </Card>;
}
