import { useEffect, useState } from 'react';
import { api } from '../../lib/api.js';
import Modal from '../../components/Modal.jsx';
import Icon from '../../components/Icon.jsx';
import Badge from '../../components/Badge.jsx';
import { formatBytes, formatDateTime } from '../../lib/format.js';

/** System operations: backup (task trigger + DB download + real archive) + Nexus sync. */
export default function SystemPage() {
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const [syncOpen, setSyncOpen] = useState(false);

  const [allRepos, setAllRepos] = useState([]);
  const [archiveMode, setArchiveMode] = useState('full');
  const [archiveRepos, setArchiveRepos] = useState([]);
  const [archiveBusy, setArchiveBusy] = useState(false);
  const [archiveHistory, setArchiveHistory] = useState([]);

  const loadArchiveHistory = async () => {
    try { setArchiveHistory(await api.get('/system/backup/archive')); } catch { /* best-effort */ }
  };

  useEffect(() => {
    api.get('/storage/repos').then(setAllRepos).catch(() => {});
    loadArchiveHistory();
  }, []);

  const startArchive = async () => {
    setErr(''); setMsg('');
    if (archiveMode === 'selective' && archiveRepos.length === 0) {
      setErr('Select at least one repository for a selective backup.');
      return;
    }
    setArchiveBusy(true);
    try {
      const r = await api.post('/system/backup/archive', {
        mode: archiveMode,
        repos: archiveMode === 'selective' ? archiveRepos : null,
      });
      setMsg(`Archive backup queued: job ${r.job_id.slice(0, 8)} — watch it under Background Jobs.`);
      setTimeout(loadArchiveHistory, 4000);
    } catch (e) { setErr(e.message); }
    setArchiveBusy(false);
  };

  const downloadArchive = async (id) => {
    setErr('');
    try {
      const base = import.meta.env.VITE_API_BASE_URL || '/api';
      const resp = await fetch(`${base}/system/backup/archive/${id}/download`, { credentials: 'include' });
      if (!resp.ok) {
        const txt = await resp.text().catch(() => '');
        throw new Error(`Download failed (${resp.status}): ${txt.slice(0, 150)}`);
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `backup-${id}.zip`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e) { setErr(e.message); }
  };

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
          <h2 className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Archive backup</h2>
          <p className="mb-3 font-mono text-[11px] text-slate-500 dark:text-slate-500">
            Download every asset's actual bytes (not just metadata) from all repositories, or a selected subset, into a dated archive on the backup volume.
          </p>
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-1.5 text-sm text-slate-700 dark:text-slate-300">
              <input type="radio" name="archive-mode" checked={archiveMode === 'full'} onChange={() => setArchiveMode('full')} className="accent-sky-500" />
              <span className="font-mono text-xs">full</span>
            </label>
            <label className="flex items-center gap-1.5 text-sm text-slate-700 dark:text-slate-300">
              <input type="radio" name="archive-mode" checked={archiveMode === 'selective'} onChange={() => setArchiveMode('selective')} className="accent-sky-500" />
              <span className="font-mono text-xs">selective</span>
            </label>
          </div>
          {archiveMode === 'selective' && (
            <div className="mb-3">
              <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Repositories (ctrl/cmd-click for multiple)</div>
              <select multiple value={archiveRepos} onChange={(e) => setArchiveRepos(Array.from(e.target.selectedOptions).map((o) => o.value))} className={`${INPUT} h-28`}>
                {allRepos.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
              </select>
            </div>
          )}
          <button onClick={startArchive} disabled={archiveBusy} className="flex items-center gap-1.5 border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">
            {archiveBusy ? '···' : 'Start backup'}
          </button>

          {archiveHistory.length > 0 && (
            <div className="mt-4 border border-slate-200 dark:border-slate-800">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="border-b border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60">
                    <th className="px-3 py-1.5 text-left font-mono text-[10px] uppercase text-slate-500">Mode</th>
                    <th className="px-3 py-1.5 text-left font-mono text-[10px] uppercase text-slate-500">Status</th>
                    <th className="px-3 py-1.5 text-right font-mono text-[10px] uppercase text-slate-500">Assets</th>
                    <th className="px-3 py-1.5 text-right font-mono text-[10px] uppercase text-slate-500">Size</th>
                    <th className="px-3 py-1.5 text-left font-mono text-[10px] uppercase text-slate-500">Started</th>
                    <th className="px-3 py-1.5 text-right font-mono text-[10px] uppercase text-slate-500">·</th>
                  </tr>
                </thead>
                <tbody>
                  {archiveHistory.map((r) => (
                    <tr key={r.id} className="border-b border-slate-100 last:border-0 dark:border-slate-800/60">
                      <td className="px-3 py-1.5 font-mono text-xs text-slate-700 dark:text-slate-300">{r.mode}</td>
                      <td className="px-3 py-1.5"><Badge tone={r.status === 'success' ? 'ok' : r.status === 'failed' ? 'bad' : 'info'}>{r.status}</Badge></td>
                      <td className="px-3 py-1.5 text-right font-mono tabular-nums text-xs text-slate-500 dark:text-slate-400">{r.asset_count}</td>
                      <td className="px-3 py-1.5 text-right font-mono tabular-nums text-xs text-slate-500 dark:text-slate-400">{formatBytes(r.total_bytes || 0)}</td>
                      <td className="px-3 py-1.5 font-mono text-xs text-slate-400 dark:text-slate-600">{formatDateTime(r.started_at)}</td>
                      <td className="px-3 py-1.5 text-right">
                        {r.status === 'success' && (
                          <button onClick={() => downloadArchive(r.id)} className="border border-slate-200 px-2 py-0.5 font-mono text-[10px] uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">download</button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="border border-slate-200 p-4 dark:border-slate-800">
          <h2 className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Sync (Nexus → Nexus)</h2>
          <p className="mb-3 font-mono text-[11px] text-slate-500 dark:text-slate-500">
            Copy all components from one or more selected repositories on this Nexus to repositories on another Nexus instance. Docker images are skipped (registry push is separate).
          </p>
          <button onClick={() => setSyncOpen(true)} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
            New sync job
          </button>
        </section>
      </div>

      {msg && <div className="mt-4 border border-emerald-200 bg-emerald-50 px-3 py-2 font-mono text-xs text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-400">{msg}</div>}
      {err && <div className="mt-4 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{err}</div>}

      {syncOpen && <SyncModal repos={allRepos} onClose={() => setSyncOpen(false)} />}
    </div>
  );
}

const INPUT = 'w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';

function SyncModal({ repos, onClose }) {
  const [selected, setSelected] = useState([]);
  const [targetNames, setTargetNames] = useState({});
  const [conn, setConn] = useState({ target_base_url: '', target_username: '', target_password: '', verify_ssl: true });
  const [msg, setMsg] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const pickSource = (e) => {
    const names = Array.from(e.target.selectedOptions).map((o) => o.value);
    setSelected(names);
    // Default each newly-selected repo's target name to match the source.
    setTargetNames((prev) => {
      const next = { ...prev };
      for (const n of names) if (!(n in next)) next[n] = n;
      return next;
    });
  };

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError('');
    try {
      const body = {
        ...conn,
        repos: selected.map((source_repo) => ({ source_repo, target_repo: targetNames[source_repo] || source_repo })),
      };
      const r = await api.post('/system/sync', body);
      setMsg(`Sync queued: job ${r.job_id.slice(0, 8)} — watch progress under Background Jobs.`);
    } catch (err) { setError(err.message); }
    setBusy(false);
  };

  return (
    <Modal open onClose={onClose} wide title="New sync job"
      footer={<>
        <button onClick={onClose} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">Cancel</button>
        <button onClick={submit} disabled={busy || selected.length === 0} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">{busy ? '···' : 'Start sync'}</button>
      </>}>
      <form onSubmit={submit} className="space-y-3">
        <Field label="Source repositories (on this Nexus, ctrl/cmd-click for multiple)">
          <select multiple value={selected} onChange={pickSource} className={`${INPUT} h-28`}>
            {repos.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
          </select>
        </Field>

        {selected.length > 0 && (
          <div className="space-y-1.5 border border-slate-200 p-2 dark:border-slate-800">
            <div className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Target repository name (defaults to source)</div>
            {selected.map((s) => (
              <div key={s} className="flex items-center gap-2">
                <span className="w-1/2 truncate font-mono text-xs text-slate-600 dark:text-slate-400">{s}</span>
                <input value={targetNames[s] ?? s} onChange={(e) => setTargetNames({ ...targetNames, [s]: e.target.value })} className={INPUT} />
              </div>
            ))}
          </div>
        )}

        <div className="border-t border-slate-200 pt-3 dark:border-slate-800">
          <div className="mb-2 font-mono text-[10px] uppercase tracking-wider text-slate-500">Target Nexus</div>
          <div className="space-y-3">
            <Field label="Base URL"><input value={conn.target_base_url} onChange={(e) => setConn({ ...conn, target_base_url: e.target.value })} placeholder="https://other-nexus.example.com" className={INPUT} /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Username"><input value={conn.target_username} onChange={(e) => setConn({ ...conn, target_username: e.target.value })} className={INPUT} /></Field>
              <Field label="Password"><input type="password" value={conn.target_password} onChange={(e) => setConn({ ...conn, target_password: e.target.value })} className={INPUT} /></Field>
            </div>
            <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
              <input type="checkbox" checked={conn.verify_ssl} onChange={(e) => setConn({ ...conn, verify_ssl: e.target.checked })} className="accent-sky-500" />
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
