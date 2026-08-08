import { useEffect, useState } from 'react';
import { api } from '../../lib/api.js';
import DataTable from '../../components/DataTable.jsx';
import Modal from '../../components/Modal.jsx';
import Badge from '../../components/Badge.jsx';
import Icon from '../../components/Icon.jsx';
import { formatDateTime } from '../../lib/format.js';

/** Retention policies: rule-based cleanup. CRUD + dry-run preview + run. */
export default function RetentionPage() {
  const [policies, setPolicies] = useState([]);
  const [repos, setRepos] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(null);
  const [preview, setPreview] = useState(null);
  const [msg, setMsg] = useState('');

  const loadRepos = async () => {
    try { setRepos(await api.get('/storage/repos')); } catch (_) {}
  };

  const load = async () => {
    setLoading(true);
    try { setPolicies(await api.get('/retention/policies')); } catch (_) {}
    setLoading(false);
  };
  useEffect(() => { load(); loadRepos(); }, []);

  const runPreview = async (id) => {
    setMsg(''); setPreview(null);
    try { setPreview(await api.post(`/retention/policies/${id}/preview`)); }
    catch (e) { setMsg(`preview failed: ${e.message}`); }
  };
  const runNow = async (id) => {
    setMsg('');
    try {
      const r = await api.post(`/retention/policies/${id}/run`);
      setMsg(`started: job ${r.job_id.slice(0, 8)} — see Background Jobs`);
    } catch (e) { setMsg(`failed: ${e.message}`); }
  };

  // DELETE /retention/policies/{id} and POST /retention/run-all both existed
  // server-side with no caller: policies could be created and edited but never
  // removed, and the only way to run every policy at once was the API.
  const remove = async (p) => {
    if (!confirm(`Delete the retention policy "${p.name}"?\n\nAlready-deleted tags are not restored.`)) return;
    setMsg('');
    try {
      await api.delete(`/retention/policies/${p.id}`);
      setMsg(`Policy "${p.name}" deleted.`);
      load();
    } catch (e) { setMsg(`delete failed: ${e.message}`); }
  };

  const runAll = async (dryRun) => {
    if (!dryRun && !confirm('Run EVERY enabled retention policy now?\n\nThis deletes tags. Preview first if you are unsure.')) return;
    setMsg('');
    try {
      const r = await api.post(`/retention/run-all?dry_run=${dryRun}`);
      setMsg(dryRun
        ? `Dry run queued: job ${String(r.job_id || '').slice(0, 8)} — results in Background Jobs, nothing deleted.`
        : `All policies running: job ${String(r.job_id || '').slice(0, 8)} — see Background Jobs.`);
    } catch (e) { setMsg(`run-all failed: ${e.message}`); }
  };

  const columns = [
    { key: 'name', header: 'Name', mono: true, className: 'text-slate-800 dark:text-slate-200' },
    { key: 'repo', header: 'Repo', mono: true },
    {
      key: 'keep_last_n', header: 'Rule',
      render: (_, p) => (
        <span className="font-mono text-xs text-slate-500 dark:text-slate-400">
          {[
            p.keep_last_n != null ? `keep last ${p.keep_last_n}/image` : null,
            p.delete_older_than_days != null ? `older than ${p.delete_older_than_days}d` : null,
          ].filter(Boolean).join(' · ') || '—'}
        </span>
      ),
    },
    { key: 'enabled', header: 'State', render: (e) => <Badge tone={e ? 'ok' : 'neutral'}>{e ? 'on' : 'off'}</Badge> },
    { key: 'last_run_at', header: 'Last run', mono: true, render: (v) => formatDateTime(v) },
    {
      key: 'id', header: '', render: (_, p) => (
        <div className="flex justify-end gap-1">
          <button onClick={(e) => { e.stopPropagation(); runPreview(p.id); }} title="Dry-run preview"
            className="border border-slate-200 px-2 py-0.5 font-mono text-[10px] uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">preview</button>
          <button onClick={(e) => { e.stopPropagation(); runNow(p.id); }} title="Run now (deletes!)"
            className="border border-rose-200 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40">run</button>
          <button onClick={(e) => { e.stopPropagation(); remove(p); }} title="Delete this policy"
            className="border border-rose-200 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 disabled:opacity-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40">delete</button>
        </div>
      ),
    },
  ];

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">Retention & Cleanup</h1>
          <p className="mt-0.5 font-mono text-[11px] text-slate-500 dark:text-slate-500">
            rule-based deletion · physical blobs are reclaimed via compaction
          </p>
        </div>
        <div className="flex items-center gap-2">
        <button onClick={() => runAll(true)} title="Dry run every enabled policy — deletes nothing"
          className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">Preview all</button>
        <button onClick={() => runAll(false)} title="Run every enabled policy now (deletes!)"
          className="border border-rose-200 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-rose-600 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40">Run all</button>
        <button onClick={() => setEditing({ policy: null })}
          className="flex items-center gap-1.5 border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
          <Icon name="plus" size={13} /> New policy
        </button>
        </div>
      </div>

      {msg && <div className="mb-3 border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-xs text-slate-600 dark:border-slate-800 dark:bg-slate-900/40 dark:text-slate-400">{msg}</div>}

      {loading ? (
        <div className="border border-slate-200 px-3 py-10 text-center font-mono text-xs text-slate-400 dark:border-slate-800 dark:text-slate-600">loading…</div>
      ) : (
        <DataTable columns={columns} rows={policies} onRowClick={(p) => setEditing({ policy: p })} />
      )}

      {editing && <PolicyModal initial={editing.policy} repos={repos} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />}
      {preview && <PreviewModal preview={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}

const INPUT = 'w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';

function PolicyModal({ initial, repos, onClose, onSaved }) {
  const isEdit = !!initial;
  const [form, setForm] = useState({
    name: initial?.name ?? '',
    repo: initial?.repo ?? '',
    keep_last_n: initial?.keep_last_n ?? '',
    delete_older_than_days: initial?.delete_older_than_days ?? '',
    enabled: initial?.enabled ?? true,
  });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError('');
    try {
      const body = {
        name: form.name,
        repo: form.repo,
        keep_last_n: form.keep_last_n === '' ? null : Number(form.keep_last_n),
        delete_older_than_days: form.delete_older_than_days === '' ? null : Number(form.delete_older_than_days),
        enabled: form.enabled,
      };
      if (isEdit) await api.patch(`/retention/policies/${initial.id}`, body);
      else await api.post('/retention/policies', body);
      onSaved();
    } catch (err) { setError(err.message); }
    setBusy(false);
  };

  return (
    <Modal open onClose={onClose} title={isEdit ? `Edit ${initial.name}` : 'New retention policy'}
      footer={<>
        <button onClick={onClose} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">Cancel</button>
        <button onClick={submit} disabled={busy} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">{busy ? '···' : 'Save'}</button>
      </>}>
      <form onSubmit={submit} className="space-y-3">
        <Field label="Name"><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={INPUT} /></Field>
        <Field label="Repository">
          <select value={form.repo} onChange={(e) => setForm({ ...form, repo: e.target.value })} className={INPUT}>
            <option value="">— select repository —</option>
            {repos.map((r) => <option key={r.name} value={r.name}>{r.name} ({r.format})</option>)}
          </select>
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Keep last N per image (blank = ignore)">
            <input type="number" value={form.keep_last_n} onChange={(e) => setForm({ ...form, keep_last_n: e.target.value })} placeholder="3" className={INPUT} />
          </Field>
          <Field label="Delete older than (days, blank = ignore)">
            <input type="number" value={form.delete_older_than_days} onChange={(e) => setForm({ ...form, delete_older_than_days: e.target.value })} placeholder="3" className={INPUT} />
          </Field>
        </div>
        <p className="font-mono text-[10px] text-slate-400 dark:text-slate-600">
          both conditions apply when set together · “keep last N” counts tags <span className="text-slate-500">within each image</span>, not across the whole repository ·
          physical blobs are reclaimed by the Nexus “Compact blob store” task, triggered after a delete
        </p>
        <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
          <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} className="accent-sky-500" />
          <span className="font-mono text-xs">enabled</span>
        </label>
        {error && <div className="font-mono text-xs text-rose-600 dark:text-rose-400">{error}</div>}
      </form>
    </Modal>
  );
}

function PreviewModal({ preview, onClose }) {
  return (
    <Modal open onClose={onClose} wide title="Cleanup preview (dry-run)"
      footer={<button onClick={onClose} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">Close</button>}>
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-2 font-mono text-xs">
          <div className="border border-slate-200 p-2 dark:border-slate-800"><div className="text-slate-500">repo</div><div className="text-slate-800 dark:text-slate-200">{preview.repo}</div></div>
          <div className="border border-slate-200 p-2 dark:border-slate-800">
            <div className="text-slate-500">{preview.dry_run ? 'would delete' : 'deleted'}</div>
            <div className="text-rose-600 dark:text-rose-400">{preview.dry_run ? preview.candidate_count : preview.deleted}</div>
          </div>
        </div>

        {/* A run that deletes nothing must say why. Silent failures here were
            indistinguishable from "the policy matched nothing". */}
        {preview.failed_count > 0 && (
          <div className="border border-rose-200 bg-rose-50 px-3 py-2 dark:border-rose-800 dark:bg-rose-950/30">
            <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-rose-700 dark:text-rose-400">
              {preview.failed_count} delete{preview.failed_count === 1 ? '' : 's'} failed
            </div>
            {(preview.failures || []).map((f) => (
              <div key={`${f.name}:${f.version}`} className="font-mono text-[11px] text-rose-700 dark:text-rose-300">
                {f.name}:{f.version} — {f.reason}
              </div>
            ))}
          </div>
        )}
        {preview.skipped_undated > 0 && (
          <div className="border border-amber-200 bg-amber-50 px-3 py-2 font-mono text-[11px] text-amber-700 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-400">
            {preview.skipped_undated} component{preview.skipped_undated === 1 ? '' : 's'} skipped: Nexus reports no
            timestamp for them, so an age rule cannot judge them. They are never deleted on a guess.
          </div>
        )}
        {preview.compact?.triggered === false && (
          <div className="border border-amber-200 bg-amber-50 px-3 py-2 font-mono text-[11px] text-amber-700 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-400">
            Disk space not reclaimed yet: {preview.compact.reason}
          </div>
        )}
        {preview.candidates?.length > 0 && (
          <div className="max-h-72 overflow-y-auto border border-slate-200 dark:border-slate-800">
            <table className="w-full border-collapse text-sm">
              <thead><tr className="border-b border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60">
                <th className="px-3 py-1.5 text-left font-mono text-[10px] uppercase text-slate-500">Name</th>
                <th className="px-3 py-1.5 text-left font-mono text-[10px] uppercase text-slate-500">Version</th>
              </tr></thead>
              <tbody>
                {preview.candidates.map((c) => (
                  <tr key={c.id} className="border-b border-slate-100 last:border-0 dark:border-slate-800/60">
                    <td className="px-3 py-1.5 font-mono text-xs text-slate-800 dark:text-slate-200">{c.name}</td>
                    <td className="px-3 py-1.5 font-mono text-xs text-slate-500 dark:text-slate-400">{c.version || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {preview.candidate_count > preview.candidates.length && (
              <div className="px-3 py-2 text-center font-mono text-[10px] text-slate-400 dark:text-slate-600">+ {preview.candidate_count - preview.candidates.length} more</div>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}

function Field({ label, children }) {
  return (<div><div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">{label}</div>{children}</div>);
}
