import { useEffect, useState } from 'react';
import { api } from '../../lib/api.js';
import Stat from '../../components/Stat.jsx';
import Badge from '../../components/Badge.jsx';
import Modal from '../../components/Modal.jsx';
import Icon from '../../components/Icon.jsx';
import { formatBytes, formatDateTime, formatNumber, relativeTime } from '../../lib/format.js';

/**
 * Vulnerability scanning dashboard.
 *  - Scanner DB status card (version, the date it's for, size, refresh)
 *  - Summary tiles (totals across latest reports)
 *  - Per-repository scan-target management (enable Trivy/Grype, auto-scan)
 *  - Latest reports table
 *  - Per-finding vulnerability list (filter by severity)
 */
export default function ScanPage() {
  const [summary, setSummary] = useState(null);
  const [targets, setTargets] = useState([]);
  const [reports, setReports] = useState([]);
  const [vulns, setVulns] = useState([]);
  const [dbStatus, setDbStatus] = useState(null);
  const [offlineStatus, setOfflineStatus] = useState(null);
  const [dbUpdating, setDbUpdating] = useState(false);
  const [sevFilter, setSevFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(null);
  const [msg, setMsg] = useState('');

  const load = async () => {
    try {
      const [s, t, r, v, d, o] = await Promise.all([
        api.get('/scan/summary').catch(() => null),
        api.get('/scan/targets').catch(() => []),
        api.get('/scan/reports?limit=50').catch(() => []),
        api.get('/scan/vulnerabilities?limit=200').catch(() => []),
        api.get('/scan/db-status').catch(() => null),
        api.get('/scan/db-offline').catch(() => null),
      ]);
      setSummary(s);
      setTargets(t);
      setReports(r);
      setVulns(v);
      setDbStatus(d);
      setOfflineStatus(o);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, []);

  const refreshDb = async () => {
    setMsg('');
    setDbUpdating(true);
    try {
      const r = await api.post('/scan/db-update');
      setMsg(`DB update queued: job ${r.job_id.slice(0,8)} — see Background Jobs for live progress.`);
      // Poll db-status while the job runs so the date/size updates.
      const interval = setInterval(async () => {
        try { setDbStatus(await api.get('/scan/db-status')); } catch (_) {}
      }, 3000);
      setTimeout(() => { clearInterval(interval); setDbUpdating(false); }, 60000);
    } catch (e) {
      setMsg(`failed: ${e.message}`);
      setDbUpdating(false);
    }
  };

  const importDbs = async () => {
    setMsg('');
    setDbUpdating(true);
    try {
      const r = await api.post('/scan/db-import');
      setMsg(`Offline import queued: job ${r.job_id.slice(0, 8)} — see Background Jobs for live progress.`);
      const interval = setInterval(async () => {
        try {
          setDbStatus(await api.get('/scan/db-status'));
          setOfflineStatus(await api.get('/scan/db-offline'));
        } catch (_) {}
      }, 3000);
      setTimeout(() => { clearInterval(interval); setDbUpdating(false); }, 60000);
    } catch (e) {
      setMsg(`import failed: ${e.message}`);
      setDbUpdating(false);
    }
  };

  const [detailReport, setDetailReport] = useState(null);

  const deleteReport = async (id, e) => {
    e.stopPropagation();
    try {
      await api.delete(`/scan/reports/${id}`);
      setMsg('Report deleted.');
      load();
    } catch (e2) { setMsg(`delete failed: ${e2.message}`); }
  };

  const clearAllReports = async () => {
    if (!confirm('Delete ALL scan reports? This cannot be undone.')) return;
    try {
      await api.delete('/scan/reports');
      setMsg('All reports cleared.');
      load();
    } catch (e) { setMsg(`clear failed: ${e.message}`); }
  };

  const totals = summary?.totals || { critical: 0, high: 0, medium: 0, low: 0, unknown: 0, scanned_images: 0, failed: 0 };
  const sevTone = { CRITICAL: 'bad', HIGH: 'warn', MEDIUM: 'info', LOW: 'neutral', UNKNOWN: 'neutral' };

  return (
    <div className="p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">Vulnerability Scanning</h1>
          <p className="mt-0.5 font-mono text-[11px] text-slate-500 dark:text-slate-500">Trivy + Grype · enable per repository · auto-scan on push</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={refreshDb} disabled={dbUpdating} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
            Refresh vuln DBs
          </button>
          <button
            onClick={importDbs}
            disabled={dbUpdating}
            title={offlineStatus?.exists
              ? `Import from ${offlineStatus.dir} (trivy: ${offlineStatus.trivy_db ? 'found' : 'missing'}, grype: ${offlineStatus.grype_db ? 'found' : 'missing'})`
              : 'Offline dir not found — mount ./offline-db and drop DB archives in'}
            className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">
            Import offline DBs
            {offlineStatus && (
              <span className="ml-1.5 normal-case text-[10px] text-sky-500 dark:text-sky-400">
                {offlineStatus.exists ? `(${(offlineStatus.files || []).length} files)` : '(no dir)'}
              </span>
            )}
          </button>
        </div>
      </div>

      {msg && <div className="mb-3 border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-xs text-slate-600 dark:border-slate-800 dark:bg-slate-900/40 dark:text-slate-400">{msg}</div>}

      {/* Scanner DB status */}
      <div className="mb-6 grid grid-cols-1 gap-px border border-slate-200 bg-slate-200 sm:grid-cols-2 dark:border-slate-800 dark:bg-slate-800">
        <DbCard name="trivy" info={dbStatus?.trivy} updating={dbUpdating} />
        <DbCard name="grype" info={dbStatus?.grype} updating={dbUpdating} />
      </div>

      {/* Summary */}
      <div className="mb-6 grid grid-cols-2 gap-px border border-slate-200 bg-slate-200 sm:grid-cols-4 lg:grid-cols-7 dark:border-slate-800 dark:bg-slate-800">
        <Stat label="Critical" count={totals.critical} tone="bad" />
        <Stat label="High" count={totals.high} tone="warn" />
        <Stat label="Medium" count={totals.medium} tone="info" />
        <Stat label="Low" count={totals.low} />
        <Stat label="Unknown" count={totals.unknown} />
        <Stat label="Scanned" count={totals.scanned_images} sub="images" />
        <Stat label="Failed" count={totals.failed} tone={totals.failed ? 'warn' : 'ok'} />
      </div>

      {/* Targets */}
      <div className="mb-6">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Scan targets</h2>
          <button onClick={() => setEditing({ target: null })} className="flex items-center gap-1.5 border border-slate-300 px-2.5 py-1 font-mono text-[11px] uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
            <Icon name="plus" size={12} /> Enable repo
          </button>
        </div>
        <div className="border border-slate-200 dark:border-slate-800">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60">
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Repo</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Scanners</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Auto</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">State</th>
                <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-wider text-slate-500">·</th>
              </tr>
            </thead>
            <tbody>
              {!loading && targets.length === 0 ? (
                <tr><td colSpan={5} className="px-3 py-8 text-center font-mono text-xs text-slate-400 dark:text-slate-600">no repositories enabled — click "Enable repo"</td></tr>
              ) : targets.map((t) => (
                <tr key={t.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50 dark:border-slate-800/60 dark:hover:bg-slate-800/30">
                  <td className="px-3 py-2 font-mono text-slate-800 dark:text-slate-200">{t.repo}</td>
                  <td className="px-3 py-2 font-mono text-xs text-slate-500 dark:text-slate-400">{t.scanners || 'default'}</td>
                  <td className="px-3 py-2"><Badge tone={t.auto_scan ? 'ok' : 'neutral'}>{t.auto_scan ? 'on push' : 'manual'}</Badge></td>
                  <td className="px-3 py-2"><Badge tone={t.enabled ? 'ok' : 'neutral'}>{t.enabled ? 'on' : 'off'}</Badge></td>
                  <td className="px-3 py-2 text-right"><button onClick={() => setEditing({ target: t })} className="border border-slate-200 px-2 py-0.5 font-mono text-[10px] uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">edit</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Recent reports */}
      <div className="mb-6">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Recent reports</h2>
          {reports.length > 0 && (
            <button onClick={clearAllReports} className="border border-rose-200 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40">clear all</button>
          )}
        </div>
        <div className="border border-slate-200 dark:border-slate-800">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60">
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Repo</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Image</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Scanner</th>
                <th className="px-3 py-2 text-center font-mono text-[10px] uppercase tracking-wider text-slate-500">C/H/M/L</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Status</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">When</th>
                <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-wider text-slate-500">·</th>
              </tr>
            </thead>
            <tbody>
              {!loading && reports.length === 0 ? (
                <tr><td colSpan={7} className="px-3 py-8 text-center font-mono text-xs text-slate-400 dark:text-slate-600">no scans run yet</td></tr>
              ) : reports.map((r) => (
                <tr key={r.id} onClick={() => r.status === 'success' && setDetailReport(r.id)} className={`border-b border-slate-100 last:border-0 dark:border-slate-800/60 ${r.status === 'success' ? 'cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800/30' : ''}`}>
                  <td className="px-3 py-2 font-mono text-xs text-slate-700 dark:text-slate-300">{r.target_repo}</td>
                  <td className="px-3 py-2 font-mono text-xs text-slate-800 dark:text-slate-200">{r.image}</td>
                  <td className="px-3 py-2 font-mono text-xs text-slate-500 dark:text-slate-400">{r.scanner}</td>
                  <td className="px-3 py-2 text-center font-mono tabular-nums text-xs">
                    <span className="text-rose-600 dark:text-rose-400">{r.critical}</span>/
                    <span className="text-amber-600 dark:text-amber-400">{r.high}</span>/
                    <span className="text-sky-600 dark:text-sky-400">{r.medium}</span>/
                    <span className="text-slate-500">{r.low}</span>
                  </td>
                  <td className="px-3 py-2"><Badge tone={r.status === 'success' ? 'ok' : r.status === 'failed' ? 'bad' : 'info'}>{r.status}</Badge></td>
                  <td className="px-3 py-2 font-mono text-xs text-slate-400 dark:text-slate-600">{formatDateTime(r.started_at)}</td>
                  <td className="px-3 py-2 text-right">
                    <div className="flex items-center justify-end gap-1">
                      {r.status === 'success' && <button onClick={(e) => { e.stopPropagation(); setDetailReport(r.id); }} className="border border-slate-200 px-2 py-0.5 font-mono text-[10px] uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">view</button>}
                      <button onClick={(e) => deleteReport(r.id, e)} className="border border-rose-200 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40">del</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Vulnerabilities */}
      <div>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Findings · {formatNumber(vulns.length)}</h2>
          <select value={sevFilter} onChange={(e) => { setSevFilter(e.target.value); api.get(`/scan/vulnerabilities?limit=200${e.target.value ? `&severity=${e.target.value}` : ''}`).then(setVulns); }} className="border border-slate-300 bg-white px-2 py-1 font-mono text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200">
            <option value="">all severities</option>
            <option value="CRITICAL">critical</option>
            <option value="HIGH">high</option>
            <option value="MEDIUM">medium</option>
            <option value="LOW">low</option>
          </select>
        </div>
        <div className="max-h-96 overflow-y-auto border border-slate-200 dark:border-slate-800">
          <table className="w-full border-collapse text-sm">
            <thead className="sticky top-0">
              <tr className="border-b border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60">
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">CVE</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Sev</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Package</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Installed</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">Fixed</th>
                <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-wider text-slate-500">CVSS</th>
              </tr>
            </thead>
            <tbody>
              {vulns.length === 0 ? (
                <tr><td colSpan={6} className="px-3 py-8 text-center font-mono text-xs text-slate-400 dark:text-slate-600">no findings</td></tr>
              ) : vulns.map((v) => (
                <tr key={v.id} className="border-b border-slate-100 last:border-0 dark:border-slate-800/60">
                  <td className="px-3 py-1.5 font-mono text-xs text-slate-800 dark:text-slate-200">{v.cve}</td>
                  <td className="px-3 py-1.5"><Badge tone={sevTone[v.severity] || 'neutral'}>{v.severity}</Badge></td>
                  <td className="px-3 py-1.5 font-mono text-xs text-slate-700 dark:text-slate-300">{v.package}</td>
                  <td className="px-3 py-1.5 font-mono text-xs text-slate-500 dark:text-slate-400">{v.installed_version || '—'}</td>
                  <td className="px-3 py-1.5 font-mono text-xs text-emerald-600 dark:text-emerald-400">{v.fixed_version || '—'}</td>
                  <td className="px-3 py-1.5 text-right font-mono tabular-nums text-xs text-slate-500 dark:text-slate-400">{v.cvss ? v.cvss.toFixed(1) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {editing && <TargetModal initial={editing.target} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />}
      {detailReport && <ReportDetailModal reportId={detailReport} onClose={() => setDetailReport(null)} />}
    </div>
  );
}

const INPUT = 'w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';

function TargetModal({ initial, onClose, onSaved }) {
  const isEdit = !!initial;
  const [form, setForm] = useState({
    repo: initial?.repo ?? '',
    enabled: initial?.enabled ?? true,
    auto_scan: initial?.auto_scan ?? true,
    scanners: initial?.scanners ?? '',
  });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [dockerRepos, setDockerRepos] = useState([]);

  useEffect(() => {
    // Load Docker-format repos only for the scanner dropdown.
    api.get('/storage/repos?format=docker').then(setDockerRepos).catch(() => {});
  }, []);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError('');
    try {
      if (isEdit) await api.patch(`/scan/targets/${initial.id}`, form);
      else await api.post('/scan/targets', form);
      onSaved();
    } catch (err) { setError(err.message); }
    setBusy(false);
  };

  return (
    <Modal open onClose={onClose} title={isEdit ? `Edit ${initial.repo}` : 'Enable scanning for a repository'}
      footer={<>
        <button onClick={onClose} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">Cancel</button>
        <button onClick={submit} disabled={busy} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">{busy ? '···' : 'Save'}</button>
      </>}>
      <form onSubmit={submit} className="space-y-3">
        <Field label="Repository (Docker only)">
          <select value={form.repo} disabled={isEdit} onChange={(e) => setForm({ ...form, repo: e.target.value })} className={INPUT}>
            <option value="">— select docker repository —</option>
            {dockerRepos.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
          </select>
        </Field>
        <Field label="Scanners (comma-separated, blank = global default)"><input value={form.scanners} onChange={(e) => setForm({ ...form, scanners: e.target.value })} placeholder="trivy,grype" className={INPUT} /></Field>
        <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
          <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} className="accent-sky-500" />
          <span className="font-mono text-xs">enabled</span>
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
          <input type="checkbox" checked={form.auto_scan} onChange={(e) => setForm({ ...form, auto_scan: e.target.checked })} className="accent-sky-500" />
          <span className="font-mono text-xs">auto-scan on push (polls for new images)</span>
        </label>
        {error && <div className="font-mono text-xs text-rose-600 dark:text-rose-400">{error}</div>}
      </form>
    </Modal>
  );
}

function Field({ label, children }) {
  return (<div><div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">{label}</div>{children}</div>);
}

/**
 * Scanner DB status card. Shows version, the date the DB is "for", its size,
 * and a freshness hint. While updating, shows a pulse + the active job state.
 */
function DbCard({ name, info, updating }) {
  const installed = info?.installed;
  const present = info?.present;
  const dateFor = info?.created_at || info?.built || info?.downloaded_at;
  const stale = info?.next_update && new Date(info.next_update).getTime() < Date.now();

  return (
    <div className="bg-white p-3 dark:bg-slate-900/40">
      <div className="mb-1 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs font-medium uppercase tracking-wider text-slate-700 dark:text-slate-300">{name}</span>
          {installed ? (
            present ? <Badge tone={stale ? 'warn' : 'ok'}>{stale ? 'stale' : 'ready'}</Badge>
                    : <Badge tone="warn">no db</Badge>
          ) : <Badge tone="bad">not installed</Badge>}
        </div>
        {updating && (
          <span className="flex items-center gap-1 font-mono text-[10px] text-sky-600 dark:text-sky-400">
            <Icon name="refresh" size={11} className="animate-spin" /> updating…
          </span>
        )}
      </div>
      {installed && present && (
        <div className="grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-[11px]">
          <div>
            <div className="text-slate-400 dark:text-slate-600">version</div>
            <div className="text-slate-700 dark:text-slate-300">{info.version || '—'}</div>
          </div>
          <div>
            <div className="text-slate-400 dark:text-slate-600">db for</div>
            <div className="text-slate-700 dark:text-slate-300" title={dateFor}>{dateFor ? relativeTime(dateFor) : '—'}</div>
          </div>
          <div>
            <div className="text-slate-400 dark:text-slate-600">size</div>
            <div className="text-slate-700 dark:text-slate-300">{formatBytes(info.size_bytes || 0)}</div>
          </div>
          <div>
            <div className="text-slate-400 dark:text-slate-600">next update</div>
            <div className={stale ? 'text-amber-600 dark:text-amber-400' : 'text-slate-700 dark:text-slate-300'}>
              {info.next_update ? relativeTime(info.next_update) : (info.built ? relativeTime(info.built) : '—')}
            </div>
          </div>
        </div>
      )}
      {installed && !present && (
        <p className="mt-1 font-mono text-[10px] text-slate-400 dark:text-slate-600">
          no vulnerability database on disk yet — click “Refresh vuln DBs”.
        </p>
      )}
    </div>
  );
}

/** Detailed vulnerability breakdown for a single scan report. */
function ReportDetailModal({ reportId, onClose }) {
  const [vulns, setVulns] = useState([]);
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [sevFilter, setSevFilter] = useState('');

  useEffect(() => {
    Promise.all([
      api.get(`/scan/reports/${reportId}/vulnerabilities`).catch(() => []),
      api.get('/scan/reports?limit=200').then((rs) => rs.find((r) => r.id === reportId)).catch(() => null),
    ]).then(([v, r]) => {
      setVulns(v);
      setReport(r);
      setLoading(false);
    });
  }, [reportId]);

  const filtered = sevFilter ? vulns.filter((v) => v.severity === sevFilter) : vulns;
  const sevTone = { CRITICAL: 'bad', HIGH: 'warn', MEDIUM: 'info', LOW: 'neutral', UNKNOWN: 'neutral' };

  return (
    <Modal open onClose={onClose} wide title={report ? `${report.image} (${report.scanner})` : 'Scan detail'}
      footer={<button onClick={onClose} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">Close</button>}>
      {loading ? (
        <div className="py-8 text-center font-mono text-xs text-slate-400">loading…</div>
      ) : (
        <div className="space-y-3">
          {report && (
            <div className="grid grid-cols-4 gap-2 font-mono text-xs">
              <div className="border border-slate-200 p-2 text-center dark:border-slate-800"><div className="text-slate-500">Critical</div><div className="text-lg text-rose-600 dark:text-rose-400">{report.critical}</div></div>
              <div className="border border-slate-200 p-2 text-center dark:border-slate-800"><div className="text-slate-500">High</div><div className="text-lg text-amber-600 dark:text-amber-400">{report.high}</div></div>
              <div className="border border-slate-200 p-2 text-center dark:border-slate-800"><div className="text-slate-500">Medium</div><div className="text-lg text-sky-600 dark:text-sky-400">{report.medium}</div></div>
              <div className="border border-slate-200 p-2 text-center dark:border-slate-800"><div className="text-slate-500">Low</div><div className="text-lg text-slate-500">{report.low}</div></div>
            </div>
          )}
          <div className="flex items-center justify-between">
            <span className="font-mono text-[10px] uppercase text-slate-500">{filtered.length} findings</span>
            <select value={sevFilter} onChange={(e) => setSevFilter(e.target.value)} className="border border-slate-300 bg-white px-2 py-1 font-mono text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200">
              <option value="">all</option>
              <option value="CRITICAL">critical</option>
              <option value="HIGH">high</option>
              <option value="MEDIUM">medium</option>
              <option value="LOW">low</option>
            </select>
          </div>
          <div className="max-h-96 overflow-y-auto border border-slate-200 dark:border-slate-800">
            <table className="w-full border-collapse text-sm">
              <thead className="sticky top-0">
                <tr className="border-b border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60">
                  <th className="px-3 py-1.5 text-left font-mono text-[10px] uppercase text-slate-500">CVE</th>
                  <th className="px-3 py-1.5 text-left font-mono text-[10px] uppercase text-slate-500">Sev</th>
                  <th className="px-3 py-1.5 text-left font-mono text-[10px] uppercase text-slate-500">Package</th>
                  <th className="px-3 py-1.5 text-left font-mono text-[10px] uppercase text-slate-500">Installed</th>
                  <th className="px-3 py-1.5 text-left font-mono text-[10px] uppercase text-slate-500">Fixed</th>
                  <th className="px-3 py-1.5 text-right font-mono text-[10px] uppercase text-slate-500">CVSS</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr><td colSpan={6} className="px-3 py-8 text-center font-mono text-xs text-slate-400">no findings</td></tr>
                ) : filtered.map((v) => (
                  <tr key={v.id} className="border-b border-slate-100 last:border-0 dark:border-slate-800/60">
                    <td className="px-3 py-1.5 font-mono text-xs text-slate-800 dark:text-slate-200">{v.cve}</td>
                    <td className="px-3 py-1.5"><Badge tone={sevTone[v.severity] || 'neutral'}>{v.severity}</Badge></td>
                    <td className="px-3 py-1.5 font-mono text-xs text-slate-700 dark:text-slate-300">{v.package}</td>
                    <td className="px-3 py-1.5 font-mono text-xs text-slate-500 dark:text-slate-400">{v.installed_version || '—'}</td>
                    <td className="px-3 py-1.5 font-mono text-xs text-emerald-600 dark:text-emerald-400">{v.fixed_version || '—'}</td>
                    <td className="px-3 py-1.5 text-right font-mono tabular-nums text-xs text-slate-500">{v.cvss ? v.cvss.toFixed(1) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Modal>
  );
}
