import { useState } from 'react';
import Badge from '../../../components/Badge.jsx';
import DataTable from '../../../components/DataTable.jsx';
import Notice from '../../../components/Notice.jsx';
import Section from '../../../components/Section.jsx';
import { useResource, useStatus } from '../../../lib/useResource.js';
import { accessApi } from '../api.js';

const INPUT = 'border border-slate-300 bg-white px-2 py-1.5 font-mono text-xs text-slate-800 focus:border-sky-500 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200';

/**
 * Which repositories can be read without logging in.
 *
 * Anonymous access could previously only be granted, only at repository
 * creation, via a checkbox — with no way to list what was already public and no
 * way to take it back. A repo made readable by accident was invisible here and
 * fixable only in the Nexus UI.
 */
export default function AnonymousPage() {
  const { data, loading, reload } = useResource(() => accessApi.anonymous(), null);
  const { data: repos } = useResource(() => accessApi.repos(), []);
  const { status, say, fail, clear } = useStatus();
  const [repo, setRepo] = useState('');
  const [busy, setBusy] = useState(false);

  const grant = async () => {
    if (!repo) return;
    setBusy(true);
    try {
      const format = repos.find((r) => r.name === repo)?.format || 'docker';
      await accessApi.grantAnonymous(repo, format);
      say(`${repo} is now readable anonymously.`, 'ok');
      setRepo('');
      reload();
    } catch (e) { fail(`grant failed: ${e.message}`); } finally { setBusy(false); }
  };

  const revoke = async (row) => {
    if (!confirm(`Remove anonymous access to "${row.repo}"?\n\nUnauthenticated clients pulling from it will start getting 401s.`)) return;
    setBusy(true);
    try {
      const r = await accessApi.revokeAnonymous(row.repo);
      if (r.revoked) say(`Anonymous access to ${row.repo} removed.`, 'ok');
      else say(r.reason || 'Nothing to revoke.', 'warn');
      reload();
    } catch (e) { fail(`revoke failed: ${e.message}`); } finally { setBusy(false); }
  };

  const columns = [
    { key: 'repo', header: 'Repository', mono: true },
    { key: 'format', header: 'Format', render: (v) => (v ? <Badge tone="info">{v}</Badge> : '—') },
    { key: 'actions', header: 'Actions', render: (v) => <span className="font-mono text-[10px] text-slate-500 dark:text-slate-400">{(v || []).join(', ') || '—'}</span> },
    {
      key: 'managed_here',
      header: 'Origin',
      render: (v) => <Badge tone={v ? 'neutral' : 'warn'}>{v ? 'this console' : 'set in Nexus'}</Badge>,
    },
    {
      key: 'privilege',
      header: '·',
      headClassName: 'text-right',
      className: 'text-right',
      render: (_v, row) => (
        <button onClick={() => revoke(row)} disabled={busy} className="border border-rose-200 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 disabled:opacity-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40">revoke</button>
      ),
    },
  ];

  if (data && !data.available) {
    return (
      <Section title="Anonymous access">
        <p className="font-mono text-[11px] text-rose-600 dark:text-rose-400">{data.reason}</p>
      </Section>
    );
  }

  return (
    <>
      <Notice status={status} onDismiss={clear} />

      <Section title="Global setting">
        <div className="flex flex-wrap items-center gap-3">
          <span className="font-mono text-[11px] text-slate-600 dark:text-slate-400">Anonymous access in Nexus</span>
          {data?.global_enabled === null || data?.global_enabled === undefined ? (
            <Badge tone="neutral">unknown</Badge>
          ) : (
            <Badge tone={data.global_enabled ? 'warn' : 'ok'}>{data.global_enabled ? 'enabled' : 'disabled'}</Badge>
          )}
        </div>
        <p className="mt-2 font-mono text-[10px] text-slate-500 dark:text-slate-500">
          This is Nexus's master switch. With it disabled, the per-repository grants below have no
          effect — Nexus refuses unauthenticated requests before privileges are consulted. It is
          shown read-only here; change it in Nexus under Security → Anonymous Access.
        </p>
      </Section>

      <Section
        title="Repositories readable without login"
        hint={loading ? 'loading…' : `${(data?.repositories || []).length} granted`}
        flush
        actions={
          <div className="flex items-center gap-2">
            <select value={repo} onChange={(e) => setRepo(e.target.value)} className={INPUT}>
              <option value="">select a repository…</option>
              {(repos || []).map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
            </select>
            <button onClick={grant} disabled={!repo || busy} className="border border-sky-300 bg-sky-50 px-2.5 py-1.5 font-mono text-[11px] uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">
              Grant
            </button>
          </div>
        }
      >
        <DataTable
          columns={columns}
          rows={data?.repositories || []}
          empty={loading ? 'loading…' : 'no repository is readable anonymously'}
        />
      </Section>

      {(data?.unmapped_privileges || []).length > 0 && (
        <Section title="Unrecognised privileges" hint="attached to nx-anonymous">
          <p className="mb-2 font-mono text-[11px] text-slate-600 dark:text-slate-400">
            These are on the anonymous role but do not map to a repository-view privilege, so this
            page cannot say what they expose. Review them in Nexus.
          </p>
          <ul className="font-mono text-[11px] text-slate-500 dark:text-slate-400">
            {data.unmapped_privileges.map((p) => <li key={p}>{p}</li>)}
          </ul>
        </Section>
      )}
    </>
  );
}
