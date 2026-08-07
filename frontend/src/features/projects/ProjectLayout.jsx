import { useEffect, useState } from 'react';
import { Link, Outlet, useParams } from 'react-router';
import { api } from '../../lib/api.js';
import Tabs from '../../components/Tabs.jsx';
import Icon from '../../components/Icon.jsx';

/**
 * Shell for /projects/:id: one header, one tab strip, one <Outlet/> — same
 * shape as ScanLayout/SettingsLayout. Security and Artifacts reuse the
 * existing Trivy/Grype and Nexus pages rather than duplicating them (see
 * their page components) — this project doesn't yet track which Nexus
 * repository/image belongs to which Project, so those tabs link out to the
 * existing global views instead of pretending to filter by project.
 */
export default function ProjectLayout() {
  const { id } = useParams();
  const [project, setProject] = useState(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    api.get(`/projects/${id}`).then(setProject).catch((e) => setErr(e.message));
  }, [id]);

  const TABS = [
    { to: `/projects/${id}`, label: 'Overview', end: true },
    { to: `/projects/${id}/repositories`, label: 'Repositories' },
    { to: `/projects/${id}/security`, label: 'Security' },
    { to: `/projects/${id}/artifacts`, label: 'Artifacts' },
    { to: `/projects/${id}/insights`, label: 'Insights' },
  ];

  return (
    <div className="p-6">
      <div className="mb-4">
        <Link to="/projects" className="mb-1 flex items-center gap-1 font-mono text-[11px] text-slate-400 hover:text-slate-600 dark:text-slate-600 dark:hover:text-slate-400">
          <Icon name="chevron" size={11} className="rotate-180" /> Projects
        </Link>
        <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">{project?.name || `Project ${id}`}</h1>
        {err && <div className="mt-1 font-mono text-xs text-rose-600 dark:text-rose-400">{err}</div>}
      </div>

      <Tabs items={TABS} className="mb-6" />

      <Outlet context={{ projectId: Number(id) }} />
    </div>
  );
}
