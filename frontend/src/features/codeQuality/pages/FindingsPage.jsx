import Section from '../../../components/Section.jsx';
import SonarFindingsTable from '../components/SonarFindingsTable.jsx';

/**
 * Every open issue, across every repository's latest successful analysis —
 * the bottom of the drill-down, same role as scan/pages/FindingsPage.jsx.
 */
export default function FindingsPage() {
  return (
    <Section title="All findings" hint="latest analysis per repository" flush>
      <SonarFindingsTable />
    </Section>
  );
}
