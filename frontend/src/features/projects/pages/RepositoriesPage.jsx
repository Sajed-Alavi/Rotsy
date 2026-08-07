import { useEffect, useState } from 'react';
import { useOutletContext } from 'react-router';
import { api } from '../../../lib/api.js';
import Badge from '../../../components/Badge.jsx';
import DataTable from '../../../components/DataTable.jsx';
import Modal from '../../../components/Modal.jsx';

const INPUT = 'w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';

/**
 * Repositories: the "put 17, or 1000, repositories into this Project" view.
 * A Project is a grouping, not a 1:1 wrapper around a single repo — each row
 * here is independently connected and independently language-detected.
 * Deliberately read-only about analysis: connecting Sonar and running
 * analysis both happen from the global Code Quality section now (pick any
 * synced repo + branch from there), not per-Project — this page only shows
 * what's synced and lets you add more, matching Settings -> Integrations
 * (which manages the GitHub App / GitLab tokens themselves, not what's
 * attached to which Project).
 */
export default function RepositoriesPage() {
  const { projectId } = useOutletContext();
  const [repos, setRepos] = useState([]);
  const [err, setErr] = useState('');

  const load = async () => {
    setErr('');
    try { setRepos(await api.get(`/projects/${projectId}/repositories`)); }
    catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); }, [projectId]);

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
    { key: 'default_branch', header: 'Default branch', mono: true },
    {
      key: 'branches', header: 'Branches', className: 'text-right',
      render: (_v, row) => <BranchesButton repo={row} />,
    },
    {
      key: 'language', header: 'Sonar language',
      render: (v) => v ? <Badge tone="info">{v}</Badge> : <span className="font-mono text-[10px] text-slate-400">not analyzed yet</span>,
    },
    {
      key: 'auto_analyze_on_push', header: 'Auto-analyze',
      render: (v, row) => <AutoAnalyzeCell repo={row} enabled={v} onDone={load} />,
    },
  ];

  return (
    <div className="grid grid-cols-1 gap-6">
      {err && <div className="border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{err}</div>}

      <DataTable columns={columns} rows={repos} empty="No repositories connected yet — add some below." />
      <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">
        Run analysis from the Code Quality section — pick any repository connected here and a branch to analyze.
      </p>

      <AddRepositories projectId={projectId} connectedFullNames={repos.map((r) => r.full_name)} onDone={load} />
    </div>
  );
}

/** Lazy-fetched on open, not eagerly per row — a Project with many
 * repositories would otherwise fire one branch-listing call per row on
 * every page load, for data most visits never look at. */
function BranchesButton({ repo }) {
  const [open, setOpen] = useState(false);
  const [branches, setBranches] = useState(null);
  const [err, setErr] = useState('');

  const openModal = () => {
    setOpen(true);
    if (branches === null && !err) {
      api.get(`/modules/${repo.source_module}/repositories/${repo.repository_id}/branches`)
        .then((r) => setBranches(r.branches))
        .catch((e) => setErr(e.message));
    }
  };

  return (
    <>
      <button
        onClick={openModal}
        className="border border-slate-300 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
      >
        View
      </button>
      <Modal open={open} title={`Branches · ${repo.full_name}`} onClose={() => setOpen(false)}>
        {err ? (
          <p className="font-mono text-xs text-rose-600 dark:text-rose-400">{err}</p>
        ) : branches === null ? (
          <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">loading…</p>
        ) : (
          <ul className="max-h-64 space-y-1 overflow-y-auto font-mono text-xs text-slate-700 dark:text-slate-300">
            {branches.map((b) => (
              <li key={b} className="flex items-center justify-between border-b border-slate-100 py-1 last:border-0 dark:border-slate-800/60">
                {b}
                {b === repo.default_branch && <Badge tone="info">default</Badge>}
              </li>
            ))}
          </ul>
        )}
      </Modal>
    </>
  );
}

/**
 * Auto-analyze badge + (once a Sonar project exists) an edit control:
 * turn push-triggered analysis on/off, and pick which branches a push must
 * match to trigger it. Nothing to edit until a repository has been
 * analyzed at least once (from Code Quality) — there's no Sonar project to
 * attach the setting to yet, same reasoning as the Language column showing
 * "not analyzed yet" instead of an editable field.
 */
function AutoAnalyzeCell({ repo, enabled, onDone }) {
  const [open, setOpen] = useState(false);
  const [branches, setBranches] = useState(null);
  const [branchesErr, setBranchesErr] = useState('');
  const [autoEnabled, setAutoEnabled] = useState(repo.auto_analyze_enabled ?? true);
  const [watched, setWatched] = useState(new Set(repo.auto_analyze_branches || []));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const openModal = () => {
    setOpen(true);
    setAutoEnabled(repo.auto_analyze_enabled ?? true);
    setWatched(new Set(repo.auto_analyze_branches || []));
    setErr('');
    if (branches === null) {
      api.get(`/modules/${repo.source_module}/repositories/${repo.repository_id}/branches`)
        .then((r) => setBranches(r.branches))
        .catch((e) => setBranchesErr(e.message));
    }
  };

  const toggleBranch = (b) => {
    setWatched((cur) => {
      const next = new Set(cur);
      next.has(b) ? next.delete(b) : next.add(b);
      return next;
    });
  };

  const save = async () => {
    setBusy(true); setErr('');
    try {
      await api.patch(`/modules/sonar/projects/${repo.sonar_project_id}`, {
        auto_analyze_enabled: autoEnabled,
        auto_analyze_branches: Array.from(watched),
      });
      setOpen(false);
      onDone();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  if (!repo.sonar_project_id) {
    return <Badge tone="warn">manual only</Badge>;
  }

  return (
    <>
      <button
        onClick={openModal}
        className="border-0 bg-transparent p-0"
        title="Edit auto-analyze settings"
      >
        <Badge tone={enabled ? 'ok' : 'warn'}>{enabled ? 'on push' : 'disabled'}</Badge>
      </button>
      <Modal
        open={open}
        title={`Auto-analyze · ${repo.full_name}`}
        onClose={() => setOpen(false)}
        footer={(
          <>
            <button onClick={() => setOpen(false)} className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
              Cancel
            </button>
            <button onClick={save} disabled={busy} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300">
              {busy ? '···' : 'Save'}
            </button>
          </>
        )}
      >
        <div className="space-y-3">
          <label className="flex items-center gap-2 font-mono text-xs text-slate-700 dark:text-slate-300">
            <input type="checkbox" checked={autoEnabled} onChange={(e) => setAutoEnabled(e.target.checked)} className="accent-sky-500" />
            Analyze automatically on push
          </label>

          {autoEnabled && (
            <div>
              <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">
                Watched branches — none selected means "{repo.default_branch} only"
              </p>
              {branchesErr ? (
                <p className="font-mono text-xs text-rose-600 dark:text-rose-400">{branchesErr}</p>
              ) : branches === null ? (
                <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">loading branches…</p>
              ) : (
                <div className="max-h-48 space-y-1 overflow-y-auto border border-slate-200 p-2 dark:border-slate-800">
                  {branches.map((b) => (
                    <label key={b} className="flex items-center gap-2 font-mono text-xs text-slate-700 dark:text-slate-300">
                      <input type="checkbox" checked={watched.has(b)} onChange={() => toggleBranch(b)} className="accent-sky-500" />
                      {b}
                      {b === repo.default_branch && <Badge tone="info">default</Badge>}
                      {b !== repo.default_branch && <span className="font-mono text-[10px] text-amber-600 dark:text-amber-400">requires Developer Edition+</span>}
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}

          {err && <p className="font-mono text-xs text-rose-600 dark:text-rose-400">{err}</p>}
        </div>
      </Modal>
    </>
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
