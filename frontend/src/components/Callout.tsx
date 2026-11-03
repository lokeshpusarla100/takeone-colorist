import type { ReactNode } from 'react';
import Icon from './Icon';
import type { IconName } from './Icon';

const ICON: Record<'bad' | 'warn' | 'info' | 'ok', IconName> = { bad: 'alert', warn: 'alert', info: 'info', ok: 'check' };
/** A message with an icon and words, so its meaning never depends on colour alone. */
export default function Callout({ tone = 'info', title, children, actions }: { tone?: 'bad' | 'warn' | 'info' | 'ok'; title?: string; children?: ReactNode; actions?: ReactNode }) {
  return <div className={'callout callout-' + tone} role={tone === 'bad' ? 'alert' : 'status'}>
    <Icon name={ICON[tone]} size={20} />
    <div className="callout-body">{title && <strong>{title}</strong>}{children}{actions && <div className="actions">{actions}</div>}</div>
  </div>;
}
