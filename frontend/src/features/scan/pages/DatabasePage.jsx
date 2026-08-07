import { useCallback, useEffect, useRef } from 'react';
import Badge from '../../../components/Badge.jsx';
import Notice from '../../../components/Notice.jsx';
import Section from '../../../components/Section.jsx';
import { formatBytes } from '../../../lib/format.js';
import { scanApi } from '../api.js';
import { useResource, useStatus } from '../../../lib/useResource.js';
import { useDbJob } from '../hooks/useDbJob.js';
import DbPanel from '../components/DbPanel.jsx';

/**
 * Manage the Trivy and Grype vulnerability databases without running a scan.
 *
 * Previously the only controls were two buttons in the page header — "Refresh
 * vuln DBs" and "Import offline DBs" — which refreshed both scanners at once
 * and reported progress as the word "updating…" for a fixed 60 seconds. There
 * was no way to update one scanner, no way to force a re-download, and no way
 * to tell a finished run from one the timer had simply given up on.
 */
export default function DatabasePage() {
  const { data: dbStatus, reload: reloadStatus } = useResource(() => scanApi.dbStatus(), null);
  const { data: offline, reload: reloadOffline } = useResource(() => scanApi.offlineStatus(), null);
  const { status, say, fail, clear } = useStatus();
  const logRef = useRef(null);

  const onFinished = useCallback((outcome) => {
    reloadStatus();
    reloadOffline();
    if (outcome === 'done') say('Database job finished.', 'ok');
    else if (outcome === 'failed') fail('Database job failed — see the log below.');
    else if (outcome === 'cancelled') say('Database job cancelled.', 'info');
  }, [reloadStatus, reloadOffline, say, fail]);

  const { job, scanners, log, running, start, cancel, dismiss } = useDbJob(onFinished);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [log]);

  const run = async (fn, what) => {
    clear();
    try {
      await start(fn);
      say(`${what} started.`, 'info');
    } catch (e) {
      fail(`${what} could not be queued: ${e.message}`);
    }
  };

  const cancelRun = async () => {
    try {
      await cancel();
      say('Cancelling — the download will stop within a few seconds.', 'info');
    } catch (e) {
      fail(`could not cancel: ${e.message}`);
    }
  };

  const overall = job?.progress ?? 0;

  return (
    <>
      <Notice status={status} onDismiss={clear} />

      <Section
        title="Scanner databases"
        hint={running ? `job ${job.id.slice(0, 8)} running` : 'updated on a schedule; refresh manually here'}
        flush
        actions={
          <button
            onClick={() => run(() => scanApi.updateDb(false), 'Update of all databases')}
            disabled={running}
            className="border border-sky-300 bg-sky-50 px-2.5 py-1 font-mono text-[11px] uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40"
          >
            Update all
          </button>
        }
      >
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {['trivy', 'grype'].map((name) => (
            <DbPanel
              key={name}
              name={name}
              info={dbStatus?.[name]}
              live={scanners[name]}
              busy={running}
              onUpdate={() => run(() => scanApi.updateDb(false), `${name} update`)}
              onForce={() => run(() => scanApi.updateDb(true), `${name} forced re-download`)}
            />
          ))}
        </div>
      </Section>

      {/* Job state — visible whether or not this tab started the run. */}
      {job && (
        <Section
          title="Current job"
          hint={job.id?.slice(0, 8)}
          actions={running ? (
            <button onClick={cancelRun} className="border border-rose-300 px-2 py-0.5 font-mono text-[10px] uppercase text-rose-600 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40">cancel</button>
          ) : (
            <button onClick={dismiss} className="border border-slate-300 px-2 py-0.5 font-mono text-[10px] uppercase text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">dismiss</button>
          )}
        >
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <Badge tone={running ? 'info' : job.status === 'done' ? 'ok' : job.status === 'failed' ? 'bad' : job.status === 'cancelled' ? 'warn' : 'neutral'}>
              {running ? 'running' : job.status}
            </Badge>
            <span className="font-mono text-[11px] text-slate-500 dark:text-slate-400">{job.message}</span>
            <span className="ml-auto font-mono text-[11px] tabular-nums text-slate-400 dark:text-slate-600">{overall}%</span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
            <div
              className={`h-full rounded-full transition-all duration-500 ${job.status === 'failed' ? 'bg-rose-500' : 'bg-sky-500'}`}
              style={{ width: `${overall}%` }}
            />
          </div>

          {log.length > 0 && (
            <div ref={logRef} className="mt-3 max-h-48 overflow-y-auto border border-slate-200 bg-slate-50 p-2 font-mono text-[10px] leading-relaxed text-slate-600 dark:border-slate-800 dark:bg-slate-900/40 dark:text-slate-400">
              {/* index is a safe key here: log only ever grows by appending
                  streamed lines, never reordered/filtered — and line text
                  alone isn't unique (repeated progress messages happen). */}
              {log.map((line, i) => <div key={i}>{line}</div>)} {/* NOSONAR */}
            </div>
          )}
        </Section>
      )}

      <Section
        title="Offline / air-gapped import"
        hint={offline?.dir}
        actions={
          <button
            onClick={() => run(() => scanApi.importDb(), 'Offline import')}
            disabled={running || !offline?.exists}
            title={offline?.exists ? `Import from ${offline.dir}` : 'Offline directory not found — mount ./offline-db and drop the archives in'}
            className="border border-slate-300 px-2.5 py-1 font-mono text-[11px] uppercase tracking-wider text-slate-600 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            Import offline DBs
          </button>
        }
      >
        {!offline?.exists ? (
          <p className="font-mono text-[11px] text-slate-500 dark:text-slate-400">
            The offline directory does not exist. Create <code>./offline-db</code> on the host (it is already
            mounted by docker-compose), populate it with <code>scripts/scanner/fetch-offline-db.sh</code> on a
            connected machine, then import here. Use this when Docker Hub and ghcr.io are unreachable.
          </p>
        ) : (
          <>
            <div className="mb-3 flex flex-wrap gap-4 font-mono text-[11px]">
              <span className="flex items-center gap-1.5">
                trivy archive <Badge tone={offline.trivy_db ? 'ok' : 'neutral'}>{offline.trivy_db ? 'found' : 'missing'}</Badge>
              </span>
              <span className="flex items-center gap-1.5">
                grype archive <Badge tone={offline.grype_db ? 'ok' : 'neutral'}>{offline.grype_db ? 'found' : 'missing'}</Badge>
              </span>
            </div>
            {(offline.files || []).length > 0 ? (
              <ul className="font-mono text-[11px] text-slate-600 dark:text-slate-400">
                {offline.files.map((f) => (
                  <li key={f.name} className="flex justify-between border-b border-slate-100 py-1 last:border-0 dark:border-slate-800/60">
                    <span>{f.name}</span>
                    <span className="tabular-nums text-slate-400 dark:text-slate-600">{formatBytes(f.size_bytes)}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="font-mono text-[11px] text-slate-500 dark:text-slate-400">Directory exists but is empty.</p>
            )}
          </>
        )}
      </Section>
    </>
  );
}
