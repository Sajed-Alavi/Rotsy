/**
 * The critical/high/medium/low count cell.
 *
 * The same four spans and the same rose→amber→sky→slate ladder appeared
 * verbatim in the images table and the reports table; they now share one
 * definition so the colours cannot drift apart.
 */
export default function SeverityCounts({ counts, placeholder = '—' }) {
  if (!counts) {
    return <span className="text-slate-400 dark:text-slate-600">{placeholder}</span>;
  }
  return (
    <span className="font-mono text-xs tabular-nums">
      <span className="text-rose-600 dark:text-rose-400">{counts.critical}</span>/
      <span className="text-amber-600 dark:text-amber-400">{counts.high}</span>/
      <span className="text-sky-600 dark:text-sky-400">{counts.medium}</span>/
      <span className="text-slate-500">{counts.low}</span>
    </span>
  );
}
