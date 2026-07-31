import { useEffect, useState } from 'react';
import { api } from '../../lib/api.js';
import DataTable from '../../components/DataTable.jsx';
import Modal from '../../components/Modal.jsx';
import Badge from '../../components/Badge.jsx';
import Icon from '../../components/Icon.jsx';
import { formatBytes, relativeTime } from '../../lib/format.js';

/** Alert rule management. */
export default function AlertsPage() {
  const [rules, setRules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(null);
  const [error, setError] = useState('');

  const load = async () => {
    setLoading(true);
    try { setRules(await api.get('/alerts')); } catch (_) {}
    setLoading(false);
  };
  useEffect(() => { load(); }, []);

  const columns = [
    { key: 'name', header: 'Name', mono: true, className: 'text-slate-800 dark:text-slate-200' },
    {
      key: 'metric', header: 'Rule',
      render: (_, r) => (
        <span className="font-mono text-xs text-slate-500 dark:text-slate-400">
          {r.metric} {r.condition} {r.threshold > 1e6 ? formatBytes(r.threshold) : r.threshold}
        </span>
      ),
    },
    { key: 'repo_filter', header: 'Repo filter', render: (v) => <span className="font-mono text-xs text-slate-400 dark:text-slate-600">{v || '%'}</span> },
    { key: 'enabled', header: 'State', render: (e) => <Badge tone={e ? 'ok' : 'neutral'}>{e ? 'on' : 'off'}</Badge> },
    {
      key: 'last_triggered_at', header: 'Last fired',
      render: (v) => <span className="font-mono text-xs text-slate-400 dark:text-slate-600">{v ? relativeTime(v) : 'never'}</span>,
    },
    {
      key: 'is_default', header: 'Type',
      render: (d) => <Badge tone={d ? 'info' : 'neutral'}>{d ? 'default' : 'custom'}</Badge>,
    },
  ];

  // DELETE /alerts/{id} had no caller: rules could be created and edited but
  // only ever disabled, never removed.
  const remove = async (rule) => {
    if (!confirm(`Delete the alert rule "${rule.name}"?`)) return;
    try {
      await api.delete(`/alerts/${rule.id}`);
      load();
    } catch (err) { setError(err.message); }
  };

  const deleteColumn = {
    key: '__actions',
    header: '\u00b7',
    headClassName: 'text-right',
    className: 'text-right',
    render: (_v, row) => (
      <button onClick={(e) => { e.stopPropagation(); remove(row); }} className="border border-rose-200 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 disabled:opacity-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40">delete</button>
    ),
  };

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">Alerts</h1>
        <button onClick={() => setEditing({ rule: null })} className="flex items-center gap-1.5 border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
          <Icon name="plus" size={13} /> New alert
        </button>
      </div>
      {error && <div className="mb-3 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-400">{error}</div>}

      {loading ? (
        <div className="border border-slate-200 px-3 py-10 text-center font-mono text-xs text-slate-400 dark:border-slate-800 dark:text-slate-600">loading…</div>
      ) : (
        <DataTable columns={[...columns, deleteColumn]} rows={rules} onRowClick={(r) => setEditing({ rule: r })} />
      )}
      {editing && <AlertModal initial={editing.rule} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />}
    </div>
  );
}

const INPUT = 'w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';

function AlertModal({ initial, onClose, onSaved }) {
  const isEdit = !!initial;
  const [form, setForm] = useState({
    name: initial?.name ?? '',
    metric: initial?.metric ?? 'storage.total',
    condition: initial?.condition ?? '>',
    threshold: initial?.threshold ?? 5368709120, // 5GB default
    repo_filter: initial?.repo_filter ?? '',
    webhook_url: initial?.webhook_url ?? '',
    enabled: initial?.enabled ?? true,
  });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      const body = { ...form, repo_filter: form.repo_filter || null, webhook_url: form.webhook_url || null };
      if (isEdit) await api.patch(`/alerts/${initial.id}`, body);
      else await api.post('/alerts', body);
      onSaved();
    } catch (err) { setError(err.message); }
    setBusy(false);
  };

  return (
    <Modal open onClose={onClose} title={isEdit ? `Edit ${initial.name}` : 'New alert'}
      footer={<>
        <button onClick={onClose} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">Cancel</button>
        <button onClick={submit} disabled={busy} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">{busy ? '···' : 'Save'}</button>
      </>}>
      <form onSubmit={submit} className="space-y-3">
        <Field label="Name"><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={INPUT} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Metric">
            <select value={form.metric} onChange={(e) => setForm({ ...form, metric: e.target.value })} className={INPUT}>
              <option value="storage.total">storage.total (bytes)</option>
              <option value="storage.asset_count">storage.asset_count</option>
              <option value="blobstore.used_pct">blobstore.used_pct (%)</option>
            </select>
          </Field>
          <Field label="Condition">
            <select value={form.condition} onChange={(e) => setForm({ ...form, condition: e.target.value })} className={INPUT}>
              <option value=">">{'>'} greater than</option>
              <option value="<">{'<'} less than</option>
              <option value="==">== equals</option>
            </select>
          </Field>
        </div>
        <Field label="Threshold (bytes, count, or % depending on metric)"><input type="number" value={form.threshold} onChange={(e) => setForm({ ...form, threshold: Number(e.target.value) })} className={INPUT} /></Field>
        <Field label={`${form.metric.startsWith('blobstore.') ? 'Blobstore' : 'Repo'} filter (SQL LIKE pattern, empty = all)`}><input value={form.repo_filter} onChange={(e) => setForm({ ...form, repo_filter: e.target.value })} placeholder="%" className={INPUT} /></Field>
        <Field label="Webhook URL (optional — the rule still evaluates without one)"><input value={form.webhook_url} onChange={(e) => setForm({ ...form, webhook_url: e.target.value })} placeholder="https://hooks.slack.com/..." className={INPUT} /></Field>
        <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
          <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} className="accent-sky-500" />
          <span className="font-mono text-xs">enabled</span>
        </label>
        {error && <div className="font-mono text-xs text-rose-600 dark:text-rose-400">{error}</div>}
      </form>
    </Modal>
  );
}

function Field({ label, children }) {
  return (<div><div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">{label}</div>{children}</div>);
}
