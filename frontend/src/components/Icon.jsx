/**
 * Inline SVG icon set — single source so the sidebar/components don't pull in
 * a heavyweight icon library. Stroke-based, 16px, currentColor. Deliberately
 * minimal line icons to match a "console/tooling" aesthetic.
 *
 * Each value is an array of <path> `d` strings.
 */
const ICONS = {
  grid: ['M3 3h7v7H3z', 'M14 3h7v7h-7z', 'M14 14h7v7h-7z', 'M3 14h7v7H3z'],
  hdd: ['M4 4h16v12H4z', 'M7 20h10', 'M9 8h6', 'M9 11h6'],
  folder: ['M3 5h6l2 2h10v12H3z'],
  database: ['M4 5c0 1.7 3.6 3 8 3s8-1.3 8-3-3.6-3-8-3-8 1.3-8 3z', 'M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5', 'M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3'],
  trash: ['M4 7h16', 'M9 7V4h6v3', 'M6 7l1 13h10l1-13'],
  shield: ['M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z'],
  'shield-check': ['M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z', 'M9 12l2 2 4-4'],
  server: ['M4 4h16v6H4z', 'M4 14h16v6H4z', 'M8 7h.01', 'M8 17h.01'],
  key: ['M14 7a4 4 0 1 1-5.6 5.6L4 17v3h3', 'M15 9l5 5'],
  chart: ['M4 20V4', 'M4 20h16', 'M8 16v-4', 'M12 16V8', 'M16 16v-7'],
  users: ['M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6z', 'M3 20c0-3 2.7-5 6-5s6 2 6 5', 'M16 7a3 3 0 0 1 0 6', 'M18 20c0-2-.5-3.5-1.5-4.5'],
  logout: ['M14 4h5v16h-5', 'M10 12h9', 'M16 9l3 3-3 3'],
  refresh: ['M4 4v6h6', 'M20 20v-6h-6', 'M5 14a8 8 0 0 0 14 1', 'M19 10A8 8 0 0 0 5 9'],
  search: ['M4 11a7 7 0 1 1 14 0 7 7 0 0 1-14 0z', 'M17 17l3 3'],
  chevron: ['M9 6l6 6-6 6'],
  play: ['M6 4l14 8-14 8z'],
  alert: ['M12 3l9 16H3z', 'M12 10v4', 'M12 17h.01'],
  check: ['M5 12l5 5 9-9'],
  plus: ['M12 5v14', 'M5 12h14'],
  x: ['M6 6l12 12', 'M18 6L6 18'],
  sun: ['M12 4V2', 'M12 22v-2', 'M4 12H2', 'M22 12h-2', 'M5.6 5.6L4.2 4.2', 'M19.8 19.8l-1.4-1.4', 'M18.4 5.6l1.4-1.4', 'M4.2 19.8l1.4-1.4', 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z'],
  moon: ['M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z'],
  download: ['M12 4v12', 'M7 11l5 5 5-5', 'M4 20h16'],
  file: ['M6 2h8l4 4v16H6z', 'M14 2v4h4'],
  copy: ['M8 8h12v12H8z', 'M4 4h12v4', 'M4 4v12h4'],
  bug: ['M12 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6z', 'M12 8v4', 'M8 16a4 4 0 0 0 8 0', 'M4 12h4', 'M16 12h4', 'M5 19l3-3', 'M19 19l-3-3'],
  book: ['M4 4h7a2 2 0 0 1 2 2v14a2 2 0 0 0-2-2H4z', 'M20 4h-7a2 2 0 0 0-2 2v14a2 2 0 0 1 2-2h7z'],
  stop: ['M6 6h12v12H6z'],
  clock: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z', 'M12 7v5l3 2'],
  github: [
    'M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48v-1.7c-2.78.6-3.37-1.34-3.37-1.34-.46-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.9 1.52 2.34 1.08 2.91.83.09-.65.35-1.08.63-1.33-2.22-.25-4.56-1.11-4.56-4.94 0-1.09.39-1.98 1.03-2.68-.1-.25-.45-1.27.1-2.65 0 0 .84-.27 2.75 1.02a9.6 9.6 0 0 1 5 0c1.91-1.29 2.75-1.02 2.75-1.02.55 1.38.2 2.4.1 2.65.64.7 1.03 1.59 1.03 2.68 0 3.84-2.34 4.68-4.57 4.93.36.31.68.92.68 1.85v2.75c0 .26.18.58.69.48A10 10 0 0 0 12 2z',
  ],
};

// Logo marks (as opposed to the line-icon set above) are authored as solid
// silhouettes, not open strokes — rendering one with stroke="currentColor"
// draws every self-intersection in the path instead of a clean glyph.
const FILLED_ICONS = new Set(['github']);

export default function Icon({ name, size = 16, className = '' }) {
  const paths = ICONS[name];
  if (!paths) return null;
  const filled = FILLED_ICONS.has(name);
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={filled ? 'currentColor' : 'none'}
      stroke={filled ? 'none' : 'currentColor'}
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      {paths.map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}
