import { useEffect, useState } from 'react';
import { api } from '../../lib/api.js';
import Badge from '../../components/Badge.jsx';
import Modal from '../../components/Modal.jsx';
import Icon from '../../components/Icon.jsx';

const INPUT = 'w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';

const FORMATS = ['docker', 'maven2', 'npm', 'pypi', 'nuget', 'raw', 'apt', 'yum', 'helm'];
const TYPES = ['hosted', 'proxy', 'group'];
const REPO_TYPE_TONE = { hosted: 'ok', proxy: 'info' };

/** Repository management: list + create (hosted/proxy/group) + delete. */
function RepoRows({ loading, repos, remove }) {
  if (loading) {
    return <tr><td colSpan={5} className="px-3 py-8 text-center font-mono text-xs text-slate-400 dark:text-slate-600">loading…</td></tr>;
  }
  if (repos.length === 0) {
    return <tr><td colSpan={5} className="px-3 py-8 text-center font-mono text-xs text-slate-400 dark:text-slate-600">no repositories — click "Create repository"</td></tr>;
  }
  return repos.map((r) => (
    <tr key={r.name} className="border-b border-slate-100 last:border-0 hover:bg-slate-50 dark:border-slate-800/60 dark:hover:bg-slate-800/30">
      <td className="px-3 py-2 font-mono text-slate-800 dark:text-slate-200">{r.name}</td>
      <td className="px-3 py-2 font-mono text-xs text-slate-500 dark:text-slate-400">{r.format}</td>
      <td className="px-3 py-2"><Badge tone={REPO_TYPE_TONE[r.type] || 'neutral'}>{r.type}</Badge></td>
      <td className="px-3 py-2 font-mono text-[11px] text-slate-400 dark:text-slate-600 truncate max-w-xs" title={r.url}>{r.url}</td>
      <td className="px-3 py-2 text-right">
        <button onClick={() => remove(r.name)} className="border border-rose-200 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40">delete</button>
      </td>
    </tr>
  ));
}

export default function RepositoriesPage() {
  const [repos, setRepos] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  const load = async () => {
    setLoading(true);
    try {
      setRepos(await api.get('/repositories?refresh=true'));
      setErr('');
    } catch (e) {
      setErr(e.message);
      setRepos([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, []);

  const remove = async (name) => {
    if (!confirm(`Delete repository "${name}"? This cannot be undone.`)) return;
    setMsg(''); setErr('');
    try {
      await api.delete(`/repositories/${encodeURIComponent(name)}`);
      setMsg(`Repository "${name}" deleted.`);
      load();
    } catch (e) { setErr(e.message); }
  };

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">Repositories</h1>
          <p className="mt-0.5 font-mono text-[11px] text-slate-500 dark:text-slate-500">hosted · proxy · group · create &amp; delete</p>
        </div>
        <button onClick={() => setCreating(true)} className="flex items-center gap-1.5 border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">
          <Icon name="plus" size={12} /> Create repository
        </button>
      </div>

      {msg && <div className="mb-3 border border-emerald-200 bg-emerald-50 px-3 py-2 font-mono text-xs text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-400">{msg}</div>}
      {err && <div className="mb-3 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-400">{err}</div>}

      <div className="border border-slate-200 dark:border-slate-800">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60">
              <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Name</th>
              <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Format</th>
              <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Type</th>
              <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">URL</th>
              <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-wider text-slate-500">·</th>
            </tr>
          </thead>
          <tbody>
            <RepoRows loading={loading} repos={repos} remove={remove} />
          </tbody>
        </table>
      </div>

      {creating && <CreateModal repos={repos} onClose={() => setCreating(false)} onSaved={(result) => {
        setCreating(false);
        if (result.warning) {
          setErr(`Repository "${result.name}" created, but: ${result.warning}`);
          setMsg('');
        } else {
          setMsg(`Repository "${result.name}" created.`);
          setErr('');
        }
        load();
      }} />}
    </div>
  );
}

function Field({ label, children }) {
  return (<div><div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">{label}</div>{children}</div>);
}

function CreateModal({ repos, onClose, onSaved }) {
  const [form, setForm] = useState({
    name: '', format: 'docker', type: 'hosted', blob_store: 'default',
    write_policy: 'ALLOW', remote_url: '', members: [],
    docker_http_port: '', docker_https_port: '', docker_force_basic_auth: true,
    anonymous_access: false,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [blobStores, setBlobStores] = useState([]);
  const [blobStoresLoading, setBlobStoresLoading] = useState(true);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const stores = await api.get('/blobstores');
        if (!cancelled) setBlobStores(stores);
      } catch {
        // best-effort; dropdown falls back to the current form value
      } finally {
        if (!cancelled) setBlobStoresLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError('');
    try {
      if (!form.name) throw new Error('name is required');
      const body = {
        name: form.name, format: form.format, type: form.type, blob_store: form.blob_store,
        anonymous_access: form.anonymous_access,
      };
      if (form.type === 'hosted') body.write_policy = form.write_policy;
      if (form.type === 'proxy') {
        if (!form.remote_url) throw new Error('proxy requires a remote URL');
        body.remote_url = form.remote_url;
      }
      if (form.type === 'group') {
        if (!form.members.length) throw new Error('group requires at least one member');
        body.members = form.members;
      }
      if (form.format === 'docker') {
        if (form.docker_http_port) body.docker_http_port = Number(form.docker_http_port);
        if (form.docker_https_port) body.docker_https_port = Number(form.docker_https_port);
        body.docker_force_basic_auth = form.docker_force_basic_auth;
      }
      const result = await api.post('/repositories', body);
      onSaved(result);
    } catch (err) { setError(err.message); }
    setBusy(false);
  };

  const sameFormat = repos.filter((r) => r.format === form.format).map((r) => r.name);

  return (
    <Modal open onClose={onClose} wide title="Create repository"
      footer={<>
        <button onClick={onClose} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">Cancel</button>
        <button onClick={submit} disabled={busy} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">{busy ? '···' : 'Create'}</button>
      </>}>
      <form onSubmit={submit} className="space-y-3">
        <Field label="Name"><input value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="my-repo" className={INPUT} /></Field>
        <div className="grid grid-cols-3 gap-2">
          <Field label="Format">
            <select value={form.format} onChange={(e) => set('format', e.target.value)} className={INPUT}>
              {FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
          </Field>
          <Field label="Type">
            <select value={form.type} onChange={(e) => set('type', e.target.value)} className={INPUT}>
              {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </Field>
          <Field label="Blob store">
            <select value={form.blob_store} onChange={(e) => set('blob_store', e.target.value)} className={INPUT} disabled={blobStoresLoading}>
              {blobStoresLoading && <option value={form.blob_store}>{form.blob_store} (loading…)</option>}
              {!blobStoresLoading && blobStores.length === 0 && <option value="default">default</option>}
              {!blobStoresLoading && blobStores.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
            </select>
          </Field>
        </div>

        <div>
          <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
            <input type="checkbox" checked={form.anonymous_access} onChange={(e) => set('anonymous_access', e.target.checked)} className="accent-sky-500" />
            <span className="font-mono text-xs">Anonymous Access (browse + read)</span>
          </label>
          <p className="mt-1 font-mono text-[11px] text-slate-500 dark:text-slate-500">
            Grants a repository-view privilege to Nexus's built-in nx-anonymous role. Requires Nexus's global Anonymous Access to also be enabled, otherwise this has no effect.
          </p>
        </div>

        {form.type === 'hosted' && (
          <Field label="Write policy">
            <select value={form.write_policy} onChange={(e) => set('write_policy', e.target.value)} className={INPUT}>
              <option value="ALLOW">ALLOW (redeploy)</option>
              <option value="ALLOW_ONCE">ALLOW_ONCE (no redeploy)</option>
              <option value="DENY">DENY (read-only)</option>
            </select>
          </Field>
        )}

        {form.type === 'proxy' && (
          <Field label="Remote URL (upstream)">
            <input value={form.remote_url} onChange={(e) => set('remote_url', e.target.value)} placeholder="https://registry-1.docker.io" className={INPUT} />
          </Field>
        )}

        {form.type === 'group' && (
          <Field label={`Members (${form.format} repos, ctrl/cmd-click for multiple)`}>
            <select multiple value={form.members} onChange={(e) => set('members', Array.from(e.target.selectedOptions).map((o) => o.value))} className={`${INPUT} h-28`}>
              {sameFormat.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </Field>
        )}

        {form.format === 'docker' && (
          <div className="grid grid-cols-2 gap-2 border border-slate-200 p-2 dark:border-slate-800">
            <Field label="Docker HTTP port"><input type="number" value={form.docker_http_port} onChange={(e) => set('docker_http_port', e.target.value)} placeholder="e.g. 8082" className={INPUT} /></Field>
            <Field label="Docker HTTPS port"><input type="number" value={form.docker_https_port} onChange={(e) => set('docker_https_port', e.target.value)} placeholder="optional" className={INPUT} /></Field>
            <label className="col-span-2 flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
              <input type="checkbox" checked={form.docker_force_basic_auth} onChange={(e) => set('docker_force_basic_auth', e.target.checked)} className="accent-sky-500" />
              <span className="font-mono text-xs">force basic auth (recommended)</span>
            </label>
          </div>
        )}

        {error && <div className="font-mono text-xs text-rose-600 dark:text-rose-400">{error}</div>}
      </form>
    </Modal>
  );
}
