import { useEffect } from 'react';
import Icon from './Icon.jsx';

/** Lightweight modal, theme-aware: overlay + centered panel + close. */
export default function Modal({ open, title, onClose, children, footer, wide }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => e.key === 'Escape' && onClose?.();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 pt-16 dark:bg-black/60"
      // A real <button> can't be used here (S6819's suggested fix): it would
      // wrap the modal panel, and HTML forbids interactive elements —
      // buttons, inputs, links, all of which the panel's children/footer
      // contain — as descendants of a <button>. role="button" + tabIndex +
      // a keydown handler is the standard accessible pattern for a
      // click-to-dismiss backdrop that must wrap interactive content.
      role="button" // NOSONAR
      tabIndex={-1}
      aria-label="Close modal"
      // Closes only when the backdrop itself is the click target, not when a
      // click inside the panel bubbles up — so the panel below needs no
      // click handler of its own (nothing to stop from propagating).
      onClick={(e) => { if (e.target === e.currentTarget) onClose?.(); }}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onClose?.(); }}
    >
      <div
        className={`w-full ${wide ? 'max-w-2xl' : 'max-w-md'} border border-slate-300 bg-white shadow-xl dark:border-slate-700 dark:bg-slate-900`}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-800">
          <h2 className="text-sm font-medium text-slate-900 dark:text-slate-100">{title}</h2>
          <button
            onClick={onClose}
            className="text-slate-400 transition-colors hover:text-slate-700 dark:text-slate-500 dark:hover:text-slate-200"
            aria-label="Close"
          >
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="px-4 py-4">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-slate-200 px-4 py-3 dark:border-slate-800">{footer}</div>}
      </div>
    </div>
  );
}
