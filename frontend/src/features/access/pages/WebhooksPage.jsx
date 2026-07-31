import { Link } from 'react-router';
import Badge from '../../../components/Badge.jsx';
import Section from '../../../components/Section.jsx';
import { useResource } from '../../scan/hooks/useResource.js';
import { accessApi } from '../api.js';

/**
 * Every webhook this app takes part in, in one place.
 *
 * They exist and work; they were just impossible to see together. The inbound
 * Nexus push hook was configured under Settings, outbound alert delivery under
 * Alerts, and nothing answered "what is wired up?". This page is an index —
 * each entry links to the page that actually owns the setting rather than
 * duplicating its controls.
 */
export default function WebhooksPage() {
  const { data, loading } = useResource(() => accessApi.webhooks(), null);

  return (
    <>
      <Section title="Inbound" hint="things that call us" flush>
        <div className="border border-slate-200 dark:border-slate-800">
          {loading && <p className="p-4 font-mono text-xs text-slate-400 dark:text-slate-600">loading…</p>}
          {(data?.inbound || []).map((w) => (
            <div key={w.name} className="border-b border-slate-100 p-4 last:border-0 dark:border-slate-800/60">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm text-slate-800 dark:text-slate-200">{w.name}</span>
                <Badge tone={w.configured ? 'ok' : 'warn'}>{w.configured ? 'secret set' : 'not configured'}</Badge>
              </div>
              <p className="mb-2 font-mono text-[11px] text-slate-600 dark:text-slate-400">{w.purpose}</p>
              <dl className="grid grid-cols-1 gap-1 font-mono text-[11px] sm:grid-cols-[7rem_1fr]">
                <dt className="text-slate-400 dark:text-slate-600">endpoint</dt>
                <dd className="text-slate-700 dark:text-slate-300">{w.path}</dd>
                <dt className="text-slate-400 dark:text-slate-600">auth</dt>
                <dd className="text-slate-700 dark:text-slate-300">{w.auth}</dd>
                <dt className="text-slate-400 dark:text-slate-600">configure in</dt>
                <dd className="text-slate-700 dark:text-slate-300">{w.setup_hint}</dd>
              </dl>
              {w.manage_at && (
                <Link to={w.manage_at} className="mt-2 inline-block border border-slate-300 px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
                  Manage secret
                </Link>
              )}
            </div>
          ))}
        </div>
      </Section>

      <Section title="Outbound" hint="things we call" flush>
        <div className="border border-slate-200 dark:border-slate-800">
          {(data?.outbound || []).map((w) => (
            <div key={w.name} className="border-b border-slate-100 p-4 last:border-0 dark:border-slate-800/60">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm text-slate-800 dark:text-slate-200">{w.name}</span>
                {w.configured_per_rule && <Badge tone="info">configured per rule</Badge>}
              </div>
              <p className="mb-2 font-mono text-[11px] text-slate-600 dark:text-slate-400">{w.purpose}</p>
              {w.note && (
                <p className="mb-2 border-l-2 border-emerald-300 pl-2 font-mono text-[10px] text-slate-500 dark:border-emerald-800 dark:text-slate-400">
                  {w.note}
                </p>
              )}
              {w.manage_at && (
                <Link to={w.manage_at} className="inline-block border border-slate-300 px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
                  Manage alert rules
                </Link>
              )}
            </div>
          ))}
        </div>
      </Section>
    </>
  );
}
