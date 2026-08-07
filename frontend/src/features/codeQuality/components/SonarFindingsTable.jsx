import { useEffect, useState } from 'react';
import Badge from '../../../components/Badge.jsx';
import Icon from '../../../components/Icon.jsx';
import { formatNumber } from '../../../lib/format.js';
import { codeQualityApi } from '../api.js';

const SEVERITIES = ['BLOCKER', 'CRITICAL', 'MAJOR', 'MINOR', 'INFO'];
const TYPES = ['BUG', 'VULNERABILITY', 'CODE_SMELL'];
const SEVERITY_TONE = { BLOCKER: 'bad', CRITICAL: 'bad', MAJOR: 'warn', MINOR: 'info', INFO: 'neutral' };
const TYPE_TONE = { BUG: 'bad', VULNERABILITY: 'bad', CODE_SMELL: 'neutral' };
const PAGE_SIZE = 50;

/**
 * Paginated, filterable, sortable Sonar findings table — same shape as
 * features/scan/components/VulnerabilityTable.jsx, adapted to issue fields.
 * Every repository's *latest successful* analysis only (see the backend's
 * _latest_successful_run_ids) — current state, not a growing history pile.
 */
export default function SonarFindingsTable() {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [severities, setSeverities] = useState([]);
  const [types, setTypes] = useState([]);
  const [qInput, setQInput] = useState('');
  const [q, setQ] = useState('');
  const [sort, setSort] = useState('severity');
  const [order, setOrder] = useState('desc');
  const [page, setPage] = useState(0);

  useEffect(() => {
    const t = setTimeout(() => { setQ(qInput); setPage(0); }, 300);
    return () => clearTimeout(t);
  }, [qInput]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE), sort, order });
    if (severities.length) params.set('severity', severities.join(','));
    if (types.length) params.set('type', types.join(','));
    if (q) params.set('q', q);
    codeQualityApi.findings(params)
      .then((res) => { if (!cancelled) { setItems(res.items); setTotal(res.total); } })
      .catch(() => { if (!cancelled) { setItems([]); setTotal(0); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [severities, types, q, sort, order, page]);

  const toggle = (setter) => (v) => {
    setPage(0);
    setter((cur) => (cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v]));
  };
  const toggleSeverity = toggle(setSeverities);
  const toggleType = toggle(setTypes);

  const toggleSort = (col) => {
    setPage(0);
    if (sort === col) setOrder((o) => (o === 'asc' ? 'desc' : 'asc'));
    else { setSort(col); setOrder('desc'); }
  };

  const sortHeader = (label, col) => (
    <th onClick={() => toggleSort(col)}
      className="cursor-pointer select-none px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">
      <span className="inline-flex items-center gap-1">
        {label}
        {sort === col && <Icon name="chevron" size={10} className={order === 'asc' ? '-rotate-90' : 'rotate-90'} />}
      </span>
    </th>
  );

  const from = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const to = Math.min(total, (page + 1) * PAGE_SIZE);

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          {SEVERITIES.map((s) => (
            <button key={s} onClick={() => toggleSeverity(s)}
              className={`border px-2 py-1 font-mono text-[10px] uppercase tracking-wider ${
                severities.includes(s)
                  ? 'border-sky-400 bg-sky-50 text-sky-700 dark:border-sky-600 dark:bg-sky-950/40 dark:text-sky-300'
                  : 'border-slate-200 text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800'
              }`}>
              {s.toLowerCase()}
            </button>
          ))}
          <span className="mx-1 text-slate-300 dark:text-slate-700">|</span>
          {TYPES.map((t) => (
            <button key={t} onClick={() => toggleType(t)}
              className={`border px-2 py-1 font-mono text-[10px] uppercase tracking-wider ${
                types.includes(t)
                  ? 'border-sky-400 bg-sky-50 text-sky-700 dark:border-sky-600 dark:bg-sky-950/40 dark:text-sky-300'
                  : 'border-slate-200 text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800'
              }`}>
              {t.replace('_', ' ').toLowerCase()}
            </button>
          ))}
        </div>
        <div className="relative">
          <Icon name="search" size={12} className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-slate-400" />
          <input value={qInput} onChange={(e) => setQInput(e.target.value)} placeholder="rule, message, file…"
            className="w-56 border border-slate-300 bg-white py-1 pl-7 pr-2 font-mono text-xs text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100" />
        </div>
      </div>

      <div className="max-h-96 overflow-y-auto border border-slate-200 dark:border-slate-800">
        <table className="w-full border-collapse text-sm">
          <thead className="sticky top-0">
            <tr className="border-b border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60">
              {sortHeader('Severity', 'severity')}
              {sortHeader('Type', 'type')}
              {sortHeader('Rule', 'rule')}
              {sortHeader('File : Line', 'component')}
              <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Message</th>
              <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Effort</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={6} className="px-3 py-8 text-center font-mono text-xs text-slate-400 dark:text-slate-600">loading…</td></tr>
            ) : items.length === 0 ? (
              <tr><td colSpan={6} className="px-3 py-8 text-center font-mono text-xs text-slate-400 dark:text-slate-600">no findings</td></tr>
            ) : items.map((i) => (
              <tr key={i.id} className="border-b border-slate-100 last:border-0 dark:border-slate-800/60">
                <td className="px-3 py-1.5"><Badge tone={SEVERITY_TONE[i.severity] || 'neutral'}>{i.severity}</Badge></td>
                <td className="px-3 py-1.5"><Badge tone={TYPE_TONE[i.type] || 'neutral'}>{i.type?.replace('_', ' ')}</Badge></td>
                <td className="px-3 py-1.5 font-mono text-xs text-slate-700 dark:text-slate-300">{i.rule}</td>
                <td className="px-3 py-1.5 font-mono text-xs text-slate-500 dark:text-slate-400">{i.component}{i.line ? `:${i.line}` : ''}</td>
                <td className="px-3 py-1.5 text-xs text-slate-700 dark:text-slate-300">{i.message}</td>
                <td className="px-3 py-1.5 font-mono text-xs text-slate-500 dark:text-slate-400">{i.effort || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-2 flex items-center justify-between font-mono text-[11px] text-slate-500 dark:text-slate-500">
        <span>{total === 0 ? 'no findings' : `${formatNumber(from)}–${formatNumber(to)} of ${formatNumber(total)}`}</span>
        <div className="flex items-center gap-1">
          <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}
            className="border border-slate-300 px-2 py-1 font-mono text-[10px] uppercase text-slate-600 hover:bg-slate-100 disabled:opacity-40 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">prev</button>
          <button onClick={() => setPage((p) => (to < total ? p + 1 : p))} disabled={to >= total}
            className="border border-slate-300 px-2 py-1 font-mono text-[10px] uppercase text-slate-600 hover:bg-slate-100 disabled:opacity-40 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">next</button>
        </div>
      </div>
    </div>
  );
}
