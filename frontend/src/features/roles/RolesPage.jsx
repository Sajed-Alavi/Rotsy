import { useEffect, useState } from 'react';
import { api } from '../../lib/api.js';
import DataTable from '../../components/DataTable.jsx';
import Modal from '../../components/Modal.jsx';
import Badge from '../../components/Badge.jsx';
import Icon from '../../components/Icon.jsx';
import Tabs from '../../components/Tabs.jsx';
import AccessRulesEditor from './AccessRulesEditor.jsx';
import { groupPermissions } from './permissionGroups.js';

/**
 * Role administration (requires roles:manage).
 *
 * Two independent axes, and the tabs keep them visibly separate:
 *   Permissions — *what* the role may do (the `resource:action` catalog).
 *   Access rules — *where* those actions reach (repository/image wildcards).
 */
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

  // DELETE /roles/{id} existed server-side with no caller, so a custom role
  // could be created and edited but never removed from the UI. System roles are
  // not deletable — the app seeds and depends on them.
  const remove = async (role) => {
    if (role.is_system) return;
    if (!confirm(`Delete the role "${role.name}"?\n\nUsers holding it lose those permissions immediately. This cannot be undone.`)) return;
    try {
      await api.delete(`/roles/${role.id}`);
      load();
    } catch (err) { setError(err.message); }
  };

  const columns = [
    { key: 'name', header: 'Role', mono: true, className: 'text-slate-800 dark:text-slate-200' },
    { key: 'description', header: 'Description' },
    {
      key: 'permissions', header: 'Permissions',
      render: (ps) => <span className="font-mono text-xs text-slate-500 dark:text-slate-400">{ps.length}</span>,
    },
    {
      key: 'access_mode', header: 'Access',
      render: (mode) => (
        <Badge
          tone={mode === 'scoped' ? 'warn' : 'neutral'}
          title={mode === 'scoped'
            ? 'Grants only what its own access rules allow'
            : 'Repositories no rule of this role matches stay fully accessible'}
        >
          {mode}
        </Badge>
      ),
    },
    {
      key: 'is_system', header: 'Type',
      render: (s) => <Badge tone={s ? 'info' : 'neutral'}>{s ? 'system' : 'custom'}</Badge>,
    },
    {
      key: '__actions',
      header: '·',
      headClassName: 'text-right',
      className: 'text-right',
      render: (_v, row) => (row.is_system ? (
        <span className="font-mono text-[10px] text-slate-300 dark:text-slate-700" title="System roles cannot be deleted">system</span>
      ) : (
        <button
          onClick={(e) => { e.stopPropagation(); remove(row); }}
          className="border border-rose-200 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 disabled:opacity-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40"
        >delete</button>
      )),
    },
  ];

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">Roles &amp; Permissions</h1>
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
const LABEL = 'mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500';

const MODE_COPY = {
  unrestricted: 'A repository none of this role’s rules match stays fully accessible to it. This is the default, and how roles behaved before access rules existed.',
  scoped: 'Deny by default: this role grants only what its own rules allow. Because effective access is the union across a user’s roles, this is what stops a baseline role everyone holds from reopening what a narrow role was meant to restrict.',
};

function RoleFormModal({ perms, initial, onClose, onSaved }) {
  const isEdit = !!initial;
  const isSystem = !!initial?.is_system;
  const isAdmin = isSystem && initial?.name === 'admin';
  const [tab, setTab] = useState('general');
  const [form, setForm] = useState({
    name: initial?.name ?? '',
    description: initial?.description ?? '',
    permission_keys: initial?.permissions?.map((p) => p.key) ?? [],
    access_mode: initial?.access_mode ?? 'unrestricted',
  });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [repos, setRepos] = useState([]);
  const [ruleCount, setRuleCount] = useState(null);

  useEffect(() => {
    if (!isEdit) return;
    api.get('/storage/repos').then(setRepos).catch(() => {});
  }, [isEdit]);

  const submit = async (e) => {
    e?.preventDefault();
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
      permission_keys: f.permission_keys.includes(key)
        ? f.permission_keys.filter((k) => k !== key)
        : [...f.permission_keys, key],
    }));

  const setGroup = (items, on) =>
    setForm((f) => {
      const keys = items.map((p) => p.key);
      const rest = f.permission_keys.filter((k) => !keys.includes(k));
      return { ...f, permission_keys: on ? [...rest, ...keys] : rest };
    });

  const tabs = [
    { key: 'general', label: 'General' },
    { key: 'permissions', label: 'Permissions', badge: form.permission_keys.length },
    ...(isEdit ? [{ key: 'rules', label: 'Access rules', badge: ruleCount ?? undefined }] : []),
  ];

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
      <Tabs items={tabs} value={tab} onChange={setTab} className="mb-4" />

      {tab === 'general' && (
        <form onSubmit={submit} className="space-y-3">
          <div>
            <div className={LABEL}>Name</div>
            <input value={form.name} disabled={isSystem} onChange={(e) => setForm({ ...form, name: e.target.value })} className={INPUT} />
          </div>
          <div>
            <div className={LABEL}>Description</div>
            <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className={INPUT} />
          </div>
          <div>
            <div className={LABEL}>Access mode</div>
            <Tabs
              items={[{ key: 'unrestricted', label: 'Unrestricted' }, { key: 'scoped', label: 'Scoped' }]}
              value={form.access_mode}
              onChange={(mode) => !isAdmin && setForm({ ...form, access_mode: mode })}
            />
            <p className="mt-2 font-mono text-[10px] leading-relaxed text-slate-400 dark:text-slate-600">
              {MODE_COPY[form.access_mode]}
            </p>
            {isAdmin && (
              <p className="mt-1 font-mono text-[10px] text-amber-600 dark:text-amber-500">
                The admin role cannot be scoped — it would be possible to lock every administrator out.
              </p>
            )}
          </div>
        </form>
      )}

      {tab === 'permissions' && (
        <div className="max-h-[26rem] space-y-3 overflow-y-auto pr-1">
          {groupPermissions(perms).map((group) => {
            const selected = group.items.filter((p) => form.permission_keys.includes(p.key)).length;
            const all = selected === group.items.length;
            return (
              <div key={group.label} className="border border-slate-200 dark:border-slate-800">
                <div className="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-2 py-1 dark:border-slate-800 dark:bg-slate-900">
                  <span className="font-mono text-[10px] uppercase tracking-wider text-slate-500">
                    {group.label} · {selected}/{group.items.length}
                  </span>
                  <button
                    type="button"
                    onClick={() => setGroup(group.items, !all)}
                    className="font-mono text-[10px] uppercase text-sky-600 hover:underline dark:text-sky-400"
                  >
                    {all ? 'none' : 'all'}
                  </button>
                </div>
                <div className="grid grid-cols-1 gap-1 p-2 sm:grid-cols-2">
                  {group.items.map((p) => (
                    // The label's accessible name comes entirely from dynamic
                    // {p.key}/{p.description} expressions — always non-empty
                    // at runtime (every permission has both), but a static
                    // linter can't verify that from the JSX alone. NOSONAR.
                    <label key={p.key} className="flex cursor-pointer items-start gap-2 text-slate-700 dark:text-slate-300"> {/* NOSONAR */}
                      <input type="checkbox" checked={form.permission_keys.includes(p.key)} onChange={() => togglePerm(p.key)} className="mt-0.5 accent-sky-500" />
                      <div>
                        <div className="font-mono text-xs text-slate-800 dark:text-slate-200">{p.key}</div>
                        <div className="font-mono text-[10px] text-slate-400 dark:text-slate-600">{p.description}</div>
                      </div>
                    </label>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {tab === 'rules' && isEdit && (
        isAdmin ? (
          <div className="border border-amber-200 bg-amber-50 px-3 py-2 font-mono text-[11px] text-amber-700 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-500">
            The admin role cannot carry access rules. A single deny rule here would lock every
            administrator out of that repository with no way back through the app. Create a scoped
            custom role instead.
          </div>
        ) : (
          <div className="space-y-3">
            <p className="font-mono text-[10px] leading-relaxed text-slate-400 dark:text-slate-600">
              Each rule allows or denies a set of actions on images matching a pattern, in
              repositories matching a pattern. <code>*</code> matches any characters except{' '}
              <code> / </code>; <code>**</code> crosses it; <code>?</code> is one character. Within
              this role a deny beats an allow — that is how you carve an exception out of a grant.
            </p>
            <AccessRulesEditor
              roleId={initial.id}
              repos={repos}
              onChanged={(rules) => setRuleCount(rules.length)}
            />
            <RuleTester roleId={initial.id} repos={repos} />
          </div>
        )
      )}

      {error && <div className="mt-3 font-mono text-xs text-rose-600 dark:text-rose-400">{error}</div>}
    </Modal>
  );
}

/**
 * "What would this role do to that image?" — answered by the server, so the
 * preview cannot drift from enforcement.
 */
function RuleTester({ roleId, repos }) {
  const [probe, setProbe] = useState({ repo: '', image: '' });
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  const run = async () => {
    setError('');
    setResult(null);
    if (!probe.repo || !probe.image) {
      setError('Enter a repository and an image name.');
      return;
    }
    try {
      setResult(await api.post(`/roles/${roleId}/access-rules/test`, probe));
    } catch (err) { setError(err.message); }
  };

  return (
    <div className="border-t border-slate-200 pt-3 dark:border-slate-800">
      <div className={LABEL}>Test these rules</div>
      <div className="flex flex-wrap items-center gap-2">
        <input
          list={`repos-${roleId}`}
          value={probe.repo}
          onChange={(e) => setProbe({ ...probe, repo: e.target.value })}
          placeholder="repository"
          className={`${INPUT} flex-1`}
        />
        <datalist id={`repos-${roleId}`}>
          {repos.map((r) => <option key={r.name} value={r.name} />)}
        </datalist>
        <input
          value={probe.image}
          onChange={(e) => setProbe({ ...probe, image: e.target.value })}
          placeholder="image name, e.g. abrisham-frontend"
          className={`${INPUT} flex-1`}
        />
        <button
          type="button"
          onClick={run}
          className="whitespace-nowrap border border-slate-300 px-2.5 py-1.5 font-mono text-xs uppercase text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
        >test</button>
      </div>

      {error && <div className="mt-2 font-mono text-xs text-rose-600 dark:text-rose-400">{error}</div>}

      {result && (
        <div className="mt-2 space-y-2 border border-slate-200 p-2 dark:border-slate-800">
          <div className="flex flex-wrap items-center gap-1.5">
            {result.allowed_actions.length === 0 ? (
              <Badge tone="bad">no access</Badge>
            ) : (
              result.allowed_actions.map((a) => <Badge key={a} tone="ok">{a}</Badge>)
            )}
            {result.unrestricted && (
              <span className="font-mono text-[10px] text-slate-400 dark:text-slate-600">
                no rule matches this repository — the role&rsquo;s access mode decides
              </span>
            )}
          </div>
          {result.matched_rules.length > 0 && (
            <ul className="space-y-0.5">
              {result.matched_rules.map((m) => (
                <li
                  key={m.rule_id}
                  className={`font-mono text-[10px] ${m.matched_image ? 'text-slate-600 dark:text-slate-400' : 'text-slate-300 dark:text-slate-700'}`}
                  title={m.matched_image ? 'Matched' : 'Matched the repository, but not the image'}
                >
                  {m.effect} [{m.actions.join(',')}] {m.repo_pattern} / {m.image_pattern}
                  {!m.matched_image && ' — image did not match'}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
