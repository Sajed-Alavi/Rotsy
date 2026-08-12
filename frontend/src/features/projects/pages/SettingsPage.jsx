import { useEffect, useState } from 'react';
import { useOutletContext } from 'react-router';
import { api } from '../../../lib/api.js';
import { useAuth } from '../../../context/AuthContext.jsx';
import DataTable from '../../../components/DataTable.jsx';
import Modal from '../../../components/Modal.jsx';
import Badge from '../../../components/Badge.jsx';
import Icon from '../../../components/Icon.jsx';
import { formatDateTime } from '../../../lib/format.js';

const INPUT = 'w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';
const ROLE_TONE = { admin: 'bad', member: 'info', viewer: 'neutral' };

/**
 * Who has access to *this* project — the other half of project-scoped
 * access control (see app/core/project_access.py). A user with no row here
 * has no access to this project at all, whatever global permissions they
 * hold; access granted on one project never carries to another.
 */
export default function SettingsPage() {
  const { projectId } = useOutletContext();
  const { user, hasPermission } = useAuth();
  const [members, setMembers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [adding, setAdding] = useState(false);

  const load = async () => {
    setLoading(true); setError('');
    try { setMembers(await api.get(`/projects/${projectId}/members`)); }
    catch (e) { setError(e.message); }
    setLoading(false);
  };
  useEffect(() => { load(); }, [projectId]);

  // A membership row for the current user with role "admin" can manage
  // membership; so can a global admin, who bypasses membership entirely on
  // the backend and so may have no row here at all — the only way this page
  // loaded without one is that bypass, since a non-admin without a row would
  // have been 403'd before reaching this project at all.
  const ownMembership = members.find((m) => m.user_id === user?.id);
  const isBypassAdmin = !loading && !ownMembership;
  const canManage = hasPermission('projects:write') && (ownMembership?.project_role === 'admin' || isBypassAdmin);

  const updateRole = async (member, project_role) => {
    setError('');
    try {
      await api.patch(`/projects/${projectId}/members/${member.id}`, { project_role });
      load();
    } catch (e) { setError(e.message); }
  };

  const removeMember = async (member) => {
    if (!confirm(`Remove ${member.username} from this project?`)) return;
    setError('');
    try {
      await api.delete(`/projects/${projectId}/members/${member.id}`);
      load();
    } catch (e) { setError(e.message); }
  };

  const columns = [
    { key: 'username', header: 'User', mono: true, className: 'text-slate-800 dark:text-slate-200' },
    { key: 'email', header: 'Email', mono: true, className: 'text-slate-500 dark:text-slate-400' },
    {
      key: 'project_role', header: 'Project role',
      render: (v, m) => canManage ? (
        <select
          value={v}
          onChange={(e) => updateRole(m, e.target.value)}
          className="border border-slate-300 bg-white px-1.5 py-1 font-mono text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
        >
          <option value="viewer">viewer</option>
          <option value="member">member</option>
          <option value="admin">admin</option>
        </select>
      ) : <Badge tone={ROLE_TONE[v] || 'neutral'}>{v}</Badge>,
    },
    { key: 'created_at', header: 'Since', mono: true, render: (v) => formatDateTime(v) },
  ];

  if (canManage) {
    columns.push({
      key: '__actions', header: '·', headClassName: 'text-right', className: 'text-right',
      render: (_v, m) => (
        <button
          onClick={() => removeMember(m)}
          className="border border-rose-200 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40"
        >
          remove
        </button>
      ),
    });
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="font-mono text-[11px] uppercase tracking-wider text-slate-500">Project access</h2>
          <p className="mt-0.5 font-mono text-[11px] text-slate-500 dark:text-slate-500">
            Only people listed here can see or act on this project — access granted here never carries to any other project.
          </p>
        </div>
        {canManage && (
          <button
            onClick={() => setAdding(true)}
            className="flex shrink-0 items-center gap-1.5 border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40"
          >
            <Icon name="plus" size={13} /> Add member
          </button>
        )}
      </div>

      {error && <div className="mb-3 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-400">{error}</div>}

      <DataTable
        columns={columns}
        rows={members}
        empty={loading ? 'loading…' : 'No members yet.'}
      />

      {adding && (
        <AddMemberModal
          projectId={projectId}
          onClose={() => setAdding(false)}
          onAdded={() => { setAdding(false); load(); }}
        />
      )}
    </div>
  );
}

function AddMemberModal({ projectId, onClose, onAdded }) {
  const [q, setQ] = useState('');
  const [candidates, setCandidates] = useState([]);
  const [userId, setUserId] = useState('');
  const [role, setRole] = useState('viewer');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const rows = await api.get(`/projects/${projectId}/members/candidates${q ? `?q=${encodeURIComponent(q)}` : ''}`);
        if (active) setCandidates(rows);
      } catch (_) { /* keep last known candidates on transient failure */ }
    })();
    return () => { active = false; };
  }, [projectId, q]);

  const submit = async (e) => {
    e.preventDefault();
    if (!userId) return;
    setBusy(true); setError('');
    try {
      await api.post(`/projects/${projectId}/members`, { user_id: Number(userId), project_role: role });
      onAdded();
    } catch (err) { setError(err.message); }
    setBusy(false);
  };

  return (
    <Modal open title="Add member" onClose={onClose}
      footer={<>
        <button onClick={onClose} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">Cancel</button>
        <button onClick={submit} disabled={busy || !userId} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">{busy ? '···' : 'Add'}</button>
      </>}>
      <form onSubmit={submit} className="space-y-3">
        <div>
          <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Search users</div>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="username or email" autoFocus className={INPUT} />
        </div>
        <div>
          <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">User</div>
          <select value={userId} onChange={(e) => setUserId(e.target.value)} className={INPUT}>
            <option value="">select a user…</option>
            {candidates.map((c) => (
              <option key={c.id} value={c.id}>{c.username} ({c.email})</option>
            ))}
          </select>
        </div>
        <div>
          <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Project role</div>
          <select value={role} onChange={(e) => setRole(e.target.value)} className={INPUT}>
            <option value="viewer">viewer — see the project</option>
            <option value="member">member — connect/manage integrations</option>
            <option value="admin">admin — manage membership, delete project</option>
          </select>
        </div>
        {error && <div className="font-mono text-xs text-rose-600 dark:text-rose-400">{error}</div>}
      </form>
    </Modal>
  );
}
