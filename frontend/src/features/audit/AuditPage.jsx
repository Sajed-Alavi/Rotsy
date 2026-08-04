import { useState } from 'react';
import { api } from '../../lib/api.js';
import Badge from '../../components/Badge.jsx';
import DataTable from '../../components/DataTable.jsx';
import Section from '../../components/Section.jsx';
import Tabs from '../../components/Tabs.jsx';
import { formatDateTime } from '../../lib/format.js';
import { useResource } from '../../lib/useResource.js';

const ACTION_TONE = { create: 'ok', update: 'info', delete: 'bad', grant: 'warn', revoke: 'warn' };

const FILTERS = [
  { key: '', label: 'All' },
  { key: 'repository', label: 'Repositories' },
  { key: 'scan_target', label: 'Scan targets' },
  { key: 'role', label: 'Roles' },
  { key: 'user', label: 'Users' },
];

/**
 * The security audit trail.
 *
 * `GET /api/audit` was fully implemented and had no UI at all — the trail
 * existed but could only be read by querying the API by hand, which rather
 * defeats the point of keeping one.
 */
export default function AuditPage() {
  const [resourceType, setResourceType] = useState('');
  const { data: entries, loading } = useResource(
    () => api.get(`/audit?limit=200${resourceType ? `&resource_type=${encodeURIComponent(resourceType)}` : ''}`),
    [],
    [resourceType],
  );

  const columns = [
    { key: 'created_at', header: 'When', render: (v) => <span className="font-mono text-xs text-slate-500 dark:text-slate-400">{formatDateTime(v)}</span> },
    { key: 'username', header: 'Actor', render: (v, row) => <span className="font-mono text-xs text-slate-800 dark:text-slate-200">{v || row.user_id || 'system'}</span> },
    { key: 'action', header: 'Action', render: (v) => <Badge tone={ACTION_TONE[String(v).toLowerCase()] || 'neutral'}>{v}</Badge> },
    { key: 'resource_type', header: 'Resource', render: (v) => <span className="font-mono text-xs text-slate-600 dark:text-slate-400">{v}</span> },
    { key: 'resource_id', header: 'Target', render: (v) => <span className="font-mono text-xs text-slate-700 dark:text-slate-300">{v || '—'}</span> },
    {
      key: 'detail',
      header: 'Detail',
      render: (v) => (v
        ? <span className="font-mono text-[10px] text-slate-500 dark:text-slate-400">{typeof v === 'string' ? v : JSON.stringify(v)}</span>
        : <span className="text-slate-400 dark:text-slate-600">—</span>),
    },
  ];

  return (
    <div className="p-6">
      <div className="mb-4">
        <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">Audit Log</h1>
        <p className="mt-0.5 font-mono text-[11px] text-slate-500 dark:text-slate-500">Who changed what, and when</p>
      </div>

      <Section
        title="Recent activity"
        hint={loading ? 'loading…' : `${(entries || []).length} entries`}
        flush
        actions={<Tabs items={FILTERS} value={resourceType} onChange={setResourceType} />}
      >
        <DataTable
          columns={columns}
          rows={entries || []}
          empty={loading ? 'loading…' : 'no audit entries recorded'}
        />
      </Section>
    </div>
  );
}
