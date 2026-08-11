import { api } from '../../lib/api.js';

/**
 * Every /scan/* call the feature makes, in one place.
 *
 * The page previously built these URLs inline across six components, so the
 * endpoint list was only discoverable by reading the whole file. Keeping them
 * here means a backend route change has exactly one place to land.
 */
export const scanApi = {
  summary: () => api.get('/scan/summary'),

  targets: () => api.get('/scan/targets'),
  createTarget: (body) => api.post('/scan/targets', body),
  updateTarget: (id, body) => api.patch(`/scan/targets/${id}`, body),
  deleteTarget: (id) => api.delete(`/scan/targets/${id}`),

  images: (limit = 200) => api.get(`/scan/images?limit=${limit}`),
  scanImage: (repo, image) => api.post('/scan/image', { repo, image }),

  /**
   * `repo`/`image` scope the history to one tag (`image` is the full
   * "name:tag" string) — used by the Images tree's per-tag report panel.
   * Omit both for the global recent-reports list (ReportsPage).
   */
  reports: ({ repo, image, limit = 50 } = {}) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (repo) params.set('repo', repo);
    if (image) params.set('image', image);
    return api.get(`/scan/reports?${params.toString()}`);
  },
  report: (id) => api.get(`/scan/reports/${id}`),
  deleteReport: (id) => api.delete(`/scan/reports/${id}`),
  deleteAllReports: () => api.delete('/scan/reports'),
  /** Delete every report (and finding) for one tag — the Images tree's
   * per-tag "delete reports" action, scoped server-side so it needs only
   * delete access to that one image, not everything. */
  deleteReportsFor: (repo, image) =>
    api.delete(`/scan/reports?${new URLSearchParams({ repo, image }).toString()}`),

  dbStatus: () => api.get('/scan/db-status'),
  offlineStatus: () => api.get('/scan/db-offline'),
  /** `force` re-downloads a database the backend would otherwise skip as
   * current; `scanner` scopes the refresh to just "trivy" or "grype" —
   * omitted (or falsy) updates every enabled scanner. */
  updateDb: (force = false, scanner = null) => {
    const params = new URLSearchParams();
    if (force) params.set('force', 'true');
    if (scanner) params.set('scanner', scanner);
    const qs = params.toString();
    const suffix = qs ? `?${qs}` : '';
    return api.post(`/scan/db-update${suffix}`);
  },
  importDb: () => api.post('/scan/db-import'),
  /** The in-flight (or most recent) DB job, so the UI can reattach after a reload. */
  dbJob: () => api.get('/scan/db-job'),
  /** Stops the active job's handler task in-process and kills any subprocess it owns. */
  cancelJob: (jobId) => api.post(`/jobs/${jobId}/cancel`),

  /** Findings, for both the page-level list and one report's modal. */
  findings: (endpoint, params) => api.get(`${endpoint}?${params.toString()}`),
  findingsEndpoint: {
    all: '/scan/vulnerabilities',
    forReport: (id) => `/scan/reports/${id}/vulnerabilities`,
  },

  /** Docker repositories, for the target picker. */
  dockerRepos: () => api.get('/storage/repos?format=docker'),
};
