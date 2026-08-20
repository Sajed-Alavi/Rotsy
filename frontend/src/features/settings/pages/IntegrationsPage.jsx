import { useEffect, useState } from 'react';
import { Link } from 'react-router';
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
      <section className="mx-auto max-w-3xl border border-slate-200 p-4 dark:border-slate-800">
        <p className="font-mono text-xs text-slate-500 dark:text-slate-500">Integration management requires admin (system:execute) permission.</p>
      </section>
    );
  }

  return (
    <div className="mx-auto grid max-w-3xl grid-cols-1 gap-6">
      <NexusCard />
      <GitHubCard />
      <GitLabCard />
      <SonarCard />
      <TelegramCard />
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
    } catch (_) { console.debug('SonarQube config fetch failed', _); }
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
            verify SSL (keep on in production){' '}
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
  const [installations, setInstallations] = useState([]);
  const [syncing, setSyncing] = useState(null);
  const [syncMsg, setSyncMsg] = useState('');
  const [connecting, setConnecting] = useState(false);
  const [connectMsg, setConnectMsg] = useState('');
  const [publicForm, setPublicForm] = useState({ full_name: '', project_id: '' });
  const [publicBusy, setPublicBusy] = useState(false);
  const [publicMsg, setPublicMsg] = useState('');
  const [err, setErr] = useState('');

  const load = async () => {
    try { setData(await api.get('/modules/github/status')); } catch (e) { setErr(e.message); }
    try { setInstallUrl((await api.get('/modules/github/install-url')).url); } catch (_) { console.debug('install-url unavailable — App may not be configured yet', _); }
    try { setInstallations(await api.get('/modules/github/installations')); } catch (_) { console.debug('installations fetch failed — not configured yet', _); }
  };

  useEffect(() => {
    load();
    // GitHub redirects back here (a real page navigation) after the App
    // Manifest flow finishes — see routers/github.py:manifest_callback.
    const params = new URLSearchParams(window.location.search);
    if (params.has('github_connected')) {
      setConnectMsg('GitHub App created and connected — install it on an account or org below.');
      window.history.replaceState({}, '', window.location.pathname);
      load();
    } else if (params.has('github_installed')) {
      setConnectMsg('Installation complete — sync repositories below, then connect one to a Project.');
      window.history.replaceState({}, '', window.location.pathname);
      load();
    } else if (params.has('github_error')) {
      setErr(`GitHub connection failed (${params.get('github_error')}). Try again.`);
      window.history.replaceState({}, '', window.location.pathname);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const connect = async () => {
    setErr(''); setConnecting(true);
    try {
      const { target_url, manifest, state, warning } = await api.get('/modules/github/manifest-form');
      if (warning && !window.confirm(`${warning}\n\nContinue creating the App without a webhook?`)) {
        setConnecting(false);
        return;
      }
      const form = document.createElement('form');
      form.method = 'post';
      form.action = `${target_url}?state=${encodeURIComponent(state)}`;
      const input = document.createElement('input');
      input.type = 'hidden';
      input.name = 'manifest';
      input.value = JSON.stringify(manifest);
      form.appendChild(input);
      document.body.appendChild(form);
      form.submit(); // navigates to github.com — nothing else runs after this
    } catch (e) { setErr(e.message); setConnecting(false); }
  };

  const sync = async (installationId) => {
    setSyncing(installationId); setSyncMsg(''); setErr('');
    try {
      const repos = await api.post(`/modules/github/installations/${installationId}/sync`, {});
      setSyncMsg(`Found ${repos.length} repositor${repos.length === 1 ? 'y' : 'ies'}. Map one to a Project from that Project's Overview tab.`);
    } catch (e) { setErr(e.message); }
    setSyncing(null);
  };

  const connectPublic = async (e) => {
    e.preventDefault();
    setErr(''); setPublicMsg(''); setPublicBusy(true);
    try {
      const repo = await api.post('/modules/github/public-repositories', {
        full_name: publicForm.full_name, project_id: Number(publicForm.project_id),
      });
      setPublicMsg(`Connected ${repo.full_name} and started its first analysis. Automatic push-triggered analysis isn't available for this repo (no App installation) — use Run Analysis to re-check it.`);
      setPublicForm({ full_name: '', project_id: '' });
    } catch (ex) { setErr(ex.message); }
    setPublicBusy(false);
  };

  let status;
  if (!data) status = { tone: 'neutral', label: '…' };
  else if (!data.configured) status = { tone: 'warn', label: 'Not Configured' };
  else if (data.connected) status = { tone: 'ok', label: 'Connected' };
  else status = { tone: 'warn', label: 'Configured — No Installations' };

  return (
    <IntegrationCard
      name="GitHub"
      description="Source repositories, push events, and commit status — via a GitHub App."
      status={status}
      facts={data?.configured ? [
        { label: 'App', value: data.app_slug || '—' },
        { label: 'Installations', value: data.installations_count },
        { label: 'Auto-analyze on push', value: data.has_webhook ? 'Yes' : 'No (manual only)' },
      ] : []}
    >
      {connectMsg && <div className="mb-3 border border-emerald-200 bg-emerald-50 px-3 py-2 font-mono text-xs text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-400">{connectMsg}</div>}
      {!data?.configured ? (
        <div className="space-y-3">
          <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">
            Nothing to configure by hand — clicking Connect creates a real GitHub App for this
            Rotsy instance automatically (GitHub's App Manifest flow) and saves its credentials.
            The only manual step is GitHub's own confirmation page.
          </p>
          <button
            onClick={connect}
            disabled={connecting}
            className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40"
          >
            {connecting ? 'Redirecting to GitHub…' : 'Connect to GitHub'}
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">
            Install the App on an organization or account, sync to discover its repositories, then
            connect individual repositories to a Rotsy Project from that Project's Overview tab.
          </p>
          {installUrl && (
            // Same tab, not target="_blank": GitHub redirects to our
            // callback after install, which redirects back to this page —
            // that chain needs to land in the tab the operator is watching.
            <a href={installUrl} className="inline-block border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">
              Install GitHub App
            </a>
          )}

          {installations.length > 0 && (
            <div className="border-t border-slate-100 pt-3 dark:border-slate-800/60">
              <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Installations</div>
              {installations.map((inst) => (
                <div key={inst.id} className="flex items-center justify-between py-1">
                  <span className="font-mono text-xs text-slate-700 dark:text-slate-300">{inst.account_login || `#${inst.installation_id}`}</span>
                  <button
                    onClick={() => sync(inst.installation_id)}
                    disabled={syncing === inst.installation_id}
                    className="border border-slate-300 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-slate-600 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                  >
                    {syncing === inst.installation_id ? '···' : 'Sync Repositories'}
                  </button>
                </div>
              ))}
            </div>
          )}
          {syncMsg && <div className="font-mono text-[11px] text-emerald-600 dark:text-emerald-400">{syncMsg}</div>}

          {!data.has_webhook && <WebhookSecretForm appSlug={data.app_slug} onSaved={load} />}
        </div>
      )}

      <div className="mt-4 border-t border-slate-100 pt-3 dark:border-slate-800/60">
        <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Connect a public repository by URL</div>
        <p className="mb-2 font-mono text-[11px] text-slate-500 dark:text-slate-500">
          For a repo you don't own or administer — no App installation needed. Trade-off: GitHub only
          sends push events to repos the App is installed on, so this repo won't auto-analyze on push,
          only when you click Run Analysis.
        </p>
        <form onSubmit={connectPublic} className="flex flex-wrap gap-2">
          <input
            value={publicForm.full_name}
            onChange={(e) => setPublicForm({ ...publicForm, full_name: e.target.value })}
            placeholder="owner/repo"
            className={`${INPUT} max-w-[14rem]`}
          />
          <input
            value={publicForm.project_id}
            onChange={(e) => setPublicForm({ ...publicForm, project_id: e.target.value })}
            placeholder="Project ID"
            className={`${INPUT} max-w-[8rem]`}
          />
          <button
            type="submit"
            disabled={publicBusy || !publicForm.full_name || !publicForm.project_id}
            className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            {publicBusy ? '···' : 'Connect'}
          </button>
        </form>
        {publicMsg && <div className="mt-2 font-mono text-[11px] text-emerald-600 dark:text-emerald-400">{publicMsg}</div>}
      </div>

      {err && <div className="mt-3 font-mono text-xs text-rose-600 dark:text-rose-400">{err}</div>}
    </IntegrationCard>
  );
}

/**
 * Manual fallback for exactly the case the automatic App Manifest flow
 * cannot handle: when FRONTEND_ORIGIN/WEBHOOK_BASE_URL isn't publicly
 * reachable (typical local dev), manifest_form never asks GitHub for a
 * webhook at all — reconnecting again is a no-op, since the same
 * unreachable address fails the same reachability check every time. This
 * doesn't call GitHub itself; it only tells Rotsy what secret to verify
 * incoming deliveries against, once the operator has added a matching
 * webhook on the App's own GitHub settings page (pointing at whatever
 * *is* reachable — a tunnel, a public deployment).
 */
function WebhookSecretForm({ appSlug, onSaved }) {
  const [secret, setSecret] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  const save = async (e) => {
    e.preventDefault();
    if (!secret.trim()) return;
    setBusy(true); setErr(''); setMsg('');
    try {
      await api.put('/modules/github/webhook-secret', { secret: secret.trim() });
      setMsg('Saved — push-triggered analysis is active as soon as GitHub can reach the webhook URL you set.');
      setSecret('');
      onSaved();
    } catch (ex) { setErr(ex.message); }
    setBusy(false);
  };

  return (
    <form onSubmit={save} className="border-t border-slate-100 pt-3 dark:border-slate-800/60">
      <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Add a webhook manually</div>
      <p className="mb-2 font-mono text-[11px] text-slate-500 dark:text-slate-500">
        No publicly reachable address for Rotsy to register one automatically. Instead,{' '}
        {appSlug ? (
          <a href={`https://github.com/settings/apps/${appSlug}`} target="_blank" rel="noreferrer" className="underline">
            open this App's settings on GitHub
          </a>
        ) : 'open this App\'s settings on GitHub'}, set the Webhook URL to a publicly-reachable address for{' '}
        <code>/api/modules/github/webhooks</code> (a tunnel works for local dev), generate a secret there, and
        paste that same secret below.
      </p>
      <div className="flex flex-wrap gap-2">
        <input
          type="password" value={secret} onChange={(e) => setSecret(e.target.value)}
          placeholder="Webhook secret" className={`${INPUT} max-w-xs`}
        />
        <button
          type="submit" disabled={busy || !secret.trim()}
          className="shrink-0 border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40"
        >
          {busy ? '···' : 'Save'}
        </button>
      </div>
      {msg && <p className="mt-2 font-mono text-[11px] text-emerald-600 dark:text-emerald-400">{msg}</p>}
      {err && <p className="mt-2 font-mono text-[11px] text-rose-600 dark:text-rose-400">{err}</p>}
    </form>
  );
}

/**
 * GitLab has no equivalent of GitHub's App Manifest flow — there is no way
 * to create a scoped credential without the operator generating a Personal
 * Access Token on GitLab's own side first. Settings only handles the
 * account-level connection (one PAT, many repos) — connecting a single
 * repository with its own independent token happens from a Project's
 * Repositories tab instead, where it belongs alongside every other way of
 * adding a repository to a Project, not split across two pages.
 */
function GitLabCard() {
  const [data, setData] = useState(null);
  const [accountForm, setAccountForm] = useState({ gitlab_url: 'https://gitlab.com', token: '' });
  const [syncing, setSyncing] = useState(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  const load = async () => {
    try { setData(await api.get('/modules/gitlab/status')); } catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); }, []);

  const connectAccount = async (e) => {
    e.preventDefault();
    setErr(''); setMsg(''); setSaving(true);
    try {
      await api.post('/modules/gitlab/connections', accountForm);
      setMsg('Account connected. Sync repositories below, then connect one to a Project.');
      setAccountForm((f) => ({ ...f, token: '' }));
      await load();
    } catch (ex) { setErr(ex.message); }
    setSaving(false);
  };

  const sync = async (connectionId) => {
    setSyncing(connectionId); setMsg(''); setErr('');
    try {
      const repos = await api.post(`/modules/gitlab/connections/${connectionId}/sync`, {});
      setMsg(`Found ${repos.length} repositor${repos.length === 1 ? 'y' : 'ies'}. Connect one to a Project from that Project's Overview tab.`);
    } catch (e) { setErr(e.message); }
    setSyncing(null);
  };

  let status;
  if (!data) status = { tone: 'neutral', label: '…' };
  else if (data.connected) status = { tone: 'ok', label: 'Connected' };
  else status = { tone: 'warn', label: 'Not Configured' };

  return (
    <IntegrationCard
      name="GitLab"
      description="Source repositories, push events, and commit status — via a Personal Access Token."
      status={status}
      facts={data?.connections?.length ? [{ label: 'Accounts', value: data.connections.length }] : []}
    >
      <div className="space-y-4">
        <form onSubmit={connectAccount} className="space-y-3">
          <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">
            One token gives Rotsy access to every repository it can see — good for a personal
            account or a single owner managing several projects. To connect one repository with
            its own independent token instead, do that from a Project's Repositories tab.
          </p>
          <div>
            <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">GitLab URL</div>
            <input value={accountForm.gitlab_url} onChange={(e) => setAccountForm({ ...accountForm, gitlab_url: e.target.value })} className={INPUT} />
          </div>
          <div>
            <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Personal Access Token (api scope)</div>
            <input type="password" value={accountForm.token} onChange={(e) => setAccountForm({ ...accountForm, token: e.target.value })} className={INPUT} />
          </div>
          <button type="submit" disabled={saving} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">
            {saving ? '···' : 'Connect Account'}
          </button>
        </form>

        {data?.connections?.length > 0 && (
          <div className="border-t border-slate-100 pt-3 dark:border-slate-800/60">
            <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Connected accounts</div>
            {data.connections.map((c) => (
              <div key={c.id} className="flex items-center justify-between py-1">
                <span className="font-mono text-xs text-slate-700 dark:text-slate-300">{c.account_username} · {c.gitlab_url}</span>
                <button
                  onClick={() => sync(c.id)}
                  disabled={syncing === c.id}
                  className="border border-slate-300 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-slate-600 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                >
                  {syncing === c.id ? '···' : 'Sync Repositories'}
                </button>
              </div>
            ))}
          </div>
        )}

      </div>
      {msg && <div className="mt-3 border border-emerald-200 bg-emerald-50 px-3 py-2 font-mono text-xs text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-400">{msg}</div>}
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
    } catch (_) { console.debug('SonarQube config not saved yet', _); }
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

  let status;
  if (!data) status = { tone: 'neutral', label: '…' };
  else if (!data.configured) status = { tone: 'warn', label: 'Not Configured' };
  else if (data.reachable) status = { tone: 'ok', label: 'Connected' };
  else status = { tone: 'bad', label: 'Error' };

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
          Once healthy, connect a repository and run analysis from{' '}
          <Link to="/code-quality" className="underline">Code Quality</Link> — Rotsy creates the Sonar
          project and issues its analysis token automatically. No manual SonarQube setup required.
          Connection health and version checks also live there now, under{' '}
          <Link to="/code-quality/settings" className="underline">Code Quality → Settings</Link>.
        </p>
      </div>
      {msg && <div className="border border-emerald-200 bg-emerald-50 px-3 py-2 font-mono text-xs text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-400">{msg}</div>}
      {data?.error && data.compatible !== false && <div className="mt-3 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{data.error}</div>}
      {err && <div className="mt-3 font-mono text-xs text-rose-600 dark:text-rose-400">{err}</div>}
    </IntegrationCard>
  );
}

/**
 * Telegram bot: account linking is admin-only and manual (no self-service
 * `/link <code>` flow) — a person messages the bot, it tells them their own
 * chat ID, and the admin pastes that here against their Rotsy account. Once
 * linked, the bot re-derives that person's live RBAC on every tap; this
 * card only manages *who's linked*, never what a linked person can do.
 */
function TelegramCard() {
  const [data, setData] = useState(null);
  const [links, setLinks] = useState([]);
  const [form, setForm] = useState({ token: '' });
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  const load = async () => {
    setErr('');
    try { setData(await api.get('/telegram/status')); } catch (e) { setErr(e.message); }
    try { setLinks(await api.get('/telegram/links')); } catch (_) { console.debug('telegram links fetch failed', _); }
  };
  useEffect(() => { load(); }, []);

  const test = async () => {
    setErr(''); setMsg(''); setTesting(true);
    try { await load(); } catch (e) { setErr(e.message); }
    setTesting(false);
  };

  const testCandidate = async () => {
    setErr(''); setMsg(''); setTesting(true);
    try {
      const r = await api.post('/telegram/config/test', { token: form.token });
      if (r.ok) setMsg(`Connection OK — bot @${r.bot_username}`);
      else setErr(r.error || 'Test failed.');
    } catch (e) { setErr(e.message); }
    setTesting(false);
  };

  const save = async (e) => {
    e.preventDefault();
    setErr(''); setMsg(''); setSaving(true);
    try {
      await api.put('/telegram/config', { token: form.token });
      setMsg('Telegram bot token saved.');
      setForm({ token: '' });
      await load();
    } catch (ex) { setErr(ex.message); }
    setSaving(false);
  };

  const unlink = async (linkId) => {
    if (!confirm('Unlink this Telegram chat from their Rotsy account?')) return;
    setErr('');
    try {
      await api.delete(`/telegram/links/${linkId}`);
      await load();
    } catch (e) { setErr(e.message); }
  };

  let status;
  if (!data) status = { tone: 'neutral', label: '…' };
  else if (!data.configured) status = { tone: 'warn', label: 'Not Configured' };
  else if (data.bot_username) status = { tone: 'ok', label: 'Connected' };
  else status = { tone: 'bad', label: 'Error' };

  return (
    <IntegrationCard
      name="Telegram"
      description="Linked users can check their Project access and trigger analysis from a Telegram bot."
      status={status}
      facts={data?.configured ? [
        { label: 'Bot', value: data.bot_username ? `@${data.bot_username}` : '—' },
        { label: 'Linked Users', value: data.link_count },
      ] : []}
      onTest={data?.configured ? test : undefined}
      testing={testing}
    >
      <div className="space-y-4">
        <form onSubmit={save} className="space-y-3">
          <div>
            <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">
              Bot Token {data?.configured && '(leave blank to keep the current one)'}
            </div>
            <input
              type="password" value={form.token} onChange={(e) => setForm({ token: e.target.value })}
              placeholder={data?.configured ? '••••••••' : 'from @BotFather'} className={INPUT}
            />
          </div>
          <div className="flex gap-2 pt-1">
            <button type="button" onClick={testCandidate} disabled={testing || !form.token} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
              {testing ? '···' : 'Test'}
            </button>
            <button type="submit" disabled={saving || !form.token} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">
              {saving ? '···' : 'Save'}
            </button>
          </div>
        </form>
        <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">
          Create a bot with <a href="https://t.me/BotFather" target="_blank" rel="noreferrer" className="underline">@BotFather</a> on
          Telegram (<code>/newbot</code>) and paste its token above. No self-service linking — a person messages the bot, it
          replies with their own chat ID, and you paste that below against their Rotsy account. Once linked, the bot only
          ever shows that person's own Project access (viewer/member/admin); managing membership or running analysis from
          the bot also needs their account to hold the global <code>projects:write</code> permission — same rule the web
          app already enforces, not a bot-specific restriction.
        </p>

        <div className="border-t border-slate-100 pt-3 dark:border-slate-800/60">
          <div className="mb-2 font-mono text-[10px] uppercase tracking-wider text-slate-500">Linked users</div>
          {links.length === 0 ? (
            <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">No one linked yet.</p>
          ) : (
            <div className="space-y-1">
              {links.map((l) => (
                <div key={l.id} className="flex items-center justify-between py-1">
                  <span className="font-mono text-xs text-slate-700 dark:text-slate-300">
                    {l.username} <span className="text-slate-400 dark:text-slate-600">· chat {l.chat_id}</span>
                  </span>
                  <button
                    onClick={() => unlink(l.id)}
                    className="border border-rose-200 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40"
                  >
                    unlink
                  </button>
                </div>
              ))}
            </div>
          )}
          <AddTelegramLinkForm onAdded={load} />
        </div>
      </div>
      {msg && <div className="border border-emerald-200 bg-emerald-50 px-3 py-2 font-mono text-xs text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-400">{msg}</div>}
      {err && <div className="mt-3 font-mono text-xs text-rose-600 dark:text-rose-400">{err}</div>}
    </IntegrationCard>
  );
}

function AddTelegramLinkForm({ onAdded }) {
  const [q, setQ] = useState('');
  const [candidates, setCandidates] = useState([]);
  const [userId, setUserId] = useState('');
  const [chatId, setChatId] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const rows = await api.get(`/telegram/users${q ? `?q=${encodeURIComponent(q)}` : ''}`);
        if (active) setCandidates(rows);
      } catch (_) { /* keep last known candidates on transient failure */ }
    })();
    return () => { active = false; };
  }, [q]);

  const link = async (e) => {
    e.preventDefault();
    if (!userId || !chatId.trim()) return;
    setBusy(true); setErr('');
    try {
      await api.post('/telegram/links', { user_id: Number(userId), chat_id: Number(chatId.trim()) });
      setUserId(''); setChatId(''); setQ('');
      onAdded();
    } catch (ex) { setErr(ex.message); }
    setBusy(false);
  };

  return (
    <form onSubmit={link} className="mt-3 space-y-2">
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="search users to link…" className={INPUT} />
      <select value={userId} onChange={(e) => setUserId(e.target.value)} className={INPUT}>
        <option value="">select a user…</option>
        {candidates.map((c) => <option key={c.id} value={c.id}>{c.username} ({c.email})</option>)}
      </select>
      <div className="flex gap-2">
        <input value={chatId} onChange={(e) => setChatId(e.target.value)} placeholder="Telegram chat ID" className={INPUT} />
        <button
          type="submit" disabled={busy || !userId || !chatId.trim()}
          className="shrink-0 border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40"
        >
          {busy ? '···' : 'Link'}
        </button>
      </div>
      {err && <p className="font-mono text-xs text-rose-600 dark:text-rose-400">{err}</p>}
    </form>
  );
}
