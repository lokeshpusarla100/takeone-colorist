import { colorNotice, parseDomain } from './sourceColor';
import type { BackendState } from '../../lib/api';
import Callout from '../../components/Callout';
import Chip from '../../components/Chip';

/** Under the video: what Take One does with this footage's colors, or why it can't be graded, in plain words. */
export default function SourceNotice({ source }: { source?: BackendState['source'] }) {
  const notice = colorNotice(parseDomain(source?.domain));
  if (!notice) return null;
  return <Callout tone={notice.tone} title={notice.title}>
    <p>{notice.lead}</p>
    {notice.chips.length > 0 && <ul className="chips" aria-label="What is different about this video">{notice.chips.map(c => <li key={c}><Chip tone={notice.tone === 'bad' ? 'bad' : 'muted'}>{c}</Chip></li>)}</ul>}
    {notice.action && <p className="help"><strong>What to do: </strong>{notice.action}</p>}
    {notice.steps.length > 0 && <details><summary>What happens to your video</summary><ul className="plain-list">{notice.steps.map((s, i) => <li key={i}>{s}</li>)}</ul></details>}
  </Callout>;
}
