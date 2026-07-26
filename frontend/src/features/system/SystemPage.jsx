import { useState } from 'react';
import { api } from '../../lib/api.js';
import Modal from '../../components/Modal.jsx';
import Icon from '../../components/Icon.jsx';

/** System operations: backup (task trigger + DB download) + Nexus sync. */
export default function SystemPage() {
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const [syncOpen, setSyncOpen] = useState(false);

  const triggerBackup = async () => {
    setErr(''); setMsg('');
    try {
      const r = await api.post('/system/backup');
      setMsg(`Backup queued: job ${r.job_id.slice(0, 8)} — watch it under Background Jobs.`);
    } catch (e) { setErr(e.message); }
  };

  const downloadDb = async () => {
    setErr(''); setMsg('');
    try {
      const base = import.meta.env.VITE_API_BASE_URL || '/api';
      const resp = await fetch(`${base}/system/backup/db`, { credentials: 'include' });
      if (!resp.ok) {
        const txt = await resp.text().catch(() => '');
        throw new Error(`Export failed (${resp.status}): ${txt.slice(0, 150)}`);
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `nexus-export-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')}.json`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
      setMsg('Export downloaded successfully.');
    } catch (e) { setErr(e.message); }
  };

  return (
    <div className="p-6">
      <h1 className="mb-5 text-base font-medium text-slate-900 dark:text-slate-100">System</h1>

      <div className="grid max-w-3xl grid-cols-1 gap-4">
        <section className="border border-slate-200 p-4 dark:border-slate-800">
          <h2 className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Backup</h2>
          <p className="mb-3 font-mono text-[11px] text-slate-500 dark:text-slate-500">
            Export all repository configs + asset manifests as a downloadable JSON file. Works on any Nexus version — useful for migration and recovery.
          </p>
          <div className="flex flex-wrap gap-2">
            <button onClick={triggerBackup} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
              Trigger backup task
            </button>
            <button onClick={downloadDb} className="flex items-center gap-1.5 border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">
              <Icon name="download" size={13} /> Export metadata (JSON)
            </button>
          </div>
        </section>

        <section className="border border-slate-200 p-4 dark:border-slate-800">
          <h2 className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Sync (Nexus → Nexus)</h2>
          <p className="mb-3 font-mono text-[11px] text-slate-500 dark:text-slate-500">
            Copy all components from a repository on this Nexus to a repository on another Nexus instance. Docker images are skipped (registry push is separate).
          </p>
          <button onClick={() => setSyncOpen(true)} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
            New sync job
          </button>
        </section>
      </div>

      {msg && <div className="mt-4 border border-emerald-200 bg-emerald-50 px-3 py-2 font-mono text-xs text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-400">{msg}</div>}
      {err && <div className="mt-4 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{err}</div>}

      {syncOpen && <SyncModal onClose={() => setSyncOpen(false)} />}
    </div>
  );
}

const INPUT = 'w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';

function SyncModal({ onClose }) {
  const [form, setForm] = useState({
    source_repo: '', target_base_url: '', target_username: '', target_password: '', target_repo: '', verify_ssl: true,
  });
  const [msg, setMsg] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError('');
    try {
      const r = await api.post('/system/sync', form);
      setMsg(`Sync queued: job ${r.job_id.slice(0, 8)} — watch progress under Background Jobs.`);
    } catch (err) { setError(err.message); }
    setBusy(false);
  };

  return (
    <Modal open onClose={onClose} wide title="New sync job"
      footer={<>
        <button onClick={onClose} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">Cancel</button>
        <button onClick={submit} disabled={busy} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">{busy ? '···' : 'Start sync'}</button>
      </>}>
      <form onSubmit={submit} className="space-y-3">
        <Field label="Source repository (on this Nexus)"><input value={form.source_repo} onChange={(e) => setForm({ ...form, source_repo: e.target.value })} placeholder="maven-releases" className={INPUT} /></Field>
        <div className="border-t border-slate-200 pt-3 dark:border-slate-800">
          <div className="mb-2 font-mono text-[10px] uppercase tracking-wider text-slate-500">Target Nexus</div>
          <div className="space-y-3">
            <Field label="Base URL"><input value={form.target_base_url} onChange={(e) => setForm({ ...form, target_base_url: e.target.value })} placeholder="https://other-nexus.example.com" className={INPUT} /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Username"><input value={form.target_username} onChange={(e) => setForm({ ...form, target_username: e.target.value })} className={INPUT} /></Field>
              <Field label="Password"><input type="password" value={form.target_password} onChange={(e) => setForm({ ...form, target_password: e.target.value })} className={INPUT} /></Field>
            </div>
            <Field label="Target repository"><input value={form.target_repo} onChange={(e) => setForm({ ...form, target_repo: e.target.value })} placeholder="maven-releases" className={INPUT} /></Field>
            <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
              <input type="checkbox" checked={form.verify_ssl} onChange={(e) => setForm({ ...form, verify_ssl: e.target.checked })} className="accent-sky-500" />
              <span className="font-mono text-xs">verify target SSL</span>
            </label>
          </div>
        </div>
        {msg && <div className="font-mono text-xs text-emerald-600 dark:text-emerald-400">{msg}</div>}
        {error && <div className="font-mono text-xs text-rose-600 dark:text-rose-400">{error}</div>}
      </form>
    </Modal>
  );
}

function Field({ label, children }) {
  return (<div><div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">{label}</div>{children}</div>);
}
