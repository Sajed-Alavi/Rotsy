import { Outlet } from 'react-router';
import Tabs from '../../components/Tabs.jsx';

/**
 * Shell for /settings: one header, one tab strip, one <Outlet/> — same shape
 * as ScanLayout. Settings used to be a single page stacking Nexus connection,
 * webhook config, scanner proxy, profile, and password as siblings in one
 * scroll; each concern is now its own route so the page doesn't grow linearly
 * with every integration Rotsy adds (GitHub, SonarQube, ...).
 */
const TABS = [
  { to: '/settings', label: 'General', end: true },
  { to: '/settings/integrations', label: 'Integrations' },
  { to: '/settings/security', label: 'Security' },
  { to: '/settings/scanning', label: 'Scanning' },
  { to: '/settings/system', label: 'System' },
];

export default function SettingsLayout() {
  return (
    <div className="p-6">
      <div className="mb-4">
        <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">Settings</h1>
        <p className="mt-0.5 font-mono text-[11px] text-slate-500 dark:text-slate-500">
          Instance configuration, connected integrations, and your account
        </p>
      </div>

      <Tabs items={TABS} className="mb-6" />

      <div className="mx-auto max-w-3xl">
        <Outlet />
      </div>
    </div>
  );
}
