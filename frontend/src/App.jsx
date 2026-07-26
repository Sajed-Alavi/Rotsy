import { Routes, Route, Navigate } from 'react-router-dom';
import AppShell from './components/AppShell.jsx';
import ProtectedRoute from './components/ProtectedRoute.jsx';
import LoginPage from './features/auth/LoginPage.jsx';
import DashboardPage from './features/dashboard/DashboardPage.jsx';
import BrowsePage from './features/browse/BrowsePage.jsx';
import StorageAnalyzerPage from './features/storage/StorageAnalyzerPage.jsx';
import MetricsPage from './features/metrics/MetricsPage.jsx';
import JobsPage from './features/jobs/JobsPage.jsx';
import AlertsPage from './features/alerts/AlertsPage.jsx';
import SettingsPage from './features/settings/SettingsPage.jsx';
import UsersPage from './features/users/UsersPage.jsx';
import RolesPage from './features/roles/RolesPage.jsx';
import RetentionPage from './features/retention/RetentionPage.jsx';
import SystemPage from './features/system/SystemPage.jsx';
import ScanPage from './features/scan/ScanPage.jsx';
import BlobstoresPage from './features/blobstores/BlobstoresPage.jsx';
import RepositoriesPage from './features/repositories/RepositoriesPage.jsx';
import {
  AccessPage, AnalyticsPage,
} from './features/comingsoon/ComingSoonPage.jsx';

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<AppShell />}>
          <Route index element={<DashboardPage />} />
          <Route path="browse" element={<BrowsePage />} />
          <Route path="storage" element={<StorageAnalyzerPage />} />
          <Route path="metrics" element={<MetricsPage />} />
          <Route path="jobs" element={<JobsPage />} />
          <Route path="alerts" element={<AlertsPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="retention" element={<RetentionPage />} />
          <Route path="blobstores" element={<BlobstoresPage />} />
          <Route path="system" element={<SystemPage />} />
          <Route path="scan" element={<ScanPage />} />
          <Route path="repositories" element={<RepositoriesPage />} />
          <Route path="access" element={<AccessPage />} />
          <Route path="analytics" element={<AnalyticsPage />} />
          <Route path="users" element={<UsersPage />} />
          <Route path="roles" element={<RolesPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
