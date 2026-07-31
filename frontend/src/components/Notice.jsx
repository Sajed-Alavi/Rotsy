import Icon from './Icon.jsx';

/**
 * Inline status strip for the result of an action.
 *
 * Replaces the bare `{msg && <div …>}` that was copy-pasted per page, and adds
 * the thing that pattern lacked: a tone. A failure and a success previously
 * rendered identically, so "clear failed: …" looked exactly like "All reports
 * cleared." Dismissable, because terminal states should persist until the user
 * has actually read them rather than being overwritten by the next action.
 */
const TONES = {
  info: 'border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-800 dark:bg-slate-900/40 dark:text-slate-400',
  ok: 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-400',
  warn: 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-400',
  bad: 'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400',
};

export default function Notice({ status, onDismiss, className = '' }) {
  if (!status) return null;
  const { text, tone = 'info' } = status;

  return (
    <div className={`mb-3 flex items-start justify-between gap-3 border px-3 py-2 font-mono text-xs ${TONES[tone] || TONES.info} ${className}`}>
      <span className="whitespace-pre-wrap">{text}</span>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="shrink-0 opacity-50 transition-opacity hover:opacity-100"
        >
          <Icon name="x" size={13} />
        </button>
      )}
    </div>
  );
}
