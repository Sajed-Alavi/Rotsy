import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { api } from '../../lib/api.js';
import DataTable from '../../components/DataTable.jsx';
import Modal from '../../components/Modal.jsx';
import Icon from '../../components/Icon.jsx';
import { formatDateTime } from '../../lib/format.js';

const INPUT = 'w-full border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';

/**
 * Projects: the top-level product concept — a Project ties together a
 * source repository, its SonarQube analysis, and (eventually) its Nexus
 * artifacts under one page, instead of the user checking three tools
 * separately. This list is the entry point; ProjectLayout is the detail view.
 */
export default function ProjectsListPage() {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const navigate = useNavigate();

  const load = async () => {
    setLoading(true); setError('');
    try { setProjects(await api.get('/projects')); }
    catch (e) { setError(e.message); }
    setLoading(false);
  };
  useEffect(() => { load(); }, []);

  const columns = [
    { key: 'name', header: 'Name', mono: true, className: 'text-slate-800 dark:text-slate-200' },
    { key: 'id', header: 'ID', mono: true },
    { key: 'created_at', header: 'Created', mono: true, render: (v) => formatDateTime(v) },
  ];

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">Projects</h1>
          <p className="mt-0.5 font-mono text-[11px] text-slate-500 dark:text-slate-500">
            Source, analysis, artifacts, and insights — one page per project
          </p>
        </div>
        <button
          onClick={() => setCreating(true)}
          className="flex items-center gap-1.5 border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40"
        >
          <Icon name="plus" size={13} /> New Project
        </button>
      </div>

      {error && <div className="mb-3 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{error}</div>}

      <DataTable
        columns={columns}
        rows={projects}
        empty={loading ? 'loading…' : 'No projects yet — create one to connect a repository.'}
        onRowClick={(p) => navigate(`/projects/${p.id}`)}
      />

      <CreateProjectModal open={creating} onClose={() => setCreating(false)} onCreated={(p) => { setCreating(false); navigate(`/projects/${p.id}`); }} />
    </div>
  );
}

function CreateProjectModal({ open, onClose, onCreated }) {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const submit = async (e) => {
    e.preventDefault();
    setErr(''); setBusy(true);
    try {
      const project = await api.post('/projects', { name });
      setName('');
      onCreated(project);
    } catch (ex) { setErr(ex.message); }
    setBusy(false);
  };

  return (
    <Modal open={open} title="New Project" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <div>
          <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">Name</div>
          <input value={name} onChange={(e) => setName(e.target.value)} autoFocus className={INPUT} />
        </div>
        <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">
          Connect a GitHub repository and SonarQube from the project's Overview tab after creating it.
        </p>
        {err && <div className="font-mono text-xs text-rose-600 dark:text-rose-400">{err}</div>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="submit" disabled={busy || !name} className="border border-sky-300 bg-sky-50 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-sky-700 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/40">
            {busy ? '···' : 'Create'}
          </button>
        </div>
      </form>
    </Modal>
  );
}
