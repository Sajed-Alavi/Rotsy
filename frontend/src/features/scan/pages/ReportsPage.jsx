import { useState } from 'react';
import Badge from '../../../components/Badge.jsx';
import DataTable from '../../../components/DataTable.jsx';
import Notice from '../../../components/Notice.jsx';
import Section from '../../../components/Section.jsx';
import { formatDateTime } from '../../../lib/format.js';
import { scanApi } from '../api.js';
import SeverityCounts from '../../../components/SeverityCounts.jsx';
import { useResource, useStatus } from '../../../lib/useResource.js';
import ReportDetailModal from '../components/ReportDetailModal.jsx';

const REPORT_STATUS_TONE = { success: 'ok', failed: 'bad' };

/** One row per scanner run. Clicking a row opens that run's CVE list. */
export default function ReportsPage() {
  const { data: reports, loading, reload } = useResource(() => scanApi.reports(), []);
  const { status, say, fail, clear } = useStatus();
  const [detailReport, setDetailReport] = useState(null);

  const remove = async (id) => {
    try {
      await scanApi.deleteReport(id);
      say('Report deleted.', 'ok');
      reload();
    } catch (e) { fail(`delete failed: ${e.message}`); }
  };

  const clearAll = async () => {
    if (!confirm('Delete ALL scan reports? This cannot be undone.')) return;
    try {
      await scanApi.deleteAllReports();
      say('All reports cleared.', 'ok');
      reload();
    } catch (e) { fail(`clear failed: ${e.message}`); }
  };

  const columns = [
    { key: 'target_repo', header: 'Repo', render: (v) => <span className="font-mono text-xs text-slate-700 dark:text-slate-300">{v}</span> },
    {
      key: 'image',
      header: 'Image',
      render: (v, row) => (
        <>
          <span className="font-mono text-xs text-slate-800 dark:text-slate-200">{v}</span>
          {row.error && <span className="block text-[10px] text-rose-600 dark:text-rose-400">{row.error}</span>}
        </>
      ),
    },
    { key: 'scanner', header: 'Scanner', render: (v) => <span className="font-mono text-xs text-slate-500 dark:text-slate-400">{v}</span> },
    {
      key: 'critical',
      header: 'C/H/M/L',
      headClassName: 'text-center',
      className: 'text-center',
      render: (_v, row) => <SeverityCounts counts={row} />,
    },
    { key: 'status', header: 'Status', render: (v) => <Badge tone={REPORT_STATUS_TONE[v] || 'info'}>{v}</Badge> },
    { key: 'started_at', header: 'When', render: (v) => <span className="font-mono text-xs text-slate-400 dark:text-slate-600">{formatDateTime(v)}</span> },
    {
      key: 'id',
      header: '·',
      headClassName: 'text-right',
      className: 'text-right',
      render: (v) => (
        <div className="flex items-center justify-end gap-1">
          <button onClick={(e) => { e.stopPropagation(); setDetailReport(v); }} className="border border-slate-200 px-2 py-0.5 font-mono text-[10px] uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">view</button>
          <button onClick={(e) => { e.stopPropagation(); remove(v); }} className="border border-rose-200 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40">del</button>
        </div>
      ),
    },
  ];

  return (
    <>
      <Notice status={status} onDismiss={clear} />

      <Section
        title="Scan reports"
        hint={loading ? 'loading…' : `${reports.length} most recent`}
        flush
        actions={reports.length > 0 && (
          <button onClick={clearAll} className="border border-rose-200 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40">clear all</button>
        )}
      >
        <DataTable columns={columns} rows={reports} empty={loading ? 'loading…' : 'no scans run yet'} onRowClick={(r) => setDetailReport(r.id)} />
      </Section>

      {detailReport && <ReportDetailModal reportId={detailReport} onClose={() => setDetailReport(null)} />}
    </>
  );
}
