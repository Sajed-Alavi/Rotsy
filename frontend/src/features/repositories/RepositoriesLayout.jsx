import { Outlet } from 'react-router';
import Tabs from '../../components/Tabs.jsx';
import { useAuth } from '../../context/AuthContext.jsx';

/**
 * Shell for /repositories: repositories, blobstores, and retention are all
 * facets of the same Nexus storage layer, so they live as tabs of one page
 * rather than three separate sidebar entries — same shape as ScanLayout.
 */
const ALL_TABS = [
  { to: '/repositories', label: 'Repositories', end: true, perm: 'repositories:read' },
  { to: '/repositories/blobstores', label: 'Blobstores', perm: 'blobstores:read' },
  { to: '/repositories/retention', label: 'Retention & Cleanup', perm: 'retention:read' },
];

export default function RepositoriesLayout() {
  const { hasPermission } = useAuth();
  const tabs = ALL_TABS.filter((t) => hasPermission(t.perm));

  return (
    <div className="p-6">
      <Tabs items={tabs} className="mb-6" />
      <Outlet />
    </div>
  );
}
