import { useEffect, useMemo, useState } from 'react';
import { useOutletContext } from 'react-router';
import { api } from '../../../lib/api.js';
import Badge from '../../../components/Badge.jsx';
import { relativeTime } from '../../../lib/format.js';

const SEVERITY_TONE = { CRITICAL: 'bad', HIGH: 'bad', MEDIUM: 'warn', LOW: 'neutral' };

/** Smart Insights, chronological, with simple severity/kind filters — not
 * overbuilt, just enough to find "what changed and why" quickly. */
export default function InsightsPage() {
  const { projectId } = useOutletContext();
  const [insights, setInsights] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [severity, setSeverity] = useState('all');
  const [kind, setKind] = useState('all');

  useEffect(() => {
    setLoading(true); setErr('');
    api.get(`/projects/${projectId}/insights`)
      .then(setInsights)
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false));
  }, [projectId]);

  const kinds = useMemo(() => ['all', ...new Set(insights.map((i) => i.kind))], [insights]);
  const filtered = insights.filter(
    (i) => (severity === 'all' || i.severity === severity) && (kind === 'all' || i.kind === kind),
  );

  return (
    <div className="grid grid-cols-1 gap-4">
      <div className="flex gap-2">
        <select value={severity} onChange={(e) => setSeverity(e.target.value)} className="border border-slate-300 bg-white px-2 py-1 font-mono text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-300">
          {['all', 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={kind} onChange={(e) => setKind(e.target.value)} className="border border-slate-300 bg-white px-2 py-1 font-mono text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-300">
          {kinds.map((k) => <option key={k} value={k}>{k.replaceAll('_', ' ')}</option>)}
        </select>
      </div>

      {err && <div className="border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{err}</div>}

      {filtered.length === 0 ? (
        <p className="font-mono text-xs text-slate-400 dark:text-slate-600">{loading ? 'loading…' : 'No insights match.'}</p>
      ) : (
        <div className="grid grid-cols-1 gap-3">
          {filtered.map((ins) => (
            <section key={ins.id} className="border border-slate-200 p-4 dark:border-slate-800">
              <div className="mb-1 flex items-center justify-between">
                <h3 className="font-mono text-sm text-slate-800 dark:text-slate-200">{ins.title}</h3>
                <Badge tone={SEVERITY_TONE[ins.severity] || 'neutral'}>{ins.severity}</Badge>
              </div>
              <div className="mb-2 font-mono text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-600">
                {ins.kind.replaceAll('_', ' ')} · {relativeTime(ins.created_at)}
                {ins.related_commit_sha && ` · commit ${ins.related_commit_sha.slice(0, 8)}`}
              </div>
              {Object.keys(ins.evidence || {}).length > 0 && (
                <dl className="grid grid-cols-2 gap-x-4 gap-y-1 border-t border-slate-100 pt-2 font-mono text-[11px] dark:border-slate-800/60 sm:grid-cols-4">
                  {Object.entries(ins.evidence).map(([k, v]) => (
                    <div key={k}>
                      <dt className="text-slate-400 dark:text-slate-600">{k.replaceAll('_', ' ')}</dt>
                      <dd className="text-slate-700 dark:text-slate-300">{String(v)}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
