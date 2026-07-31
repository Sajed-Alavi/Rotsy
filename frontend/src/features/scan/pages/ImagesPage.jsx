import { useState } from 'react';
import Badge from '../../../components/Badge.jsx';
import DataTable from '../../../components/DataTable.jsx';
import Notice from '../../../components/Notice.jsx';
import Section from '../../../components/Section.jsx';
import Tabs from '../../../components/Tabs.jsx';
import { formatNumber, relativeTime } from '../../../lib/format.js';
import { scanApi } from '../api.js';
import { useResource, useStatus } from '../hooks/useResource.js';

const STATE_TONE = { scanned: 'ok', queued: 'info', failed: 'bad', baseline: 'neutral' };
const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'scanned', label: 'Scanned' },
  { key: 'baseline', label: 'Baseline' },
  { key: 'queued', label: 'Queued' },
  { key: 'failed', label: 'Failed' },
];

/**
 * The image ledger, with the per-image scan action.
 *
 * This is the top of the drill-down: image → its reports → the CVEs in one
 * report. Previously all three levels sat on one page as sibling tables, each
 * in its own 96-row-tall scroll box.
 */
export default function ImagesPage() {
  const { data: images, loading, reload } = useResource(() => scanApi.images(), []);
  const { status, say, fail, clear } = useStatus();
  const [scanning, setScanning] = useState({});
  const [filter, setFilter] = useState('all');

  const scan = async (repo, image) => {
    const key = `${repo}/${image}`;
    setScanning((s) => ({ ...s, [key]: true }));
    try {
      const r = await scanApi.scanImage(repo, image);
      say(`Scan queued for ${key} — job ${r.job_id.slice(0, 8)}.`, 'ok');
      setTimeout(reload, 4000);
    } catch (e) {
      fail(`could not queue a scan for ${key}: ${e.message}`);
    } finally {
      setScanning((s) => ({ ...s, [key]: false }));
    }
  };

  const rows = filter === 'all' ? images : images.filter((i) => i.state === filter);
  const counts = FILTERS.reduce((acc, f) => {
    acc[f.key] = f.key === 'all' ? images.length : images.filter((i) => i.state === f.key).length;
    return acc;
  }, {});

  const columns = [
    { key: 'repo', header: 'Repo', render: (v) => <span className="font-mono text-xs text-slate-600 dark:text-slate-400">{v}</span> },
    {
      key: 'image',
      header: 'Image',
      render: (v, row) => {
        const failure = (row.reports || []).find((r) => r.status === 'failed' && r.error);
        return (
          <>
            <span className="font-mono text-xs text-slate-800 dark:text-slate-200">{v}</span>
            {failure && <span className="block text-[10px] text-rose-600 dark:text-rose-400" title={failure.error}>{failure.scanner}: {failure.error}</span>}
          </>
        );
      },
    },
    {
      key: 'state',
      header: 'State',
      render: (v, row) => (
        <Badge tone={STATE_TONE[v] || 'neutral'} title={v === 'baseline' ? 'Present before scanning was enabled — not auto-scanned' : `source: ${row.source}`}>{v}</Badge>
      ),
    },
    {
      key: 'critical',
      header: 'C/H/M/L',
      headClassName: 'text-center',
      className: 'text-center',
      render: (_v, row) => (row.state === 'scanned' ? (
        <span className="font-mono tabular-nums text-xs">
          <span className="text-rose-600 dark:text-rose-400">{row.critical}</span>/
          <span className="text-amber-600 dark:text-amber-400">{row.high}</span>/
          <span className="text-sky-600 dark:text-sky-400">{row.medium}</span>/
          <span className="text-slate-500">{row.low}</span>
        </span>
      ) : <span className="text-slate-400 dark:text-slate-600">—</span>),
    },
    { key: 'last_scan_at', header: 'Last scan', render: (v) => <span className="font-mono text-xs text-slate-400 dark:text-slate-600">{v ? relativeTime(v) : 'never'}</span> },
    {
      key: 'id',
      header: '·',
      headClassName: 'text-right',
      className: 'text-right',
      render: (_v, row) => {
        const key = `${row.repo}/${row.image}`;
        const busy = !!scanning[key] || row.state === 'queued';
        return (
          <button
            onClick={() => scan(row.repo, row.image)}
            disabled={busy}
            className="border border-sky-300 bg-sky-50 px-2 py-0.5 font-mono text-[10px] uppercase text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40"
          >
            {busy ? '···' : (row.scan_count > 0 ? 'rescan' : 'scan')}
          </button>
        );
      },
    },
  ];

  return (
    <>
      <Notice status={status} onDismiss={clear} />

      <Section
        title={`Images · ${formatNumber(images.length)}`}
        hint="scan runs on push, or when you click scan"
        flush
        actions={<Tabs items={FILTERS.map((f) => ({ ...f, badge: counts[f.key] }))} value={filter} onChange={setFilter} />}
      >
        <DataTable
          columns={columns}
          rows={rows}
          empty={loading ? 'loading…' : filter === 'all' ? 'no images known yet — enable a repository under Targets' : `no images in state "${filter}"`}
        />
      </Section>
    </>
  );
}
