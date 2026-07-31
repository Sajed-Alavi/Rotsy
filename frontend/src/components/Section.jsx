/**
 * Bordered panel with a monospace uppercase heading and an optional right-hand
 * action slot.
 *
 * This exact shape — `<h2 className="font-mono text-[10px] uppercase tracking-wider
 * text-slate-500">` over a bordered box — was repeated by hand in the settings,
 * system, scan, dashboard, metrics and storage pages. Extracting it means the
 * heading style is defined once.
 *
 * `flush` drops the inner padding for panels whose child is a full-bleed table.
 */
export default function Section({ title, actions, hint, flush = false, className = '', children }) {
  return (
    <section className={`mb-6 ${className}`}>
      {(title || actions) && (
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-baseline gap-2">
            {title && (
              <h2 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">{title}</h2>
            )}
            {hint && (
              <span className="font-mono text-[10px] text-slate-400 dark:text-slate-600">{hint}</span>
            )}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className={flush ? '' : 'border border-slate-200 p-4 dark:border-slate-800'}>{children}</div>
    </section>
  );
}
