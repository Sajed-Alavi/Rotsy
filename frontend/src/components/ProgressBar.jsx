import { formatBytes } from '../lib/format.js';

/**
 * Horizontal bar showing used vs free of a total. Theme-aware.
 *
 * @param {object} props
 * @param {number} props.used
 * @param {number} props.total
 * @param {string} [props.label]
 * @param {('ok'|'warn'|'bad')} [props.tone]  auto from ratio if omitted
 */
function toneFor(pct) {
  if (pct >= 90) return 'bad';
  if (pct >= 75) return 'warn';
  return 'ok';
}

const FILLS = {
  ok: 'bg-emerald-500',
  warn: 'bg-amber-500',
  bad: 'bg-rose-500',
};

export default function ProgressBar({ used, total, label, tone }) {
  const pct = total > 0 ? Math.min(100, (used / total) * 100) : 0;
  const t = tone || toneFor(pct);
  return (
    <div>
      {label && (
        <div className="mb-1 flex items-baseline justify-between font-mono text-[11px]">
          <span className="text-slate-700 dark:text-slate-300">{label}</span>
          <span className="tabular-nums text-slate-500 dark:text-slate-400">
            {formatBytes(used)} <span className="text-slate-400 dark:text-slate-600">/ {formatBytes(total)}</span>
            <span className="ml-1.5 text-slate-400 dark:text-slate-600">({pct.toFixed(1)}%)</span>
          </span>
        </div>
      )}
      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
        <div className={`h-full rounded-full transition-all duration-500 ${FILLS[t]}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
