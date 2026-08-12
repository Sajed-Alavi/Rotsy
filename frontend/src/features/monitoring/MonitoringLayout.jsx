import { Outlet } from 'react-router';
import Tabs from '../../components/Tabs.jsx';
import { useAuth } from '../../context/AuthContext.jsx';

/**
 * Shell for /monitoring: Metrics, Background Jobs, and Alerts used to be
 * three separate sidebar entries under one "Monitoring" section header —
 * this collapses them into one page with tabs (same shape as ScanLayout),
 * so there is one real place to watch the system instead of three.
 */
const ALL_TABS = [
  { to: '/monitoring', label: 'Metrics', end: true, perm: 'metrics:read' },
  { to: '/monitoring/jobs', label: 'Background Jobs', perm: 'jobs:read' },
  { to: '/monitoring/alerts', label: 'Alerts', perm: 'alerts:read' },
];

export default function MonitoringLayout() {
  const { hasPermission } = useAuth();
  const tabs = ALL_TABS.filter((t) => hasPermission(t.perm));

  return (
    <div className="p-6">
      <Tabs items={tabs} className="mb-6" />
      <Outlet />
    </div>
  );
}
