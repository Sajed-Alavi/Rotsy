/**
 * Sidebar navigation definition. Items are filtered by user permissions.
 *
 * Three item shapes:
 *   { section: 'Label' }                       a non-clickable group header
 *   { to, label, icon, perm?, end? }           a leaf link
 *   { to, label, icon, perm?, children: [] }   a parent that expands when the
 *                                              user is anywhere inside it
 *
 * Children are leaf links only — the sidebar renders one level of nesting, not
 * an arbitrary tree. A child inherits its parent's `perm` unless it sets its own.
 */
export const NAV = [
  { section: 'Overview' },
  { to: '/', label: 'Dashboard', icon: 'grid', end: true },
  { to: '/projects', label: 'Projects', icon: 'folder', perm: 'projects:read' },
  { to: '/browse', label: 'Browse Files', icon: 'folder', perm: 'repositories:read' },

  { section: 'Monitoring' },
  { to: '/monitoring', label: 'Monitoring', icon: 'chart', perm: 'metrics:read' },

  { section: 'Repositories' },
  { to: '/repositories', label: 'Repositories', icon: 'folder', perm: 'repositories:read' },

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
