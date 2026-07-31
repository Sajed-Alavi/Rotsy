import { Outlet } from 'react-router';
import Tabs from '../../components/Tabs.jsx';

/**
 * Shell for the whole /scan section: one header, one tab strip, one <Outlet/>.
 *
 * The section used to be a single page stacking nine unrelated blocks in one
 * scroll — DB status, summary tiles, ledger prose, then three tables of
 * increasing granularity (image → report → CVE) presented as siblings. Each of
 * those is now its own route, so the tables are a drill-down rather than a pile,
 * every view is linkable, and no page nests a scroll region inside another.
 *
 * The tab strip duplicates the sidebar children on purpose: the sidebar is for
 * arriving, the tabs are for moving sideways once you are here.
 */
const TABS = [
  { to: '/scan', label: 'Overview', end: true },
  { to: '/scan/targets', label: 'Targets' },
  { to: '/scan/images', label: 'Images' },
  { to: '/scan/reports', label: 'Reports' },
  { to: '/scan/findings', label: 'Findings' },
  { to: '/scan/database', label: 'Database' },
];

export default function ScanLayout() {
  return (
    <div className="p-6">
      <div className="mb-4">
        <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">Vulnerability Scanning</h1>
        <p className="mt-0.5 font-mono text-[11px] text-slate-500 dark:text-slate-500">
          Trivy + Grype · static registry analysis · scans on push or on request only
        </p>
      </div>

      <Tabs items={TABS} className="mb-6" />

      <Outlet />
    </div>
  );
}
