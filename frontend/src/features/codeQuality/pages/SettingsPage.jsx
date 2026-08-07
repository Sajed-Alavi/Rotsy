import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { api } from '../../../lib/api.js';
import Badge from '../../../components/Badge.jsx';
import Section from '../../../components/Section.jsx';
import { relativeTime } from '../../../lib/format.js';

/**
 * SonarQube connection health + version check for the Code Quality section.
 * Credentials (server URL / token) stay in Settings -> Integrations ->
 * SonarQube — that's where every other integration's credentials live, and
 * duplicating that form here would just be two places to keep in sync. This
 * page is the "how is it doing, is it up to date" view, which belongs where
 * Sonar is actually used day to day rather than tucked behind a collapsed
 * card's Configure disclosure.
 */
export default function SettingsPage() {
  const [status, setStatus] = useState(null);
  const [err, setErr] = useState('');
  const [checking, setChecking] = useState(false);
  const [updateInfo, setUpdateInfo] = useState(null);
  const [updateErr, setUpdateErr] = useState('');

  const load = async () => {
    setErr('');
    try { setStatus(await api.get('/modules/sonar/status')); }
    catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); }, []);

  const checkForUpdates = async () => {
    setChecking(true); setUpdateErr(''); setUpdateInfo(null);
    try { setUpdateInfo(await api.post('/modules/sonar/check-updates', {})); }
    catch (e) { setUpdateErr(e.message); }
    setChecking(false);
  };

  if (err) {
    return <div className="border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{err}</div>;
  }
  if (status === null) {
    return <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">loading…</p>;
  }

  if (!status.configured) {
    return (
      <p className="font-mono text-[11px] text-slate-500 dark:text-slate-500">
        SonarQube isn't configured yet — set the server URL and token in{' '}
        <Link to="/settings/integrations" className="underline">Settings → Integrations → SonarQube</Link>.
      </p>
    );
  }

  const healthTone = status.reachable ? 'ok' : 'bad';

  return (
    <div className="max-w-xl space-y-6">
      <Section title="Connection">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 font-mono text-xs">
          <Field label="Server" value={status.server_url} />
          <Field label="Version" value={status.version || '—'} />
          <Field label="Health" value={<Badge tone={healthTone}>{status.reachable ? 'Healthy' : 'Unreachable'}</Badge>} />
          <Field label="Last successful check" value={status.last_success_at ? relativeTime(status.last_success_at) : '—'} />
        </dl>
        {status.error && (
          <div className="mt-3 border border-rose-200 bg-rose-50 px-3 py-2 font-mono text-xs text-rose-600 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-400">{status.error}</div>
        )}
        <p className="mt-3 font-mono text-[11px] text-slate-500 dark:text-slate-500">
          To change the server URL or token, go to{' '}
          <Link to="/settings/integrations" className="underline">Settings → Integrations → SonarQube</Link>.
        </p>
      </Section>

      <Section title="Version check">
        <button
          onClick={checkForUpdates}
          disabled={checking}
          className="border border-slate-300 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-slate-600 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          {checking ? '···' : 'Check for Updates'}
        </button>
        {updateInfo && (
          <p className="mt-2 font-mono text-[11px] text-slate-600 dark:text-slate-400">
            {updateInfo.update_available
              ? `Update available: ${updateInfo.current_version} → ${updateInfo.latest_version}. Upgrading SonarQube itself is a deployment change Rotsy doesn't perform automatically — see SonarQube's own upgrade guide.`
              : `SonarQube ${updateInfo.current_version} is up to date.`}
          </p>
        )}
        {updateErr && <p className="mt-2 font-mono text-[11px] text-rose-600 dark:text-rose-400">{updateErr}</p>}
      </Section>
    </div>
  );
}

function Field({ label, value }) {
  return (
    <div>
      <dt className="font-mono text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-600">{label}</dt>
      <dd className="mt-0.5 text-slate-800 dark:text-slate-200">{value}</dd>
    </div>
  );
}
