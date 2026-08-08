import React, { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../lib/api.js';
import Stat from '../../components/Stat.jsx';
import Icon from '../../components/Icon.jsx';
import { formatBytes, formatDateTime, formatNumber, percent, relativeTime } from '../../lib/format.js';

const PHASES = {
  init: 'initialising',
  detect_format: 'detecting repository format',
  scanning_assets: 'scanning physical disk (assets api)',
  collecting_tags: 'collecting live image tags (components api)',
  collecting_components: 'aggregating components and assets',
  deep_scan: 'recursively extracting layer metrics',
};

/**
 * Deep Storage Analyzer (Feature A) — multi-format, theme-aware.
 *
 * Works for any repository format: docker repos use deep manifest traversal
 * (mode=docker), everything else uses asset aggregation (mode=generic).
 * Flow: pick a repo → open EventSource on the SSE endpoint → drive a progress
 * log from the events → render stats + a dense, searchable, expandable table.
 */
export default function StorageAnalyzerPage() {
  const [repos, setRepos] = useState([]);
  const [repo, setRepo] = useState('');
  const [loadingRepos, setLoadingRepos] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const [running, setRunning] = useState(false);
  const [phase, setPhase] = useState('init');
  const [pct, setPct] = useState(0);
  const [log, setLog] = useState([]);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  const [query, setQuery] = useState('');
  const [expanded, setExpanded] = useState(() => new Set());

  const esRef = useRef(null);

  const loadRepos = async (refresh = false) => {
    if (refresh) setRefreshing(true);
    try {
      const data = await api.get(`/storage/repos${refresh ? '?refresh=true' : ''}`);
      setRepos(data ?? []);
      if (data?.length && !repo) setRepo(data[0].name);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingRepos(false);
      setRefreshing(false);
    }
  };

  useEffect(() => { loadRepos(false); /* eslint-disable-line react-hooks/exhaustive-deps */ }, []);
  useEffect(() => () => esRef.current?.close(), []);

  const append = (line) => setLog((p) => [...p.slice(-200), line]);

  function start() {
    if (!repo || running) return;
    setRunning(true);
    setError('');
    setResult(null);
    setPhase('init');
    setPct(0);
    setLog([]);
    setExpanded(new Set());

    const base = import.meta.env.VITE_API_BASE_URL || '/api';
    // We already know the format from the repo list — passing it saves the
    // backend an extra round trip to Nexus to look it up again.
    const knownFormat = repos.find((r) => r.name === repo)?.format;
    const qs = knownFormat ? `?format=${encodeURIComponent(knownFormat)}` : '';
    const es = new EventSource(`${base}/storage/${encodeURIComponent(repo)}/analyze/stream${qs}`);
    esRef.current = es;

    const safeParse = (raw, fallback = {}) => {
      try { return JSON.parse(raw); } catch { return fallback; }
    };

    const cleanup = () => {
      es.close();
      esRef.current = null;
      setRunning(false);
    };

    const onPhase = (e) => {
      const d = safeParse(e.data);
      setPhase(d.phase || 'init');
      append(`▸ ${d.message || PHASES[d.phase] || d.phase || ''}`);
    };
    const onProgress = (e) => {
      const d = safeParse(e.data);
      if (typeof d.percent === 'number') setPct(d.percent);
      if (d.message) append(`  ${d.message}`);
    };
    const finish = (d) => {
      if (!d || typeof d !== 'object') { cleanup(); return; }
      setResult(d);
      setPct(100);
      append(`✓ done — ${formatNumber(d?.items?.length ?? 0)} items`);
      cleanup();
      const top = (d?.items ?? []).slice(0, 3).map((i) => i?.name).filter(Boolean);
      setExpanded(new Set(top));
    };

    es.addEventListener('phase', onPhase);
    es.addEventListener('progress', onProgress);
    es.addEventListener('cache', (e) => { append('• cached result'); finish(safeParse(e.data).result); });
    es.addEventListener('result', (e) => finish(safeParse(e.data).result));
    es.addEventListener('error', () => {
      // EventSource fires 'error' on disconnect AND on transport failure.
      // Check readyState: CLOSED means it gave up; otherwise it's auto-reconnecting.
      if (es.readyState === EventSource.CLOSED) {
        setError('Connection closed. Click Analyze to retry.');
        append('✗ connection closed');
        cleanup();
      }
    });
  }

  function cancel() {
    esRef.current?.close();
    esRef.current = null;
    setRunning(false);
    append('— cancelled —');
  }

  const filtered = useMemo(() => {
    if (!result?.items) return [];
    const q = query.trim().toLowerCase();
    if (!q) return result.items;
    return result.items
      .map((it) => {
        if (!it || !it.name) return null;
        if (it.name.toLowerCase().includes(q)) return it;
        const versions = (it.versions || []).filter((v) => (v.version || '').toLowerCase().includes(q));
        return versions.length ? { ...it, versions } : null;
      })
      .filter(Boolean);
  }, [result, query]);

  const toggle = (name) =>
    setExpanded((p) => {
      const n = new Set(p);
      n.has(name) ? n.delete(name) : n.add(name);
      return n;
    });

  const stats = result?.stats;
  const isDocker = result?.mode === 'docker';

  return (
    <div className="p-6">
      <div className="mb-5">
        <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">Storage Analyzer</h1>
        <p className="mt-0.5 font-mono text-[11px] text-slate-500 dark:text-slate-500">
          physical sizes + per-component breakdown · supports any repository format
        </p>
      </div>

      {/* Controls */}
      <div className="mb-5 flex flex-wrap items-end gap-3 border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900/40">
        <div>
          <label className="mb-1 block font-mono text-[10px] uppercase tracking-wider text-slate-500">Repository</label>
          <select
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            disabled={running || loadingRepos}
            className="min-w-[16rem] border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200"
          >
            {loadingRepos && <option>loading…</option>}
            {!loadingRepos && !repos.length && <option value="">no repositories</option>}
            {repos.map((r) => (
              <option key={r.name} value={r.name}>{r.name}</option>
            ))}
          </select>
        </div>
        <button
          onClick={() => loadRepos(true)}
          disabled={running || refreshing}
          title="Refresh repository list (bypasses cache)"
          className="flex items-center gap-1.5 border border-slate-300 px-2.5 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-500 transition-colors hover:text-slate-800 disabled:opacity-50 dark:border-slate-700 dark:text-slate-400 dark:hover:text-slate-100"
        >
          <Icon name="refresh" size={13} className={refreshing ? 'animate-spin' : ''} /> {refreshing ? '···' : 'Refresh'}
        </button>
        {!running ? (
          <button
            onClick={start}
            disabled={!repo}
            className="flex items-center gap-1.5 border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 transition-colors hover:bg-sky-100 disabled:opacity-40 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40"
          >
            <Icon name="play" size={13} /> Analyze
          </button>
        ) : (
          <button
            onClick={cancel}
            className="flex items-center gap-1.5 border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            <Icon name="refresh" size={13} /> Cancel
          </button>
        )}
        {repo && (
          <span className="font-mono text-[10px] text-slate-400 dark:text-slate-600">
            {repos.find((r) => r.name === repo)?.format ?? '?'} · {repos.find((r) => r.name === repo)?.type ?? '?'}
          </span>
        )}
      </div>

      {/* Progress */}
      {running && (
        <div className="mb-5 border border-sky-200 bg-sky-50/50 p-3 dark:border-sky-900 dark:bg-sky-950/20">
          <div className="flex items-center justify-between font-mono text-[11px]">
            <span className="text-sky-700 dark:text-sky-300">{PHASES[phase] || phase}</span>
            <span className="text-sky-600 tabular-nums dark:text-sky-400">{pct}%</span>
          </div>
          <div className="mt-2 h-1 w-full bg-sky-100 dark:bg-sky-950">
            <div className="h-full bg-sky-500 transition-all duration-300" style={{ width: `${pct}%` }} />
          </div>
          {log.length > 0 && (
            <pre className="mt-2 max-h-24 overflow-y-auto whitespace-pre-wrap font-mono text-[10px] leading-relaxed text-slate-500 dark:text-slate-500">
              {log.join('\n')}
            </pre>
          )}
        </div>
      )}

      {error && (
        <div className="mb-5 flex items-center gap-2 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-400">
          <Icon name="alert" size={14} /> {error}
        </div>
      )}

      {/* Stats */}
      {stats && (
        <div className="mb-6 grid grid-cols-1 gap-px border border-slate-200 bg-slate-200 sm:grid-cols-4 dark:border-slate-800 dark:bg-slate-800">
          <Stat label="Total Disk" bytes={stats.total_bytes} sub="raw physical size" tone="info" />
          <Stat
            label={isDocker ? "Active Payload" : "Referenced"}
            bytes={stats.active_bytes}
            sub={isDocker ? `${percent(stats.active_bytes, stats.total_bytes)} · deduped` : 'all assets referenced'}
            tone="ok"
          />
          {isDocker && (
            <Stat
              label="Wasted / Dangling"
              bytes={stats.wasted_bytes}
              sub={`${percent(stats.wasted_bytes, stats.total_bytes)} · cleanup candidate`}
              tone="bad"
            />
          )}
          <Stat
            label={isDocker ? "Tags" : "Components"}
            count={stats.item_count}
            sub={stats.asset_count ? `${formatNumber(stats.asset_count)} assets` : undefined}
          />
        </div>
      )}

      {/* Item / version table */}
      {result && (
        <div>
          <div className="mb-2 flex items-center justify-between gap-3">
            <h2 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">
              {result.format} · {result.mode} · {formatNumber(filtered.length)} of {formatNumber(result.items.length)} items
            </h2>
            <div className="relative">
              <span className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-slate-400">
                <Icon name="search" size={12} />
              </span>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="filter…"
                className="w-56 border border-slate-300 bg-white py-1 pl-7 pr-2 font-mono text-xs text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200"
              />
            </div>
          </div>

          <div className="border border-slate-200 dark:border-slate-800">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60">
                  <th className="w-6 px-2 py-2" />
                  <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Name</th>
                  <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-wider text-slate-500">{isDocker ? 'Tags' : 'Versions'}</th>
                  <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-wider text-slate-500">Size</th>
                  <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Created</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr><td colSpan={5} className="px-3 py-8 text-center font-mono text-xs text-slate-400 dark:text-slate-600">no items match "{query}"</td></tr>
                ) : (
                  filtered.map((it) => {
                    if (!it || !it.name) return null;
                    const open = expanded.has(it.name);
                    return (
                      <React.Fragment key={it.name}>
                        <tr onClick={() => toggle(it.name)} className="cursor-pointer border-b border-slate-100 hover:bg-slate-50 dark:border-slate-800/60 dark:hover:bg-slate-800/30">
                          <td className="px-2 py-2 text-slate-400 dark:text-slate-500"><Icon name="chevron" size={12} className={open ? 'rotate-90' : ''} /></td>
                          <td className="px-3 py-2 font-mono text-slate-800 dark:text-slate-200">{it.name}</td>
                          <td className="px-3 py-2 text-right font-mono tabular-nums text-slate-500 dark:text-slate-400">{formatNumber(it.version_count || 0)}</td>
                          <td className="px-3 py-2 text-right font-mono tabular-nums text-slate-800 dark:text-slate-200">{formatBytes(it.total_bytes || 0)}</td>
                          {/* Newest push across this item's versions — size alone
                              doesn't tell you whether something is worth keeping. */}
                          <td className="px-3 py-2 font-mono text-xs text-slate-400 dark:text-slate-600" title={it.last_pushed_at || 'unknown'}>
                            {it.last_pushed_at ? relativeTime(it.last_pushed_at) : '—'}
                          </td>
                        </tr>
                        {open && (it.versions || []).map((v) => (
                          <tr key={`${it.name}-${v.version}`} className="border-b border-slate-50 bg-slate-50/50 dark:border-slate-800/40 dark:bg-slate-900/30">
                            <td className="px-2" />
                            <td className="px-3 py-1.5 pl-8 font-mono text-xs text-slate-500 dark:text-slate-400">#{v.version}</td>
                            <td />
                            <td className="px-3 py-1.5 text-right font-mono tabular-nums text-xs text-slate-500 dark:text-slate-400">{formatBytes(v.size_bytes || 0)}</td>
                            <td className="px-3 py-1.5 font-mono text-xs text-slate-400 dark:text-slate-600" title={v.created_at || 'unknown'}>
                              {v.created_at ? formatDateTime(v.created_at) : '—'}
                            </td>
                          </tr>
                        ))}
                      </React.Fragment>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
          <p className="mt-2 font-mono text-[10px] text-slate-400 dark:text-slate-600">
            {isDocker
              ? 'per-tag size is logical (no cross-tag dedup); active payload dedupes shared layers.'
              : 'every asset is referenced by its component — no wasted space by definition.'}
          </p>
        </div>
      )}

      {!result && !running && !error && (
        <div className="border border-dashed border-slate-300 p-12 text-center dark:border-slate-800">
          <div className="mx-auto mb-2 text-slate-400 dark:text-slate-600"><Icon name="hdd" size={24} /></div>
          <p className="font-mono text-xs text-slate-500 dark:text-slate-500">
            select a repository and click <span className="text-slate-700 dark:text-slate-300">Analyze</span>
          </p>
        </div>
      )}
    </div>
  );
}
