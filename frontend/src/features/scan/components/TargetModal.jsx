import { useEffect, useState } from 'react';
import Modal from '../../../components/Modal.jsx';
import { scanApi } from '../api.js';

const INPUT = 'w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';

function Field({ label, children }) {
  return (<div><div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">{label}</div>{children}</div>);
}

/** Create or edit a scan target (a repository opted in to scanning). */
export default function TargetModal({ initial, onClose, onSaved }) {
  const isEdit = !!initial;
  const [form, setForm] = useState({
    repo: initial?.repo ?? '',
    enabled: initial?.enabled ?? true,
    auto_scan: initial?.auto_scan ?? true,
    scanners: initial?.scanners ?? '',
  });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [dockerRepos, setDockerRepos] = useState([]);

  useEffect(() => {
    // Load Docker-format repos only for the scanner dropdown.
    scanApi.dockerRepos().then(setDockerRepos).catch(() => {});
  }, []);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError('');
    try {
      if (isEdit) await scanApi.updateTarget(initial.id, form);
      else await scanApi.createTarget(form);
      onSaved();
    } catch (err) { setError(err.message); }
    setBusy(false);
  };

  return (
    <Modal open onClose={onClose} title={isEdit ? `Edit ${initial.repo}` : 'Enable scanning for a repository'}
      footer={<>
        <button onClick={onClose} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">Cancel</button>
        <button onClick={submit} disabled={busy} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">{busy ? '···' : 'Save'}</button>
      </>}>
      <form onSubmit={submit} className="space-y-3">
        <Field label="Repository (Docker only)">
          <select value={form.repo} disabled={isEdit} onChange={(e) => setForm({ ...form, repo: e.target.value })} className={INPUT}>
            <option value="">— select docker repository —</option>
            {dockerRepos.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
          </select>
        </Field>
        <Field label="Scanners (comma-separated, blank = global default)"><input value={form.scanners} onChange={(e) => setForm({ ...form, scanners: e.target.value })} placeholder="trivy,grype" className={INPUT} /></Field>
        <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
          <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} className="accent-sky-500" />
          <span className="font-mono text-xs">enabled</span>
        </label>
        <label className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-300">
          <input type="checkbox" checked={form.auto_scan} onChange={(e) => setForm({ ...form, auto_scan: e.target.checked })} className="mt-0.5 accent-sky-500" />
          <span className="font-mono text-xs">
            scan images pushed from now on{' '}
            <span className="block text-[10px] text-slate-400 dark:text-slate-600">Images already in this repository are recorded as baseline and left unscanned — scan those individually if you want them covered.</span>
          </span>
        </label>
        {error && <div className="font-mono text-xs text-rose-600 dark:text-rose-400">{error}</div>}
      </form>
    </Modal>
  );
}
