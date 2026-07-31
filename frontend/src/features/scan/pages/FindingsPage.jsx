import Section from '../../../components/Section.jsx';
import { scanApi } from '../api.js';
import VulnerabilityTable from '../components/VulnerabilityTable.jsx';

/**
 * Every CVE across every report — the bottom of the drill-down.
 *
 * The table owns its own severity filters, search and pagination. It used to be
 * the last block of a long page, so reaching it meant scrolling past three other
 * tables and then scrolling again inside a nested 96-row box.
 */
export default function FindingsPage() {
  return (
    <Section title="All findings" hint="across every scanned image" flush>
      <VulnerabilityTable endpoint={scanApi.findingsEndpoint.all} />
    </Section>
  );
}
