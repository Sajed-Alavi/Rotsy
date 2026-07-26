import Icon from './Icon.jsx';

/**
 * Compact status tile for a single Nexus health probe.
 * Color is driven by BOTH health status AND category:
 *  - unhealthy + critical  → red (action needed)
 *  - unhealthy + security  → amber (advisory, usually Nexus defaults)
 *  - unhealthy + info      → amber (informational)
 *  - healthy (any)         → green
 *
 * @param {object} props
 * @param {string} props.name
 * @param {boolean} props.healthy
 * @param {string} [props.message]
 * @param {('critical'|'security'|'info')} [props.category='info']
 */
export default function HealthTile({ name, healthy, message, category = 'info' }) {
  let tone;
  if (healthy) {
    tone = 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-400';
  } else if (category === 'critical') {
    tone = 'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400';
  } else {
    // security + info advisories: amber, not red (these are Nexus's own
    // recommendations like "change default admin password", not wrapper errors).
    tone = 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-400';
  }

  return (
    <div className={`flex items-start gap-2 border p-2 ${tone}`} title={message || ''}>
      <span className="mt-0.5">
        {healthy ? <Icon name="check" size={14} /> : <Icon name="alert" size={14} />}
      </span>
      <div className="min-w-0">
        <div className="truncate font-mono text-[11px] font-medium">{name}</div>
        {message && !healthy && (
          <div className="mt-0.5 line-clamp-2 font-mono text-[10px] opacity-80">{message}</div>
        )}
      </div>
    </div>
  );
}
