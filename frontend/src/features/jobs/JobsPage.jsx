import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router';
import { api } from '../../lib/api.js';
import Badge from '../../components/Badge.jsx';
import Stat from '../../components/Stat.jsx';
import { formatDateTime } from '../../lib/format.js';

// One entry per job `type`: which section it belongs to, a human label, and
// where its result actually shows up — so a row can link straight there
// instead of making you go find the right section yourself.
const JOB_TYPES = {
  clone_and_analyze: { category: 'Code Quality', label: 'Clone & analyze', to: '/code-quality/runs' },
  scan_image: { category: 'Vulnerability Scanning', label: 'Scan image', to: '/scan/images' },
  scanner_db_update: { category: 'Vulnerability Scanning', label: 'Database update', to: '/scan/database' },
  scanner_db_import: { category: 'Vulnerability Scanning', label: 'Database import', to: '/scan/database' },
  analyze_repo: { category: 'Storage', label: 'Storage analysis', to: '/browse/storage' },
  collect_metrics: { category: 'Monitoring', label: 'Collect metrics', to: '/monitoring' },
  run_retention: { category: 'Repositories', label: 'Retention run', to: '/repositories/retention' },
  provision_repository: { category: 'Repositories', label: 'Provision repository', to: '/repositories' },
  backup: { category: 'System', label: 'Backup', to: '/system' },
  backup_archive: { category: 'System', label: 'Backup archive', to: '/system' },
  run_scheduled_backup: { category: 'System', label: 'Scheduled backup', to: '/system' },
  sync: { category: 'System', label: 'Sync', to: '/system' },
};
// 'Other' is last on purpose — it's the jobMeta() fallback for a job type
// missing from JOB_TYPES above (a real, if rare, way to hit it: a job type
// added to a job handler and not to this map). Without it here, such a job
// could never be selected in the section filter even though it's right
// there in the table with an "Other" badge.
const CATEGORY_ORDER = ['Code Quality', 'Vulnerability Scanning', 'Storage', 'Repositories', 'Monitoring', 'System', 'Other'];
const CATEGORY_TONE = {
  'Code Quality': 'info', 'Vulnerability Scanning': 'bad', Storage: 'warn',
  Repositories: 'neutral', Monitoring: 'ok', System: 'neutral', Other: 'neutral',
};
const jobMeta = (type) => JOB_TYPES[type] || { category: 'Other', label: type, to: null };

const STATUS_TONE = { pending: 'neutral', running: 'info', done: 'ok', failed: 'bad', cancelled: 'warn' };

function JobRows({ loading, jobs, cancelJob }) {
  if (loading) {
    return <tr><td colSpan={6} className="px-3 py-6 text-center font-mono text-xs text-slate-400 dark:text-slate-600">loading…</td></tr>;
  }
  if (jobs.length === 0) {
    return <tr><td colSpan={6} className="px-3 py-6 text-center font-mono text-xs text-slate-400 dark:text-slate-600">no jobs match this filter</td></tr>;
  }
  return jobs.map((j) => {
    const meta = jobMeta(j.type);
    return (
      <tr key={j.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50 dark:border-slate-800/60 dark:hover:bg-slate-800/30">
        <td className="px-3 py-2"><Badge tone={CATEGORY_TONE[meta.category] || 'neutral'}>{meta.category}</Badge></td>
        <td className="px-3 py-2 font-mono text-slate-800 dark:text-slate-200">{meta.label}</td>
        <td className="px-3 py-2"><Badge tone={STATUS_TONE[j.status] || 'neutral'}>{j.status}</Badge></td>
        <td className="px-3 py-2 font-mono text-xs text-slate-500 dark:text-slate-400">{j.message}</td>
        <td className="px-3 py-2 font-mono text-xs text-slate-400 dark:text-slate-600">{formatDateTime(new Date(j.created_at * 1000).toISOString())}</td>
        <td className="px-3 py-2 text-right">
          <div className="flex items-center justify-end gap-1.5">
            {meta.to && (
              <Link to={meta.to} className="border border-slate-300 px-2 py-0.5 font-mono text-[10px] uppercase text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
                view
              </Link>
            )}
            {(j.status === 'running' || j.status === 'pending') && (
              <button onClick={() => cancelJob(j.id)} className="border border-rose-200 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40">
                cancel
              </button>
            )}
          </div>
        </td>
      </tr>
    );
  });
}

const SELECT = 'border border-slate-300 bg-white px-2 py-1.5 font-mono text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200';

/** Background job manager: every async task Rotsy runs, grouped by the
 * section it belongs to, with filters and an at-a-glance status summary. */
export default function JobsPage() {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState('');
  const [category, setCategory] = useState('all');
  const [status, setStatus] = useState('all');

  const load = async () => {
    setLoading(true);
    try { setJobs(await api.get('/jobs')); } catch (_) { console.debug('jobs fetch failed', _); }
    setLoading(false);
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, []);

  const trigger = async (endpoint, label) => {
    try {
      const res = await api.post(endpoint);
      setMsg(`${label} queued: ${res.job_id?.slice(0, 8) ?? res.count ?? 'ok'}`);
    } catch (e) { setMsg(`failed: ${e.message}`); }
  };

  const cancelJob = async (jobId) => {
    try {
      await api.post(`/jobs/${jobId}/cancel`);
      setMsg(`Job ${jobId.slice(0, 8)} cancelled.`);
      load();
    } catch (e) { setMsg(`cancel failed: ${e.message}`); }
  };

  const categories = useMemo(() => {
    const present = new Set(jobs.map((j) => jobMeta(j.type).category));
    return CATEGORY_ORDER.filter((c) => present.has(c));
  }, [jobs]);

  const counts = useMemo(() => ({
    running: jobs.filter((j) => j.status === 'running').length,
    pending: jobs.filter((j) => j.status === 'pending').length,
    failed: jobs.filter((j) => j.status === 'failed').length,
    done: jobs.filter((j) => j.status === 'done').length,
  }), [jobs]);

  const filtered = jobs.filter((j) => {
    if (category !== 'all' && jobMeta(j.type).category !== category) return false;
    if (status !== 'all' && j.status !== status) return false;
    return true;
  });

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <h1 className="mr-auto text-base font-medium text-slate-900 dark:text-slate-100">Background Jobs</h1>
        <button onClick={() => trigger('/jobs/collect-metrics', 'metric collection')} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
          Collect metrics
        </button>
      </div>

      <div className="mb-4 grid grid-cols-2 gap-px border border-slate-200 bg-slate-200 sm:grid-cols-4 dark:border-slate-800 dark:bg-slate-800">
        <Stat label="Running" count={counts.running} tone={counts.running > 0 ? 'info' : 'neutral'} />
        <Stat label="Pending" count={counts.pending} tone={counts.pending > 0 ? 'warn' : 'neutral'} />
        <Stat label="Failed" count={counts.failed} tone={counts.failed > 0 ? 'bad' : 'neutral'} />
        <Stat label="Done" count={counts.done} tone="ok" />
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select value={category} onChange={(e) => setCategory(e.target.value)} className={SELECT}>
          <option value="all">All sections</option>
          {categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} className={SELECT}>
          <option value="all">All statuses</option>
          <option value="running">Running</option>
          <option value="pending">Pending</option>
          <option value="done">Done</option>
          <option value="failed">Failed</option>
          <option value="cancelled">Cancelled</option>
        </select>
        <span className="font-mono text-[11px] text-slate-400 dark:text-slate-600">{filtered.length} of {jobs.length}</span>
      </div>

      {msg && <div className="mb-3 border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-xs text-slate-600 dark:border-slate-800 dark:bg-slate-900/40 dark:text-slate-400">{msg}</div>}

      <div className="overflow-x-auto border border-slate-200 dark:border-slate-800">
        <table className="w-full border-collapse text-sm">
          <thead>
              <tr className="border-b border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60">
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Section</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Job</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Status</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Message</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Created</th>
                <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-wider text-slate-500">·</th>
              </tr>
            </thead>
            <tbody>
              <JobRows loading={loading} jobs={filtered} cancelJob={cancelJob} />
            </tbody>
        </table>
      </div>
    </div>
  );
}
