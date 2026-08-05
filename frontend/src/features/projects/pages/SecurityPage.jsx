import { Link } from 'react-router';
import Icon from '../../../components/Icon.jsx';

/**
 * Reuses the existing Trivy/Grype vulnerability scanning section rather than
 * duplicating it here. Rotsy does not yet track which Nexus repository/image
 * corresponds to this specific Project (that correlation — Project ->
 * Nexus artifact -> security scan — is a known gap, not implemented), so
 * this links to the global scanning views instead of pretending to filter
 * by project.
 */
export default function SecurityPage() {
  return (
    <section className="border border-slate-200 dark:border-slate-800">
      <div className="p-4">
        <h2 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Artifact security</h2>
        <p className="mt-1 font-mono text-[11px] text-slate-500 dark:text-slate-500">
          Trivy + Grype vulnerability scanning of container images. Not yet correlated to a specific
          project's commits or artifacts — this opens the global scanning views.
        </p>
      </div>
      <Link to="/scan" className="group flex items-center justify-between border-t border-slate-100 px-4 py-3 text-slate-700 hover:bg-slate-50 dark:border-slate-800/60 dark:text-slate-300 dark:hover:bg-slate-900/60">
        <span className="font-mono text-xs">Open Vulnerability Scanning</span>
        <Icon name="chevron" size={13} className="text-slate-400 transition-transform group-hover:translate-x-0.5" />
      </Link>
    </section>
  );
}
