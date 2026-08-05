import { Link } from 'react-router';
import Icon from '../../../components/Icon.jsx';

/**
 * Reuses the existing Nexus repository browsing/storage views rather than
 * duplicating them — same reasoning as SecurityPage: no Project-to-artifact
 * correlation exists yet, so this links out instead of fabricating a
 * project-scoped artifact list.
 */
export default function ArtifactsPage() {
  return (
    <section className="border border-slate-200 dark:border-slate-800">
      <div className="p-4">
        <h2 className="font-mono text-[10px] uppercase tracking-wider text-slate-500">Nexus artifacts</h2>
        <p className="mt-1 font-mono text-[11px] text-slate-500 dark:text-slate-500">
          Repository and artifact storage. Not yet correlated to a specific project's commits or images
          — this opens the global repository views.
        </p>
      </div>
      <Link to="/repositories" className="group flex items-center justify-between border-t border-slate-100 px-4 py-3 text-slate-700 hover:bg-slate-50 dark:border-slate-800/60 dark:text-slate-300 dark:hover:bg-slate-900/60">
        <span className="font-mono text-xs">Open Repositories</span>
        <Icon name="chevron" size={13} className="text-slate-400 transition-transform group-hover:translate-x-0.5" />
      </Link>
      <Link to="/browse" className="group flex items-center justify-between border-t border-slate-100 px-4 py-3 text-slate-700 hover:bg-slate-50 dark:border-slate-800/60 dark:text-slate-300 dark:hover:bg-slate-900/60">
        <span className="font-mono text-xs">Browse Files</span>
        <Icon name="chevron" size={13} className="text-slate-400 transition-transform group-hover:translate-x-0.5" />
      </Link>
    </section>
  );
}
