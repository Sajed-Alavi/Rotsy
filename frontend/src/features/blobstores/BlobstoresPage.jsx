import { useEffect, useState } from 'react';
import { api } from '../../lib/api.js';
import Badge from '../../components/Badge.jsx';
import Modal from '../../components/Modal.jsx';
import Icon from '../../components/Icon.jsx';
import { formatBytes } from '../../lib/format.js';

const INPUT = 'w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';

/** Blobstore management: list + create (File/S3) + delete. */
export default function BlobstoresPage() {
  const [stores, setStores] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  const load = async () => {
    setLoading(true);
    try {
      setStores(await api.get('/blobstores'));
      setErr('');
    } catch (e) {
      setErr(e.message);
      setStores([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, []);

  const remove = async (name) => {
    if (!confirm(`Delete blobstore "${name}"? This cannot be undone.`)) return;
    setMsg(''); setErr('');
    try {
      await api.delete(`/blobstores/${encodeURIComponent(name)}`);
      setMsg(`Blobstore "${name}" deleted.`);
      load();
    } catch (e) { setErr(e.message); }
  };

  return (
    <div className="p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">Blobstores</h1>
          <p className="mt-0.5 font-mono text-[11px] text-slate-500 dark:text-slate-500">physical storage backends · File or S3 · create &amp; delete</p>
        </div>
        <button onClick={() => setCreating(true)} className="flex items-center gap-1.5 border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">
          <Icon name="plus" size={12} /> Create blobstore
        </button>
      </div>

      {msg && <div className="mb-3 border border-emerald-200 bg-emerald-50 px-3 py-2 font-mono text-xs text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-400">{msg}</div>}
      {err && <div className="mb-3 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-400">{err}</div>}

      <div className="border border-slate-200 dark:border-slate-800">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60">
              <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Name</th>
              <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Type</th>
              <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-wider text-slate-500">Blobs</th>
              <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-wider text-slate-500">Total size</th>
              <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-wider text-slate-500">Available</th>
              <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-wider text-slate-500">·</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={6} className="px-3 py-8 text-center font-mono text-xs text-slate-400 dark:text-slate-600">loading…</td></tr>
            ) : stores.length === 0 ? (
              <tr><td colSpan={6} className="px-3 py-8 text-center font-mono text-xs text-slate-400 dark:text-slate-600">no blobstores — click "Create blobstore"</td></tr>
            ) : stores.map((s) => (
              <tr key={s.name} className="border-b border-slate-100 last:border-0 hover:bg-slate-50 dark:border-slate-800/60 dark:hover:bg-slate-800/30">
                <td className="px-3 py-2 font-mono text-slate-800 dark:text-slate-200">{s.name}</td>
                <td className="px-3 py-2"><Badge tone="info">{(s.type || '?').toUpperCase()}</Badge></td>
                <td className="px-3 py-2 text-right font-mono tabular-nums text-slate-500 dark:text-slate-400">{(s.blobCount ?? 0).toLocaleString()}</td>
                <td className="px-3 py-2 text-right font-mono tabular-nums text-slate-800 dark:text-slate-200">{formatBytes(s.totalSizeInBytes || 0)}</td>
                <td className="px-3 py-2 text-right font-mono tabular-nums text-slate-500 dark:text-slate-400">{s.availableSpaceInBytes != null ? formatBytes(s.availableSpaceInBytes) : '—'}</td>
                <td className="px-3 py-2 text-right">
                  <button onClick={() => remove(s.name)} className="border border-rose-200 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40">delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {creating && <CreateModal onClose={() => setCreating(false)} onSaved={(name) => { setCreating(false); setMsg(`Blobstore "${name}" created.`); load(); }} />}
    </div>
  );
}

function Field({ label, children }) {
  return (<div><div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">{label}</div>{children}</div>);
}

function CreateModal({ onClose, onSaved }) {
  const [kind, setKind] = useState('file'); // 'file' | 's3'
  const [form, setForm] = useState({
    name: '', path: '',
    bucket: '', region: 'us-east-1', prefix: '', endpoint: '',
    access_key_id: '', secret_access_key: '', expiration: 3,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError('');
    try {
      if (kind === 'file') {
        if (!form.name || !form.path) throw new Error('name and path are required');
        await api.post('/blobstores/file', { name: form.name, path: form.path });
      } else {
        if (!form.name || !form.bucket) throw new Error('name and bucket are required');
        await api.post('/blobstores/s3', {
          name: form.name, bucket: form.bucket, region: form.region, prefix: form.prefix,
          endpoint: form.endpoint, access_key_id: form.access_key_id,
          secret_access_key: form.secret_access_key, expiration: Number(form.expiration),
        });
      }
      onSaved(form.name);
    } catch (err) { setError(err.message); }
    setBusy(false);
  };

  return (
    <Modal open onClose={onClose} wide title="Create blobstore"
      footer={<>
        <button onClick={onClose} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">Cancel</button>
        <button onClick={submit} disabled={busy} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">{busy ? '···' : 'Create'}</button>
      </>}>
      <form onSubmit={submit} className="space-y-3">
        <div className="flex gap-2">
          {['file', 's3'].map((k) => (
            <button key={k} type="button" onClick={() => setKind(k)}
              className={`border px-3 py-1 font-mono text-xs uppercase ${kind === k ? 'border-sky-400 bg-sky-50 text-sky-700 dark:border-sky-600 dark:bg-sky-950/40 dark:text-sky-300' : 'border-slate-300 text-slate-500 dark:border-slate-700 dark:text-slate-400'}`}>
              {k}
            </button>
          ))}
        </div>

        <Field label="Name"><input value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="my-store" className={INPUT} /></Field>

        {kind === 'file' ? (
          <Field label="Path (absolute, or relative to Nexus data dir)">
            <input value={form.path} onChange={(e) => set('path', e.target.value)} placeholder="my-store  or  /nexus-data/blobs/my-store" className={INPUT} />
          </Field>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Bucket"><input value={form.bucket} onChange={(e) => set('bucket', e.target.value)} placeholder="my-bucket" className={INPUT} /></Field>
              <Field label="Region"><input value={form.region} onChange={(e) => set('region', e.target.value)} placeholder="us-east-1" className={INPUT} /></Field>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Prefix (optional)"><input value={form.prefix} onChange={(e) => set('prefix', e.target.value)} className={INPUT} /></Field>
              <Field label="Expiration (days, -1 off)"><input type="number" value={form.expiration} onChange={(e) => set('expiration', e.target.value)} className={INPUT} /></Field>
            </div>
            <Field label="Endpoint (blank = AWS; set for MinIO/S3-compatible)">
              <input value={form.endpoint} onChange={(e) => set('endpoint', e.target.value)} placeholder="https://minio.internal:9000" className={INPUT} />
            </Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Access key ID"><input value={form.access_key_id} onChange={(e) => set('access_key_id', e.target.value)} className={INPUT} /></Field>
              <Field label="Secret access key"><input type="password" value={form.secret_access_key} onChange={(e) => set('secret_access_key', e.target.value)} className={INPUT} /></Field>
            </div>
          </>
        )}

        {error && <div className="font-mono text-xs text-rose-600 dark:text-rose-400">{error}</div>}
      </form>
    </Modal>
  );
}
