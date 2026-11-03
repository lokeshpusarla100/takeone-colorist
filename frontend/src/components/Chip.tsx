import type { ReactNode } from 'react';
import Icon from './Icon';
import type { IconName } from './Icon';

export type Tone = 'ok' | 'warn' | 'bad' | 'info' | 'accent' | 'muted';
/** A short state label. Colour is never the only signal: it always carries words, and often an icon. */
export default function Chip({ tone = 'muted', icon, children, title, className = '' }: {
  tone?: Tone; icon?: IconName; children: ReactNode; title?: string; className?: string;
}) {
  return <span className={`chip chip-${tone} ${className}`} title={title}>{icon && <Icon name={icon} size={14} />}{children}</span>;
}
