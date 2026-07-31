import Badge from '../../../components/Badge.jsx';
import Icon from '../../../components/Icon.jsx';
import ProgressBar from '../../../components/ProgressBar.jsx';
import { formatBytes, formatDateTime, relativeTime } from '../../../lib/format.js';

/**
 * One scanner's database: what is installed, and what is happening to it.
 *
 * The old card had two states — a static 2x2 grid, or a spinning icon and the
 * word "updating…". It could not say which scanner was moving, how far along it
 * was, how fast, or whether the run had succeeded. Everything below is data the
 * backend was already computing and discarding.
 */
const STAGE_LABELS = {
  connecting: 'Connecting',
  downloading: 'Downloading',
  extracting: 'Extracting',
  importing: 'Importing',
  verifying: 'Verifying',
  done: 'Up to date',
  failed: 'Failed',
  skipped: 'Already current',
};

function statusOf(info) {
  if (!info?.installed) return { tone: 'bad', label: 'not installed' };
  if (!info.present) return { tone: 'bad', label: 'no database' };
  if (info.stale) return { tone: 'warn', label: 'stale' };
  return { tone: 'ok', label: 'ready' };
}

function eta(seconds) {
  if (seconds == null || seconds <= 0) return null;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

export default function DbPanel({ name, info, live, busy, onUpdate, onForce }) {
  const status = statusOf(info);
  const stage = live?.stage;
  const active = busy && stage && !['done', 'failed', 'skipped'].includes(stage);

  return (
    <div className="border border-slate-200 p-4 dark:border-slate-800">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm text-slate-800 dark:text-slate-200">{name}</span>
          {active
            ? <Badge tone="info">{STAGE_LABELS[stage] || stage}</Badge>
            : <Badge tone={status.tone}>{status.label}</Badge>}
          {stage === 'failed' && !busy && <Badge tone="bad">last run failed</Badge>}
          {stage === 'skipped' && !busy && <Badge tone="ok">already current</Badge>}
        </div>
        <div className="flex items-center gap-1.5">
          <button
            onClick={onUpdate}
            disabled={busy}
            className="border border-sky-300 bg-sky-50 px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40"
          >
            Update
          </button>
          <button
            onClick={onForce}
            disabled={busy}
            title="Re-download even if the local database is already current"
            className="border border-slate-300 px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider text-slate-600 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            Force
          </button>
        </div>
      </div>

      {/* Live progress — only while this scanner is the one moving. */}
      {active && (
        <div className="mb-3 border border-sky-200 bg-sky-50/50 p-3 dark:border-sky-900 dark:bg-sky-950/20">
          <ProgressBar
            used={live.done_bytes || 0}
            total={live.total_bytes || 0}
            estimated={!!live.estimated}
            indeterminate={!!live.indeterminate || stage !== 'downloading' || !live.total_bytes}
            tone="ok"
            label={live.artifact || STAGE_LABELS[stage] || name}
          />
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[10px] text-slate-500 dark:text-slate-400">
            <span>{STAGE_LABELS[stage] || stage}</span>
            {live.speed_bps > 0 && <span>{formatBytes(live.speed_bps)}/s</span>}
            {eta(live.eta_seconds) && <span>{eta(live.eta_seconds)} left</span>}
            {live.estimated && <span className="text-amber-600 dark:text-amber-500">total is an estimate</span>}
            {live.note && <span className="text-slate-400 dark:text-slate-600">{live.note}</span>}
          </div>
        </div>
      )}

      {/* Terminal failure detail persists until the next run starts. */}
      {!active && live?.stage === 'failed' && live.error && (
        <div className="mb-3 flex items-start gap-2 border border-rose-200 bg-rose-50 p-2 font-mono text-[11px] text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-400">
          <Icon name="alert" size={13} className="mt-0.5 shrink-0" />
          <span className="whitespace-pre-wrap">{live.error}</span>
        </div>
      )}

      {info?.present ? (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 font-mono text-[11px] sm:grid-cols-4">
          <Field label="version" value={info.version || '—'} />
          <Field label="built" value={info.built ? relativeTime(info.built) : (info.downloaded_at ? relativeTime(info.downloaded_at) : '—')} title={info.built || info.downloaded_at} />
          <Field label="size" value={info.size_bytes ? formatBytes(info.size_bytes) : '—'} />
          <Field
            label="next update"
            value={info.next_update ? formatDateTime(info.next_update) : 'on schedule'}
          />
          {name === 'trivy' && (
            <Field label="java db" value={info.java_db_present ? 'present' : 'absent'} />
          )}
          {info.schema_version && <Field label="schema" value={info.schema_version} />}
        </dl>
      ) : (
        <p className="font-mono text-[11px] text-slate-500 dark:text-slate-400">
          {info?.reason || 'No database installed. Click Update, or import an offline archive below.'}
        </p>
      )}
    </div>
  );
}

function Field({ label, value, title }) {
  return (
    <div title={title}>
      <dt className="text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-600">{label}</dt>
      <dd className="mt-0.5 text-slate-700 dark:text-slate-300">{value}</dd>
    </div>
  );
}
