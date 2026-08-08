import { useCallback, useEffect, useRef, useState } from 'react';
import { API_BASE } from '../../../lib/api.js';
import { scanApi } from '../api.js';

/**
 * Live state of the vulnerability-database job.
 *
 * Replaces a fixed 60-second `setInterval` that re-fetched db-status and then
 * declared itself finished regardless of what the job was doing. Grype's
 * database is ~208 MB, so that timer routinely expired mid-download and the
 * spinner stopped while bytes were still moving.
 *
 * Instead: ask the backend which job is current, then subscribe to its existing
 * SSE stream. That also means the page attaches to updates it did not start —
 * the startup fetch, the nightly schedule, another operator — and survives a
 * reload, because the job's last `detail` is stored on the job itself.
 *
 * Per-scanner state is tracked separately: an update refreshes Trivy and Grype
 * in sequence, and one shared boolean could not say which was moving.
 */
const TERMINAL = new Set(['done', 'failed', 'cancelled']);

export function useDbJob(onFinished) {
  const [job, setJob] = useState(null);          // { id, status, progress, message, detail }
  const [scanners, setScanners] = useState({});  // { trivy: {...}, grype: {...} }
  const [log, setLog] = useState([]);
  const sourceRef = useRef(null);
  const finishedRef = useRef(onFinished);
  finishedRef.current = onFinished;

  const closeStream = useCallback(() => {
    if (sourceRef.current) {
      sourceRef.current.close();
      sourceRef.current = null;
    }
  }, []);

  /** Fold one progress event into per-scanner state. */
  const applyDetail = useCallback((detail, message) => {
    if (!detail) return;
    const name = detail.scanner;
    if (!name) return;
    setScanners((prev) => ({
      ...prev,
      [name]: { ...prev[name], ...detail, message },
    }));
  }, []);

  const subscribe = useCallback((jobId) => {
    closeStream();
    const src = new EventSource(`${API_BASE}/jobs/${jobId}/stream`, { withCredentials: true });
    sourceRef.current = src;

    const safeParse = (raw) => { try { return JSON.parse(raw); } catch { return null; } };

    src.addEventListener('progress', (e) => {
      const d = safeParse(e.data);
      if (!d) return;
      setJob((j) => ({ ...j, id: jobId, status: 'running', progress: d.percent, message: d.message, detail: d.detail }));
      applyDetail(d.detail, d.message);
      if (d.message) setLog((l) => [...l.slice(-199), d.message]);
    });

    src.addEventListener('phase', (e) => {
      const d = safeParse(e.data) || {};
      if (d.message) setLog((l) => [...l.slice(-199), d.message]);
      if (TERMINAL.has(d.status)) {
        setJob((j) => ({ ...j, id: jobId, status: d.status, message: d.message }));
        closeStream();
        finishedRef.current?.(d.status);
      }
    });

    src.addEventListener('result', (e) => {
      const d = safeParse(e.data) || {};
      setJob((j) => ({ ...j, id: jobId, status: 'done', result: d.result }));
      closeStream();
      finishedRef.current?.('done');
    });

    src.addEventListener('error', (e) => {
      const d = safeParse(e.data);
      if (d?.message) setLog((l) => [...l.slice(-199), d.message]);
      // EventSource reconnects on transient drops; only a CLOSED socket means
      // it has given up. Same distinction the storage analyzer makes.
      if (src.readyState === EventSource.CLOSED) {
        setJob((j) => ({ ...j, status: 'failed', message: d?.message || 'connection lost' }));
        closeStream();
        finishedRef.current?.('failed');
      }
    });
  }, [applyDetail, closeStream]);

  /** On mount, adopt whatever job is current — started here or not. */
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const current = await scanApi.dbJob();
        if (cancelled || !current) return;
        setJob(current);
        applyDetail(current.detail, current.message);
        if (current.active) subscribe(current.id);
      } catch { /* no current job, or cache unavailable — nothing to attach to */ }
    })();
    return () => { cancelled = true; closeStream(); };
  }, [applyDetail, subscribe, closeStream]);

  /** Start an update/import and follow it. */
  const start = useCallback(async (fn) => {
    setLog([]);
    setScanners({});
    const r = await fn();
    setJob({ id: r.job_id, status: 'running', progress: 0, message: 'queued' });
    subscribe(r.job_id);
    return r;
  }, [subscribe]);

  /** Stop the active job. The backend kills the subprocess it owns (see
   * services/scanning/db/process.py) before marking the job cancelled, so this
   * is real termination, not a UI-only dismissal — the SSE 'phase' event with
   * status 'cancelled' (already subscribed to above) reflects it back here. */
  const cancel = useCallback(async () => {
    if (!job?.id) return;
    await scanApi.cancelJob(job.id);
  }, [job?.id]);

  const running = !!job && !TERMINAL.has(job.status);

  return { job, scanners, log, running, start, cancel, dismiss: () => setJob(null) };
}
