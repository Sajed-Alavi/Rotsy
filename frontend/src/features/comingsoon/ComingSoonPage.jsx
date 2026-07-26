import EmptyState from '../../components/EmptyState.jsx';

/** Shared "scaffolded feature" page. Reduces B–H boilerplate to a one-liner. */
export default function ComingSoonPage(props) {
  return <EmptyState {...props} />;
}

// Pre-configured instances for each scaffolded feature.
export const RetentionPage = () => (
  <ComingSoonPage
    icon="trash"
    title="Retention & Cleanup"
    description="Custom cleanup rules beyond Nexus' rigid policies, with dry-run previews and safe execution."
    points={['keep last N tags per image', 'keep images from last N days', 'delete specific unused blobs', 'dry-run + estimated savings']}
  />
);
export const SystemPage = () => (
  <ComingSoonPage
    icon="server"
    title="System & Host Scripts"
    description="Check Nexus version and available updates, trigger whitelisted maintenance scripts."
    points={['nexus version + update-availability', 'trigger allow-listed host scripts', 'view run status and output']}
  />
);
export const AccessPage = () => (
  <ComingSoonPage
    icon="key"
    title="Access & Webhooks"
    description="Generate short-lived scoped tokens for CI/CD and configure Slack/Discord alerts."
    points={['temporary, expiring CI/CD tokens', 'token list + revocation', 'Slack / Discord webhooks', 'event-driven alert rules']}
  />
);
export const AnalyticsPage = () => (
  <ComingSoonPage
    icon="chart"
    title="Analytics & Tasks"
    description="Bandwidth per repository, top downloads, cache hit rates, and a live task manager."
    points={['bandwidth per repository', 'top-downloaded images / packages', 'proxy cache hit-rate', 'task manager: start / stop / live logs']}
  />
);
