/** Sidebar navigation definition. Items filtered by user permissions. */
export const NAV = [
  { section: 'Overview' },
  { to: '/', label: 'Dashboard', icon: 'grid', end: true },
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
  { to: '/scan', label: 'Vulnerability Scan', icon: 'bug', perm: 'scan:read' },
  { to: '/system', label: 'System & Scripts', icon: 'server', perm: 'system:read' },

  { section: 'Integrations' },
  { to: '/access', label: 'Access & Webhooks', icon: 'key', perm: 'access:read' },
  { to: '/analytics', label: 'Analytics', icon: 'chart', perm: 'analytics:read' },

  { section: 'Administration' },
  { to: '/users', label: 'Users', icon: 'users', perm: 'users:manage' },
  { to: '/roles', label: 'Roles & Permissions', icon: 'shield-check', perm: 'roles:manage' },
  { to: '/settings', label: 'Settings', icon: 'grid', perm: 'profile:edit' },
];
