import { lazy, Suspense } from 'react';
import { Routes, Route, Navigate } from 'react-router';
import AppShell from './components/AppShell.jsx';
import ProtectedRoute from './components/ProtectedRoute.jsx';
import LoginPage from './features/auth/LoginPage.jsx';
import DashboardPage from './features/dashboard/DashboardPage.jsx';
import BrowseLayout from './features/browse/BrowseLayout.jsx';
import BrowsePage from './features/browse/BrowsePage.jsx';
import StorageAnalyzerPage from './features/storage/StorageAnalyzerPage.jsx';
import MonitoringLayout from './features/monitoring/MonitoringLayout.jsx';
import MetricsPage from './features/metrics/MetricsPage.jsx';
import JobsPage from './features/jobs/JobsPage.jsx';
import AlertsPage from './features/alerts/AlertsPage.jsx';
import ProjectsListPage from './features/projects/ProjectsListPage.jsx';
import ProjectLayout from './features/projects/ProjectLayout.jsx';
import ProjectOverviewPage from './features/projects/pages/OverviewPage.jsx';
import ProjectRepositoriesPage from './features/projects/pages/RepositoriesPage.jsx';
import ProjectInsightsPage from './features/projects/pages/InsightsPage.jsx';
import ProjectSettingsPage from './features/projects/pages/SettingsPage.jsx';
import SettingsLayout from './features/settings/SettingsLayout.jsx';
import SettingsGeneralPage from './features/settings/pages/GeneralPage.jsx';
import SettingsIntegrationsPage from './features/settings/pages/IntegrationsPage.jsx';
import SettingsSecurityPage from './features/settings/pages/SecurityPage.jsx';
import SettingsScanningPage from './features/settings/pages/ScanningPage.jsx';
import SettingsSystemPage from './features/settings/pages/SystemStatusPage.jsx';
import UsersPage from './features/users/UsersPage.jsx';
import RolesPage from './features/roles/RolesPage.jsx';
import AuditPage from './features/audit/AuditPage.jsx';
import RetentionPage from './features/retention/RetentionPage.jsx';
import SystemPage from './features/system/SystemPage.jsx';
import BlobstoresPage from './features/blobstores/BlobstoresPage.jsx';
import RepositoriesLayout from './features/repositories/RepositoriesLayout.jsx';
import RepositoriesPage from './features/repositories/RepositoriesPage.jsx';
import TasksPage from './features/tasks/TasksPage.jsx';

import CodeQualityLayout from './features/codeQuality/CodeQualityLayout.jsx';
import CodeQualityOverviewPage from './features/codeQuality/pages/OverviewPage.jsx';
import CodeQualityRunsPage from './features/codeQuality/pages/RunsPage.jsx';
import CodeQualityFindingsPage from './features/codeQuality/pages/FindingsPage.jsx';
import CodeQualitySettingsPage from './features/codeQuality/pages/SettingsPage.jsx';

import ScanLayout from './features/scan/ScanLayout.jsx';
import ScanOverviewPage from './features/scan/pages/OverviewPage.jsx';
import ScanTargetsPage from './features/scan/pages/TargetsPage.jsx';
import ScanImagesPage from './features/scan/pages/ImagesPage.jsx';
import ScanReportsPage from './features/scan/pages/ReportsPage.jsx';
import ScanFindingsPage from './features/scan/pages/FindingsPage.jsx';
import ScanDatabasePage from './features/scan/pages/DatabasePage.jsx';

import AccessLayout from './features/access/AccessLayout.jsx';
import TokensPage from './features/access/pages/TokensPage.jsx';
import WebhooksPage from './features/access/pages/WebhooksPage.jsx';
import AnonymousPage from './features/access/pages/AnonymousPage.jsx';

// The docs bundle every Markdown page inline. That is ~35 files nobody needs
// on the dashboard, so the whole section is a separate chunk fetched on first
// visit to /docs rather than part of the initial load.
const DocsLayout = lazy(() => import('./features/docs/DocsLayout.jsx'));
const DocPage = lazy(() => import('./features/docs/DocPage.jsx'));

function DocsFallback() {
  return <div className="p-8 font-mono text-xs text-slate-400 dark:text-slate-600">loading documentation…</div>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<AppShell />}>
          <Route index element={<DashboardPage />} />
          <Route path="browse" element={<BrowseLayout />}>
            <Route index element={<BrowsePage />} />
            <Route path="storage" element={<StorageAnalyzerPage />} />
          </Route>
          <Route path="storage" element={<Navigate to="/browse/storage" replace />} />
          <Route path="monitoring" element={<MonitoringLayout />}>
            <Route index element={<MetricsPage />} />
            <Route path="jobs" element={<JobsPage />} />
            <Route path="alerts" element={<AlertsPage />} />
          </Route>
          <Route path="metrics" element={<Navigate to="/monitoring" replace />} />
          <Route path="jobs" element={<Navigate to="/monitoring/jobs" replace />} />
          <Route path="alerts" element={<Navigate to="/monitoring/alerts" replace />} />
          <Route path="projects" element={<ProjectsListPage />} />
          <Route path="projects/:id" element={<ProjectLayout />}>
            <Route index element={<ProjectOverviewPage />} />
            <Route path="repositories" element={<ProjectRepositoriesPage />} />
            <Route path="insights" element={<ProjectInsightsPage />} />
            <Route path="settings" element={<ProjectSettingsPage />} />
          </Route>
          <Route path="settings" element={<SettingsLayout />}>
            <Route index element={<SettingsGeneralPage />} />
            <Route path="integrations" element={<SettingsIntegrationsPage />} />
            <Route path="security" element={<SettingsSecurityPage />} />
            <Route path="scanning" element={<SettingsScanningPage />} />
            <Route path="system" element={<SettingsSystemPage />} />
            <Route path="users" element={<UsersPage />} />
            <Route path="roles" element={<RolesPage />} />
          </Route>
          {/* Old top-level locations, kept as redirects for bookmarks/links. */}
          <Route path="users" element={<Navigate to="/settings/users" replace />} />
          <Route path="roles" element={<Navigate to="/settings/roles" replace />} />
          <Route path="repositories" element={<RepositoriesLayout />}>
            <Route index element={<RepositoriesPage />} />
            <Route path="blobstores" element={<BlobstoresPage />} />
            <Route path="retention" element={<RetentionPage />} />
          </Route>
          <Route path="blobstores" element={<Navigate to="/repositories/blobstores" replace />} />
          <Route path="retention" element={<Navigate to="/repositories/retention" replace />} />
          <Route path="system" element={<SystemPage />} />
          <Route path="tasks" element={<TasksPage />} />
          <Route path="audit" element={<AuditPage />} />

          {/* Code quality: global, not Project-scoped — mirrors the vulnerability
              scanning section below (pick a repo/branch, run, browse results). */}
          <Route path="code-quality" element={<CodeQualityLayout />}>
            <Route index element={<CodeQualityOverviewPage />} />
            <Route path="runs" element={<CodeQualityRunsPage />} />
            <Route path="findings" element={<CodeQualityFindingsPage />} />
            <Route path="settings" element={<CodeQualitySettingsPage />} />
          </Route>

          {/* Vulnerability scanning: one section, six views, each linkable. */}
          <Route path="scan" element={<ScanLayout />}>
            <Route index element={<ScanOverviewPage />} />
            <Route path="targets" element={<ScanTargetsPage />} />
            <Route path="images" element={<ScanImagesPage />} />
            <Route path="reports" element={<ScanReportsPage />} />
            <Route path="findings" element={<ScanFindingsPage />} />
            <Route path="database" element={<ScanDatabasePage />} />
          </Route>

          <Route path="access" element={<AccessLayout />}>
            <Route index element={<TokensPage />} />
            <Route path="webhooks" element={<WebhooksPage />} />
            <Route path="anonymous" element={<AnonymousPage />} />
          </Route>

          <Route path="docs" element={<Suspense fallback={<DocsFallback />}><DocsLayout /></Suspense>}>
            <Route index element={<Suspense fallback={<DocsFallback />}><DocPage /></Suspense>} />
            <Route path=":slug" element={<Suspense fallback={<DocsFallback />}><DocPage /></Suspense>} />
          </Route>
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
