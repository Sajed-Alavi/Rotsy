import { Link } from 'react-router';
import Stat from '../../../components/Stat.jsx';
import Badge from '../../../components/Badge.jsx';
import Section from '../../../components/Section.jsx';
import { formatNumber } from '../../../lib/format.js';
import { scanApi } from '../api.js';
import { useResource } from '../../../lib/useResource.js';

const EMPTY_TOTALS = { critical: 0, high: 0, medium: 0, low: 0, unknown: 0, scanned_images: 0, failed: 0 };

/**
 * The section's landing page: severity totals, what the ledger knows, and
 * whether the databases are usable — each with a link to the page that acts on
 * it. Read-only by design; every mutation lives on its own page.
 */
export default function OverviewPage() {
  const { data: summary } = useResource(() => scanApi.summary(), null);
  const { data: dbStatus } = useResource(() => scanApi.dbStatus(), null);

  const totals = summary?.totals || EMPTY_TOTALS;
  const ledger = summary?.ledger;

  return (
    <>
      <Section title="Findings by severity" flush>
        <div className="grid grid-cols-2 gap-px border border-slate-200 bg-slate-200 sm:grid-cols-3 lg:grid-cols-6 dark:border-slate-800 dark:bg-slate-800">
          <Stat label="Critical" count={totals.critical} tone="bad" />
          <Stat label="High" count={totals.high} tone="warn" />
          <Stat label="Medium" count={totals.medium} tone="info" />
          <Stat label="Low" count={totals.low} />
          <Stat label="Unknown" count={totals.unknown} />
          <Stat label="Scanned" count={totals.scanned_images} sub="images" />
        </div>
      </Section>

      <Section
        title="Image ledger"
        hint="history is never scanned automatically"
        actions={<Link to="/scan/images" className="border border-slate-300 px-2.5 py-1 font-mono text-[11px] uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">View images</Link>}
      >
        {ledger ? (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <LedgerCell label="Scanned" value={ledger.scanned} tone="ok" />
            <LedgerCell label="Baseline" value={ledger.baseline} hint="present before scanning was enabled" />
            <LedgerCell label="Queued" value={ledger.queued} tone="info" />
            <LedgerCell label="Failed" value={ledger.failed} tone={ledger.failed ? 'bad' : 'neutral'} />
          </div>
        ) : (
          <p className="font-mono text-xs text-slate-400 dark:text-slate-600">no ledger data yet</p>
        )}
      </Section>

      <Section
        title="Scanner databases"
        actions={<Link to="/scan/database" className="border border-slate-300 px-2.5 py-1 font-mono text-[11px] uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">Manage</Link>}
      >
        <div className="flex flex-wrap gap-6">
          {['trivy', 'grype'].map((name) => {
            const info = dbStatus?.[name];
            let tone = 'ok';
            let label = 'ready';
            if (!info?.installed) { tone = 'bad'; label = 'not installed'; }
            else if (!info.present) { tone = 'bad'; label = 'no database'; }
            else if (info.stale) { tone = 'warn'; label = 'stale'; }
            return (
              <div key={name} className="flex items-center gap-2">
                <span className="font-mono text-xs text-slate-700 dark:text-slate-300">{name}</span>
                <Badge tone={tone}>{label}</Badge>
                {info?.version && <span className="font-mono text-[10px] text-slate-400 dark:text-slate-600">v{info.version}</span>}
              </div>
            );
          })}
        </div>
      </Section>
    </>
  );
}

function LedgerCell({ label, value, tone = 'neutral', hint }) {
  return (
    <div>
      <div className="flex items-center gap-2">
        <span className="font-mono tabular-nums text-lg text-slate-800 dark:text-slate-200">{formatNumber(value || 0)}</span>
        <Badge tone={tone}>{label}</Badge>
      </div>
      {hint && <p className="mt-1 font-mono text-[10px] text-slate-400 dark:text-slate-600">{hint}</p>}
    </div>
  );
}
