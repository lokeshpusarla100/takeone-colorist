import { useId } from 'react';
import type { ReactNode } from 'react';
import Chip from './Chip';
import type { Tone } from './Chip';
import type { IconName } from './Icon';

/** One titled section of the editor. The number says where it sits in the flow; the chip says where it stands. */
export default function Card({ n, title, status, actions, children, className = '', id, icon, label }: {
  n?: number; title: string; status?: { tone: Tone; label: string; icon?: IconName }; actions?: ReactNode;
  children: ReactNode; className?: string; id?: string; icon?: ReactNode; label?: string;
}) {
  const headingId = useId();
  return <section className={'card ' + className} aria-labelledby={label ? undefined : headingId} aria-label={label} id={id}>
    <header className="card-head">
      {n !== undefined ? <span className="card-num" aria-hidden="true">{n}</span> : icon}
      <h2 id={headingId}>{title}</h2>
      {status && <Chip tone={status.tone} icon={status.icon}>{status.label}</Chip>}
      {actions && <div className="card-actions">{actions}</div>}
    </header>
    <div className="card-body">{children}</div>
  </section>;
}
