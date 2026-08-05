import { useEffect, useState } from 'react';
import { useOutletContext } from 'react-router';
import { api } from '../../../lib/api.js';
import DataTable from '../../../components/DataTable.jsx';
import Badge from '../../../components/Badge.jsx';
import Modal from '../../../components/Modal.jsx';
import Icon from '../../../components/Icon.jsx';
import { formatDateTime, relativeTime } from '../../../lib/format.js';

const GATE_TONE = { OK: 'ok', WARN: 'warn', ERROR: 'bad' };
const STATUS_TONE = { success: 'ok', failed: 'bad', running: 'info', pending: 'neutral' };

/** Analysis history for this project, plus the manual "Run Analysis" trigger
 * — same backend job as an automatic push, just started on demand. */
export default function AnalysisPage() {
  const { projectId } = useOutletContext();
  const [runs, setRuns] = useState([]);
  const [gates, setGates] = useState({}); // run_id -> quality gate status
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [running, setRunning] = useState(false);
  const [runMsg, setRunMsg] = useState('');
  const [detail, setDetail] = useState(null);

  const load = async () => {
    setLoading(true); setErr('');
    try {
      const rows = await api.get(`/modules/sonar/projects/${projectId}/analysis-runs`);
      setRuns(rows);
      const successful = rows.filter((r) => r.status === 'success');
      const entries = await Promise.all(successful.map(async (r) => {
        try { return [r.id, (await api.get(`/modules/sonar/analysis-runs/${r.id}/quality-gate`)).status]; }
        catch (_) { return [r.id, null]; }
      }));
      setGates(Object.fromEntries(entries));
    } catch (e) { setErr(e.message); }
    setLoading(false);
  };
  useEffect(() => { load(); }, [projectId]);

  const runAnalysis = async () => {
    setRunning(true); setRunMsg(''); setErr('');
    try {
      const r = await api.post(`/modules/sonar/projects/${projectId}/run-analysis`, {});
      setRunMsg(`Queued — analyzing commit ${r.commit_sha.slice(0, 8)}. Refresh in a moment to see it.`);
    } catch (e) { setErr(e.message); }
    setRunning(false);
  };

  const columns = [
    { key: 'commit_sha', header: 'Commit', mono: true, render: (v) => v.slice(0, 8) },
    { key: 'ref', header: 'Branch', mono: true },
    { key: 'trigger', header: 'Trigger', render: (v) => <Badge tone="neutral">{v}</Badge> },
    { key: 'status', header: 'Status', render: (v) => <Badge tone={STATUS_TONE[v] || 'neutral'}>{v}</Badge> },
    {
      key: 'gate', header: 'Quality Gate',
      render: (_v, row) => gates[row.id] ? <Badge tone={GATE_TONE[gates[row.id]] || 'neutral'}>{gates[row.id]}</Badge> : <span className="font-mono text-[10px] text-slate-400">—</span>,
    },
    { key: 'issues_count', header: 'Issues', mono: true, render: (v) => v ?? '—' },
    { key: 'coverage', header: 'Coverage', mono: true, render: (v) => v != null ? `${v.toFixed(0)}%` : '—' },
    { key: 'started_at', header: 'Date', mono: true, render: (v) => formatDateTime(v) },
  ];

  return (
    <div className="grid grid-cols-1 gap-4">
      <div className="flex items-center justify-between">
        <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">
          Every push to the default branch analyzes automatically. Use Run Analysis to trigger one on demand.
        </p>
        <button onClick={runAnalysis} disabled={running} className="flex items-center gap-1.5 border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">
          <Icon name="play" size={12} /> {running ? '···' : 'Run Analysis'}
        </button>
      </div>

      {runMsg && <div className="border border-emerald-200 bg-emerald-50 px-3 py-2 font-mono text-xs text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-400">{runMsg}</div>}
      {err && <div className="border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{err}</div>}

      <DataTable columns={columns} rows={runs} empty={loading ? 'loading…' : 'No analysis runs yet.'} onRowClick={setDetail} />

      <AnalysisDetailModal run={detail} gate={detail ? gates[detail.id] : null} onClose={() => setDetail(null)} />
    </div>
  );
}

function AnalysisDetailModal({ run, gate, onClose }) {
  if (!run) return null;
  const duration = run.finished_at
    ? `${Math.round((new Date(run.finished_at) - new Date(run.started_at)) / 1000)}s`
    : '—';
  return (
    <Modal open={!!run} title={`Analysis · ${run.commit_sha.slice(0, 8)}`} onClose={onClose} wide>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 font-mono text-xs">
        <Field label="Commit" value={run.commit_sha} />
        <Field label="Branch" value={run.ref} />
        <Field label="Trigger" value={run.trigger} />
        <Field label="Status" value={run.status} />
        <Field label="Quality Gate" value={gate || '—'} />
        <Field label="Duration" value={duration} />
        <Field label="Started" value={relativeTime(run.started_at)} />
        <Field label="Bugs" value={run.bugs ?? '—'} />
        <Field label="Vulnerabilities" value={run.vulnerabilities ?? '—'} />
        <Field label="Code Smells" value={run.code_smells ?? '—'} />
        <Field label="Security Hotspots" value={run.security_hotspots ?? '—'} />
        <Field label="Coverage" value={run.coverage != null ? `${run.coverage.toFixed(1)}%` : '—'} />
        <Field label="Duplication" value={run.duplication_pct != null ? `${run.duplication_pct.toFixed(1)}%` : '—'} />
      </dl>
      {run.error && (
        <div className="mt-4 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{run.error}</div>
      )}
    </Modal>
  );
}

function Field({ label, value }) {
  return (
    <div>
      <dt className="font-mono text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-600">{label}</dt>
      <dd className="mt-0.5 text-slate-800 dark:text-slate-200">{value}</dd>
    </div>
  );
}
