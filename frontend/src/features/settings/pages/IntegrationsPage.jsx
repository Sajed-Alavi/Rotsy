import { useEffect, useState } from 'react';
import { api } from '../../../lib/api.js';
import { useAuth } from '../../../context/AuthContext.jsx';
import { relativeTime } from '../../../lib/format.js';
import IntegrationCard from '../components/IntegrationCard.jsx';

const INPUT = 'w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';

/**
 * Integrations: one card per external system Rotsy connects to. Each card
 * shows Connected/Disconnected/Error at a glance; the detail form only
 * appears behind "Configure" so this page stays scannable as more
 * integrations are added (GitLab, Harbor, ...) instead of growing into
 * another giant form.
 */
export default function IntegrationsPage() {
  const { user } = useAuth();
  const canEdit = user?.permissions?.includes('system:execute');

  if (!canEdit) {
    return (
      <section className="border border-slate-200 p-4 dark:border-slate-800">
        <p className="font-mono text-xs text-slate-500 dark:text-slate-500">Integration management requires admin (system:execute) permission.</p>
      </section>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-6">
      <NexusCard />
      <GitHubCard />
      <SonarCard />
    </div>
  );
}

function NexusCard() {
  const [info, setInfo] = useState(null);
  const [form, setForm] = useState({ url: '', username: '', password: '', verify_ssl: false });
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const data = await api.get('/settings/nexus');
      setInfo(data);
      if (data.configured) {
        setForm({ url: data.url, username: data.username, password: '', verify_ssl: data.verify_ssl });
      }
    } catch (_) {}
  };
  useEffect(() => { load(); }, []);

  const test = async () => {
    setErr(''); setMsg(''); setTesting(true);
    try {
      const r = await api.post('/settings/nexus/test', form);
      if (r.ok) setMsg(`Connection OK — Nexus ${r.version || ''} (HTTP ${r.status_code})`);
      else setErr(`Test failed: ${r.error || 'HTTP ' + (r.status_code ?? 'unknown')}`);
    } catch (e) { setErr(e.message); }
    setTesting(false);
  };

  const save = async (e) => {
    e.preventDefault();
    setErr(''); setMsg(''); setSaving(true);
    try {
      await api.put('/settings/nexus', form);
      setMsg('Nexus connection saved — applied live.');
      setForm((f) => ({ ...f, password: '' }));
      await load();
    } catch (e) { setErr(e.message); }
    setSaving(false);
  };

  const status = info?.configured
    ? { tone: 'ok', label: 'Connected' }
    : { tone: 'warn', label: 'Not Configured' };

  return (
    <IntegrationCard
      name="Nexus Repository Manager"
      description="Artifact storage + container image discovery for vulnerability scanning."
      status={status}
      facts={info?.configured ? [
        { label: 'Server', value: info.url },
        { label: 'Username', value: info.username },
        { label: 'Verify SSL', value: info.verify_ssl ? 'Yes' : 'No' },
        { label: 'Updated', value: info.updated_at ? relativeTime(info.updated_at) : '—' },
      ] : []}
    >
      <form onSubmit={save} className="space-y-3">
        <div>
          <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">URL</div>
          <input value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} className={INPUT} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Username</div>
            <input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} className={INPUT} />
          </div>
          <div>
            <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Password {info?.password_set && '(set — leave blank to keep)'}</div>
            <input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} placeholder={info?.password_set ? '••••••••' : ''} className={INPUT} />
          </div>
        </div>
        <label className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-300">
          <input type="checkbox" checked={form.verify_ssl} onChange={(e) => setForm({ ...form, verify_ssl: e.target.checked })} className="mt-0.5 accent-sky-500" />
          <span className="font-mono text-xs">
            verify SSL (keep on in production)
            <span className="block text-[10px] text-slate-400 dark:text-slate-600">Applies to this REST connection only. How scanners reach each Docker connector is taken from the connector Nexus reports.</span>
          </span>
        </label>
        <div className="flex gap-2 pt-1">
          <button type="button" onClick={test} disabled={testing} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
            {testing ? '···' : 'Test'}
          </button>
          <button type="submit" disabled={saving} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">
            {saving ? '···' : 'Save'}
          </button>
        </div>
      </form>
      {msg && <div className="mt-3 border border-emerald-200 bg-emerald-50 px-3 py-2 font-mono text-xs text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-400">{msg}</div>}
      {err && <div className="mt-3 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{err}</div>}
    </IntegrationCard>
  );
}

function GitHubCard() {
  const [data, setData] = useState(null);
  const [installUrl, setInstallUrl] = useState('');
  const [err, setErr] = useState('');

  const load = async () => {
    try { setData(await api.get('/modules/github/status')); } catch (e) { setErr(e.message); }
    try { setInstallUrl((await api.get('/modules/github/install-url')).url); } catch (_) { /* App may not be configured yet */ }
  };
  useEffect(() => { load(); }, []);

  const status = !data
    ? { tone: 'neutral', label: '…' }
    : !data.configured
      ? { tone: 'warn', label: 'Not Configured' }
      : data.connected
        ? { tone: 'ok', label: 'Connected' }
        : { tone: 'warn', label: 'Configured — No Installations' };

  return (
    <IntegrationCard
      name="GitHub"
      description="Source repositories, push events, and commit status — via a GitHub App."
      status={status}
      facts={data?.configured ? [
        { label: 'App', value: data.app_slug || '—' },
        { label: 'Installations', value: data.installations_count },
      ] : []}
    >
      {!data?.configured ? (
        <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">
          No GitHub App is configured. Create one at github.com/settings/apps and set
          <code className="mx-1 rounded bg-slate-100 px-1 dark:bg-slate-800">GITHUB_APP_ID</code>,
          <code className="mx-1 rounded bg-slate-100 px-1 dark:bg-slate-800">GITHUB_APP_PRIVATE_KEY</code>, and
          <code className="mx-1 rounded bg-slate-100 px-1 dark:bg-slate-800">GITHUB_WEBHOOK_SECRET</code> for this instance.
        </p>
      ) : (
        <div className="space-y-3">
          <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">
            Install the App on an organization or account to discover its repositories. Once
            installed, connect individual repositories to a Rotsy Project from that Project's page.
          </p>
          {installUrl && (
            <a href={installUrl} target="_blank" rel="noreferrer" className="inline-block border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">
              Install GitHub App
            </a>
          )}
        </div>
      )}
      {err && <div className="mt-3 font-mono text-xs text-rose-600 dark:text-rose-400">{err}</div>}
    </IntegrationCard>
  );
}

function SonarCard() {
  const [data, setData] = useState(null);
  const [form, setForm] = useState({ url: '', token: '' });
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  const load = async () => {
    setErr('');
    try { setData(await api.get('/modules/sonar/status')); } catch (e) { setErr(e.message); }
    try {
      const cfg = await api.get('/modules/sonar/config');
      if (cfg.configured) setForm((f) => ({ ...f, url: cfg.url }));
    } catch (_) { /* no config saved yet */ }
  };
  useEffect(() => { load(); }, []);

  const test = async () => {
    setErr(''); setMsg(''); setTesting(true);
    try {
      // Test the currently-saved connection by re-checking status, which
      // always reflects the effective (DB-first, env-fallback) connection —
      // one code path for both the card's badge and this button.
      await load();
    } catch (e) { setErr(e.message); }
    setTesting(false);
  };

  const testCandidate = async () => {
    setErr(''); setMsg(''); setTesting(true);
    try {
      const r = await api.post('/modules/sonar/config/test', form);
      if (r.ok) setMsg(`Connection OK — SonarQube ${r.version || ''}${r.compatible === false ? ' (unsupported version)' : ''}`);
      else setErr(r.error || 'Test failed.');
    } catch (e) { setErr(e.message); }
    setTesting(false);
  };

  const save = async (e) => {
    e.preventDefault();
    setErr(''); setMsg(''); setSaving(true);
    try {
      await api.put('/modules/sonar/config', form);
      setMsg('SonarQube connection saved.');
      setForm((f) => ({ ...f, token: '' }));
      await load();
    } catch (ex) { setErr(ex.message); }
    setSaving(false);
  };

  const status = !data
    ? { tone: 'neutral', label: '…' }
    : !data.configured
      ? { tone: 'warn', label: 'Not Configured' }
      : data.reachable
        ? { tone: 'ok', label: 'Connected' }
        : { tone: 'bad', label: 'Error' };

  return (
    <IntegrationCard
      name="SonarQube"
      description="Automatic code quality and security analysis on every push."
      status={status}
      facts={data?.configured ? [
        { label: 'Server', value: data.server_url },
        { label: 'Version', value: data.version || '—' },
        { label: 'Health', value: data.reachable ? 'Healthy' : 'Unreachable' },
        { label: 'Last OK', value: data.last_success_at ? relativeTime(data.last_success_at) : '—' },
      ] : []}
      onTest={data?.configured ? test : undefined}
      testing={testing}
    >
      <div className="space-y-4">
        {data?.configured && data.compatible === false && (
          <div className="border border-amber-200 bg-amber-50 px-3 py-2 font-mono text-xs text-amber-700 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-400">
            {data.error || `SonarQube ${data.version} may not be fully supported (minimum tested: 9.x).`}
          </div>
        )}
        <form onSubmit={save} className="space-y-3">
          <div>
            <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Server URL</div>
            <input value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="https://sonarqube.internal" className={INPUT} />
          </div>
          <div>
            <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">
              Token {data?.configured && '(leave blank to keep the current one)'}
            </div>
            <input type="password" value={form.token} onChange={(e) => setForm({ ...form, token: e.target.value })} placeholder={data?.configured ? '••••••••' : ''} className={INPUT} />
          </div>
          <div className="flex gap-2 pt-1">
            <button type="button" onClick={testCandidate} disabled={testing} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
              {testing ? '···' : 'Test'}
            </button>
            <button type="submit" disabled={saving} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">
              {saving ? '···' : 'Save'}
            </button>
          </div>
        </form>
        <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">
          Once healthy, attach a project to SonarQube analysis below — Rotsy creates the Sonar project and
          issues its analysis token automatically. No manual SonarQube setup required.
        </p>
        <RunAnalysisTool />
      </div>
      {msg && <div className="border border-emerald-200 bg-emerald-50 px-3 py-2 font-mono text-xs text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-400">{msg}</div>}
      {data?.error && data.compatible !== false && <div className="mt-3 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{data.error}</div>}
      {err && <div className="mt-3 font-mono text-xs text-rose-600 dark:text-rose-400">{err}</div>}
    </IntegrationCard>
  );
}

/**
 * Minimal manual-trigger tool: run analysis for a project by id. A stopgap
 * until the full Project page (with a proper repository picker) exists —
 * still calls the exact same backend endpoint/job that endpoint will use.
 */
function RunAnalysisTool() {
  const [projectId, setProjectId] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  const run = async (e) => {
    e.preventDefault();
    setErr(''); setMsg(''); setBusy(true);
    try {
      const r = await api.post(`/modules/sonar/projects/${projectId}/run-analysis`, {});
      setMsg(`Queued job ${r.job_id} for commit ${r.commit_sha.slice(0, 8)} — see Background Jobs for progress.`);
    } catch (ex) { setErr(ex.message); }
    setBusy(false);
  };

  return (
    <div className="border-t border-slate-100 pt-3 dark:border-slate-800/60">
      <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Run analysis manually</div>
      <form onSubmit={run} className="flex gap-2">
        <input
          value={projectId}
          onChange={(e) => setProjectId(e.target.value)}
          placeholder="Project ID"
          className={`${INPUT} max-w-[10rem]`}
        />
        <button type="submit" disabled={busy || !projectId} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
          {busy ? '···' : 'Run Analysis'}
        </button>
      </form>
      {msg && <div className="mt-2 font-mono text-[11px] text-emerald-600 dark:text-emerald-400">{msg}</div>}
      {err && <div className="mt-2 font-mono text-[11px] text-rose-600 dark:text-rose-400">{err}</div>}
    </div>
  );
}
