import { useEffect, useState } from 'react';
import { api } from '../../lib/api.js';
import Stat from '../../components/Stat.jsx';
import ProgressBar from '../../components/ProgressBar.jsx';
import HealthTile from '../../components/HealthTile.jsx';
import TimeSeriesChart from '../../components/TimeSeriesChart.jsx';
import RankedBarList from '../../components/RankedBarList.jsx';
import { formatBytes, formatNumber, relativeTime } from '../../lib/format.js';

function BlobstoreUsageSection({ loading, blobstores, diskTone }) {
  let body;
  if (loading) {
    body = <div className="py-6 text-center font-mono text-xs text-slate-400 dark:text-slate-600">loading…</div>;
  } else if (blobstores.length === 0) {
    body = <div className="py-6 text-center font-mono text-xs text-slate-400 dark:text-slate-600">no blobstores</div>;
  } else {
    body = blobstores.map((b) => (
      <div key={b.name}>
        <ProgressBar used={b.used_bytes} total={b.capacity_bytes} label={`${b.name} (${b.type}, ${formatNumber(b.blob_count)} blobs)`} tone={diskTone(b.used_pct)} />
        {b.unavailable && <div className="mt-1 font-mono text-[10px] text-rose-600 dark:text-rose-400">⚠ unavailable</div>}
      </div>
    ));
  }
  return (
    <section className="mb-6">
      <h2 className="mb-2 font-mono text-[10px] uppercase tracking-wider text-slate-500">Blobstore disk usage</h2>
      <div className="space-y-3 border border-slate-200 p-3 dark:border-slate-800">{body}</div>
    </section>
  );
}

function HealthChecksSection({ loading, health, failingProbes }) {
  let body;
  if (loading) {
    body = <div className="col-span-full py-6 text-center font-mono text-xs text-slate-400 dark:text-slate-600">loading…</div>;
  } else if (!health?.probes?.length) {
    body = <div className="col-span-full py-6 text-center font-mono text-xs text-slate-400 dark:text-slate-600">no probes</div>;
  } else {
    body = health.probes.map((p) => (
      <HealthTile key={p.name} name={p.name} healthy={p.healthy} message={p.message} category={p.category} />
    ));
  }
  return (
    <section className="mb-6">
      <h2 className="mb-2 font-mono text-[10px] uppercase tracking-wider text-slate-500">
        Nexus health checks · {health?.total ?? 0} probes{failingProbes > 0 && <span className="text-rose-500"> · {failingProbes} failing</span>}
      </h2>
      <div className="mb-2 flex gap-4 font-mono text-[10px] text-slate-400 dark:text-slate-600">
        <span><span className="inline-block h-2 w-2 rounded-full bg-rose-500 align-middle mr-1" />critical</span>
        <span><span className="inline-block h-2 w-2 rounded-full bg-amber-500 align-middle mr-1" />security advisory</span>
        <span><span className="inline-block h-2 w-2 rounded-full bg-slate-400 align-middle mr-1" />info</span>
      </div>
      <div className="grid grid-cols-1 gap-1.5 border border-slate-200 p-3 sm:grid-cols-2 lg:grid-cols-3 dark:border-slate-800">{body}</div>
    </section>
  );
}

/**
 * Monitoring dashboard — real Nexus health, not just storage snapshots.
 * Sections:
 *  - System: version, edition, anonymous access, security warnings
 *  - Health checks: Nexus status/check probes as color-coded tiles
 *  - Blobstores: disk usage with progress bars (the real "is disk full?" view)
 *  - Storage trend: per-repo timeseries (kept for capacity planning)
 */
export default function MetricsPage() {
  const [system, setSystem] = useState(null);
  const [health, setHealth] = useState(null);
  const [blobstores, setBlobstores] = useState([]);
  const [overview, setOverview] = useState([]);
  const [selected, setSelected] = useState('');
  const [series, setSeries] = useState([]);
  const [hours, setHours] = useState(24);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [diskSeries, setDiskSeries] = useState([]);

  const loadRealtime = async () => {
    try {
      const [s, h, b, o] = await Promise.allSettled([
        api.get('/metrics/system'),
        api.get('/metrics/health'),
        api.get('/metrics/blobstores'),
        api.get('/metrics/overview'),
      ]);
      if (s.status === 'fulfilled') setSystem(s.value);
      if (h.status === 'fulfilled') setHealth(h.value);
      if (b.status === 'fulfilled') setBlobstores(b.value ?? []);
      if (o.status === 'fulfilled') {
        setOverview(o.value ?? []);
        if (o.value?.length && !selected) setSelected(o.value[0].repo);
      }
      if (s.status === 'rejected') setError(s.reason.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadRealtime();
    const t = setInterval(loadRealtime, 30000); // refresh every 30s
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selected) return;
    let active = true;
    (async () => {
      try {
        const data = await api.get(`/metrics/${encodeURIComponent(selected)}/timeseries?hours=${hours}`);
        if (active) setSeries(data);
      } catch (_) { console.debug('metric refresh failed, keeping last known values', _); }
    })();
    return () => { active = false; };
  }, [selected, hours]);

  // Sparkline for the "Disk Used" tile: the worst (highest used_pct)
  // blobstore's recent history — the one most worth a glance.
  useEffect(() => {
    if (!blobstores.length) return;
    const worst = [...blobstores].sort((a, b) => (b.used_pct || 0) - (a.used_pct || 0))[0];
    let active = true;
    (async () => {
      try {
        const data = await api.get(`/metrics/blobstore/${encodeURIComponent(worst.name)}/timeseries?hours=24`);
        if (active) setDiskSeries(data.map((d) => d.used_pct || 0));
      } catch (_) { console.debug('disk sparkline refresh failed, keeping last known values', _); }
    })();
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [blobstores.length]);

  const totalDiskUsed = blobstores.reduce((s, b) => s + (b.used_bytes || 0), 0);
  const totalDiskCapacity = blobstores.reduce((s, b) => s + (b.capacity_bytes || 0), 0);
  const failingProbes = health?.failing ?? 0;

  let nexusValue = 'unreachable';
  if (loading) nexusValue = '···';
  else if (system) nexusValue = system.version ? `v${system.version}` : 'reachable';

  let healthValue = `${failingProbes} failing`;
  if (loading) healthValue = '···';
  else if (failingProbes === 0) healthValue = 'all green';

  const diskTone = (pct) => {
    if (pct > 90) return 'bad';
    if (pct > 75) return 'warn';
    return 'ok';
  };

  return (
    <div className="p-6">
      <div className="mb-5 flex items-baseline justify-between">
        <div>
          <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">Monitoring</h1>
          <p className="mt-0.5 font-mono text-[11px] text-slate-500 dark:text-slate-500">
            Nexus health · blobstore disk · system security · storage growth
          </p>
        </div>
        {system?.version && (
          <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-600">
            nexus {system.version} · {system.edition || ''}
          </span>
        )}
      </div>

      {error && (
        <div className="mb-4 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-400">
          {error}
        </div>
      )}

      {/* Top stats */}
      <div className="mb-6 grid grid-cols-2 gap-px border border-slate-200 bg-slate-200 sm:grid-cols-4 dark:border-slate-800 dark:bg-slate-800">
        <Stat
          label="Nexus"
          value={nexusValue}
          sub={system?.edition || ''}
          tone={system?.version ? 'ok' : 'bad'}
        />
        <Stat label="Disk Used" bytes={totalDiskUsed} sub={`of ${formatBytes(totalDiskCapacity)}`} tone={totalDiskUsed / (totalDiskCapacity || 1) > 0.85 ? 'warn' : 'info'} series={diskSeries} />
        <Stat label="Health" value={healthValue} tone={failingProbes === 0 ? 'ok' : 'bad'} sub={`${health?.total ?? 0} probes`} />
        <Stat label="Security" value={system?.warnings?.length ? `${system.warnings.length} warnings` : 'clean'} tone={system?.warnings?.length ? 'warn' : 'ok'} />
      </div>

      {/* Security warnings */}
      {system?.warnings?.length > 0 && (
        <section className="mb-6">
          <h2 className="mb-2 font-mono text-[10px] uppercase tracking-wider text-slate-500">Security warnings</h2>
          <div className="space-y-1">
            {system.warnings.map((w) => (
              <div key={w.name} className="flex items-start gap-2 border border-amber-200 bg-amber-50 px-3 py-2 dark:border-amber-800 dark:bg-amber-950/30">
                <span className="mt-0.5 font-mono text-[11px] font-medium text-amber-700 dark:text-amber-400">{w.name}</span>
                <span className="flex-1 font-mono text-[11px] text-amber-700/80 dark:text-amber-400/70">{w.message}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      <BlobstoreUsageSection loading={loading} blobstores={blobstores} diskTone={diskTone} />

      <HealthChecksSection loading={loading} health={health} failingProbes={failingProbes} />

      {/* Top repositories by size — at-a-glance, no click-through needed */}
      <section className="mb-6">
        <h2 className="mb-2 font-mono text-[10px] uppercase tracking-wider text-slate-500">Top repositories by size</h2>
        <div className="border border-slate-200 p-3 dark:border-slate-800">
          <RankedBarList
            items={overview.map((r) => ({ label: r.repo, value: r.total_bytes || 0, sub: formatNumber(r.asset_count || 0) + ' assets' }))}
            formatValue={formatBytes}
            limit={5}
          />
        </div>
      </section>

      {/* Storage growth (kept — useful for capacity planning) */}
      <section>
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <h2 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Storage growth</h2>
          <select value={selected} onChange={(e) => setSelected(e.target.value)} className="border border-slate-300 bg-white px-2 py-1 font-mono text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200">
            {overview.map((r) => <option key={r.repo} value={r.repo}>{r.repo}</option>)}
          </select>
          <select value={hours} onChange={(e) => setHours(Number(e.target.value))} className="border border-slate-300 bg-white px-2 py-1 font-mono text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200">
            <option value={6}>6h</option>
            <option value={24}>24h</option>
            <option value={168}>7d</option>
            <option value={720}>30d</option>
          </select>
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div>
            <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Total size</div>
            <TimeSeriesChart data={series} valueKey="total_bytes" kind="bytes" />
          </div>
          <div>
            <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Asset count</div>
            <TimeSeriesChart data={series} valueKey="asset_count" kind="number" />
          </div>
        </div>
        <div className="mt-2 font-mono text-[10px] text-slate-400 dark:text-slate-600">
          last sample {relativeTime(overview.find((r) => r.repo === selected)?.timestamp)}
        </div>
      </section>
    </div>
  );
}
