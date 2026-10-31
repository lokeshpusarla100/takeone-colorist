import type { CSSProperties } from 'react';

const paths = {
  upload: 'M12 16V3m-5 5 5-5 5 5M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5',
  download: 'M12 3v13m-5-5 5 5 5-5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4',
  film: 'M4 4h16v16H4zM8 4v16m8-16v16M4 9h4m-4 6h4m8-6h4m-4 6h4',
  sliders: 'M4 7h7m4 0h5M4 17h3m4 0h9M11 4v6M7 14v6',
  clock: 'M21 12a9 9 0 1 1-9-9 9 9 0 0 1 9 9M12 7v5l3 2',
  log: 'M8 6h12M8 12h12M8 18h8M4 6h.01M4 12h.01M4 18h.01',
  play: 'm8 5 11 7-11 7z',
  pause: 'M8 5v14m8-14v14',
  rewind: 'M5 5v14M19 5 7 12l12 7z',
  volume: 'M11 4 6 8H3v8h3l5 4zM15 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14',
  mute: 'M11 4 6 8H3v8h3l5 4zM16 9l6 6m0-6-6 6',
  expand: 'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5',
  compare: 'M12 3v18M8 5H4v14h4m8-14h4v14h-4',
  plus: 'M12 5v14M5 12h14',
  chevron: 'm6 9 6 6 6-6',
  arrow: 'M5 12h14m-6-6 6 6-6 6',
  check: 'm5 12 4 4L19 6',
  reset: 'M3 10a9 9 0 1 1 2 8M3 4v6h6',
  close: 'm6 6 12 12M18 6 6 18',
  info: 'M12 11v6m0-10h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  spark: 'm12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z',
  search: 'M16 16l5 5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  folder: 'M3 7V4h6l3 3h9v13H3z',
  loop: 'm17 2 4 4-4 4M3 11V8a2 2 0 0 1 2-2h16M7 22l-4-4 4-4m14-1v3a2 2 0 0 1-2 2H3',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12m13 0a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
  save: 'M5 3h12l4 4v14H3V3zM7 3v6h10V3M7 21v-8h10v8',
  sun: 'M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5M17 12a5 5 0 1 1-10 0 5 5 0 0 1 10 0',
  contrast: 'M12 3a9 9 0 0 1 0 18V3m9 9a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  droplet: 'M12 3s7 7.5 7 12a7 7 0 0 1-14 0c0-4.5 7-12 7-12z',
  warning: 'm12 3 10 18H2zM12 9v5m0 3h.01',
  keyboard: 'M2 5h20v14H2zM6 9h.01M10 9h.01M14 9h.01M18 9h.01M6 13h.01M10 13h.01M14 13h.01M18 13h.01M8 16h8',
} as const;

export type IconName = keyof typeof paths;

export default function Icon({ name, size = 18, className, style }: {
  name: IconName; size?: number; className?: string; style?: CSSProperties;
}) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true" className={className} style={style}>
      <path d={paths[name]} />
    </svg>
  );
}
