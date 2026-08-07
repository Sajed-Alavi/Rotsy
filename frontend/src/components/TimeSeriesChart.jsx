import { useMemo } from 'react';
import { formatBytes, formatNumber } from '../lib/format.js';

/**
 * Lightweight inline-SVG time-series chart — no chart library dependency.
 *
 * @param {object} props
 * @param {Array<{timestamp: string, [key: string]: any}>} props.data
 * @param {string} props.valueKey   key in each point whose value to plot (e.g. 'total_bytes')
 * @param {'bytes'|'number'} [props.kind='bytes'] how to format the y-axis / tooltip
 * @param {number} [props.height=160]
 */
export default function TimeSeriesChart({ data, valueKey, kind = 'bytes', height = 160 }) {
  const { points, minY, maxY, ticks } = useMemo(() => {
    if (!data || data.length === 0) {
      return { points: [], minY: 0, maxY: 1, ticks: [0, 1] };
    }
    const vals = data.map((d) => Number(d[valueKey]) || 0);
    const lo = Math.min(...vals, 0);
    const hi = Math.max(...vals, 1);
    const pad = (hi - lo) * 0.1 || 1;
    const min = Math.max(0, lo - pad);
    const max = hi + pad;
    const W = 100; // viewBox width (normalized)
    const H = 100;
    const span = max - min || 1;
    const pts = data.map((d, i) => {
      const x = data.length === 1 ? W / 2 : (i / (data.length - 1)) * W;
      const y = H - ((Number(d[valueKey]) || 0) - min) / span * H;
      return { x, y, raw: d };
    });
    const t = [min, (min + max) / 2, max];
    return { points: pts, minY: min, maxY: max, ticks: t };
  }, [data, valueKey]);

  if (!points.length) {
    return (
      <div className="flex h-40 items-center justify-center border border-slate-200 font-mono text-xs text-slate-400 dark:border-slate-800 dark:text-slate-600">
        no data yet
      </div>
    );
  }

  const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' ');
  const area = `${path} L100,100 L0,100 Z`;
  const fmt = (v) => (kind === 'bytes' ? formatBytes(v) : formatNumber(v));

  return (
    <div className="border border-slate-200 p-3 dark:border-slate-800">
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ height }} className="w-full">
        {/* gridlines */}
        {ticks.map((t) => {
          const y = 100 - ((t - minY) / (maxY - minY || 1)) * 100;
          return (
            <line key={t} x1="0" x2="100" y1={y} y2={y}
              stroke="currentColor" strokeWidth="0.3" className="text-slate-200 dark:text-slate-800" />
          );
        })}
        <defs>
          <linearGradient id="tsc-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="currentColor" stopOpacity="0.25" />
            <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={area} fill="url(#tsc-fill)" className="text-sky-500" />
        <path d={path} fill="none" stroke="currentColor" strokeWidth="1" className="text-sky-500" />
        {points.map((p) => (
          <circle key={p.raw.timestamp} cx={p.x} cy={p.y} r="0.8" fill="currentColor" className="text-sky-500">
            <title>{`${new Date(p.raw.timestamp).toLocaleString()}: ${fmt(Number(p.raw[valueKey]) || 0)}`}</title>
          </circle>
        ))}
      </svg>
      <div className="mt-1 flex justify-between font-mono text-[10px] text-slate-400 dark:text-slate-600">
        <span>{fmt(maxY)}</span>
        <span>{fmt(minY)}</span>
      </div>
    </div>
  );
}
