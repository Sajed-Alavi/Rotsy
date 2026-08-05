import { useState } from 'react';
import Badge from '../../../components/Badge.jsx';
import Icon from '../../../components/Icon.jsx';

/**
 * One integration's summary card: name, status badge, a few key facts, and an
 * optional "Configure" disclosure for the advanced form. This is the shape
 * every card on Settings -> Integrations uses (Nexus, GitHub, SonarQube, ...)
 * so a new integration card is a `facts` array and a status, not new layout.
 *
 * `facts` is `[{ label, value }]` — keep this short (3-4 items). Anything
 * more belongs behind Configure, not on the card face.
 */
export default function IntegrationCard({
  name, description, status, facts = [], onTest, testing, children,
}) {
  const [open, setOpen] = useState(false);

  return (
    <section className="border border-slate-200 dark:border-slate-800">
      <div className="flex items-center justify-between p-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">{name}</h2>
            {status && <Badge tone={status.tone}>{status.label}</Badge>}
          </div>
          {description && (
            <p className="mt-1 font-mono text-[11px] text-slate-500 dark:text-slate-500">{description}</p>
          )}
        </div>
      </div>

      {facts.length > 0 && (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 border-t border-slate-100 px-4 py-3 dark:border-slate-800/60 sm:grid-cols-4">
          {facts.map((f) => (
            <div key={f.label}>
              <dt className="font-mono text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-600">{f.label}</dt>
              <dd className="mt-0.5 font-mono text-xs text-slate-700 dark:text-slate-300">{f.value ?? '—'}</dd>
            </div>
          ))}
        </dl>
      )}

      {(children || onTest) && (
        <div className="flex gap-2 border-t border-slate-100 px-4 py-2.5 dark:border-slate-800/60">
          {children && (
            <button
              onClick={() => setOpen((v) => !v)}
              className="flex items-center gap-1.5 border border-slate-300 px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              <Icon name="chevron" size={11} className={open ? 'rotate-90' : ''} /> Configure
            </button>
          )}
          {onTest && (
            <button
              onClick={onTest}
              disabled={testing}
              className="flex items-center gap-1.5 border border-slate-300 px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider text-slate-600 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              <Icon name="refresh" size={11} className={testing ? 'animate-spin' : ''} /> Test Connection
            </button>
          )}
        </div>
      )}

      {open && children && (
        <div className="border-t border-slate-100 p-4 dark:border-slate-800/60">{children}</div>
      )}
    </section>
  );
}
