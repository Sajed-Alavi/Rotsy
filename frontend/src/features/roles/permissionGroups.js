/**
 * Grouping for the permission picker.
 *
 * Derived from the `resource:action` key rather than hard-coded, so a new
 * permission added to the backend catalog appears in the right group without a
 * UI change. An unrecognised prefix falls back to a title-cased version of
 * itself rather than being hidden.
 */
const GROUP_LABELS = {
  repositories: 'Repositories',
  storage: 'Storage analyzer',
  retention: 'Retention & cleanup',
  blobstores: 'Blob stores',
  scan: 'Vulnerability scanning',
  metrics: 'Monitoring',
  jobs: 'Background jobs',
  alerts: 'Alerts',
  tasks: 'Nexus tasks',
  access: 'Access & webhooks',
  system: 'System & settings',
  users: 'Administration',
  roles: 'Administration',
  profile: 'Profile',
};

/** Display order; anything unlisted sorts to the end, alphabetically. */
const GROUP_ORDER = [
  'Repositories', 'Storage analyzer', 'Retention & cleanup', 'Blob stores',
  'Vulnerability scanning', 'Monitoring', 'Background jobs', 'Alerts',
  'Nexus tasks', 'Access & webhooks', 'System & settings', 'Administration', 'Profile',
];

function labelFor(key) {
  const prefix = key.split(':')[0];
  return GROUP_LABELS[prefix] || prefix.charAt(0).toUpperCase() + prefix.slice(1);
}

/** `[{ label, items: [permission] }]`, ordered for display. */
export function groupPermissions(permissions) {
  const byLabel = new Map();
  for (const permission of permissions) {
    const label = labelFor(permission.key);
    if (!byLabel.has(label)) byLabel.set(label, []);
    byLabel.get(label).push(permission);
  }
  return [...byLabel.entries()]
    .map(([label, items]) => ({ label, items }))
    .sort((a, b) => {
      const ai = GROUP_ORDER.indexOf(a.label);
      const bi = GROUP_ORDER.indexOf(b.label);
      if (ai === -1 && bi === -1) return a.label.localeCompare(b.label);
      if (ai === -1) return 1;
      if (bi === -1) return -1;
      return ai - bi;
    });
}
