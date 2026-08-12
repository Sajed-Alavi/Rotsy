import { useEffect, useState } from 'react';
import { Link, useOutletContext } from 'react-router';
import { api } from '../../../lib/api.js';
import Badge from '../../../components/Badge.jsx';
import { relativeTime } from '../../../lib/format.js';

function HEALTH_TONE(score) {
  if (score >= 80) return 'ok';
  if (score >= 50) return 'warn';
  return 'bad';
}

function healthLabel(score) {
  if (score >= 80) return 'healthy';
  if (score >= 50) return 'warning';
  return 'at risk';
}

function insightTone(severity) {
  if (severity === 'HIGH' || severity === 'CRITICAL') return 'bad';
  if (severity === 'MEDIUM') return 'warn';
  return 'neutral';
}

function gateTone(status) {
  if (status === 'OK') return 'ok';
  if (status === 'WARN') return 'warn';
  return 'bad';
}

/**
 * Overview: the "understand this project without opening three tools"
 * summary — health score (aggregated across every connected repository),
 * latest quality gate, latest analysis, connected integrations, and the
 * most recent insights. Adding/organizing repositories happens on the
 * Repositories tab, not here — a Project can hold many, so that's its own
 * page rather than a one-repo setup wizard bolted onto this one.
 */
export default function OverviewPage() {
  const { projectId } = useOutletContext();
  const [health, setHealth] = useState(null);
  const [integrations, setIntegrations] = useState([]);
  const [latestRun, setLatestRun] = useState(null);
  const [gate, setGate] = useState(null);
  const [insights, setInsights] = useState([]);
  const [err, setErr] = useState('');

  const load = async () => {
    setErr('');
    try {
      const [h, ints, insightRows] = await Promise.all([
        api.get(`/projects/${projectId}/health`),
        api.get(`/projects/${projectId}/integrations`),
        api.get(`/projects/${projectId}/insights`),
      ]);
      setHealth(h);
      setIntegrations(ints);
      setInsights(insightRows.slice(0, 5));
    } catch (e) { setErr(e.message); }

    try {
      const runs = await api.get(`/modules/sonar/projects/${projectId}/analysis-runs`);
      const latest = runs[0] || null;
      setLatestRun(latest);
      if (latest?.status === 'success') {
        try { setGate(await api.get(`/modules/sonar/analysis-runs/${latest.id}/quality-gate`)); }
        catch (_) { setGate(null); console.debug('quality gate fetch failed for latest run', _); }
      }
    } catch (_) { console.debug('no Sonar project connected yet', _); }
  };
  useEffect(() => { load(); }, [projectId]);

  const hasGitHub = integrations.some((i) => i.module_key === 'github');
  const hasSonar = integrations.some((i) => i.module_key === 'sonar');

  return (
    <div className="grid grid-cols-1 gap-6">
      {err && <div className="border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{err}</div>}

      {(!hasGitHub || !hasSonar) && (
        <section className="border border-amber-200 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950/30">
          <h2 className="mb-1 font-mono text-[10px] uppercase tracking-wider text-amber-700 dark:text-amber-400">Setup incomplete</h2>
          <p className="font-mono text-[11px] text-amber-700 dark:text-amber-400">
            No repositories connected yet.{' '}
            <Link to={`/projects/${projectId}/repositories`} className="underline">Add one (or many) on the Repositories tab</Link>.
          </p>
        </section>
      )}

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Tile label="Project Health">
          {health?.has_data ? (
            <span className="flex items-center gap-2">
              <span className="text-2xl font-semibold tabular-nums text-slate-900 dark:text-slate-100">{health.score}</span>
              <span className="font-mono text-[10px] text-slate-400">/ 100</span>
              <Badge tone={HEALTH_TONE(health.score)}>{healthLabel(health.score)}</Badge>
            </span>
          ) : (
            <span className="font-mono text-xs text-slate-400 dark:text-slate-600">no data yet</span>
          )}
        </Tile>
        <Tile label="Quality Gate">
          <QualityGateBadge run={latestRun} gate={gate} />
        </Tile>
        <Tile label="Coverage">
          <span className="font-mono text-lg text-slate-800 dark:text-slate-200">{latestRun?.coverage != null ? `${latestRun.coverage.toFixed(0)}%` : '—'}</span>
        </Tile>
        <Tile label="Latest Analysis">
          <span className="font-mono text-xs text-slate-600 dark:text-slate-400">{latestRun ? relativeTime(latestRun.started_at) : 'never'}</span>
        </Tile>
      </div>

      {health?.factors?.length > 0 && (
        <section className="border border-slate-200 p-4 dark:border-slate-800">
          <h2 className="mb-2 font-mono text-[10px] uppercase tracking-wider text-slate-500">Health factors</h2>
          <ul className="list-disc space-y-0.5 pl-5 font-mono text-[11px] text-slate-600 dark:text-slate-400">
            {health.factors.map((f) => <li key={f}>{f}</li>)}
          </ul>
        </section>
      )}

      <section className="border border-slate-200 dark:border-slate-800">
        <div className="p-4">
          <h2 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Connected integrations</h2>
        </div>
        {integrations.length === 0 ? (
          <div className="border-t border-slate-100 px-4 py-3 font-mono text-xs text-slate-400 dark:border-slate-800/60 dark:text-slate-600">None yet.</div>
        ) : (
          integrations.map((i) => (
            <div key={i.id} className="flex items-center justify-between border-t border-slate-100 px-4 py-2.5 dark:border-slate-800/60">
              <span className="font-mono text-xs capitalize text-slate-700 dark:text-slate-300">{i.module_key}</span>
              <Badge tone={i.status === 'active' ? 'ok' : 'neutral'}>{i.status}</Badge>
            </div>
          ))
        )}
      </section>

      <section className="border border-slate-200 dark:border-slate-800">
        <div className="p-4">
          <h2 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Recent insights</h2>
        </div>
        {insights.length === 0 ? (
          <div className="border-t border-slate-100 px-4 py-3 font-mono text-xs text-slate-400 dark:border-slate-800/60 dark:text-slate-600">No insights yet.</div>
        ) : (
          insights.map((ins) => (
            <div key={ins.id} className="flex items-center justify-between gap-3 border-t border-slate-100 px-4 py-2.5 dark:border-slate-800/60">
              <span className="font-mono text-xs text-slate-700 dark:text-slate-300">{ins.title}</span>
              <Badge tone={insightTone(ins.severity)}>{ins.severity}</Badge>
            </div>
          ))
        )}
      </section>

      {/* Not yet correlated to this specific Project (see docs) — a link
          out, not a filtered view that doesn't actually exist. */}
      <section className="border border-slate-200 dark:border-slate-800">
        <div className="p-4">
          <h2 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Related views</h2>
        </div>
        <Link to="/scan" className="group flex items-center justify-between border-t border-slate-100 px-4 py-2.5 text-slate-700 hover:bg-slate-50 dark:border-slate-800/60 dark:text-slate-300 dark:hover:bg-slate-900/60">
          <span className="font-mono text-xs">Vulnerability Scanning</span>
          <span className="font-mono text-[10px] text-slate-400 dark:text-slate-600">Trivy + Grype, global</span>
        </Link>
        <Link to="/repositories" className="group flex items-center justify-between border-t border-slate-100 px-4 py-2.5 text-slate-700 hover:bg-slate-50 dark:border-slate-800/60 dark:text-slate-300 dark:hover:bg-slate-900/60">
          <span className="font-mono text-xs">Repositories</span>
          <span className="font-mono text-[10px] text-slate-400 dark:text-slate-600">Nexus, global</span>
        </Link>
      </section>
    </div>
  );
}

function Tile({ label, children }) {
  return (
    <div className="border border-slate-200 p-3 dark:border-slate-800">
      <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      {children}
    </div>
  );
}

function QualityGateBadge({ run, gate }) {
  if (!run) return <span className="font-mono text-xs text-slate-400 dark:text-slate-600">—</span>;
  if (run.status !== 'success') return <Badge tone={run.status === 'failed' ? 'bad' : 'neutral'}>{run.status}</Badge>;
  if (!gate) return <span className="font-mono text-xs text-slate-400 dark:text-slate-600">—</span>;
  return <Badge tone={gateTone(gate.status)}>{gate.status}</Badge>;
}
