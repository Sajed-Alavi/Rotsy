import { useEffect, useState } from 'react';
import { api } from '../../lib/api.js';
import DataTable from '../../components/DataTable.jsx';
import Modal from '../../components/Modal.jsx';
import Badge from '../../components/Badge.jsx';
import Icon from '../../components/Icon.jsx';
import { formatDateTime } from '../../lib/format.js';

/** User administration (requires users:manage). Theme-aware. */
export default function UsersPage() {
  const [users, setUsers] = useState([]);
  const [roles, setRoles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(null);

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const [u, r] = await Promise.all([api.get('/users'), api.get('/roles')]);
      setUsers(u);
      setRoles(r);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const columns = [
    { key: 'username', header: 'Username', mono: true, className: 'text-slate-800 dark:text-slate-200' },
    { key: 'email', header: 'Email' },
    {
      key: 'roles', header: 'Roles',
      render: (rs) => (
        <span className="flex flex-wrap gap-1">
          {rs.map((r) => <Badge key={r.id} tone={r.is_system ? 'info' : 'neutral'}>{r.name}</Badge>)}
        </span>
      ),
    },
    {
      key: 'is_active', header: 'State',
      render: (a) => <Badge tone={a ? 'ok' : 'bad'}>{a ? 'active' : 'disabled'}</Badge>,
    },
    { key: 'created_at', header: 'Created', mono: true, render: (v) => formatDateTime(v) },
  ];

  // DELETE /users/{id} had no caller. Deactivating is usually the better move —
  // it keeps the audit trail intact and still invalidates every API token the
  // user issued, since token permissions resolve against the live account.
  const remove = async (user) => {
    if (!confirm(`Delete the user "${user.username}"?\n\nDeactivating instead keeps their audit history. Deletion cannot be undone.`)) return;
    try {
      await api.delete(`/users/${user.id}`);
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
        <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">Users</h1>
        <button
          onClick={() => setEditing({ user: null })}
          className="flex items-center gap-1.5 border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          <Icon name="plus" size={13} /> New user
        </button>
      </div>

      {error && <div className="mb-3 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-400">{error}</div>}

      {loading ? (
        <div className="border border-slate-200 px-3 py-10 text-center font-mono text-xs text-slate-400 dark:border-slate-800 dark:text-slate-600">loading…</div>
      ) : (
        <DataTable columns={[...columns, deleteColumn]} rows={users} onRowClick={(u) => setEditing({ user: u })} />
      )}

      {editing && (
        <UserFormModal roles={roles} initial={editing.user} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />
      )}
    </div>
  );
}

const INPUT = 'w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';

function UserFormModal({ roles, initial, onClose, onSaved }) {
  const isEdit = !!initial;
  const [form, setForm] = useState({
    username: initial?.username ?? '',
    email: initial?.email ?? '',
    password: '',
    is_active: initial?.is_active ?? true,
    role_ids: initial?.roles?.map((r) => r.id) ?? [],
  });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      const body = isEdit
        ? { email: form.email, ...(form.password ? { password: form.password } : {}), is_active: form.is_active, role_ids: form.role_ids }
        : form;
      if (isEdit) await api.patch(`/users/${initial.id}`, body);
      else await api.post('/users', body);
      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const toggleRole = (id) =>
    setForm((f) => ({ ...f, role_ids: f.role_ids.includes(id) ? f.role_ids.filter((x) => x !== id) : [...f.role_ids, id] }));

  return (
    <Modal
      open
      onClose={onClose}
      title={isEdit ? `Edit ${initial.username}` : 'New user'}
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
        {!isEdit && (
          <Field label="Username"><input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} className={INPUT} /></Field>
        )}
        <Field label="Email"><input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} className={INPUT} /></Field>
        <Field label={isEdit ? 'New password (optional)' : 'Password'}><input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} className={INPUT} /></Field>
        <Field label="Roles">
          <div className="space-y-1">
            {roles.map((r) => (
              <label key={r.id} className="flex cursor-pointer items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
                <input type="checkbox" checked={form.role_ids.includes(r.id)} onChange={() => toggleRole(r.id)} className="accent-sky-500" />
                <span className="font-mono text-xs">{r.name}</span>
                <span className="font-mono text-[10px] text-slate-400 dark:text-slate-600">{r.is_system ? '· system' : ''}</span>
              </label>
            ))}
          </div>
        </Field>
        <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
          <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} className="accent-sky-500" />
          <span className="font-mono text-xs">active</span>
        </label>
        {error && <div className="font-mono text-xs text-rose-600 dark:text-rose-400">{error}</div>}
      </form>
    </Modal>
  );
}

function Field({ label, children }) {
  return (
    <div>
      <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      {children}
    </div>
  );
}
