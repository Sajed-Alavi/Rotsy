/**
 * Sidebar navigation definition. Items are filtered by user permissions.
 *
 * Three item shapes:
 *   { section: 'Label' }                       a non-clickable group header
 *   { to, label, icon, perm?, anyPerm?, end? }  a leaf link
 *   { to, label, icon, perm?, children: [] }   a parent that expands when the
 *                                              user is anywhere inside it
 *
 * `perm` requires that one key. `anyPerm` is for a link into a page whose own
 * tabs are individually gated on different permissions (Monitoring,
 * Repositories, Browse Files) — the link shows if the user holds *any* of
 * them, since a role scoped to just one of those tabs (e.g. `alerts:read`
 * alone) still needs a way to reach it, not just the tab that happens to be
 * first/default.
 *
 * Children are leaf links only — the sidebar renders one level of nesting, not
 * an arbitrary tree. A child inherits its parent's `perm` unless it sets its own.
 */
export const NAV = [
  { section: 'Overview' },
  { to: '/', label: 'Dashboard', icon: 'grid', end: true },
  { to: '/projects', label: 'Projects', icon: 'folder', perm: 'projects:read' },
  { to: '/browse', label: 'Browse Files', icon: 'folder', anyPerm: ['repositories:read', 'storage:read'] },

  { section: 'Monitoring' },
  { to: '/monitoring', label: 'Monitoring', icon: 'chart', anyPerm: ['metrics:read', 'jobs:read', 'alerts:read'] },

  { section: 'Repositories' },
  { to: '/repositories', label: 'Repositories', icon: 'folder', anyPerm: ['repositories:read', 'blobstores:read', 'retention:read'] },

  { section: 'Security' },
  { to: '/code-quality', label: 'Code Quality', icon: 'check', perm: 'projects:read' },
  { to: '/scan', label: 'Vulnerability Scanning', icon: 'bug', perm: 'scan:read' },
  { to: '/system', label: 'System & Scripts', icon: 'server', perm: 'system:read' },

  { section: 'Integrations' },
  { to: '/access', label: 'Access & Webhooks', icon: 'key', perm: 'access:read' },
  { to: '/tasks', label: 'Task Manager', icon: 'play', perm: 'tasks:control' },

  { section: 'Administration' },
  { to: '/audit', label: 'Audit Log', icon: 'file', perm: 'roles:manage' },
  { to: '/settings', label: 'Settings', icon: 'grid', perm: 'profile:edit' },

  { section: 'Help' },
  { to: '/docs', label: 'Documentation', icon: 'book' },
];
