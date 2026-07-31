import { NavLink } from 'react-router';

/**
 * Segmented control, in two flavours.
 *
 * The visual treatment is lifted verbatim from the View switcher that was
 * written inline in BrowsePage — butted-together buttons, sky accent for the
 * active one, `-ml-px` to collapse the shared border. It lives here now so the
 * scan section and BrowsePage cannot drift apart.
 *
 *   <Tabs items={[{ key, label }]} value={k} onChange={fn} />   local state
 *   <Tabs items={[{ to, label, end }]} />                        routed
 *
 * Routed mode renders NavLinks, so the active tab follows the URL and each tab
 * is a real, linkable location.
 */
const ACTIVE = 'border-sky-300 bg-sky-50 text-sky-700 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300';
const IDLE = 'border-slate-300 text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800';
const BASE = 'border px-3 py-1.5 font-mono text-xs uppercase tracking-wider transition-colors';

export default function Tabs({ items, value, onChange, className = '' }) {
  return (
    <div className={`flex flex-wrap ${className}`}>
      {items.map((item, i) => {
        const overlap = i > 0 ? '-ml-px' : '';

        if (item.to !== undefined) {
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => `${BASE} ${isActive ? ACTIVE : IDLE} ${overlap}`}
            >
              {item.label}
              {item.badge != null && (
                <span className="ml-1.5 normal-case tabular-nums opacity-60">{item.badge}</span>
              )}
            </NavLink>
          );
        }

        return (
          <button
            key={item.key}
            type="button"
            onClick={() => onChange(item.key)}
            className={`${BASE} ${value === item.key ? ACTIVE : IDLE} ${overlap}`}
          >
            {item.label}
            {item.badge != null && (
              <span className="ml-1.5 normal-case tabular-nums opacity-60">{item.badge}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
