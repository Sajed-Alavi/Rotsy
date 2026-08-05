import { Link } from 'react-router';
import { useAuth } from '../../../context/AuthContext.jsx';
import Icon from '../../../components/Icon.jsx';

/**
 * System links out to the existing System & Scripts page rather than
 * duplicating its health checks, versions, and maintenance actions here —
 * that page already owns this information; this tab is the entry point for
 * it from Settings, not a second copy.
 */
export default function SystemStatusPage() {
  const { hasPermission } = useAuth();

  if (!hasPermission('system:read')) {
    return (
      <section className="border border-slate-200 p-4 dark:border-slate-800">
        <p className="font-mono text-xs text-slate-500 dark:text-slate-500">System status requires the system:read permission.</p>
      </section>
    );
  }

  return (
    <section className="border border-slate-200 dark:border-slate-800">
      <div className="p-4">
        <h2 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">System status</h2>
        <p className="mt-1 font-mono text-[11px] text-slate-500 dark:text-slate-500">
          Nexus health, background job workers, versions, and service connectivity.
        </p>
      </div>
      <Link
        to="/system"
        className="group flex items-center justify-between border-t border-slate-100 px-4 py-3 text-slate-700 hover:bg-slate-50 dark:border-slate-800/60 dark:text-slate-300 dark:hover:bg-slate-900/60"
      >
        <span className="font-mono text-xs">Open System & Scripts</span>
        <Icon name="chevron" size={13} className="text-slate-400 transition-transform group-hover:translate-x-0.5" />
      </Link>
      <Link
        to="/jobs"
        className="group flex items-center justify-between border-t border-slate-100 px-4 py-3 text-slate-700 hover:bg-slate-50 dark:border-slate-800/60 dark:text-slate-300 dark:hover:bg-slate-900/60"
      >
        <span className="font-mono text-xs">Background Jobs</span>
        <Icon name="chevron" size={13} className="text-slate-400 transition-transform group-hover:translate-x-0.5" />
      </Link>
    </section>
  );
}
