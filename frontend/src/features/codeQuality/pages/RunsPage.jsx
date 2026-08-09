import { useEffect, useState } from 'react';
import { codeQualityApi } from '../api.js';
import DataTable from '../../../components/DataTable.jsx';
import Badge from '../../../components/Badge.jsx';
import Modal from '../../../components/Modal.jsx';
import Icon from '../../../components/Icon.jsx';
import { formatDateTime, relativeTime } from '../../../lib/format.js';

const GATE_TONE = { OK: 'ok', WARN: 'warn', ERROR: 'bad' };
const STATUS_TONE = { success: 'ok', failed: 'bad', running: 'info', pending: 'neutral' };
const ISSUE_SEVERITY_TONE = { BLOCKER: 'bad', CRITICAL: 'bad', MAJOR: 'warn', MINOR: 'info', INFO: 'neutral' };
const ISSUE_TYPE_TONE = { BUG: 'bad', VULNERABILITY: 'bad', CODE_SMELL: 'neutral' };
const HOTSPOT_PROBABILITY_TONE = { HIGH: 'bad', MEDIUM: 'warn', LOW: 'info' };

/** Global analysis run history — every repository, every Project. Triggering
 * a new run happens on the Overview tab; this is history + drill-down only. */
export default function RunsPage() {
  const [runs, setRuns] = useState([]);
  const [gates, setGates] = useState({}); // run_id -> quality gate status
  const [repoLabels, setRepoLabels] = useState({}); // sonar_project_id -> "owner/repo"
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [detail, setDetail] = useState(null);

  const load = async () => {
    setLoading(true); setErr('');
    try {
      const [rows, repos] = await Promise.all([
        codeQualityApi.analysisRuns(),
        codeQualityApi.repositories(),
      ]);
      setRuns(rows);
      setRepoLabels(Object.fromEntries(repos.filter((r) => r.sonar_project_id).map((r) => [r.sonar_project_id, r.full_name])));
      const successful = rows.filter((r) => r.status === 'success');
      const entries = await Promise.all(successful.map(async (r) => {
        try { return [r.id, (await codeQualityApi.qualityGate(r.id)).status]; }
        catch (e) {
          // best-effort per-run gate lookup; a failure here just leaves that row's gate unknown
          console.debug('quality gate lookup failed for run', r.id, e);
          return [r.id, null];
        }
      }));
      setGates(Object.fromEntries(entries));
    } catch (e) { setErr(e.message); }
    setLoading(false);
  };
  useEffect(() => { load(); }, []);

  const columns = [
    { key: 'sonar_project_id', header: 'Repository', mono: true, render: (v) => repoLabels[v] || `#${v}` },
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
      {err && <div className="border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{err}</div>}

      <DataTable columns={columns} rows={runs} empty={loading ? 'loading…' : 'No analysis runs yet — run one from Overview.'} onRowClick={setDetail} />

      <AnalysisDetailModal run={detail} gate={detail ? gates[detail.id] : null} onClose={() => setDetail(null)} />
    </div>
  );
}

function AnalysisDetailModal({ run, gate, onClose }) {
  const [tab, setTab] = useState('overview');
  const [issues, setIssues] = useState(null); // null = not loaded yet
  const [hotspots, setHotspots] = useState(null);
  const [findingsErr, setFindingsErr] = useState('');

  useEffect(() => {
    setTab('overview');
    setIssues(null);
    setHotspots(null);
    setFindingsErr('');
  }, [run?.id]);

  useEffect(() => {
    if (run?.status !== 'success') return;
    if (tab === 'issues' && issues === null) {
      codeQualityApi.issuesForRun(run.id, new URLSearchParams({ limit: '100' }))
        .then(setIssues).catch((e) => setFindingsErr(e.message));
    }
    if (tab === 'hotspots' && hotspots === null) {
      codeQualityApi.hotspotsForRun(run.id, new URLSearchParams({ limit: '100' }))
        .then(setHotspots).catch((e) => setFindingsErr(e.message));
    }
  }, [run, tab, issues, hotspots]);

  if (!run) return null;
  const duration = run.finished_at
    ? `${Math.round((new Date(run.finished_at) - new Date(run.started_at)) / 1000)}s`
    : '—';

  const tabButton = (key, label) => (
    <button
      type="button"
      onClick={() => setTab(key)}
      className={`border-b-2 px-3 py-1.5 font-mono text-[10px] uppercase tracking-wider ${
        tab === key
          ? 'border-sky-500 text-sky-700 dark:text-sky-300'
          : 'border-transparent text-slate-500 hover:text-slate-700 dark:text-slate-500 dark:hover:text-slate-300'
      }`}
    >
      {label}
    </button>
  );

  return (
    <Modal open={!!run} title={`Analysis · ${run.commit_sha.slice(0, 8)}`} onClose={onClose} wide
      footer={run.status === 'success' && (
        <a
          href={codeQualityApi.reportUrl(run.id)}
          target="_blank" rel="noreferrer"
          className="flex items-center gap-1.5 border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          <Icon name="download" size={12} /> Download Report (PDF)
        </a>
      )}
    >
      <div className="-mt-1 mb-3 flex gap-1 border-b border-slate-200 dark:border-slate-800">
        {tabButton('overview', 'Overview')}
        {tabButton('issues', run.issues_count != null ? `Issues (${run.issues_count})` : 'Issues')}
        {tabButton('hotspots', run.security_hotspots != null ? `Hotspots (${run.security_hotspots})` : 'Hotspots')}
      </div>

      {tab === 'overview' && (
        <>
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
        </>
      )}

      {tab !== 'overview' && findingsErr && (
        <div className="border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{findingsErr}</div>
      )}

      {tab === 'issues' && (
        <IssuesTable issues={issues} />
      )}

      {tab === 'hotspots' && (
        <HotspotsTable hotspots={hotspots} />
      )}
    </Modal>
  );
}

function fileLineLabel(v, row) {
  if (!v) return '—';
  return row.line ? `${v}:${row.line}` : v;
}

function IssuesTable({ issues }) {
  if (issues === null) return <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">loading…</p>;
  const columns = [
    { key: 'severity', header: 'Severity', render: (v) => <Badge tone={ISSUE_SEVERITY_TONE[v] || 'neutral'}>{v}</Badge> },
    { key: 'type', header: 'Type', render: (v) => <Badge tone={ISSUE_TYPE_TONE[v] || 'neutral'}>{v?.replace('_', ' ')}</Badge> },
    { key: 'rule', header: 'Rule', mono: true },
    {
      key: 'component', header: 'File : Line', mono: true,
      render: fileLineLabel,
    },
    { key: 'message', header: 'Message' },
    { key: 'effort', header: 'Effort', mono: true, render: (v) => v || '—' },
  ];
  return (
    <div className="space-y-2">
      <DataTable columns={columns} rows={issues.items} empty="No open issues for this analysis." />
      {issues.total > issues.items.length && (
        <p className="font-mono text-[10px] text-slate-400 dark:text-slate-600">
          Showing {issues.items.length} of {issues.total} — download the full report for the complete list.
        </p>
      )}
    </div>
  );
}

function HotspotsTable({ hotspots }) {
  if (hotspots === null) return <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">loading…</p>;
  const columns = [
    {
      key: 'vulnerability_probability', header: 'Probability',
      render: (v) => <Badge tone={HOTSPOT_PROBABILITY_TONE[v] || 'neutral'}>{v || '—'}</Badge>,
    },
    {
      key: 'component', header: 'File : Line', mono: true,
      render: fileLineLabel,
    },
    { key: 'security_category', header: 'Category', render: (v) => v || '—' },
    { key: 'message', header: 'Message' },
  ];
  return (
    <div className="space-y-2">
      <DataTable columns={columns} rows={hotspots.items} empty="No security hotspots for this analysis." />
      {hotspots.total > hotspots.items.length && (
        <p className="font-mono text-[10px] text-slate-400 dark:text-slate-600">
          Showing {hotspots.items.length} of {hotspots.total} — download the full report for the complete list.
        </p>
      )}
    </div>
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
