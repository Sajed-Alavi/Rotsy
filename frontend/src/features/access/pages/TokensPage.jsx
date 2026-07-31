import { useState } from 'react';
import Badge from '../../../components/Badge.jsx';
import DataTable from '../../../components/DataTable.jsx';
import Icon from '../../../components/Icon.jsx';
import Modal from '../../../components/Modal.jsx';
import Notice from '../../../components/Notice.jsx';
import Section from '../../../components/Section.jsx';
import { formatDateTime, relativeTime } from '../../../lib/format.js';
import { useResource, useStatus } from '../../scan/hooks/useResource.js';
import { accessApi } from '../api.js';

const INPUT = 'w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-xs text-slate-800 focus:border-sky-500 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200';

/**
 * Issue and revoke API tokens for CI/CD.
 *
 * A pipeline cannot hold the httpOnly session cookie the dashboard uses, so
 * before this the only options were embedding a person's password or handing
 * out the service account. Tokens are narrow (scopes intersect the owner's
 * permissions), expiring, and revocable.
 */
export default function TokensPage() {
  const { data: tokens, loading, reload } = useResource(() => accessApi.tokens(), []);
  const { data: permissions } = useResource(() => accessApi.permissions(), []);
  const { status, say, fail, clear } = useStatus();
  const [creating, setCreating] = useState(false);
  const [issued, setIssued] = useState(null);

  const revoke = async (t) => {
    if (!confirm(`Revoke "${t.name}"?\n\nAny automation using it will start failing immediately. This cannot be undone.`)) return;
    try {
      await accessApi.revokeToken(t.id);
      say(`Token "${t.name}" revoked.`, 'ok');
      reload();
    } catch (e) { fail(`revoke failed: ${e.message}`); }
  };

  const stateOf = (t) => {
    if (t.revoked) return { tone: 'bad', label: 'revoked' };
    if (t.expires_at && new Date(t.expires_at) <= new Date()) return { tone: 'warn', label: 'expired' };
    return { tone: 'ok', label: 'active' };
  };

  const columns = [
    { key: 'name', header: 'Name', render: (v) => <span className="text-slate-800 dark:text-slate-200">{v}</span> },
    { key: 'prefix', header: 'Token', render: (v) => <span className="font-mono text-xs text-slate-500 dark:text-slate-400">{v}…</span> },
    {
      key: 'scopes',
      header: 'Scopes',
      render: (v) => (v
        ? <span className="font-mono text-[10px] text-slate-500 dark:text-slate-400">{v.split(',').join(', ')}</span>
        : <span className="font-mono text-[10px] text-slate-400 dark:text-slate-600">owner's full permissions</span>),
    },
    { key: 'revoked', header: 'State', render: (_v, row) => { const s = stateOf(row); return <Badge tone={s.tone}>{s.label}</Badge>; } },
    { key: 'last_used_at', header: 'Last used', render: (v) => <span className="font-mono text-xs text-slate-400 dark:text-slate-600">{v ? relativeTime(v) : 'never'}</span> },
    { key: 'expires_at', header: 'Expires', render: (v) => <span className="font-mono text-xs text-slate-400 dark:text-slate-600">{v ? formatDateTime(v) : 'never'}</span> },
    {
      key: 'id',
      header: '·',
      headClassName: 'text-right',
      className: 'text-right',
      render: (_v, row) => (row.revoked ? null : (
        <button onClick={() => revoke(row)} className="border border-rose-200 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40">revoke</button>
      )),
    },
  ];

  return (
    <>
      <Notice status={status} onDismiss={clear} />

      <Section
        title="API tokens"
        hint={loading ? 'loading…' : `${tokens.length} issued`}
        flush
        actions={
          <button onClick={() => setCreating(true)} className="flex items-center gap-1.5 border border-sky-300 bg-sky-50 px-2.5 py-1 font-mono text-[11px] uppercase tracking-wider text-sky-700 hover:bg-sky-100 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">
            <Icon name="plus" size={12} /> New token
          </button>
        }
      >
        <DataTable columns={columns} rows={tokens} empty={loading ? 'loading…' : 'no tokens issued'} />
      </Section>

      <Section title="Using a token">
        <p className="mb-2 font-mono text-[11px] text-slate-600 dark:text-slate-400">
          Send it as a bearer header. A token never grants more than its owner currently has —
          scopes intersect with the owner's live permissions on every request, so removing
          someone's role immediately narrows every token they issued.
        </p>
        <pre className="overflow-x-auto border border-slate-200 bg-slate-50 p-3 font-mono text-[11px] text-slate-700 dark:border-slate-800 dark:bg-slate-900/40 dark:text-slate-300">
{`curl -H "Authorization: Bearer shp_…" \\
     https://your-host/api/scan/summary`}
        </pre>
      </Section>

      {creating && (
        <CreateTokenModal
          permissions={permissions}
          onClose={() => setCreating(false)}
          onCreated={(r) => { setCreating(false); setIssued(r); reload(); }}
          onError={fail}
        />
      )}

      {issued && <IssuedTokenModal issued={issued} onClose={() => setIssued(null)} />}
    </>
  );
}

function CreateTokenModal({ permissions, onClose, onCreated, onError }) {
  const [name, setName] = useState('');
  const [days, setDays] = useState(90);
  const [scopes, setScopes] = useState([]);
  const [saving, setSaving] = useState(false);

  const toggle = (key) => setScopes((s) => (s.includes(key) ? s.filter((k) => k !== key) : [...s, key]));

  const submit = async () => {
    setSaving(true);
    try {
      onCreated(await accessApi.createToken({
        name: name.trim(),
        scopes,
        expires_in_days: days ? Number(days) : null,
      }));
    } catch (e) {
      onError(`could not create the token: ${e.message}`);
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      wide
      title="New API token"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">Cancel</button>
          <button onClick={submit} disabled={!name.trim() || saving} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300">
            {saving ? 'Creating…' : 'Create'}
          </button>
        </>
      }
    >
      <label className="mb-3 block">
        <span className="mb-1 block font-mono text-[10px] uppercase tracking-wider text-slate-500">Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="gitlab-ci scan trigger" className={INPUT} />
      </label>

      <label className="mb-3 block">
        <span className="mb-1 block font-mono text-[10px] uppercase tracking-wider text-slate-500">Expires in (days)</span>
        <input type="number" min="1" max="365" value={days} onChange={(e) => setDays(e.target.value)} className={INPUT} />
        <span className="mt-1 block font-mono text-[10px] text-slate-400 dark:text-slate-600">
          Clear the field for a non-expiring token. Prefer a real expiry — a token nobody revisits is a token nobody revokes.
        </span>
      </label>

      <div>
        <span className="mb-1 block font-mono text-[10px] uppercase tracking-wider text-slate-500">
          Scopes {scopes.length === 0 && <span className="normal-case text-slate-400">— none selected: inherits your full permissions</span>}
        </span>
        <div className="max-h-48 overflow-y-auto border border-slate-200 p-2 dark:border-slate-800">
          {(permissions || []).map((p) => {
            const key = p.key || p;
            return (
              <label key={key} className="flex items-center gap-2 py-0.5 font-mono text-[11px] text-slate-600 dark:text-slate-400">
                <input type="checkbox" checked={scopes.includes(key)} onChange={() => toggle(key)} className="accent-sky-500" />
                <span>{key}</span>
                {p.description && <span className="text-slate-400 dark:text-slate-600">— {p.description}</span>}
              </label>
            );
          })}
        </div>
      </div>
    </Modal>
  );
}

function IssuedTokenModal({ issued, onClose }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(issued.token);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard blocked — the value is selectable on screen */ }
  };

  return (
    <Modal
      open
      wide
      title="Token created"
      onClose={onClose}
      footer={<button onClick={onClose} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300">Done</button>}
    >
      <div className="mb-3 flex items-start gap-2 border border-amber-200 bg-amber-50 p-2 font-mono text-[11px] text-amber-700 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-400">
        <Icon name="alert" size={13} className="mt-0.5 shrink-0" />
        <span>{issued.warning}</span>
      </div>
      <div className="flex items-center gap-2">
        <code className="flex-1 select-all break-all border border-slate-200 bg-slate-50 p-2 font-mono text-[11px] text-slate-800 dark:border-slate-800 dark:bg-slate-900/40 dark:text-slate-200">
          {issued.token}
        </code>
        <button onClick={copy} title="Copy" className="shrink-0 border border-slate-300 p-2 text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
          <Icon name={copied ? 'check' : 'copy'} size={14} />
        </button>
      </div>
    </Modal>
  );
}
