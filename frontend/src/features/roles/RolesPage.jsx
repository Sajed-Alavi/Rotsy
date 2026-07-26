import { useEffect, useState } from 'react';
import { api } from '../../lib/api.js';
import DataTable from '../../components/DataTable.jsx';
import Modal from '../../components/Modal.jsx';
import Badge from '../../components/Badge.jsx';
import Icon from '../../components/Icon.jsx';

/** Role + permission administration (requires roles:manage). Theme-aware. */
export default function RolesPage() {
  const [roles, setRoles] = useState([]);
  const [perms, setPerms] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(null);

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const [r, p] = await Promise.all([api.get('/roles'), api.get('/roles/permissions')]);
      setRoles(r);
      setPerms(p);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const columns = [
    { key: 'name', header: 'Role', mono: true, className: 'text-slate-800 dark:text-slate-200' },
    { key: 'description', header: 'Description' },
    {
      key: 'permissions', header: 'Permissions',
      render: (ps) => <span className="font-mono text-xs text-slate-500 dark:text-slate-400">{ps.length}</span>,
    },
    {
      key: 'is_system', header: 'Type',
      render: (s) => <Badge tone={s ? 'info' : 'neutral'}>{s ? 'system' : 'custom'}</Badge>,
    },
  ];

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">Roles & Permissions</h1>
        <button
          onClick={() => setEditing({ role: null })}
          className="flex items-center gap-1.5 border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          <Icon name="plus" size={13} /> New role
        </button>
      </div>

      {error && <div className="mb-3 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-400">{error}</div>}

      {loading ? (
        <div className="border border-slate-200 px-3 py-10 text-center font-mono text-xs text-slate-400 dark:border-slate-800 dark:text-slate-600">loading…</div>
      ) : (
        <DataTable columns={columns} rows={roles} onRowClick={(r) => setEditing({ role: r })} />
      )}

      {editing && (
        <RoleFormModal perms={perms} initial={editing.role} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />
      )}
    </div>
  );
}

const INPUT = 'w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';

function RoleFormModal({ perms, initial, onClose, onSaved }) {
  const isEdit = !!initial;
  const isSystem = !!initial?.is_system;
  const [form, setForm] = useState({
    name: initial?.name ?? '',
    description: initial?.description ?? '',
    permission_keys: initial?.permissions?.map((p) => p.key) ?? [],
  });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      if (isEdit) await api.patch(`/roles/${initial.id}`, form);
      else await api.post('/roles', form);
      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const togglePerm = (key) =>
    setForm((f) => ({
      ...f,
      permission_keys: f.permission_keys.includes(key) ? f.permission_keys.filter((k) => k !== key) : [...f.permission_keys, key],
    }));

  return (
    <Modal
      open
      onClose={onClose}
      wide
      title={isEdit ? `Edit ${initial.name}` : 'New role'}
      footer={
        <>
          <button onClick={onClose} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">Cancel</button>
          <button onClick={submit} disabled={busy} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">
            {busy ? '···' : 'Save'}
          </button>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-3">
        <div>
          <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Name</div>
          <input value={form.name} disabled={isSystem} onChange={(e) => setForm({ ...form, name: e.target.value })} className={INPUT} />
        </div>
        <div>
          <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Description</div>
          <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className={INPUT} />
        </div>
        <div>
          <div className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-500">
            Permissions · {form.permission_keys.length} selected
          </div>
          <div className="grid max-h-64 grid-cols-1 gap-1 overflow-y-auto border border-slate-200 bg-white p-2 sm:grid-cols-2 dark:border-slate-800 dark:bg-slate-950">
            {perms.map((p) => (
              <label key={p.key} className="flex cursor-pointer items-start gap-2 text-slate-700 dark:text-slate-300">
                <input type="checkbox" checked={form.permission_keys.includes(p.key)} onChange={() => togglePerm(p.key)} className="mt-0.5 accent-sky-500" />
                <div>
                  <div className="font-mono text-xs text-slate-800 dark:text-slate-200">{p.key}</div>
                  <div className="font-mono text-[10px] text-slate-400 dark:text-slate-600">{p.description}</div>
                </div>
              </label>
            ))}
          </div>
        </div>
        {error && <div className="font-mono text-xs text-rose-600 dark:text-rose-400">{error}</div>}
      </form>
    </Modal>
  );
}
