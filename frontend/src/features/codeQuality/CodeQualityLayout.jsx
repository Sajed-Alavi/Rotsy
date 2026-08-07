import { Outlet } from 'react-router';
import Tabs from '../../components/Tabs.jsx';

/**
 * Shell for the whole /code-quality section — same shape as
 * features/scan/ScanLayout.jsx: one header, one tab strip, one <Outlet/>.
 *
 * Global rather than Project-scoped on purpose: picking a repository to
 * analyze shouldn't require first finding which Project it happens to be
 * grouped under, any more than picking an image to vulnerability-scan does.
 */
const TABS = [
  { to: '/code-quality', label: 'Overview', end: true },
  { to: '/code-quality/runs', label: 'Analysis Runs' },
  { to: '/code-quality/findings', label: 'Findings' },
  { to: '/code-quality/settings', label: 'Settings' },
];

export default function CodeQualityLayout() {
  return (
    <div className="p-6">
      <div className="mb-4">
        <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">Code Quality</h1>
        <p className="mt-0.5 font-mono text-[11px] text-slate-500 dark:text-slate-500">
          SonarQube static analysis · pick a repository and branch, run on request
        </p>
      </div>

      <Tabs items={TABS} className="mb-6" />

      <Outlet />
    </div>
  );
}
