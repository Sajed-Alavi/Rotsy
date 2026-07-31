import { useEffect, useState } from 'react';
import Modal from '../../../components/Modal.jsx';
import { scanApi } from '../api.js';
import VulnerabilityTable from './VulnerabilityTable.jsx';

/**
 * Detailed breakdown for a single scan report. For a failure this shows the
 * reason and the scanner's own output — the point where "FAILED" stops being
 * a dead end.
 */
export default function ReportDetailModal({ reportId, onClose }) {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    scanApi.report(reportId).catch(() => null).then((r) => {
      setReport(r);
      setLoading(false);
    });
  }, [reportId]);

  return (
    <Modal open onClose={onClose} wide title={report ? `${report.image} (${report.scanner})` : 'Scan detail'}
      footer={<button onClick={onClose} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">Close</button>}>
      {loading ? (
        <div className="py-8 text-center font-mono text-xs text-slate-400">loading…</div>
      ) : (
        <div className="space-y-3">
          {report?.registry_ref && (
            <div className="font-mono text-[11px] text-slate-500 dark:text-slate-500">
              scanned <span className="text-slate-700 dark:text-slate-300">{report.registry_ref}</span>
              {report.duration_ms ? ` · ${(report.duration_ms / 1000).toFixed(1)}s` : ''}
            </div>
          )}
          {report?.status === 'failed' && (
            <div className="border border-rose-200 bg-rose-50 p-3 dark:border-rose-800 dark:bg-rose-950/30">
              <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-rose-700 dark:text-rose-400">Scan failed</div>
              <div className="font-mono text-xs text-rose-700 dark:text-rose-300">{report.error || 'no reason recorded'}</div>
              {report.detail && (
                <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-all border border-rose-200 bg-white/60 p-2 font-mono text-[10px] text-slate-600 dark:border-rose-900 dark:bg-slate-950/60 dark:text-slate-400">{report.detail}</pre>
              )}
            </div>
          )}
          {report && (
            <div className="grid grid-cols-4 gap-2 font-mono text-xs">
              <div className="border border-slate-200 p-2 text-center dark:border-slate-800"><div className="text-slate-500">Critical</div><div className="text-lg text-rose-600 dark:text-rose-400">{report.critical}</div></div>
              <div className="border border-slate-200 p-2 text-center dark:border-slate-800"><div className="text-slate-500">High</div><div className="text-lg text-amber-600 dark:text-amber-400">{report.high}</div></div>
              <div className="border border-slate-200 p-2 text-center dark:border-slate-800"><div className="text-slate-500">Medium</div><div className="text-lg text-sky-600 dark:text-sky-400">{report.medium}</div></div>
              <div className="border border-slate-200 p-2 text-center dark:border-slate-800"><div className="text-slate-500">Low</div><div className="text-lg text-slate-500">{report.low}</div></div>
            </div>
          )}
          <VulnerabilityTable endpoint={scanApi.findingsEndpoint.forReport(reportId)} />
        </div>
      )}
    </Modal>
  );
}
