import { useEffect, useState } from 'react';
import { api } from '../../lib/api.js';
import Stat from '../../components/Stat.jsx';
import Badge from '../../components/Badge.jsx';
import { formatNumber } from '../../lib/format.js';

/** Dashboard: compact status overview. Pulls /health and /repositories. */
export default function DashboardPage() {
  const [health, setHealth] = useState(null);
  const [repos, setRepos] = useState([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const [h, r] = await Promise.allSettled([api.get('/health'), api.get('/repositories')]);
        if (!active) return;
        if (h.status === 'fulfilled') setHealth(h.value);
        else setError(h.reason.message);
        if (r.status === 'fulfilled') setRepos(r.value ?? []);
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => { active = false; };
  }, []);

  const nexusTone = health?.nexus_reachable ? 'ok' : 'bad';
  const redisTone = health?.redis_reachable ? 'ok' : 'warn';

  return (
    <div className="p-6">
      <div className="mb-5 flex items-baseline justify-between">
        <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">Dashboard</h1>
        <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-600">
          {health ? `wrapper v${health.version}` : '—'}
        </span>
      </div>

      <div className="mb-6 grid grid-cols-2 gap-px border border-slate-200 bg-slate-200 sm:grid-cols-4 dark:border-slate-800 dark:bg-slate-800">
        <Stat label="Nexus" value={loading ? '···' : health?.nexus_reachable ? 'reachable' : 'unreachable'} sub={health ? 'status /check' : ''} tone={nexusTone} />
        <Stat label="Redis Cache" value={loading ? '···' : health?.redis_reachable ? 'connected' : 'degraded'} tone={redisTone} />
        <Stat label="Repositories" count={repos.length} sub="all formats" />
        <Stat label="Wrapper API" value={error ? 'error' : 'ok'} tone={error ? 'bad' : 'ok'} sub={error || 'responding'} />
      </div>

      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Repositories</h2>
      </div>

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
          <tbody>
            {loading ? (
              <tr><td colSpan={4} className="px-3 py-6 text-center font-mono text-xs text-slate-400 dark:text-slate-600">loading…</td></tr>
            ) : repos.length === 0 ? (
              <tr><td colSpan={4} className="px-3 py-6 text-center font-mono text-xs text-slate-400 dark:text-slate-600">no repositories</td></tr>
            ) : (
              repos.map((r) => (
                <tr key={r.name} className="border-b border-slate-100 last:border-0 hover:bg-slate-50 dark:border-slate-800/60 dark:hover:bg-slate-800/30">
                  <td className="px-3 py-2 font-mono text-slate-800 dark:text-slate-200">{r.name}</td>
                  <td className="px-3 py-2"><Badge tone="info">{r.format}</Badge></td>
                  <td className="px-3 py-2 text-slate-500 dark:text-slate-400">{r.type}</td>
                  <td className="px-3 py-2"><Badge tone={r.online === false ? 'bad' : 'ok'}>{r.online === false ? 'offline' : 'online'}</Badge></td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
