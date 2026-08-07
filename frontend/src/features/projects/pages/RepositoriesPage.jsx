import { useEffect, useState } from 'react';
import { useOutletContext } from 'react-router';
import { api } from '../../../lib/api.js';
import Badge from '../../../components/Badge.jsx';
import DataTable from '../../../components/DataTable.jsx';

const INPUT = 'w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';
const SONAR_LANGUAGES = ['python', 'javascript', 'typescript', 'go', 'php', 'ruby', 'css', 'html'];

/**
 * Repositories: the "put 17, or 1000, repositories into this Project" view.
 * A Project is a grouping, not a 1:1 wrapper around a single repo — each row
 * here is independently connected, independently language-detected, and
 * independently analyzed. Deliberately separate from Settings -> Integrations
 * (which manages the GitHub App / GitLab tokens themselves, not what's
 * attached to which Project).
 */
export default function RepositoriesPage() {
  const { projectId } = useOutletContext();
  const [repos, setRepos] = useState([]);
  const [err, setErr] = useState('');
  const [busyRepoId, setBusyRepoId] = useState(null);
  const [msg, setMsg] = useState('');

  const load = async () => {
    setErr('');
    try { setRepos(await api.get(`/projects/${projectId}/repositories`)); }
    catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); }, [projectId]);

  const runAnalysis = async (repo) => {
    if (!repo.sonar_project_id) return;
    setBusyRepoId(repo.repository_id); setMsg(''); setErr('');
    try {
      const r = await api.post(`/modules/sonar/repositories/${repo.sonar_project_id}/run-analysis`, {});
      setMsg(`Queued analysis for ${repo.full_name} (commit ${r.commit_sha.slice(0, 8)}).`);
    } catch (e) { setErr(e.message); }
    setBusyRepoId(null);
  };

  const columns = [
    {
      key: 'full_name', header: 'Repository', mono: true,
      render: (v, row) => (
        <span className="flex items-center gap-2">
          <Badge tone="neutral">{row.source_module}</Badge>
          {v}
        </span>
      ),
    },
    { key: 'default_branch', header: 'Branch', mono: true },
    {
      key: 'language', header: 'Language',
      render: (v) => v ? <Badge tone="info">{v}</Badge> : <span className="font-mono text-[10px] text-slate-400">not connected</span>,
    },
    {
      key: 'auto_analyze_on_push', header: 'Auto-analyze',
      render: (v) => <Badge tone={v ? 'ok' : 'warn'}>{v ? 'on push' : 'manual only'}</Badge>,
    },
    {
      key: 'actions', header: '', className: 'text-right',
      render: (_v, row) => (
        row.sonar_project_id ? (
          <button
            onClick={() => runAnalysis(row)}
            disabled={busyRepoId === row.repository_id}
            className="border border-slate-300 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-slate-600 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            {busyRepoId === row.repository_id ? '···' : 'Run Analysis'}
          </button>
        ) : (
          <ConnectSonarButton repo={row} projectId={projectId} onDone={load} />
        )
      ),
    },
  ];

  return (
    <div className="grid grid-cols-1 gap-6">
      {err && <div className="border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{err}</div>}
      {msg && <div className="border border-emerald-200 bg-emerald-50 px-3 py-2 font-mono text-xs text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-400">{msg}</div>}

      <DataTable columns={columns} rows={repos} empty="No repositories connected yet — add some below." />

      <AddRepositories projectId={projectId} connectedFullNames={repos.map((r) => r.full_name)} onDone={load} />
    </div>
  );
}

function ConnectSonarButton({ repo, projectId, onDone }) {
  const [open, setOpen] = useState(false);
  const [language, setLanguage] = useState(SONAR_LANGUAGES[0]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const connect = async () => {
    setBusy(true); setErr('');
    try {
      const repoIdField = repo.source_module === 'github' ? 'github_repository_id' : 'gitlab_repository_id';
      await api.post('/modules/sonar/projects', {
        project_id: projectId, language, [repoIdField]: repo.repository_id,
      });
      setOpen(false);
      onDone();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className="border border-sky-300 bg-sky-50 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-sky-700 hover:bg-sky-100 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300">
        Connect Sonar
      </button>
    );
  }
  return (
    <span className="flex items-center justify-end gap-1.5">
      <select value={language} onChange={(e) => setLanguage(e.target.value)} className="border border-slate-300 bg-white px-1.5 py-1 font-mono text-[10px] text-slate-700 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-300">
        {SONAR_LANGUAGES.map((l) => <option key={l} value={l}>{l}</option>)}
      </select>
      <button onClick={connect} disabled={busy} className="border border-sky-300 bg-sky-50 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300">
        {busy ? '···' : 'Go'}
      </button>
      {err && <span className="font-mono text-[10px] text-rose-600 dark:text-rose-400">{err}</span>}
    </span>
  );
}

/**
 * Three ways to add repositories, matching the three backend paths:
 *   1. Pick from GitHub repos already discovered (App-installed) — bulk-map.
 *   2. Paste public GitHub repo names, one per line — bulk-connect-by-URL.
 *   3. Pick from GitLab repos already discovered (account-connected) — bulk-map.
 * All three are scalable to hundreds/thousands: mapping is immediate,
 * Sonar provisioning + first analysis runs as background jobs per repo.
 */
function AddRepositories({ projectId, connectedFullNames, onDone }) {
  const [mode, setMode] = useState('github-discovered');
  const [githubUnmapped, setGithubUnmapped] = useState([]);
  const [gitlabUnmapped, setGitlabUnmapped] = useState([]);
  const [selected, setSelected] = useState(new Set());
  const [publicNames, setPublicNames] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    api.get('/modules/github/repositories?unmapped=true').then(setGithubUnmapped).catch(() => {});
    api.get('/modules/gitlab/repositories?unmapped=true').then(setGitlabUnmapped).catch(() => {});
  }, []);

  const toggle = (id) => {
    setSelected((s) => {
      const next = new Set(s);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const addSelected = async (source) => {
    setBusy(true); setErr(''); setResult(null);
    try {
      const path = source === 'github' ? '/modules/github/repositories/bulk-map' : '/modules/gitlab/repositories/bulk-map';
      const r = await api.post(path, { project_id: projectId, repo_ids: Array.from(selected) });
      setResult(r);
      setSelected(new Set());
      onDone();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  const addPublic = async () => {
    const names = publicNames.split('\n').map((s) => s.trim()).filter(Boolean);
    if (names.length === 0) return;
    setBusy(true); setErr(''); setResult(null);
    try {
      const r = await api.post('/modules/github/public-repositories/bulk', { project_id: projectId, full_names: names });
      setResult(r);
      setPublicNames('');
      onDone();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  const modeButton = (key, label) => (
    <button
      type="button"
      onClick={() => { setMode(key); setSelected(new Set()); }} // IDs aren't comparable across GitHub/GitLab repo tables
      className={`border px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider ${mode === key ? 'border-sky-300 bg-sky-50 text-sky-700 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300' : 'border-slate-300 text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800'}`}
    >
      {label}
    </button>
  );

  return (
    <section className="border border-slate-200 p-4 dark:border-slate-800">
      <h2 className="mb-3 font-mono text-[10px] uppercase tracking-wider text-slate-500">Add repositories</h2>
      <div className="mb-3 flex flex-wrap gap-1.5">
        {modeButton('github-discovered', `GitHub (${githubUnmapped.length} discovered)`)}
        {modeButton('github-public', 'GitHub — public by URL')}
        {modeButton('gitlab-discovered', `GitLab (${gitlabUnmapped.length} discovered)`)}
      </div>

      {mode === 'github-discovered' && (
        <RepoPicker
          repos={githubUnmapped} label={(r) => r.full_name} selected={selected} onToggle={toggle}
          onSubmit={() => addSelected('github')} busy={busy}
          empty="No unmapped GitHub repositories. Install/sync the App from Settings → Integrations."
        />
      )}
      {mode === 'gitlab-discovered' && (
        <RepoPicker
          repos={gitlabUnmapped} label={(r) => r.full_path} selected={selected} onToggle={toggle}
          onSubmit={() => addSelected('gitlab')} busy={busy}
          empty="No unmapped GitLab repositories. Connect an account and sync from Settings → Integrations."
        />
      )}
      {mode === 'github-public' && (
        <div className="space-y-2">
          <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">
            One <code>owner/repo</code> per line. No GitHub App install needed — trade-off: automatic
            push-triggered analysis isn't available for these (see Settings → Integrations → GitHub).
            Subject to GitHub's unauthenticated rate limit (60/hour) — a few dozen at a time, not a thousand.
          </p>
          <textarea
            value={publicNames}
            onChange={(e) => setPublicNames(e.target.value)}
            rows={6}
            placeholder={'facebook/react\nvuejs/vue\ndjango/django'}
            className={`${INPUT} font-mono`}
          />
          <button onClick={addPublic} disabled={busy || !publicNames.trim()} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">
            {busy ? '···' : 'Connect'}
          </button>
        </div>
      )}

      {result && (
        <div className="mt-3 font-mono text-[11px] text-emerald-600 dark:text-emerald-400">
          {(result.mapped ?? result.connected)} connected, {result.queued} queued for Sonar provisioning.
          {result.errors?.length > 0 && (
            <ul className="mt-1 list-disc pl-4 text-rose-600 dark:text-rose-400">
              {result.errors.map((e, i) => <li key={i}>{e}</li>)}
            </ul>
          )}
        </div>
      )}
      {err && <div className="mt-3 font-mono text-[11px] text-rose-600 dark:text-rose-400">{err}</div>}
      {connectedFullNames.length > 0 && (
        <p className="mt-3 font-mono text-[10px] text-slate-400 dark:text-slate-600">{connectedFullNames.length} already connected.</p>
      )}
    </section>
  );
}

function RepoPicker({ repos, label, selected, onToggle, onSubmit, busy, empty }) {
  if (repos.length === 0) {
    return <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">{empty}</p>;
  }
  return (
    <div className="space-y-2">
      <div className="max-h-64 overflow-y-auto border border-slate-200 dark:border-slate-800">
        {repos.map((r) => (
          <label key={r.id} className="flex items-center gap-2 border-b border-slate-100 px-2 py-1.5 font-mono text-xs last:border-0 hover:bg-slate-50 dark:border-slate-800/60 dark:hover:bg-slate-900/40">
            <input type="checkbox" checked={selected.has(r.id)} onChange={() => onToggle(r.id)} className="accent-sky-500" />
            {label(r)}
          </label>
        ))}
      </div>
      <button onClick={onSubmit} disabled={busy || selected.size === 0} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">
        {busy ? '···' : `Add ${selected.size || ''} selected`.trim()}
      </button>
    </div>
  );
}
