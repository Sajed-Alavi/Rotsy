import { useEffect, useState } from 'react';
import { api } from '../../lib/api.js';
import { useAuth } from '../../context/AuthContext.jsx';
import Badge from '../../components/Badge.jsx';
import Icon from '../../components/Icon.jsx';
import { relativeTime } from '../../lib/format.js';

/**
 * Settings page.
 *  - Nexus Connection (admin): URL, username, password, verify SSL, test, save.
 *  - Docker registries (admin): read-only view of what discovery found.
 *  - Push webhook (admin): the values to paste into Nexus.
 *  - Scanner proxy (admin): proxy for vulnerability database downloads.
 *  - Profile / Password (self).
 *
 * There is deliberately no Docker registry URL or port field. Every Docker
 * repository's connector endpoint is discovered from Nexus, so scaling from 7
 * projects to 12 needs nothing here.
 */
export default function SettingsPage() {
  const { user, refreshMe } = useAuth();
  const canEditNexus = user?.permissions?.includes('system:execute');

  return (
    <div className="p-6">
      <h1 className="mb-5 text-base font-medium text-slate-900 dark:text-slate-100">Settings</h1>
      <div className="grid max-w-3xl grid-cols-1 gap-6">
        {canEditNexus ? <NexusSection /> : (
          <section className="border border-slate-200 p-4 dark:border-slate-800">
            <p className="font-mono text-xs text-slate-500 dark:text-slate-500">Nexus connection management requires admin (system:execute) permission.</p>
          </section>
        )}
        {canEditNexus && <RegistryDiscoverySection />}
        {canEditNexus && <WebhookSection />}
        {canEditNexus && <ScannerProxySection />}
        <ProfileSection refreshMe={refreshMe} />
        <PasswordSection />
      </div>
    </div>
  );
}

/**
 * Read-only: the Docker registry endpoint discovered for each repository.
 * This is what used to be a text box the operator had to keep in sync by hand.
 */
function RegistryDiscoverySection() {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const load = async (opts = {}) => {
    setBusy(true); setErr('');
    try {
      const qs = new URLSearchParams({ check: 'true', ...(opts.refresh ? { refresh: 'true' } : {}) });
      setData(await api.get(`/scan/registry?${qs}`));
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };
  useEffect(() => { load(); }, []);

  const registries = Object.values(data?.registries || {});
  const unresolved = Object.entries(data?.unresolved || {});

  return (
    <section className="border border-slate-200 p-4 dark:border-slate-800">
      <div className="mb-1 flex items-center justify-between">
        <h2 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Docker registries · auto-discovered</h2>
        <button onClick={() => load({ refresh: true })} disabled={busy} className="flex items-center gap-1.5 border border-slate-300 px-2 py-0.5 font-mono text-[10px] uppercase text-slate-500 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">
          <Icon name="refresh" size={11} className={busy ? 'animate-spin' : ''} /> re-discover
        </button>
      </div>
      <p className="mb-3 font-mono text-[11px] text-slate-500 dark:text-slate-500">
        Connector ports come from Nexus itself — nothing to configure. New repositories appear
        automatically{data?.source ? ` (source: ${data.source})` : ''}.
      </p>

      {registries.length === 0 && !err && (
        <p className="font-mono text-[11px] text-slate-400 dark:text-slate-600">
          {busy ? 'discovering…' : 'no Docker repositories discovered yet'}
        </p>
      )}

      {registries.length > 0 && (
        <div className="overflow-x-auto border border-slate-200 dark:border-slate-800">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60">
                <th className="px-3 py-1.5 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Repository</th>
                <th className="px-3 py-1.5 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Endpoint</th>
                <th className="px-3 py-1.5 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Type</th>
                <th className="px-3 py-1.5 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Reachable</th>
              </tr>
            </thead>
            <tbody>
              {registries.map((r) => (
                <tr key={r.repo} className="border-b border-slate-100 last:border-0 dark:border-slate-800/60">
                  <td className="px-3 py-1.5 font-mono text-xs text-slate-800 dark:text-slate-200">{r.repo}</td>
                  <td className="px-3 py-1.5 font-mono text-xs text-slate-600 dark:text-slate-400">{r.scheme}://{r.host}:{r.port}</td>
                  <td className="px-3 py-1.5 font-mono text-xs text-slate-500">{r.repo_type}</td>
                  <td className="px-3 py-1.5">
                    {r.probe
                      ? <Badge tone={r.probe.reachable ? 'ok' : 'bad'} title={r.probe.error || r.probe.url}>{r.probe.reachable ? 'yes' : 'no'}</Badge>
                      : <span className="font-mono text-[10px] text-slate-400">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {unresolved.length > 0 && (
        <div className="mt-3 border border-amber-200 bg-amber-50 px-3 py-2 dark:border-amber-800 dark:bg-amber-950/30">
          <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-amber-700 dark:text-amber-400">Not scannable</div>
          {unresolved.map(([repo, reason]) => (
            <div key={repo} className="font-mono text-[11px] text-amber-700 dark:text-amber-400">
              <span className="font-medium">{repo}</span>: {reason}
            </div>
          ))}
        </div>
      )}
      {err && <div className="mt-2 font-mono text-xs text-rose-600 dark:text-rose-400">{err}</div>}
    </section>
  );
}

/** The push webhook: shows the exact values to enter in Nexus. */
function WebhookSection() {
  const [info, setInfo] = useState(null);
  const [reveal, setReveal] = useState(false);
  const [err, setErr] = useState('');

  const load = async () => {
    try { setInfo(await api.get('/scan/webhook')); } catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); }, []);

  const rotate = async () => {
    if (!confirm('Issue a new secret? Scans stop triggering on push until you update the Nexus capability to match.')) return;
    setErr('');
    try { await api.post('/scan/webhook/rotate'); await load(); setReveal(true); }
    catch (e) { setErr(e.message); }
  };

  if (!info) return null;
  return (
    <section className="border border-slate-200 p-4 dark:border-slate-800">
      <h2 className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Scan-on-push webhook</h2>
      <p className="mb-3 font-mono text-[11px] text-slate-500 dark:text-slate-500">
        Nexus calls this endpoint the moment an image is pushed. Create the capability in Nexus once
        per repository; creating capabilities is an action in Nexus, so it is not done for you.
      </p>
      <dl className="mb-3 grid grid-cols-[auto,1fr] gap-x-3 gap-y-1 font-mono text-[11px]">
        <dt className="text-slate-400 dark:text-slate-600">event</dt>
        <dd className="text-slate-700 dark:text-slate-300">{info.event_id}</dd>
        <dt className="text-slate-400 dark:text-slate-600">path</dt>
        <dd className="text-slate-700 dark:text-slate-300">{info.path}</dd>
        <dt className="text-slate-400 dark:text-slate-600">secret</dt>
        <dd className="flex items-center gap-2 text-slate-700 dark:text-slate-300">
          <code className="break-all">{reveal ? info.secret : '•'.repeat(24)}</code>
          <button onClick={() => setReveal(!reveal)} className="border border-slate-200 px-1.5 py-0.5 text-[10px] uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">{reveal ? 'hide' : 'show'}</button>
          {reveal && <button onClick={() => navigator.clipboard?.writeText(info.secret)} className="border border-slate-200 px-1.5 py-0.5 text-[10px] uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">copy</button>}
        </dd>
      </dl>
      <ol className="mb-3 list-decimal space-y-0.5 pl-5 font-mono text-[11px] text-slate-600 dark:text-slate-400">
        {(info.instructions || []).map((step, i) => <li key={i}>{step}</li>)}
      </ol>
      <button onClick={rotate} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">Rotate secret</button>
      {err && <div className="mt-2 font-mono text-xs text-rose-600 dark:text-rose-400">{err}</div>}
    </section>
  );
}

function ScannerProxySection() {
  const [proxy, setProxy] = useState('');
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  useEffect(() => {
    api.get('/settings/scanner-proxy').then((d) => setProxy(d.proxy || '')).catch(() => {});
  }, []);

  const save = async (e) => {
    e.preventDefault();
    setErr(''); setMsg('');
    try {
      await api.put('/settings/scanner-proxy', { proxy });
      setMsg('Scanner proxy saved.');
    } catch (ex) { setErr(ex.message); }
  };

  return (
    <section className="border border-slate-200 p-4 dark:border-slate-800">
      <h2 className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Scanner proxy</h2>
      <p className="mb-3 font-mono text-[11px] text-slate-500 dark:text-slate-500">
        Proxy for downloading vulnerability databases (Trivy DB + Grype DB). Leave empty for direct download.
      </p>
      <form onSubmit={save} className="flex gap-2">
        <input value={proxy} onChange={(e) => setProxy(e.target.value)} placeholder="http://127.0.0.1:2080" className="w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100" />
        <button type="submit" className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">Save</button>
      </form>
      {msg && <div className="mt-2 font-mono text-xs text-emerald-600 dark:text-emerald-400">{msg}</div>}
      {err && <div className="mt-2 font-mono text-xs text-rose-600 dark:text-rose-400">{err}</div>}
    </section>
  );
}

const INPUT = 'w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';

function NexusSection() {
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

  return (
    <section className="border border-slate-200 p-4 dark:border-slate-800">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Nexus connection</h2>
        {info?.configured ? (
          <Badge tone="ok">configured · {relativeTime(info.updated_at)}</Badge>
        ) : (
          <Badge tone="warn">not configured</Badge>
        )}
      </div>
      <form onSubmit={save} className="space-y-3">
        <div>
          <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">URL</div>
          <input value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="http://host.docker.internal:8081" className={INPUT} />
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
          <button type="button" onClick={test} disabled={testing} className="flex items-center gap-1.5 border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
            <Icon name="refresh" size={13} className={testing ? 'animate-spin' : ''} /> Test
          </button>
          <button type="submit" disabled={saving} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">
            {saving ? '···' : 'Save'}
          </button>
        </div>
      </form>
      {msg && <div className="mt-3 border border-emerald-200 bg-emerald-50 px-3 py-2 font-mono text-xs text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-400">{msg}</div>}
      {err && <div className="mt-3 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{err}</div>}
    </section>
  );
}

function ProfileSection({ refreshMe }) {
  const [profile, setProfile] = useState(null);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  useEffect(() => { api.get('/settings/profile').then(setProfile).catch(() => {}); }, []);

  const saveProfile = async (e) => {
    e.preventDefault();
    setErr(''); setMsg('');
    try {
      const updated = await api.patch('/settings/profile', { username: profile.username, email: profile.email });
      setProfile(updated);
      await refreshMe();
      setMsg('Profile updated.');
    } catch (ex) { setErr(ex.message); }
  };

  if (!profile) return null;
  return (
    <section className="border border-slate-200 p-4 dark:border-slate-800">
      <h2 className="mb-3 font-mono text-[10px] uppercase tracking-wider text-slate-500">Profile</h2>
      <form onSubmit={saveProfile} className="space-y-3">
        <div>
          <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Username</div>
          <input value={profile.username} onChange={(e) => setProfile({ ...profile, username: e.target.value })} className={INPUT} />
        </div>
        <div>
          <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Email</div>
          <input value={profile.email} onChange={(e) => setProfile({ ...profile, email: e.target.value })} className={INPUT} />
        </div>
        <button type="submit" className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">Save profile</button>
      </form>
      {msg && <div className="mt-3 border border-emerald-200 bg-emerald-50 px-3 py-2 font-mono text-xs text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-400">{msg}</div>}
      {err && <div className="mt-3 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{err}</div>}
    </section>
  );
}

function PasswordSection() {
  const [pw, setPw] = useState({ current_password: '', new_password: '' });
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  const changePw = async (e) => {
    e.preventDefault();
    setErr(''); setMsg('');
    try {
      await api.post('/settings/password', pw);
      setPw({ current_password: '', new_password: '' });
      setMsg('Password changed.');
    } catch (ex) { setErr(ex.message); }
  };

  return (
    <section className="border border-slate-200 p-4 dark:border-slate-800">
      <h2 className="mb-3 font-mono text-[10px] uppercase tracking-wider text-slate-500">Change password</h2>
      <form onSubmit={changePw} className="space-y-3">
        <div>
          <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Current password</div>
          <input type="password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} className={INPUT} />
        </div>
        <div>
          <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">New password</div>
          <input type="password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} className={INPUT} />
        </div>
        <button type="submit" className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">Update password</button>
      </form>
      {msg && <div className="mt-3 border border-emerald-200 bg-emerald-50 px-3 py-2 font-mono text-xs text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-400">{msg}</div>}
      {err && <div className="mt-3 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{err}</div>}
    </section>
  );
}
