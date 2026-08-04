import { useState } from 'react';
import Badge from '../../../components/Badge.jsx';
import DataTable from '../../../components/DataTable.jsx';
import Section from '../../../components/Section.jsx';
import SeverityCounts from '../../../components/SeverityCounts.jsx';
import { formatDateTime } from '../../../lib/format.js';
import { useResource } from '../../../lib/useResource.js';
import { scanApi } from '../api.js';
import ReportDetailModal from './ReportDetailModal.jsx';

/**
 * Report history for one selected tag — what the Images tree drills down to.
 *
 * Sourced from the same `/scan/reports` endpoint ReportsPage.jsx already uses
 * (via `scanApi.reports`), just scoped to this one (repo, image:tag) pair.
 * Selecting a report opens the existing `ReportDetailModal` unchanged — this
 * component only lists the history, it doesn't reimplement the detail view.
 */
export default function TagReportsPanel({ repo, imageName, tag, onClose }) {
  const image = tag ? `${imageName}:${tag}` : imageName;
  const { data: reports, loading, reload } = useResource(
    () => scanApi.reports({ repo, image }),
    [],
    [repo, image],
  );
  const [detailReport, setDetailReport] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState('');

  const deleteAll = async () => {
    if (!confirm(`Delete all ${reports.length} report(s) for ${repo}/${image}? This cannot be undone.`)) return;
    setDeleting(true);
    setError('');
    try {
      await scanApi.deleteReportsFor(repo, image);
      reload();
    } catch (e) {
      setError(`delete failed: ${e.message}`);
    } finally {
      setDeleting(false);
    }
  };

  const columns = [
    { key: 'scanner', header: 'Scanner', render: (v) => <span className="font-mono text-xs text-slate-500 dark:text-slate-400">{v}</span> },
    {
      key: 'critical',
      header: 'C/H/M/L',
      headClassName: 'text-center',
      className: 'text-center',
      render: (_v, row) => <SeverityCounts counts={row} />,
    },
    { key: 'status', header: 'Status', render: (v) => <Badge tone={v === 'success' ? 'ok' : v === 'failed' ? 'bad' : 'info'}>{v}</Badge> },
    { key: 'started_at', header: 'When', render: (v) => <span className="font-mono text-xs text-slate-400 dark:text-slate-600">{formatDateTime(v)}</span> },
  ];

  return (
    <>
      <Section
        title={`Report history · ${repo} / ${image}`}
        hint={loading ? 'loading…' : `${reports.length} report${reports.length === 1 ? '' : 's'}`}
        flush
        actions={
          <button
            onClick={onClose}
            className="border border-slate-300 px-2 py-0.5 font-mono text-[10px] uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800"
          >
            close
          </button>
        }
      >
        <DataTable
          columns={columns}
          rows={reports}
          empty={loading ? 'loading…' : 'no scan reports for this tag yet'}
          onRowClick={(r) => setDetailReport(r.id)}
        />

        {error && (
          <div className="mt-3 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-[11px] text-rose-600 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-400">{error}</div>
        )}

        {!loading && reports.length > 0 && (
          <div className="mt-3 flex justify-end border-t border-slate-100 pt-3 dark:border-slate-800/60">
            <button
              onClick={deleteAll}
              disabled={deleting}
              className="border border-rose-300 px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider text-rose-600 hover:bg-rose-50 disabled:opacity-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40"
            >
              {deleting ? 'deleting…' : `delete all reports for this tag`}
            </button>
          </div>
        )}
      </Section>

      {detailReport && <ReportDetailModal reportId={detailReport} onClose={() => setDetailReport(null)} />}
    </>
  );
}
