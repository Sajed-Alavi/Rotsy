import { useEffect, useState } from 'react';
import { api } from '../../lib/api.js';
import Badge from '../../components/Badge.jsx';
import { formatDateTime } from '../../lib/format.js';

/** Background job manager: list jobs + trigger metric collection / analyze. */
export default function JobsPage() {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState('');

  const load = async () => {
    setLoading(true);
    try { setJobs(await api.get('/jobs')); } catch (_) {}
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

  const statusTone = (s) => ({ pending: 'neutral', running: 'info', done: 'ok', failed: 'bad', cancelled: 'warn' }[s] || 'neutral');

  return (
    <div className="p-6">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <h1 className="mr-auto text-base font-medium text-slate-900 dark:text-slate-100">Background Jobs</h1>
        <button onClick={() => trigger('/jobs/collect-metrics', 'metric collection')} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
          Collect metrics
        </button>
      </div>

      {msg && <div className="mb-3 border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-xs text-slate-600 dark:border-slate-800 dark:bg-slate-900/40 dark:text-slate-400">{msg}</div>}

      <div className="overflow-x-auto border border-slate-200 dark:border-slate-800">
        <table className="w-full border-collapse text-sm">
          <thead>
              <tr className="border-b border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60">
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Type</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Status</th>
                <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-wider text-slate-500">Progress</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Message</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Created</th>
                <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-wider text-slate-500">·</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={6} className="px-3 py-6 text-center font-mono text-xs text-slate-400 dark:text-slate-600">loading…</td></tr>
              ) : jobs.length === 0 ? (
                <tr><td colSpan={6} className="px-3 py-6 text-center font-mono text-xs text-slate-400 dark:text-slate-600">no jobs yet — trigger one above</td></tr>
            ) : jobs.map((j) => (
              <tr key={j.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50 dark:border-slate-800/60 dark:hover:bg-slate-800/30">
                <td className="px-3 py-2 font-mono text-slate-800 dark:text-slate-200">{j.type}</td>
                <td className="px-3 py-2"><Badge tone={statusTone(j.status)}>{j.status}</Badge></td>
                <td className="px-3 py-2 text-right font-mono tabular-nums text-slate-500 dark:text-slate-400">{j.progress}%</td>
                <td className="px-3 py-2 font-mono text-xs text-slate-500 dark:text-slate-400">{j.message}</td>
                <td className="px-3 py-2 font-mono text-xs text-slate-400 dark:text-slate-600">{formatDateTime(new Date(j.created_at * 1000).toISOString())}</td>
                <td className="px-3 py-2 text-right">
                  {(j.status === 'running' || j.status === 'pending') && (
                    <button onClick={() => cancelJob(j.id)} className="border border-rose-200 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40">
                      cancel
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
