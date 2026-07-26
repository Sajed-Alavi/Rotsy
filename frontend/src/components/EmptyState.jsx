import Icon from './Icon.jsx';

/** Placeholder panel for scaffolded features, theme-aware. */
export default function EmptyState({ title, description, points = [], icon = 'chart' }) {
  return (
    <div className="p-8">
      <div className="mx-auto max-w-2xl border border-dashed border-slate-300 bg-white p-10 text-center dark:border-slate-800 dark:bg-slate-900/40">
        <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded border border-slate-200 text-slate-400 dark:border-slate-700 dark:text-slate-500">
          <Icon name={icon} size={18} />
        </div>
        <h1 className="text-base font-medium text-slate-800 dark:text-slate-200">{title}</h1>
        <p className="mx-auto mt-1.5 max-w-md text-xs leading-relaxed text-slate-500 dark:text-slate-500">{description}</p>
        {points.length > 0 && (
          <div className="mx-auto mt-5 max-w-sm text-left">
            <div className="mb-2 font-mono text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-600">Planned</div>
            <ul className="space-y-1">
              {points.map((p) => (
                <li key={p} className="font-mono text-[11px] text-slate-500 dark:text-slate-400">
                  · {p}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
