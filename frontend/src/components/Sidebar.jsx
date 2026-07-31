import { NavLink, useLocation } from 'react-router';
import Icon from './Icon.jsx';
import { NAV } from '../lib/nav.js';
import { useAuth } from '../context/AuthContext.jsx';

/**
 * Dense sidebar, theme-aware. Active item gets a left edge bar rather than a
 * filled background. Items are filtered by the current user's permissions.
 *
 * Parents with `children` expand while the user is inside that subtree, so the
 * sub-pages of a section are visible exactly when they are relevant instead of
 * permanently inflating the nav.
 */
const LINK_BASE =
  'group relative flex items-center gap-2.5 py-1.5 text-slate-600 transition-colors hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100';

function ActiveBar() {
  return <span className="absolute left-0 top-0 h-full w-0.5 bg-sky-500" />;
}

export default function Sidebar() {
  const { hasPermission } = useAuth();
  const { pathname } = useLocation();

  /** True when the current URL is at or below `to` — drives auto-expansion. */
  const inSubtree = (to) => pathname === to || pathname.startsWith(`${to}/`);

  return (
    <aside className="flex h-full w-56 flex-col border-r border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950">
      <div className="flex h-12 items-center gap-2 border-b border-slate-200 dark:border-slate-800 px-4">
        <div className="h-2 w-2 rounded-full bg-emerald-400" />
        <span className="font-mono text-xs tracking-tight text-slate-600 dark:text-slate-300">sharpy</span>
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

          const expanded = item.children && inSubtree(item.to);

          return (
            <div key={item.to}>
              <NavLink
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `${LINK_BASE} px-4 ${isActive || expanded ? 'text-slate-900 dark:text-slate-100' : ''}`
                }
              >
                {({ isActive }) => (
                  <>
                    {isActive && <ActiveBar />}
                    <Icon name={item.icon} size={15} />
                    <span>{item.label}</span>
                  </>
                )}
              </NavLink>

              {expanded && (
                <div className="mb-1 border-l border-slate-200 pl-0 dark:border-slate-800 ml-[26px]">
                  {item.children
                    .filter((c) => !c.perm || hasPermission(c.perm))
                    .map((child) => (
                      <NavLink
                        key={child.to}
                        to={child.to}
                        end={child.end}
                        className={({ isActive }) =>
                          `${LINK_BASE} py-1 pl-3 pr-4 text-[13px] ${
                            isActive ? 'text-sky-700 dark:text-sky-300' : ''
                          }`
                        }
                      >
                        {({ isActive }) => (
                          <>
                            {isActive && <span className="absolute -left-px top-0 h-full w-0.5 bg-sky-500" />}
                            <span>{child.label}</span>
                          </>
                        )}
                      </NavLink>
                    ))}
                </div>
              )}
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
