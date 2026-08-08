/**
 * Compact ranked horizontal bar list — no chart library, same hand-rolled
 * approach as TimeSeriesChart/Stat's sparkline. Good for "top N by size"
 * style at-a-glance views (repos by storage, formats by count, ...).
 *
 * @param {object} props
 * @param {Array<{label: string, value: number, sub?: string}>} props.items
 * @param {(n: number) => string} [props.formatValue]  defaults to String(n)
 * @param {number} [props.limit=5]
 */
export default function RankedBarList({ items, formatValue = String, limit = 5 }) {
  const sorted = [...(items || [])].sort((a, b) => b.value - a.value).slice(0, limit);
  const max = Math.max(1, ...sorted.map((it) => it.value));

  if (sorted.length === 0) {
    return <div className="py-6 text-center font-mono text-xs text-slate-400 dark:text-slate-600">no data</div>;
  }

  return (
    <div className="space-y-2">
      {sorted.map((it) => (
        <div key={it.label}>
          <div className="mb-0.5 flex items-baseline justify-between gap-2 font-mono text-xs">
            <span className="truncate text-slate-700 dark:text-slate-300" title={it.label}>{it.label}</span>
            <span className="shrink-0 tabular-nums text-slate-500 dark:text-slate-400">
              {formatValue(it.value)}{it.sub ? ` · ${it.sub}` : ''}
            </span>
          </div>
          <div className="h-1.5 bg-slate-100 dark:bg-slate-800">
            <div className="h-full bg-sky-500" style={{ width: `${Math.max(2, (it.value / max) * 100)}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}
