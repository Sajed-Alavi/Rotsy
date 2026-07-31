import { Outlet } from 'react-router';
import Tabs from '../../components/Tabs.jsx';

/**
 * Access & Webhooks.
 *
 * This route used to render a "coming soon" placeholder listing four features,
 * backed by three endpoints that returned 501. Two of those features already
 * existed elsewhere (the inbound scan webhook under Settings, outbound alert
 * webhooks under Alerts); the section now gathers them and adds the parts that
 * genuinely did not exist — API tokens, and anonymous-access management for
 * repositories that already exist.
 */
const TABS = [
  { to: '/access', label: 'API Tokens', end: true },
  { to: '/access/webhooks', label: 'Webhooks' },
  { to: '/access/anonymous', label: 'Anonymous Access' },
];

export default function AccessLayout() {
  return (
    <div className="p-6">
      <div className="mb-4">
        <h1 className="text-base font-medium text-slate-900 dark:text-slate-100">Access &amp; Webhooks</h1>
        <p className="mt-0.5 font-mono text-[11px] text-slate-500 dark:text-slate-500">
          Credentials for automation, event delivery, and who can read without logging in
        </p>
      </div>

      <Tabs items={TABS} className="mb-6" />

      <Outlet />
    </div>
  );
}
