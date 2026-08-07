import { useEffect, useState } from 'react';
import Badge from '../../../components/Badge.jsx';
import Icon from '../../../components/Icon.jsx';
import { codeQualityApi } from '../api.js';

const INPUT = 'w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';

/**
 * The picker: choose a synced repository, choose a branch, run analysis.
 * Deliberately just the picker + trigger — run history lives on the
 * "Analysis Runs" tab so it isn't duplicated in two places.
 */
export default function OverviewPage() {
  const [repos, setRepos] = useState(null);
  const [reposErr, setReposErr] = useState('');
  const [selectedKey, setSelectedKey] = useState('');
  const [branches, setBranches] = useState(null);
  const [branchesErr, setBranchesErr] = useState('');
  const [branch, setBranch] = useState('');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    codeQualityApi.repositories()
      .then(setRepos)
      .catch((e) => setReposErr(e.message));
  }, []);

  const selected = repos?.find((r) => `${r.source_module}:${r.repository_id}` === selectedKey);

  useEffect(() => {
    setBranches(null);
    setBranchesErr('');
    setBranch('');
    if (!selected) return;
    codeQualityApi.branches(selected.source_module, selected.repository_id)
      .then((res) => { setBranches(res.branches); setBranch(res.default_branch); })
      .catch((e) => setBranchesErr(e.message));
  }, [selectedKey]);

  const runAnalysis = async () => {
    if (!selected || !branch) return;
    setRunning(true); setErr(''); setResult(null);
    try {
      const r = await codeQualityApi.analyze(selected.source_module, selected.repository_id, branch);
      setResult(r);
    } catch (e) { setErr(e.message); }
    setRunning(false);
  };

  if (reposErr) {
    return <div className="border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{reposErr}</div>;
  }
  if (repos === null) {
    return <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">loading…</p>;
  }

  return (
    <div className="max-w-xl space-y-4">
      {repos.length === 0 ? (
        <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">
          No repositories connected yet — add one from a Project's Repositories tab first.
        </p>
      ) : (
        <>
          <div>
            <label className="mb-1 block font-mono text-[10px] uppercase tracking-wider text-slate-500">Repository</label>
            <select value={selectedKey} onChange={(e) => setSelectedKey(e.target.value)} className={INPUT}>
              <option value="">Select a repository…</option>
              {repos.map((r) => (
                <option key={`${r.source_module}:${r.repository_id}`} value={`${r.source_module}:${r.repository_id}`}>
                  {r.project_name ? `${r.project_name} — ` : ''}{r.full_name} ({r.source_module})
                </option>
              ))}
            </select>
          </div>

          {selected && (
            <div>
              <label className="mb-1 block font-mono text-[10px] uppercase tracking-wider text-slate-500">Branch</label>
              {branchesErr ? (
                <p className="font-mono text-xs text-rose-600 dark:text-rose-400">{branchesErr}</p>
              ) : branches === null ? (
                <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">loading branches…</p>
              ) : (
                <>
                  <select value={branch} onChange={(e) => setBranch(e.target.value)} className={INPUT}>
                    {branches.map((b) => (
                      <option key={b} value={b}>{b}{b === selected.default_branch ? ' (default)' : ''}</option>
                    ))}
                  </select>
                  {branch !== selected.default_branch && (
                    <p className="mt-1 font-mono text-[10px] text-amber-600 dark:text-amber-400">
                      Non-default branches require SonarQube Developer Edition or above — this will fail
                      clearly on Community Edition rather than silently falling back to the default branch.
                    </p>
                  )}
                </>
              )}
            </div>
          )}

          <button
            onClick={runAnalysis}
            disabled={!selected || !branch || running}
            className="flex items-center gap-1.5 border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40"
          >
            <Icon name="play" size={12} /> {running ? '···' : 'Run Analysis'}
          </button>

          {result && (
            <div className="flex items-center gap-2 border border-emerald-200 bg-emerald-50 px-3 py-2 font-mono text-xs text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-400">
              <Badge tone="ok">queued</Badge>
              Analyzing commit {result.commit_sha.slice(0, 8)} on {result.ref} — see the Analysis Runs tab for progress.
            </div>
          )}
          {err && <div className="border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{err}</div>}
        </>
      )}
    </div>
  );
}
