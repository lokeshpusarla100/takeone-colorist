import { useState } from 'react';
import { parseMarkdown } from '../lib/markdown';
import type { Inline } from '../lib/markdown';

const spans = (inline: Inline) => inline.map((part, i) => part.bold ? <strong key={i}>{part.text}</strong> : <span key={i}>{part.text}</span>);

/** Shows the direction the way it was meant to read (headings, bold, bullets) instead of as raw `##` and `**`. */
export default function RichText({ source, collapsedHeight = 260 }: { source: string; collapsedHeight?: number }) {
  const [open, setOpen] = useState(false);
  const blocks = parseMarkdown(source);
  const long = source.length > 700;
  if (!blocks.length) return null;
  return <div className="rich">
    <div className={'rich-body' + (long && !open ? ' clamped' : '')} style={long && !open ? { maxHeight: collapsedHeight } : undefined}>
      {blocks.map((block, i) => block.kind === 'heading'
        ? <p key={i} className={'rich-h rich-h' + block.level}>{spans(block.inline)}</p>
        : block.kind === 'list'
          ? <ul key={i}>{block.items.map((item, j) => <li key={j}>{spans(item)}</li>)}</ul>
          : <p key={i}>{spans(block.inline)}</p>)}
    </div>
    {long && <button type="button" className="link-button" aria-expanded={open} onClick={() => setOpen(o => !o)}>{open ? 'Show less' : 'Show the full direction'}</button>}
  </div>;
}
