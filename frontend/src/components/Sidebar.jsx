import { NavLink } from 'react-router-dom';
import Icon from './Icon.jsx';
import { NAV } from '../lib/nav.js';
import { useAuth } from '../context/AuthContext.jsx';

/**
 * Dense sidebar, theme-aware. Active item gets a left edge bar rather than a
 * filled background. Items are filtered by the current user's permissions.
 */
export default function Sidebar() {
  const { hasPermission } = useAuth();

  return (
    <aside className="flex h-full w-56 flex-col border-r border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950">
      <div className="flex h-12 items-center gap-2 border-b border-slate-200 dark:border-slate-800 px-4">
        <div className="h-2 w-2 rounded-full bg-emerald-400" />
        <span className="font-mono text-xs tracking-tight text-slate-600 dark:text-slate-300">nexus-console</span>
      </div>

      <nav className="flex-1 overflow-y-auto py-2 text-sm">
        {NAV.map((item, idx) => {
          if (item.section) {
            return (
              <div
                key={`s-${idx}`}
                className="px-4 pb-1 pt-4 font-mono text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-600"
              >
                {item.section}
              </div>
            );
          }
          if (item.perm && !hasPermission(item.perm)) return null;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                [
                  'group relative flex items-center gap-2.5 px-4 py-1.5 text-slate-600 transition-colors hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100',
                  isActive ? 'text-slate-900 dark:text-slate-100' : '',
                ].join(' ')
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && <span className="absolute left-0 top-0 h-full w-0.5 bg-sky-500" />}
                  <Icon name={item.icon} size={15} />
                  <span>{item.label}</span>
                </>
              )}
            </NavLink>
          );
        })}
      </nav>
    </aside>
  );
}
