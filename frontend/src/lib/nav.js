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
  { to: '/storage', label: 'Storage Analyzer', icon: 'hdd', perm: 'storage:read' },

  { section: 'Monitoring' },
  { to: '/metrics', label: 'Metrics', icon: 'chart', perm: 'metrics:read' },
  { to: '/jobs', label: 'Background Jobs', icon: 'server', perm: 'jobs:read' },
  { to: '/alerts', label: 'Alerts', icon: 'shield', perm: 'alerts:read' },

  { section: 'Repositories' },
  { to: '/repositories', label: 'Repositories', icon: 'folder', perm: 'repositories:read' },
  { to: '/blobstores', label: 'Blobstores', icon: 'database', perm: 'blobstores:read' },
  { to: '/retention', label: 'Retention & Cleanup', icon: 'trash', perm: 'retention:read' },

  { section: 'Security' },
  {
    to: '/code-quality',
    label: 'Code Quality',
    icon: 'check',
    perm: 'projects:read',
    children: [
      { to: '/code-quality', label: 'Overview', end: true },
      { to: '/code-quality/runs', label: 'Analysis Runs' },
      { to: '/code-quality/findings', label: 'Findings' },
      { to: '/code-quality/settings', label: 'Settings' },
    ],
  },
  {
    to: '/scan',
    label: 'Vulnerability Scanning',
    icon: 'bug',
    perm: 'scan:read',
    children: [
      { to: '/scan', label: 'Overview', end: true },
      { to: '/scan/targets', label: 'Scan Targets' },
      { to: '/scan/images', label: 'Images' },
      { to: '/scan/reports', label: 'Reports' },
      { to: '/scan/findings', label: 'Findings' },
      { to: '/scan/database', label: 'Database Management' },
    ],
  },
  { to: '/system', label: 'System & Scripts', icon: 'server', perm: 'system:read' },

  { section: 'Integrations' },
  {
    to: '/access',
    label: 'Access & Webhooks',
    icon: 'key',
    perm: 'access:read',
    children: [
      { to: '/access', label: 'API Tokens', end: true },
      { to: '/access/webhooks', label: 'Webhooks' },
      { to: '/access/anonymous', label: 'Anonymous Access' },
    ],
  },
  { to: '/tasks', label: 'Task Manager', icon: 'play', perm: 'tasks:control' },

  { section: 'Administration' },
  { to: '/users', label: 'Users', icon: 'users', perm: 'users:manage' },
  { to: '/roles', label: 'Roles & Permissions', icon: 'shield-check', perm: 'roles:manage' },
  { to: '/audit', label: 'Audit Log', icon: 'file', perm: 'roles:manage' },
  {
    to: '/settings',
    label: 'Settings',
    icon: 'grid',
    perm: 'profile:edit',
    children: [
      { to: '/settings', label: 'General', end: true },
      { to: '/settings/integrations', label: 'Integrations' },
      { to: '/settings/security', label: 'Security' },
      { to: '/settings/scanning', label: 'Scanning' },
      { to: '/settings/system', label: 'System' },
    ],
  },

  { section: 'Help' },
  { to: '/docs', label: 'Documentation', icon: 'book' },
];
