import { useEffect, useState } from 'react';
import Modal from '../../../components/Modal.jsx';
import { API_BASE } from '../../../lib/api.js';
import { scanApi } from '../api.js';
import VulnerabilityTable from './VulnerabilityTable.jsx';

/** "myapp:27" + scanner "trivy" -> "myapp-27-trivy" — a readable download
 * name instead of the report's opaque numeric id, with the scanner in it so
 * the trivy and grype PDFs for the same tag don't land as indistinguishable
 * files (both were previously just "myapp-27.pdf", "myapp-27 (1).pdf").
 * `/`-separated image names (e.g. "team/app") flatten to dashes so the
 * result is a single safe filename component. */
function pdfFilename(report) {
  const image = report?.image || 'scan-report';
  const idx = image.lastIndexOf(':');
  const name = idx === -1 ? image : image.slice(0, idx);
  const tag = idx === -1 ? '' : image.slice(idx + 1);
  // `report.image`/`report.scanner` are Nexus-sourced scan metadata, not raw
  // user input — no adversarial input path for the flagged
  // super-linear-backtracking concern.
  const safe = (s) => s.replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+|-+$/g, ''); // NOSONAR
  return [safe(name), safe(tag), safe(report?.scanner || '')].filter(Boolean).join('-') || 'scan-report';
}

/**
 * Detailed breakdown for a single scan report. For a failure this shows the
 * reason and the scanner's own output — the point where "FAILED" stops being
 * a dead end.
 */
export default function ReportDetailModal({ reportId, onClose }) {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfError, setPdfError] = useState('');

  useEffect(() => {
    scanApi.report(reportId).catch(() => null).then((r) => {
      setReport(r);
      setLoading(false);
    });
  }, [reportId]);

  // Cookie-based auth (not bearer tokens), so a plain <a href> download
  // wouldn't reliably carry credentials — fetch the PDF as a blob and trigger
  // the save via a temporary object URL, same pattern as the backup-archive
  // and metadata-export downloads in SystemPage.jsx.
  const downloadPdf = async () => {
    setPdfError('');
    setPdfBusy(true);
    try {
      const resp = await fetch(`${API_BASE}/scan/reports/${reportId}/pdf`, { credentials: 'include' });
      if (!resp.ok) {
        const txt = await resp.text().catch(() => '');
        throw new Error(`Download failed (${resp.status}): ${txt.slice(0, 150)}`);
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${pdfFilename(report)}.pdf`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e) { setPdfError(e.message); }
    setPdfBusy(false);
  };

  return (
    <Modal open onClose={onClose} wide title={report ? `${report.image} (${report.scanner})` : 'Scan detail'}
      footer={<>
        <button onClick={downloadPdf} disabled={pdfBusy || !report}
          className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">
          {pdfBusy ? '···' : 'Download PDF'}
        </button>
        <button onClick={onClose} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">Close</button>
      </>}>
      {pdfError && (
        <div className="mb-3 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{pdfError}</div>
      )}
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
