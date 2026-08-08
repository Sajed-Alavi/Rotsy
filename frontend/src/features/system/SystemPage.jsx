import { useEffect, useState } from 'react';
import { api } from '../../lib/api.js';
import Modal from '../../components/Modal.jsx';
import Icon from '../../components/Icon.jsx';
import Badge from '../../components/Badge.jsx';
import DataTable from '../../components/DataTable.jsx';
import { formatBytes, formatDateTime } from '../../lib/format.js';

const DAYS_OF_WEEK = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const ARCHIVE_STATUS_TONE = { success: 'ok', failed: 'bad' };

/** System operations: backup (task trigger + DB download + real archive + schedules) + Nexus sync. */
export default function SystemPage() {
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const [syncOpen, setSyncOpen] = useState(false);

  const [allRepos, setAllRepos] = useState([]);
  const [archiveMode, setArchiveMode] = useState('full');
  const [archiveRepos, setArchiveRepos] = useState([]);
  const [archiveBusy, setArchiveBusy] = useState(false);
  const [archiveHistory, setArchiveHistory] = useState([]);

  const [schedules, setSchedules] = useState([]);
  const [schedulesLoading, setSchedulesLoading] = useState(true);
  const [editingSchedule, setEditingSchedule] = useState(null);

  const loadArchiveHistory = async () => {
    try { setArchiveHistory(await api.get('/system/backup/archive')); } catch { /* best-effort */ }
  };

  const loadSchedules = async () => {
    setSchedulesLoading(true);
    try { setSchedules(await api.get('/system/backup/schedules')); } catch { /* best-effort */ }
    setSchedulesLoading(false);
  };

  useEffect(() => {
    api.get('/storage/repos').then(setAllRepos).catch(() => {});
    loadArchiveHistory();
    loadSchedules();
  }, []);

  const runScheduleNow = async (id) => {
    setErr(''); setMsg('');
    try {
      const r = await api.post(`/system/backup/schedules/${id}/run`);
      setMsg(`Scheduled backup queued: job ${r.job_id.slice(0, 8)} — watch it under Background Jobs.`);
      setTimeout(loadArchiveHistory, 4000);
    } catch (e) { setErr(e.message); }
  };

  const deleteSchedule = async (s) => {
    if (!confirm(`Delete the backup schedule "${s.name}"?\n\nArchives it already created are kept; nothing new will run.`)) return;
    setErr('');
    try {
      await api.delete(`/system/backup/schedules/${s.id}`);
      loadSchedules();
    } catch (e) { setErr(e.message); }
  };

  const scheduleColumns = [
    { key: 'name', header: 'Name', mono: true, className: 'text-slate-800 dark:text-slate-200' },
    {
      key: 'mode', header: 'Target',
      render: (mode, s) => (
        <span className="font-mono text-xs text-slate-500 dark:text-slate-400">
          {mode}{mode === 'selective' && s.repos?.length ? ` (${s.repos.join(', ')})` : ''}
        </span>
      ),
    },
    { key: 'frequency', header: 'Cadence', render: (_, s) => <span className="font-mono text-xs text-slate-500 dark:text-slate-400">{describeCadence(s)}</span> },
    { key: 'enabled', header: 'State', render: (e) => <Badge tone={e ? 'ok' : 'neutral'}>{e ? 'on' : 'off'}</Badge> },
    { key: 'last_run_at', header: 'Last run', mono: true, render: (v) => formatDateTime(v) },
    { key: 'next_run_at', header: 'Next run', mono: true, render: (v) => formatDateTime(v) },
    {
      key: 'id', header: '', render: (_, s) => (
        <div className="flex justify-end gap-1">
          <button onClick={(e) => { e.stopPropagation(); runScheduleNow(s.id); }} title="Run now"
            className="border border-sky-200 px-2 py-0.5 font-mono text-[10px] uppercase text-sky-600 hover:bg-sky-50 dark:border-sky-800 dark:text-sky-400 dark:hover:bg-sky-950/40">run</button>
          <button onClick={(e) => { e.stopPropagation(); deleteSchedule(s); }} title="Delete this schedule"
            className="border border-rose-200 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40">delete</button>
        </div>
      ),
    },
  ];

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
                      <td className="px-3 py-1.5"><Badge tone={ARCHIVE_STATUS_TONE[r.status] || 'info'}>{r.status}</Badge></td>
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
          <div className="mb-1 flex items-center justify-between">
            <h2 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Scheduled backups</h2>
            <button onClick={() => setEditingSchedule({ schedule: null })}
              className="flex items-center gap-1.5 border border-slate-300 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
              <Icon name="plus" size={12} /> New schedule
            </button>
          </div>
          <p className="mb-3 font-mono text-[11px] text-slate-500 dark:text-slate-500">
            Run archive backups automatically on a daily, weekly, monthly, or custom cron cadence. Scheduled runs are written as compressed <span className="text-slate-600 dark:text-slate-400">.tar.gz</span> archives and pruned by each schedule's own retention rule.
          </p>
          {schedulesLoading ? (
            <div className="border border-slate-200 px-3 py-6 text-center font-mono text-xs text-slate-400 dark:border-slate-800 dark:text-slate-600">loading…</div>
          ) : (
            <DataTable columns={scheduleColumns} rows={schedules} empty="No scheduled backups configured." onRowClick={(s) => setEditingSchedule({ schedule: s })} />
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
      {editingSchedule && (
        <ScheduleModal
          initial={editingSchedule.schedule}
          repos={allRepos}
          onClose={() => setEditingSchedule(null)}
          onSaved={() => { setEditingSchedule(null); loadSchedules(); }}
        />
      )}
    </div>
  );
}

function describeCadence(s) {
  switch (s.frequency) {
    case 'daily': return `daily @ ${s.time_of_day}`;
    case 'weekly': return `weekly, ${DAYS_OF_WEEK[s.day_of_week] ?? '?'} @ ${s.time_of_day}`;
    case 'monthly': return `monthly, day ${s.day_of_month} @ ${s.time_of_day}`;
    case 'cron': return s.cron_expression;
    default: return s.frequency;
  }
}

const INPUT = 'w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';

function ScheduleModal({ initial, repos, onClose, onSaved }) {
  const isEdit = !!initial;
  const [form, setForm] = useState({
    name: initial?.name ?? '',
    mode: initial?.mode ?? 'full',
    repos: initial?.repos ?? [],
    frequency: initial?.frequency ?? 'daily',
    time_of_day: initial?.time_of_day ?? '02:00',
    day_of_week: initial?.day_of_week ?? 0,
    day_of_month: initial?.day_of_month ?? 1,
    cron_expression: initial?.cron_expression ?? '',
    retention_keep_last: initial?.retention_keep_last ?? '',
    retention_max_age_days: initial?.retention_max_age_days ?? '',
    enabled: initial?.enabled ?? true,
  });
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const buildBody = () => ({
    name: form.name,
    mode: form.mode,
    repos: form.mode === 'selective' ? form.repos : null,
    frequency: form.frequency,
    time_of_day: form.frequency === 'cron' ? null : form.time_of_day,
    day_of_week: form.frequency === 'weekly' ? Number(form.day_of_week) : null,
    day_of_month: form.frequency === 'monthly' ? Number(form.day_of_month) : null,
    cron_expression: form.frequency === 'cron' ? form.cron_expression : null,
    retention_keep_last: form.retention_keep_last === '' ? null : Number(form.retention_keep_last),
    retention_max_age_days: form.retention_max_age_days === '' ? null : Number(form.retention_max_age_days),
    enabled: form.enabled,
  });

  const runPreview = async () => {
    setError(''); setPreview(null);
    try {
      // Preview against the saved schedule if editing, or best-effort against
      // form values before the first save is not supported server-side (the
      // endpoint reads the persisted row) — so this only previews an
      // already-saved schedule.
      if (!isEdit) { setError('Save the schedule once to preview its next run time.'); return; }
      const r = await api.get(`/system/backup/schedules/${initial.id}/preview`);
      setPreview(r.next_run_at);
    } catch (e) { setError(e.message); }
  };

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError('');
    try {
      const body = buildBody();
      if (isEdit) await api.patch(`/system/backup/schedules/${initial.id}`, body);
      else await api.post('/system/backup/schedules', body);
      onSaved();
    } catch (err) { setError(err.message); }
    setBusy(false);
  };

  return (
    <Modal open onClose={onClose} title={isEdit ? `Edit ${initial.name}` : 'New backup schedule'}
      footer={<>
        <button onClick={onClose} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">Cancel</button>
        <button onClick={submit} disabled={busy} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">{busy ? '···' : 'Save'}</button>
      </>}>
      <form onSubmit={submit} className="space-y-3">
        <Field label="Name"><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={INPUT} /></Field>

        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-1.5 text-sm text-slate-700 dark:text-slate-300">
            <input type="radio" name="schedule-mode" checked={form.mode === 'full'} onChange={() => setForm({ ...form, mode: 'full' })} className="accent-sky-500" />
            <span className="font-mono text-xs">full</span>
          </label>
          <label className="flex items-center gap-1.5 text-sm text-slate-700 dark:text-slate-300">
            <input type="radio" name="schedule-mode" checked={form.mode === 'selective'} onChange={() => setForm({ ...form, mode: 'selective' })} className="accent-sky-500" />
            <span className="font-mono text-xs">selective</span>
          </label>
        </div>
        {form.mode === 'selective' && (
          <Field label="Repositories (ctrl/cmd-click for multiple)">
            <select multiple value={form.repos} onChange={(e) => setForm({ ...form, repos: Array.from(e.target.selectedOptions).map((o) => o.value) })} className={`${INPUT} h-24`}>
              {repos.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
            </select>
          </Field>
        )}

        <Field label="Frequency">
          <select value={form.frequency} onChange={(e) => setForm({ ...form, frequency: e.target.value })} className={INPUT}>
            <option value="daily">daily</option>
            <option value="weekly">weekly</option>
            <option value="monthly">monthly</option>
            <option value="cron">custom cron expression</option>
          </select>
        </Field>

        {form.frequency === 'cron' ? (
          <Field label="Cron expression (5-field, server-UTC)">
            <input value={form.cron_expression} onChange={(e) => setForm({ ...form, cron_expression: e.target.value })} placeholder="0 3 * * *" className={`${INPUT} font-mono`} />
          </Field>
        ) : (
          <div className="grid grid-cols-2 gap-3">
            <Field label="Time of day (UTC, HH:MM)">
              <input value={form.time_of_day} onChange={(e) => setForm({ ...form, time_of_day: e.target.value })} placeholder="02:00" className={INPUT} />
            </Field>
            {form.frequency === 'weekly' && (
              <Field label="Day of week">
                <select value={form.day_of_week} onChange={(e) => setForm({ ...form, day_of_week: e.target.value })} className={INPUT}>
                  {DAYS_OF_WEEK.map((d, i) => <option key={d} value={i}>{d}</option>)}
                </select>
              </Field>
            )}
            {form.frequency === 'monthly' && (
              <Field label="Day of month (clamped to month length)">
                <input type="number" min={1} max={31} value={form.day_of_month} onChange={(e) => setForm({ ...form, day_of_month: e.target.value })} className={INPUT} />
              </Field>
            )}
          </div>
        )}

        <div className="grid grid-cols-2 gap-3">
          <Field label="Keep last N archives (blank = ignore)">
            <input type="number" value={form.retention_keep_last} onChange={(e) => setForm({ ...form, retention_keep_last: e.target.value })} placeholder="7" className={INPUT} />
          </Field>
          <Field label="Delete archives older than (days, blank = ignore)">
            <input type="number" value={form.retention_max_age_days} onChange={(e) => setForm({ ...form, retention_max_age_days: e.target.value })} placeholder="30" className={INPUT} />
          </Field>
        </div>
        <p className="font-mono text-[10px] text-slate-400 dark:text-slate-600">
          both retention conditions apply when set together · only archives this schedule created are ever pruned · scheduled archives are compressed .tar.gz, unlike on-demand backups
        </p>

        <div className="flex items-center justify-between">
          <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
            <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} className="accent-sky-500" />
            <span className="font-mono text-xs">enabled</span>
          </label>
          <button type="button" onClick={runPreview} className="border border-slate-200 px-2 py-1 font-mono text-[10px] uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">preview next run</button>
        </div>
        {preview && <div className="font-mono text-xs text-sky-600 dark:text-sky-400">next run: {formatDateTime(preview)}</div>}
        {error && <div className="font-mono text-xs text-rose-600 dark:text-rose-400">{error}</div>}
      </form>
    </Modal>
  );
}

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
