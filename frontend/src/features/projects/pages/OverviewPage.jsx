import { useEffect, useState } from 'react';
import { Link, useOutletContext } from 'react-router';
import { api } from '../../../lib/api.js';
import Badge from '../../../components/Badge.jsx';
import { relativeTime } from '../../../lib/format.js';

const HEALTH_TONE = (score) => (score >= 80 ? 'ok' : score >= 50 ? 'warn' : 'bad');
const SONAR_LANGUAGES = ['python', 'javascript', 'typescript'];

/**
 * Overview: the "understand this project without opening three tools"
 * summary — health score, latest quality gate, latest analysis, connected
 * integrations, and the most recent insights. If GitHub/Sonar aren't
 * connected yet, this is also where that setup happens.
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
      if (latest && latest.status === 'success') {
        try { setGate(await api.get(`/modules/sonar/analysis-runs/${latest.id}/quality-gate`)); }
        catch (_) { setGate(null); }
      }
    } catch (_) { /* no Sonar project connected yet */ }
  };
  useEffect(() => { load(); }, [projectId]);

  const hasGitHub = integrations.some((i) => i.module_key === 'github');
  const hasSonar = integrations.some((i) => i.module_key === 'sonar');

  return (
    <div className="grid grid-cols-1 gap-6">
      {err && <div className="border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{err}</div>}

      {(!hasGitHub || !hasSonar) && (
        <SetupPanel projectId={projectId} needsGitHub={!hasGitHub} needsSonar={!hasSonar} onDone={load} />
      )}

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Tile label="Project Health">
          {health?.has_data ? (
            <span className="flex items-center gap-2">
              <span className="text-2xl font-semibold tabular-nums text-slate-900 dark:text-slate-100">{health.score}</span>
              <span className="font-mono text-[10px] text-slate-400">/ 100</span>
              <Badge tone={HEALTH_TONE(health.score)}>{health.score >= 80 ? 'healthy' : health.score >= 50 ? 'warning' : 'at risk'}</Badge>
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
            {health.factors.map((f, i) => <li key={i}>{f}</li>)}
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
              <Badge tone={ins.severity === 'HIGH' || ins.severity === 'CRITICAL' ? 'bad' : ins.severity === 'MEDIUM' ? 'warn' : 'neutral'}>{ins.severity}</Badge>
            </div>
          ))
        )}
      </section>
    </div>
  );
}

/** Setup panel: picks from discovered-but-unmapped GitHub repositories, and
 * connects SonarQube with a language pick — the two steps needed before
 * "push" actually triggers anything for this project. */
function SetupPanel({ projectId, needsGitHub, needsSonar, onDone }) {
  const [repos, setRepos] = useState([]);
  const [selectedRepo, setSelectedRepo] = useState('');
  const [language, setLanguage] = useState(SONAR_LANGUAGES[0]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  useEffect(() => {
    if (!needsGitHub) return;
    api.get('/modules/github/repositories?unmapped=true').then(setRepos).catch(() => {});
  }, [needsGitHub]);

  const mapRepo = async () => {
    setErr(''); setMsg(''); setBusy(true);
    try {
      await api.post(`/modules/github/repositories/${selectedRepo}/map`, { project_id: projectId });
      setMsg('Repository connected.');
      onDone();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  const connectSonar = async () => {
    setErr(''); setMsg(''); setBusy(true);
    try {
      await api.post('/modules/sonar/projects', { project_id: projectId, language });
      setMsg('SonarQube connected.');
      onDone();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  return (
    <section className="border border-amber-200 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950/30">
      <h2 className="mb-2 font-mono text-[10px] uppercase tracking-wider text-amber-700 dark:text-amber-400">Setup incomplete</h2>

      {needsGitHub && (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {repos.length === 0 ? (
            <p className="font-mono text-[11px] text-amber-700 dark:text-amber-400">
              No unmapped repositories discovered yet. Install and sync the GitHub App from{' '}
              <Link to="/settings/integrations" className="underline">Settings → Integrations</Link>.
            </p>
          ) : (
            <>
              <select value={selectedRepo} onChange={(e) => setSelectedRepo(e.target.value)} className="border border-amber-300 bg-white px-2 py-1 font-mono text-xs text-slate-700 dark:border-amber-800 dark:bg-slate-950 dark:text-slate-300">
                <option value="">Select a repository…</option>
                {repos.map((r) => <option key={r.id} value={r.id}>{r.full_name}</option>)}
              </select>
              <button onClick={mapRepo} disabled={!selectedRepo || busy} className="border border-amber-400 bg-white px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider text-amber-700 hover:bg-amber-100 disabled:opacity-50 dark:border-amber-700 dark:bg-slate-950 dark:text-amber-400">
                Connect Repository
              </button>
            </>
          )}
        </div>
      )}

      {needsSonar && (
        <div className="flex flex-wrap items-center gap-2">
          <select value={language} onChange={(e) => setLanguage(e.target.value)} className="border border-amber-300 bg-white px-2 py-1 font-mono text-xs text-slate-700 dark:border-amber-800 dark:bg-slate-950 dark:text-slate-300">
            {SONAR_LANGUAGES.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
          <button onClick={connectSonar} disabled={busy} className="border border-amber-400 bg-white px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider text-amber-700 hover:bg-amber-100 disabled:opacity-50 dark:border-amber-700 dark:bg-slate-950 dark:text-amber-400">
            Connect SonarQube
          </button>
          <span className="font-mono text-[10px] text-amber-600 dark:text-amber-500">Only languages analyzable without a build step are supported.</span>
        </div>
      )}

      {msg && <div className="mt-2 font-mono text-[11px] text-emerald-700 dark:text-emerald-400">{msg}</div>}
      {err && <div className="mt-2 font-mono text-[11px] text-rose-600 dark:text-rose-400">{err}</div>}
    </section>
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
  return <Badge tone={gate.status === 'OK' ? 'ok' : gate.status === 'WARN' ? 'warn' : 'bad'}>{gate.status}</Badge>;
}
