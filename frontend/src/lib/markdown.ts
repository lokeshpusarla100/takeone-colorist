/**
 * Just enough Markdown for a direction someone typed or pasted: headings, bullet lists, paragraphs and **bold**.
 * The result is data, not HTML, so nothing typed can inject markup; anything else stays plain text.
 */
export type Inline = { text: string; bold: boolean }[];
export type Block =
  | { kind: 'heading'; level: 1 | 2 | 3; inline: Inline }
  | { kind: 'paragraph'; inline: Inline }
  | { kind: 'list'; items: Inline[] };

export function inline(text: string): Inline {
  const parts: Inline = [];
  const re = /\*\*(.+?)\*\*/g;
  let last = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) parts.push({ text: text.slice(last, m.index), bold: false });
    parts.push({ text: m[1], bold: true });
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push({ text: text.slice(last), bold: false });
  return parts.length ? parts : [{ text: '', bold: false }];
}

export function parseMarkdown(source: string): Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let list: Inline[] | null = null;
  const flush = () => {
    if (paragraph.length) blocks.push({ kind: 'paragraph', inline: inline(paragraph.join(' ')) });
    if (list) blocks.push({ kind: 'list', items: list });
    paragraph = []; list = null;
  };
  for (const line of source.replace(/\r\n?/g, '\n').split('\n')) {
    const trimmed = line.trim();
    const heading = /^(#{1,6})\s+(.+?)\s*#*$/.exec(trimmed);
    const bullet = /^[-*•]\s+(.+)$/.exec(trimmed);
    if (!trimmed) flush();
    else if (heading) { flush(); blocks.push({ kind: 'heading', level: Math.min(3, heading[1].length) as 1 | 2 | 3, inline: inline(heading[2]) }); }
    else if (bullet) { if (!list) { flush(); list = []; } list.push(inline(bullet[1])); }
    else { if (list) flush(); paragraph.push(trimmed); }
  }
  flush();
  return blocks;
}

/** What the plain text of a block list says, for a one-line summary. */
export function plainText(blocks: Block[]): string {
  const join = (i: Inline) => i.map(p => p.text).join('');
  return blocks.map(b => b.kind === 'list' ? b.items.map(join).join('; ') : join(b.inline)).join(' ');
}
