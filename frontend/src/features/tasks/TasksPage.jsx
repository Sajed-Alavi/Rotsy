import { useState } from 'react';
import { api } from '../../lib/api.js';
import Badge from '../../components/Badge.jsx';
import DataTable from '../../components/DataTable.jsx';
import Notice from '../../components/Notice.jsx';
import Section from '../../components/Section.jsx';
import { formatDateTime } from '../../lib/format.js';
import { useResource, useStatus } from '../scan/hooks/useResource.js';

const STATE_TONE = { RUNNING: 'info', WAITING: 'ok', OK: 'ok', DISABLED: 'neutral', SLEEPING: 'neutral' };
const RESULT_TONE = { OK: 'ok', SUCCESS: 'ok', ERROR: 'bad', FAILED: 'bad', CANCELED: 'warn', CANCELLED: 'warn' };

/**
 * Nexus scheduled tasks: see them, run them, stop them.
 *
 * This replaces the "Analytics & Tasks" placeholder. The tasks half is real —
 * the app already drove Nexus's task API internally to trigger blob-store
 * compaction after a delete, it just never exposed it. The analytics half
 * (bandwidth, top downloads, cache hit rate) was dropped rather than faked:
 * Nexus OSS publishes none of that data and this app counts no requests, so
 * those tiles could only ever have shown zeros.
 *
 * Compaction matters in particular — deleting an image frees no disk until the
 * "Compact blob store" task runs, which is why usage can look unchanged after a
 * successful delete.
 */
export default function TasksPage() {
  const { data, loading, reload } = useResource(() => api.get('/tasks'), null);
  const { status, say, fail, clear } = useStatus();
  const [busy, setBusy] = useState({});

  const act = async (task, action) => {
    if (action === 'stop' && !confirm(`Stop "${task.name}"?`)) return;
    setBusy((b) => ({ ...b, [task.id]: true }));
    try {
      const r = await api.post(`/tasks/${encodeURIComponent(task.id)}/${action}`);
      if (r.ok) say(r.note ? `${task.name}: ${r.note}` : `${task.name} ${action === 'run' ? 'started' : 'stopping'}.`, 'ok');
      else fail(r.error || `could not ${action} ${task.name}`);
      setTimeout(reload, 1500);
    } catch (e) {
      fail(`${action} failed: ${e.message}`);
    } finally {
      setBusy((b) => ({ ...b, [task.id]: false }));
    }
  };

  const columns = [
    {
      key: 'name',
      header: 'Task',
      render: (v, row) => (
        <>
          <span className="text-slate-800 dark:text-slate-200">{v}</span>
          <span className="block font-mono text-[10px] text-slate-400 dark:text-slate-600">{row.type}</span>
        </>
      ),
    },
    {
      key: 'current_state',
      header: 'State',
      render: (v) => <Badge tone={STATE_TONE[String(v).toUpperCase()] || 'neutral'}>{v}</Badge>,
    },
    {
      key: 'last_run_result',
      header: 'Last result',
      render: (v) => (v ? <Badge tone={RESULT_TONE[String(v).toUpperCase()] || 'neutral'}>{v}</Badge> : <span className="text-slate-400 dark:text-slate-600">—</span>),
    },
    { key: 'last_run', header: 'Last run', render: (v) => <span className="font-mono text-xs text-slate-400 dark:text-slate-600">{v ? formatDateTime(v) : 'never'}</span> },
    { key: 'next_run', header: 'Next run', render: (v) => <span className="font-mono text-xs text-slate-400 dark:text-slate-600">{v ? formatDateTime(v) : 'not scheduled'}</span> },
    {
      key: 'id',
      header: '·',
      headClassName: 'text-right',
      className: 'text-right',
      render: (_v, row) => {
        const running = String(row.current_state).toUpperCase() === 'RUNNING';
        return (
          <div className="flex items-center justify-end gap-1">
            {running ? (
              <button onClick={() => act(row, 'stop')} disabled={busy[row.id]} className="border border-rose-200 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 disabled:opacity-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40">stop</button>
            ) : (
              <button onClick={() => act(row, 'run')} disabled={busy[row.id] || !row.enabled} title={row.enabled ? 'Run now' : 'Task is disabled in Nexus'} className="border border-sky-300 bg-sky-50 px-2 py-0.5 font-mono text-[10px] uppercase text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">run</button>
            )}
          </div>
        );
      },
    },
  ];

  return (
    <div className="p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">Task Manager</h1>
          <p className="mt-0.5 font-mono text-[11px] text-slate-500 dark:text-slate-500">Nexus scheduled tasks · run and stop on demand</p>
        </div>
        <button onClick={reload} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">Refresh</button>
      </div>

      <Notice status={status} onDismiss={clear} />

      {data && !data.available ? (
        <Section title="Tasks unavailable">
          <p className="font-mono text-[11px] text-slate-600 dark:text-slate-400">{data.reason}</p>
        </Section>
      ) : (
        <Section title="Scheduled tasks" hint={loading ? 'loading…' : data?.source} flush>
          <DataTable
            columns={columns}
            rows={data?.tasks || []}
            empty={loading ? 'loading…' : 'Nexus reports no scheduled tasks. Create them in Nexus under Administration → System → Tasks.'}
          />
        </Section>
      )}

      <Section title="Why this matters for disk usage">
        <p className="font-mono text-[11px] text-slate-600 dark:text-slate-400">
          Deleting an image removes the tag immediately but leaves its blobs on disk until Nexus
          runs its <strong>Compact blob store</strong> task. If storage looks unchanged after a
          delete, that task is the reason — run it here. Nexus does not create one by default.
        </p>
      </Section>
    </div>
  );
}
