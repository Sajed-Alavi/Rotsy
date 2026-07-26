import { formatBytes, formatNumber } from '../lib/format.js';

/**
 * Compact stat tile, theme-aware. Optional sparkline renders a tiny inline
 * trend strip (no chart library) — pass `series={[num, num, ...]}`.
 *
 * @param {object} props
 * @param {string} props.label
 * @param {number} [props.bytes]
 * @param {string} [props.value]
 * @param {number} [props.count]
 * @param {string} [props.sub]
 * @param {('neutral'|'ok'|'warn'|'bad'|'info')} [props.tone='neutral']
 * @param {number[]} [props.series]  optional sparkline data
 */
const TONES = {
  neutral: 'text-slate-900 dark:text-slate-100',
  ok: 'text-emerald-600 dark:text-emerald-400',
  warn: 'text-amber-600 dark:text-amber-400',
  bad: 'text-rose-600 dark:text-rose-400',
  info: 'text-sky-600 dark:text-sky-400',
};

function Sparkline({ data }) {
  if (!data || data.length < 2) return null;
  const W = 100, H = 24;
  const lo = Math.min(...data);
  const hi = Math.max(...data, lo + 1);
  const span = hi - lo || 1;
  const pts = data.map((v, i) => {
    const x = data.length === 1 ? W / 2 : (i / (data.length - 1)) * W;
    const y = H - ((v - lo) / span) * H;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="mt-1 h-6 w-full">
      <polyline points={pts.join(' ')} fill="none" stroke="currentColor" strokeWidth="1.5" className="text-sky-500" />
    </svg>
  );
}

export default function Stat({ label, bytes, value, count, sub, tone = 'neutral', series }) {
  let display = value;
  if (display === undefined) {
    if (bytes !== undefined) display = formatBytes(bytes);
    else if (count !== undefined) display = formatNumber(count);
    else display = '—';
  }
  return (
    <div className="border border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900/40">
      <div className="font-mono text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className={`mt-1 font-mono text-xl tabular-nums ${TONES[tone]}`}>{display}</div>
      {sub && <div className="mt-0.5 font-mono text-[10px] text-slate-400 dark:text-slate-600">{sub}</div>}
      {series && <Sparkline data={series} />}
    </div>
  );
}
