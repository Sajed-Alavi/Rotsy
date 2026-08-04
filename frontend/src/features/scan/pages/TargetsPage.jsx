import { useState } from 'react';
import Badge from '../../../components/Badge.jsx';
import Icon from '../../../components/Icon.jsx';
import DataTable from '../../../components/DataTable.jsx';
import Notice from '../../../components/Notice.jsx';
import Section from '../../../components/Section.jsx';
import { scanApi } from '../api.js';
import { useResource, useStatus } from '../../../lib/useResource.js';
import TargetModal from '../components/TargetModal.jsx';

/**
 * Which repositories are enabled for scanning, and how.
 *
 * Delete is new here: DELETE /api/scan/targets/{id} has existed server-side all
 * along but had no caller in the UI, so a repository could be enabled and edited
 * but never disabled without hitting the API by hand.
 */
export default function TargetsPage() {
  const { data: targets, loading, reload } = useResource(() => scanApi.targets(), []);
  const { status, say, fail, clear } = useStatus();
  const [editing, setEditing] = useState(null);

  const remove = async (t) => {
    if (!confirm(`Stop scanning "${t.repo}"?\n\nExisting reports are kept; the repository is simply no longer a scan target.`)) return;
    try {
      await scanApi.deleteTarget(t.id);
      say(`Scanning disabled for ${t.repo}.`, 'ok');
      reload();
    } catch (e) {
      fail(`could not remove ${t.repo}: ${e.message}`);
    }
  };

  const columns = [
    { key: 'repo', header: 'Repo', mono: true },
    { key: 'scanners', header: 'Scanners', render: (v) => <span className="font-mono text-xs text-slate-500 dark:text-slate-400">{v || 'default'}</span> },
    { key: 'auto_scan', header: 'Auto', render: (v) => <Badge tone={v ? 'ok' : 'neutral'}>{v ? 'on push' : 'manual'}</Badge> },
    { key: 'enabled', header: 'State', render: (v) => <Badge tone={v ? 'ok' : 'neutral'}>{v ? 'on' : 'off'}</Badge> },
    {
      key: 'id',
      header: '·',
      headClassName: 'text-right',
      className: 'text-right',
      render: (_v, row) => (
        <div className="flex items-center justify-end gap-1">
          <button onClick={() => setEditing({ target: row })} className="border border-slate-200 px-2 py-0.5 font-mono text-[10px] uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">edit</button>
          <button onClick={() => remove(row)} className="border border-rose-200 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40">remove</button>
        </div>
      ),
    },
  ];

  return (
    <>
      <Notice status={status} onDismiss={clear} />

      <Section
        title="Scan targets"
        hint={loading ? 'loading…' : `${targets.length} enabled`}
        flush
        actions={
          <button onClick={() => setEditing({ target: null })} className="flex items-center gap-1.5 border border-slate-300 px-2.5 py-1 font-mono text-[11px] uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
            <Icon name="plus" size={12} /> Enable repo
          </button>
        }
      >
        <DataTable columns={columns} rows={targets} empty='no repositories enabled — click "Enable repo"' />
      </Section>

      {editing && (
        <TargetModal
          initial={editing.target}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); say('Target saved.', 'ok'); reload(); }}
        />
      )}
    </>
  );
}
