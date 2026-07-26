/**
 * Display formatting helpers.
 *
 * Numbers are rendered in a monospaced, tabular form throughout the console
 * UI — this is the single biggest lever for a "data tooling" feel rather than
 * a generic SaaS look.
 */

export function formatBytes(bytes, decimals = 2) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / Math.pow(1024, i);
  return `${value.toFixed(i === 0 ? 0 : decimals)} ${units[i]}`;
}

export function formatNumber(n) {
  if (!Number.isFinite(n)) return '0';
  return n.toLocaleString('en-US');
}

export function percent(part, whole, decimals = 1) {
  if (!whole) return '—';
  return `${((part / whole) * 100).toFixed(decimals)}%`;
}

/** Compact ISO timestamp -> "2026-07-17 14:03". */
export function formatDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const pad = (x) => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** ISO/Date → "5m ago", "2h ago", "3d ago", or the date if older than a week. */
export function relativeTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const sec = Math.floor((Date.now() - d.getTime()) / 1000);
  if (sec < 60) return 'just now';
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  if (sec < 7 * 86400) return `${Math.floor(sec / 86400)}d ago`;
  return formatDateTime(iso);
}
