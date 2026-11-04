import Icon from './Icon';
import { parseEnvelope, safeguards, colorName, codecName, megabytes, seconds, sizeName } from '../lib/briefVisual';
import type { Fact } from '../lib/briefVisual';
import type { BackendState } from '../lib/api';
import { colorTile, parseDomain } from '../features/color/sourceColor';

/** Facts from the prepared source when the backend sent them as data; the sentence form is only a fallback. */
function sourceFacts(source: BackendState['source'], parsed: Fact[]): Fact[] {
  if (!source) return parsed;
  const facts: Fact[] = [
    { label: 'Resolution', value: `${source.width} × ${source.height}`, note: sizeName(source.width, source.height) },
    { label: 'Duration', value: seconds(source.duration) },
    { label: 'Codec', value: codecName(source.video_codec) },
  ];
  if (source.pixel_format) facts.push({ label: 'Pixel format', value: source.pixel_format });
  const domain = parseDomain(source.domain);
  const tile = colorTile(domain);
  if (tile) facts.push({ label: 'Color', value: tile.value, note: tile.note });
  else {
    const color = colorName(source.color_space ?? source.color_primaries);
    if (color) facts.push({ label: 'Color space', value: color });
  }
  facts.push({ label: 'File size', value: megabytes(source.bytes) });
  return facts;
}

/** The brief's safeguards and technical envelope as things to scan, not paragraphs to read. */
export default function Guardrails({ protections, envelope, source }: { protections: string[]; envelope: string[]; source: BackendState['source'] }) {
  const cards = safeguards(protections);
  const env = parseEnvelope(envelope);
  const facts = sourceFacts(source, env.facts);
  return <div className="guardrails">
    {cards.length > 0 && <section aria-labelledby="gr-protect">
      <h3 id="gr-protect"><Icon name="shield" size={18} />Always protected</h3>
      <ul className="safeguards">{cards.map((c, i) => <li key={i} className="safeguard">
        <span className="safeguard-icon"><Icon name={c.icon} size={20} /></span>
        <div><strong>{c.title}</strong><p>{c.text}</p></div></li>)}</ul>
    </section>}
    {facts.length > 0 && <section aria-labelledby="gr-source">
      <h3 id="gr-source"><Icon name="film" size={18} />Your footage</h3>
      <dl className="facts">{facts.map(f => <div key={f.label} className="fact"><dt>{f.label}</dt><dd>{f.value}</dd>{f.note && <small>{f.note}</small>}</div>)}
      </dl>
    </section>}
    {env.supported.length > 0 && <section aria-labelledby="gr-can">
      <h3 id="gr-can"><Icon name="sliders" size={18} />What can be adjusted</h3>
      <ul className="chips">{env.supported.map((s, i) => <li key={i} className="tag tag-ok"><Icon name="check" size={14} />{s}</li>)}</ul>
      {env.supportedNote && <p className="note">{env.supportedNote}</p>}
    </section>}
    {env.limits.length > 0 && <section aria-labelledby="gr-limits">
      <h3 id="gr-limits"><Icon name="info" size={18} />Good to know</h3>
      <ul className="limits">{env.limits.map((l, i) => <li key={i}><span className="limit-icon"><Icon name={l.icon} size={18} /></span>
        <p>{l.lead && <strong>{l.lead}. </strong>}{l.rest}</p></li>)}</ul>
    </section>}
    {(env.handling.length > 0 || env.wont.length > 0) && <section aria-labelledby="gr-how">
      <h3 id="gr-how"><Icon name="link" size={18} />How your footage is handled</h3>
      {env.handling.length > 0 && <ul className="limits">{env.handling.map((h, i) => <li key={i}><span className="limit-icon ok"><Icon name={h.icon} size={18} /></span><p>{h.text}</p></li>)}</ul>}
      {env.wont.length > 0 && <><p className="note">Never done:</p>
        <ul className="chips">{env.wont.map((w, i) => <li key={i} className="tag tag-no"><Icon name="ban" size={14} />{w.charAt(0).toUpperCase() + w.slice(1)}</li>)}</ul></>}
    </section>}
    {env.notes.length > 0 && <ul className="limits">{env.notes.map((n, i) => <li key={i}><span className="limit-icon"><Icon name="info" size={18} /></span><p>{n}</p></li>)}</ul>}
  </div>;
}
