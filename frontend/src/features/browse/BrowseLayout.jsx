import { Outlet } from 'react-router';
import Tabs from '../../components/Tabs.jsx';
import { useAuth } from '../../context/AuthContext.jsx';

/**
 * Shell for /browse: repository browsing and deep storage analysis are two
 * views over the same repositories, so they live as tabs of one page rather
 * than two separate sidebar entries — same shape as ScanLayout.
 */
const ALL_TABS = [
  { to: '/browse', label: 'Browse', end: true, perm: 'repositories:read' },
  { to: '/browse/storage', label: 'Storage Analyzer', perm: 'storage:read' },
];

export default function BrowseLayout() {
  const { hasPermission } = useAuth();
  const tabs = ALL_TABS.filter((t) => hasPermission(t.perm));

  return (
    <div className="p-6">
      <Tabs items={tabs} className="mb-6" />
      <Outlet />
    </div>
  );
}
