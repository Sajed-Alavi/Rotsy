import { useEffect, useState } from 'react';
import { api } from '../../lib/api.js';
import Stat from '../../components/Stat.jsx';
import Badge from '../../components/Badge.jsx';
import RankedBarList from '../../components/RankedBarList.jsx';
import { formatBytes, formatNumber, relativeTime } from '../../lib/format.js';

const HOST_POLL_MS = 5000;
const HOST_HISTORY_LEN = 20;

function RecentActivityTable({ jobs, jobStatusTone }) {
  if (jobs.length === 0) {
    return <div className="py-6 text-center font-mono text-xs text-slate-400 dark:text-slate-600">no background jobs yet</div>;
  }
  return (
    <table className="w-full border-collapse text-sm">
      <tbody>
        {jobs.map((j) => (
          <tr key={j.id} className="border-b border-slate-100 last:border-0 dark:border-slate-800/60">
            <td className="px-3 py-1.5 font-mono text-xs text-slate-700 dark:text-slate-300">{j.type}</td>
            <td className="px-3 py-1.5"><Badge tone={jobStatusTone(j.status)}>{j.status}</Badge></td>
            <td className="px-3 py-1.5 text-right font-mono text-[11px] text-slate-400 dark:text-slate-600">{relativeTime(j.updated_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function HostResourcesSection({ host, cpuHistory, memHistory, diskHistory }) {
  const cpuValue = host ? `${host.cpu_percent.toFixed(0)}%` : '···';
  const cpuTone = host?.cpu_percent > 85 ? 'warn' : 'neutral';

  const memValue = host ? `${host.memory_percent.toFixed(0)}%` : '···';
  const memSub = host ? formatBytes(host.memory_used_bytes) : '';
  const memTone = host?.memory_percent > 85 ? 'warn' : 'neutral';

  const diskPct = host ? (host.disk_used_bytes / (host.disk_total_bytes || 1)) * 100 : null;
  const diskValue = diskPct === null ? '···' : `${diskPct.toFixed(0)}%`;
  const diskSub = host ? formatBytes(host.disk_used_bytes) : '';
  const diskTone = diskPct !== null && diskPct > 85 ? 'warn' : 'neutral';

  return (
    <section className="mb-6">
      <h2 className="mb-2 font-mono text-[10px] uppercase tracking-wider text-slate-500">Host resources</h2>
      <div className="grid grid-cols-1 gap-px border border-slate-200 bg-slate-200 sm:grid-cols-3 dark:border-slate-800 dark:bg-slate-800">
        <Stat label="CPU" value={cpuValue} series={cpuHistory} tone={cpuTone} />
        <Stat label="Memory" value={memValue} sub={memSub} series={memHistory} tone={memTone} />
        <Stat label="Disk" value={diskValue} sub={diskSub} series={diskHistory} tone={diskTone} />
      </div>
    </section>
  );
}

function RepositoriesTable({ loading, repos }) {
  let body;
  if (loading) {
    body = <tr><td colSpan={4} className="px-3 py-6 text-center font-mono text-xs text-slate-400 dark:text-slate-600">loading…</td></tr>;
  } else if (repos.length === 0) {
    body = <tr><td colSpan={4} className="px-3 py-6 text-center font-mono text-xs text-slate-400 dark:text-slate-600">no repositories</td></tr>;
  } else {
    body = repos.map((r) => (
      <tr key={r.name} className="border-b border-slate-100 last:border-0 hover:bg-slate-50 dark:border-slate-800/60 dark:hover:bg-slate-800/30">
        <td className="px-3 py-2 font-mono text-slate-800 dark:text-slate-200">{r.name}</td>
        <td className="px-3 py-2"><Badge tone="info">{r.format}</Badge></td>
        <td className="px-3 py-2 text-slate-500 dark:text-slate-400">{r.type}</td>
        <td className="px-3 py-2"><Badge tone={r.online === false ? 'bad' : 'ok'}>{r.online === false ? 'offline' : 'online'}</Badge></td>
      </tr>
    ));
  }

  return (
    <div className="border border-slate-200 dark:border-slate-800">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60">
            <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Name</th>
            <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Format</th>
            <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Type</th>
            <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Status</th>
          </tr>
        </thead>
        <tbody>{body}</tbody>
      </table>
    </div>
  );
}

/** Dashboard: status overview + activity, storage, vulnerability and host-resource glance. */
export default function DashboardPage() {
  const [health, setHealth] = useState(null);
  const [repos, setRepos] = useState([]);
  const [scanSummary, setScanSummary] = useState(null);
  const [blobstores, setBlobstores] = useState([]);
  const [overview, setOverview] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const [cpuHistory, setCpuHistory] = useState([]);
  const [memHistory, setMemHistory] = useState([]);
  const [diskHistory, setDiskHistory] = useState([]);
  const [host, setHost] = useState(null);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const results = await Promise.allSettled([
          api.get('/health'),
          api.get('/repositories'),
          api.get('/scan/summary'),
          api.get('/metrics/blobstores'),
          api.get('/metrics/overview'),
          api.get('/jobs?limit=10'),
        ]);
        if (!active) return;
        const [h, r, s, b, o, j] = results;
        if (h.status === 'fulfilled') setHealth(h.value);
        else setError(h.reason.message);
        if (r.status === 'fulfilled') setRepos(r.value ?? []);
        if (s.status === 'fulfilled') setScanSummary(s.value);
        if (b.status === 'fulfilled') setBlobstores(b.value ?? []);
        if (o.status === 'fulfilled') setOverview(o.value ?? []);
        if (j.status === 'fulfilled') setJobs(j.value ?? []);
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => { active = false; };
  }, []);

  // Host resource sparklines: poll while this page is mounted, keep a
  // bounded rolling window client-side (no backend history for this — a
  // live probe, same idea as /metrics/realtime).
  useEffect(() => {
    const push = (setter, value) => setter((prev) => [...prev.slice(-(HOST_HISTORY_LEN - 1)), value]);
    const poll = async () => {
      try {
        const h = await api.get('/metrics/host');
        setHost(h);
        push(setCpuHistory, h.cpu_percent);
        push(setMemHistory, h.memory_percent);
        push(setDiskHistory, (h.disk_used_bytes / (h.disk_total_bytes || 1)) * 100);
      } catch { /* best-effort — host metrics are a nice-to-have */ }
    };
    poll();
    const interval = setInterval(poll, HOST_POLL_MS);
    return () => clearInterval(interval);
  }, []);

  const nexusTone = health?.nexus_reachable ? 'ok' : 'bad';
  const redisTone = health?.redis_reachable ? 'ok' : 'warn';
  let nexusValue = 'unreachable';
  if (loading) nexusValue = '···';
  else if (health?.nexus_reachable) nexusValue = 'reachable';

  let redisValue = 'degraded';
  if (loading) redisValue = '···';
  else if (health?.redis_reachable) redisValue = 'connected';

  const totals = scanSummary?.totals || { critical: 0, high: 0 };
  const totalStorage = overview.reduce((s, r) => s + (r.total_bytes || 0), 0);
  const worstDiskPct = blobstores.reduce((max, b) => Math.max(max, b.used_pct || 0), 0);

  const formatCounts = {};
  for (const r of repos) formatCounts[r.format] = (formatCounts[r.format] || 0) + 1;
  const formatItems = Object.entries(formatCounts).map(([label, value]) => ({ label, value }));

  const JOB_STATUS_TONE = { done: 'ok', failed: 'bad', cancelled: 'neutral' };
  const jobStatusTone = (s) => JOB_STATUS_TONE[s] || 'info';

  return (
    <div className="p-6">
      <div className="mb-5 flex items-baseline justify-between">
        <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">Dashboard</h1>
        <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-600">
          {health ? `wrapper v${health.version}` : '—'}
        </span>
      </div>

      {error && <div className="mb-4 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-400">{error}</div>}

      {/* Status + key repository metrics */}
      <div className="mb-6 grid grid-cols-2 gap-px border border-slate-200 bg-slate-200 sm:grid-cols-4 lg:grid-cols-7 dark:border-slate-800 dark:bg-slate-800">
        <Stat label="Nexus" value={nexusValue} tone={nexusTone} />
        <Stat label="Redis Cache" value={redisValue} tone={redisTone} />
        <Stat label="Repositories" count={repos.length} sub="all formats" />
        <Stat label="Total Storage" bytes={totalStorage} sub={`${overview.length} repos tracked`} />
        <Stat label="Disk Used" value={`${worstDiskPct.toFixed(0)}%`} sub="worst blobstore" tone={worstDiskPct > 85 ? 'warn' : 'info'} />
        <Stat label="Critical CVEs" count={totals.critical} tone={totals.critical ? 'bad' : 'ok'} />
        <Stat label="High CVEs" count={totals.high} tone={totals.high ? 'warn' : 'ok'} />
      </div>

      <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Repository formats */}
        <section>
          <h2 className="mb-2 font-mono text-[10px] uppercase tracking-wider text-slate-500">Repository formats</h2>
          <div className="border border-slate-200 p-3 dark:border-slate-800">
            <RankedBarList items={formatItems} formatValue={formatNumber} limit={6} />
          </div>
        </section>

        {/* Recent activity */}
        <section>
          <h2 className="mb-2 font-mono text-[10px] uppercase tracking-wider text-slate-500">Recent activity</h2>
          <div className="max-h-64 overflow-y-auto border border-slate-200 dark:border-slate-800">
            <RecentActivityTable jobs={jobs} jobStatusTone={jobStatusTone} />
          </div>
        </section>
      </div>

      <HostResourcesSection host={host} cpuHistory={cpuHistory} memHistory={memHistory} diskHistory={diskHistory} />

      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Repositories</h2>
      </div>

      <RepositoriesTable loading={loading} repos={repos} />
    </div>
  );
}
